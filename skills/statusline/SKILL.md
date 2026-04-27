---
name: statusline
description: This skill should be used when the user asks to "configure statusline", "change statusline theme", "install statusline", "uninstall statusline", "toggle statusline segment", "edit statusline colors", "show statusline config", "switch to tmux mode", "/statusline", or mentions customizing the Claude Code statusline. Provides an interactive wizard and direct subcommands for the cc-vitals plugin.
argument-hint: "[install | uninstall | mode native|tmux | preset vs-dark-modern|high-contrast|claude-default | toggle <segment> | show | edit]"
allowed-tools: Read, Edit, Write, Bash, AskUserQuestion
---

# Statusline Configuration

Configure the `cc-vitals` plugin. Edits `~/.claude/statusline.json`
(plugin config) and — for install/uninstall/mode only —
`~/.claude/settings.json` (Claude Code user settings).

## Rendering modes

cc-vitals supports two rendering modes; the user picks one per machine.

- **`native`** (default): Claude Code's `statusLine` command runs
  `scripts/statusline.py` and CC re-renders the bar event-driven. No
  external dependencies. Cache TTL shows the wall-clock expiry time
  (HH:mm) — minute-grained, robust to event-driven re-renders.
- **`tmux`**: CC's `statusLine` runs `scripts/ingest.py` (mutates state,
  dumps stdin, prints nothing). tmux's status bar runs
  `scripts/render-tmux.py` every second and paints the line itself.
  Cache TTL ticks live as a `mm:ss` countdown. Multi-CC routing handled
  via `CC_VITALS_SLOT` (one tmux session per CC). Requires `tmux ≥ 3.2`.

## Resolving the plugin root

Always resolve `$CLAUDE_PLUGIN_ROOT` once via Bash before writing paths into
files. Claude Code does **not** expand `${CLAUDE_PLUGIN_ROOT}` inside
`~/.claude/settings.json` at runtime — an absolute path must be written.

```bash
echo "$CLAUDE_PLUGIN_ROOT"
```

If the variable is empty (skill not invoked through the plugin runtime), stop
and ask the user to run the skill via the plugin.

## Dispatch on $ARGUMENTS

Parse the first word of `$ARGUMENTS`:

| Argument                   | Action                                             |
|----------------------------|----------------------------------------------------|
| `install`                  | Write the mode-appropriate statusLine command into user settings |
| `uninstall`                | Remove statusLine command from user settings       |
| `mode <native\|tmux>`      | Switch rendering mode (rewrites statusLine; tmux mode also installs the tmux conf snippet) |
| `preset <name>`            | Apply a shipped preset (`vs-dark-modern`, `high-contrast`, `claude-default`) |
| `toggle <segment>`         | Add/remove a segment from line 1 (only line 1 is affected) |
| `show`                     | Print effective config + live preview              |
| `edit`                     | Guide user through editing `~/.claude/statusline.json` |
| empty / unrecognized       | Run the interactive wizard                         |

## Config files

- **Plugin config (writable)**: `~/.claude/statusline.json` — user overrides.
  Write only keys being overridden; never dump the full defaults.
- **Shipped defaults (read-only)**: `$CLAUDE_PLUGIN_ROOT/scripts/default-config.json`.
- **Claude Code user settings**: `~/.claude/settings.json` — only for
  install/uninstall.

## Atomic write pattern

For every write to `~/.claude/settings.json` or `~/.claude/statusline.json`,
use this exact pattern so that the file is never corrupted mid-write:

1. Compute the desired full file contents in memory (whole JSON object,
   **preserving every unrelated key**).
2. `Write` the contents to a sibling temp path, e.g.
   `~/.claude/settings.json.tmp`.
3. Validate via Bash:
   ```bash
   python3 -c 'import json,sys; json.load(open(sys.argv[1]))' ~/.claude/settings.json.tmp
   ```
4. If validation succeeds, `Bash: mv ~/.claude/settings.json.tmp ~/.claude/settings.json`.
5. If validation fails, `Bash: rm ~/.claude/settings.json.tmp` and report the
   error; do not touch the live file.

## Merging JSON while preserving unrelated keys

Never use `Edit` on JSON files for structural mutations — it is string-based
and easily corrupts commas or brackets. Use this pattern instead:

```bash
python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path.home() / ".claude" / "settings.json"
data = json.loads(p.read_text()) if p.exists() else {}
# mutate `data` here (only the keys being changed)
# ...
tmp = p.with_suffix(p.suffix + ".tmp")
tmp.write_text(json.dumps(data, indent=2))
PY
```

Then proceed to the validation + `mv` steps from the atomic write pattern.
This guarantees unrelated keys (other plugins, theme, permissions, hooks…)
are preserved verbatim.

## Install flow

1. `PLUGIN_ROOT=$(echo "$CLAUDE_PLUGIN_ROOT")` — verify non-empty.
2. Read `~/.claude/statusline.json` (`{}` if missing); the active mode is
   `mode` from the merged config (defaults to `native`).
3. Pick the entrypoint script based on the mode:
   - `native`: `SCRIPT="$PLUGIN_ROOT/scripts/statusline.py"`
   - `tmux`:   `SCRIPT="$PLUGIN_ROOT/scripts/ingest.py"`
4. Verify the file exists (`Bash: test -f "$SCRIPT"`).
5. `Read ~/.claude/settings.json` if it exists; treat as `{}` otherwise.
6. If a `statusLine` key already exists and its `.command` does **not**
   reference any cc-vitals script (substring match on `statusline.py` or
   `ingest.py` under the plugin root), use `AskUserQuestion` to confirm
   overwriting before proceeding.
7. Set the `statusLine` key to:
   ```json
   {
     "type": "command",
     "command": "python3 <ABSOLUTE-SCRIPT-PATH>",
     "padding": 0
   }
   ```
   Use a bash heredoc that calls `python3` to mutate the JSON and write the
   temp file (see "Merging JSON" above). Interpolate the absolute script path.
   Explicitly **remove** any pre-existing `refreshInterval` from the block —
   prior versions of cc-vitals recommended `refreshInterval: 1`, which
   corrupts the CC TUI; neither mode needs it (native uses event-driven
   re-renders; tmux owns its own 1 Hz render loop).
8. Validate the temp file parses as JSON; then `mv` into place.
9. For `tmux` mode, also run the tmux conf install (see "Tmux conf install"
   below) and remind the user about the `cct` wrapper.
10. Inform the user the statusline activates on the next Claude Code
    session restart.

## Uninstall flow

1. Read `~/.claude/settings.json`. If no `statusLine` key, inform the user
   nothing to do and stop.
2. If `statusLine.command` references this plugin (substring match on
   `statusline.py` *or* `ingest.py` under the plugin root), use
   `AskUserQuestion` to confirm removal.
3. If it references a different statusline, warn and stop — do not remove
   a customized entry without explicit confirmation.
4. On confirm: remove the `statusLine` key from the settings object,
   validate, and atomic-write.
5. Leave `~/.claude/statusline.json`, cost data, and the tmux conf snippet
   (if any) in place. Mention each path in the final message so the user
   knows where to delete them if desired:
   - `~/.claude/statusline.json` — plugin config / mode
   - `~/.claude/plugin-data/cc-vitals/` — cost history, dumps, cache state
   - `~/.claude/plugin-data/cc-vitals/cc-vitals.tmux.conf` — tmux snippet
     (and the `source-file` line in `~/.tmux.conf`, if added).

## Mode flow

Switches rendering mode and rewrites everything that has to follow.

1. Validate `<name>` is `native` or `tmux`.
2. For `tmux`: verify `tmux ≥ 3.2` is on PATH:
   ```bash
   tmux -V
   ```
   Refuse with a link to install instructions if missing.
3. Atomic-write `~/.claude/statusline.json` with `config["mode"] = <name>`.
   For `tmux`, also set `config["segments"]["cache"]["style"] = "countdown"`
   unless the user already pinned a style; for `native`, set it to
   `"expiry_clock"` (or remove the key if it matches the default).
4. If `~/.claude/settings.json` already has a `statusLine` block pointing
   at a cc-vitals entrypoint, run the install flow to point it at the new
   mode's entrypoint. Otherwise inform the user that `/statusline install`
   is still required to activate.
5. For `tmux`, run the "Tmux conf install" flow below.
6. For `native`, mention the tmux snippet stays in place (no-op when no
   CC dumps state) and offer to print the line to remove from
   `~/.tmux.conf` if the user is done with tmux mode.

## Tmux conf install

The shipped template at `$CLAUDE_PLUGIN_ROOT/tmux/cc-vitals.conf.template`
uses `__PLUGIN_ROOT__` placeholders. Materialize a per-user copy with
absolute paths so tmux can find the render script.

1. Build the substituted contents in a bash heredoc and write to
   `~/.claude/plugin-data/cc-vitals/cc-vitals.tmux.conf`:
   ```bash
   PLUGIN_ROOT="$CLAUDE_PLUGIN_ROOT"
   DEST="$HOME/.claude/plugin-data/cc-vitals/cc-vitals.tmux.conf"
   mkdir -p "$(dirname "$DEST")"
   sed "s|__PLUGIN_ROOT__|$PLUGIN_ROOT|g" \
     "$PLUGIN_ROOT/tmux/cc-vitals.conf.template" > "$DEST.tmp"
   mv "$DEST.tmp" "$DEST"
   ```
2. Tell the user to add this line to `~/.tmux.conf`:
   ```tmux
   source-file ~/.claude/plugin-data/cc-vitals/cc-vitals.tmux.conf
   ```
   Use `AskUserQuestion` to optionally append it for them. If appending,
   first check the line isn't already present (`grep -F`).
3. Print the launch instructions:
   - `cct` wrapper at `$CLAUDE_PLUGIN_ROOT/bin/cct` — symlink into PATH
     or paste the function form into `~/.bashrc`/`~/.zshrc`:
     ```sh
     cct() { "$CLAUDE_PLUGIN_ROOT/bin/cct" "$@"; }
     ```
   - Manual form: `tmux new-session -s <slot> "claude $@"` (no slot
     routing without `cct`; mtime fallback applies).
4. Mention that an existing tmux session won't pick up the conf changes
   automatically — `tmux source-file ~/.tmux.conf` to reload.

## Preset flow

1. Validate `<name>` is one of `vs-dark-modern`, `high-contrast`,
   `claude-default`. Reject unknown names with a list of valid options.
2. Read `~/.claude/statusline.json` (or `{}` if missing).
3. Set `config["theme"] = "<name>"`. Preserve every other key.
4. Atomic-write back to `~/.claude/statusline.json`.

## Toggle flow

Only line 1 of `lines` is affected. Document this in the response if adding
or removing.

1. Read `~/.claude/statusline.json` (`{}` if missing).
2. Determine effective line 1: user config `lines[0]` if set, else shipped
   default `["model","cwd","git"]`.
3. If `<segment>` is in line 1, remove it; otherwise append it.
4. Write `config["lines"]` (preserving other entries in `lines` from user
   config, or reconstructing from shipped defaults if the user had no
   `lines` override).
5. Atomic-write.

## Show flow

1. Print effective config:
   ```bash
   python3 -c 'import sys; sys.path.insert(0,"'"$CLAUDE_PLUGIN_ROOT"'/scripts/lib"); from config import load_config; import json; print(json.dumps(load_config(), indent=2))'
   ```
   If the import fails (plugin root wrong), fall back to
   `cat ~/.claude/statusline.json` and note that full defaults are in
   `$CLAUDE_PLUGIN_ROOT/scripts/default-config.json`.
2. Render a preview by piping a synthetic session JSON through the script:
   ```bash
   echo '{"session_id":"preview","cwd":"'"$PWD"'","model":{"display_name":"Claude"},"cost":{"total_cost_usd":0}}' \
     | python3 "$CLAUDE_PLUGIN_ROOT/scripts/statusline.py"
   ```

## Edit flow

Do **not** spawn interactive editors (`$EDITOR`, `vi`, `nano`) via `Bash` —
they hang in non-TTY tool execution. Instead:

1. `Read ~/.claude/statusline.json` (create `{}` if missing, write it as a
   seed).
2. Ask the user what they want to change (theme, a specific color, a segment
   setting, layout). Use `AskUserQuestion` to keep it bounded.
3. Apply changes via the "Merging JSON" pattern above.
4. Atomic-write.
5. If the user prefers to edit by hand, print the path and let them edit in
   their own editor outside Claude Code.

## Interactive wizard (no args or unrecognized)

One `AskUserQuestion` with these options:

- Apply a preset
- Toggle a segment on line 1
- Change colors or layout (→ Edit flow)
- Install the statusline into settings.json
- Switch rendering mode (native ↔ tmux)
- Uninstall the statusline
- Show current config and preview
- Cancel

Then run the corresponding flow. Cap interaction at two rounds — advise
`/statusline edit` for deeper tweaks.

## Config schema (for reference while editing)

```jsonc
{
  "mode": "native" | "tmux",
  "theme": "vs-dark-modern" | "high-contrast" | "claude-default" | { ...custom palette... },
  "icons": "nerd" | "ascii",
  "separator": " │ ",
  "lines": [ ["model","cwd","git"], ["env","cost","context"] ],
  "segments": {
    "cwd":  { "max_length": 40, "basename_only": false, "icon": "..." },
    "git":  { "dirty_glyph": "●", "ahead_glyph": "↑", "behind_glyph": "↓" },
    "cost": { "show_session": true, "show_day": true, "show_month": true },
    "cache": { "style": "expiry_clock" | "countdown" }
  },
  "colors": {
    "model": "accent", "cwd": "primary",
    "git.branch": "secondary", "git.dirty": "warning",
    "env": "muted",
    "cost.session": "primary", "cost.day": "muted", "cost.month": "muted",
    "context.normal": "muted", "context.warn": "warning", "context.crit": "error",
    "separator": "dim"
  }
}
```

Custom theme palettes must define: `primary`, `secondary`, `accent`, `muted`,
`warning`, `error`, `success`, `dim`. Color values can be palette tokens
(`"accent"`) or hex strings (`"#D97757"`).

Available segments: `model`, `effort`, `cwd`, `git`, `env`, `cost`,
`cost-day-forecast`, `cost-month-forecast`, `context`, `limits`, `tokens`,
`tokens-session`, `cache`, `duration`, `runtime`, `cc-version`.
(`cost-avg` is a legacy alias for `cost-day-forecast`.)

The `cache` segment shows the wall-clock expiry time of Anthropic's prompt
cache (auto-detects 5m vs 1h tier) plus an urgency-glyph tier
(⏳ → ⏰ <alert → ⚠ <warn → ⚠ expired). Do **not** set `refreshInterval`
in the `statusLine` block — sub-second polling corrupts the CC TUI's diff
renderer, and the HH:mm clock only needs the event-driven re-renders CC
already does. The shipped plugin includes a `PostToolUse` hook that
keeps the expiry accurate during long agent turns.

## Safety recap

- Always atomic-write (tmp file + validate + `mv`).
- Always preserve unrelated keys when editing JSON.
- Never edit `$CLAUDE_PLUGIN_ROOT/scripts/default-config.json`.
- Never invoke interactive editors via Bash.
