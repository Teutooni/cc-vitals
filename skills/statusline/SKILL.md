---
name: statusline
description: This skill should be used when the user asks to "configure statusline", "change statusline theme", "install statusline", "uninstall statusline", "toggle statusline segment", "edit statusline colors", "show statusline config", "/statusline", or mentions customizing the Claude Code statusline. Provides an interactive wizard and direct subcommands for the cc-vitals plugin.
argument-hint: "[install | uninstall | preset vs-dark-modern|high-contrast|claude-default | toggle <segment> | show | edit]"
allowed-tools: Read, Edit, Write, Bash, AskUserQuestion
---

# Statusline Configuration

Configure the `cc-vitals` plugin. Edits `~/.claude/statusline.json`
(plugin config) and — for install/uninstall only — `~/.claude/settings.json`
(Claude Code user settings).

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
| `install`                  | Write statusLine command into user settings        |
| `uninstall`                | Remove statusLine command from user settings       |
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
2. `SCRIPT="$PLUGIN_ROOT/scripts/statusline.py"` — verify the file exists
   (`Bash: test -f "$SCRIPT"`).
3. `Read ~/.claude/settings.json` if it exists; treat as `{}` otherwise.
4. If a `statusLine` key already exists and its `.command` does **not**
   reference `$SCRIPT`, use `AskUserQuestion` to confirm overwriting before
   proceeding.
5. Set the `statusLine` key to:
   ```json
   {
     "type": "command",
     "command": "python3 <ABSOLUTE-SCRIPT-PATH>",
     "padding": 0
   }
   ```
   Using a bash heredoc that calls `python3` to mutate the JSON and write the
   temp file (see "Merging JSON" above). Interpolate the absolute script path.
6. Validate the temp file parses as JSON; then `mv` into place.
7. Inform the user the statusline activates on the next Claude Code session
   restart.

## Uninstall flow

1. Read `~/.claude/settings.json`. If no `statusLine` key, inform the user
   nothing to do and stop.
2. If `statusLine.command` references this plugin (substring match on
   `statusline.py` under the plugin root), use `AskUserQuestion` to confirm
   removal.
3. If it references a different statusline, warn and stop — do not remove
   a customized entry without explicit confirmation.
4. On confirm: remove the `statusLine` key from the settings object, validate,
   and atomic-write.
5. Leave `~/.claude/statusline.json` and cost data in place (mention this in
   the final message so the user knows where to delete them if desired).

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
- Uninstall the statusline
- Show current config and preview
- Cancel

Then run the corresponding flow. Cap interaction at two rounds — advise
`/statusline edit` for deeper tweaks.

## Config schema (for reference while editing)

```jsonc
{
  "theme": "vs-dark-modern" | "high-contrast" | "claude-default" | { ...custom palette... },
  "icons": "nerd" | "ascii",
  "separator": " │ ",
  "lines": [ ["model","cwd","git"], ["env","cost","context"] ],
  "segments": {
    "cwd":  { "max_length": 40, "icon": "..." },
    "git":  { "dirty_glyph": "●", "ahead_glyph": "↑", "behind_glyph": "↓" },
    "cost": { "show_session": true, "show_day": true, "show_month": true }
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
`cost-avg`, `context`, `limits`, `tokens`, `tokens-session`, `cache`,
`duration`, `runtime`, `cc-version`.

The `cache` segment shows a TTL countdown for Anthropic's 5-minute prompt
cache. Without `refreshInterval` set in `~/.claude/settings.json`, the TTL
only updates when an assistant message arrives — set
`"refreshInterval": 1` in the `statusLine` block to make it tick live.

## Safety recap

- Always atomic-write (tmp file + validate + `mv`).
- Always preserve unrelated keys when editing JSON.
- Never edit `$CLAUDE_PLUGIN_ROOT/scripts/default-config.json`.
- Never invoke interactive editors via Bash.
