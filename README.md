# cc-vitals

Customizable multi-line statusline for Claude Code. Shows at a glance what
model you're using, where you are, your git state, host environment, rolling
cost (session / day / month), and context window usage — themed with a
VS Code Dark Modern palette by default.

## Features

- **Themeable colors** — ships with `vs-dark-modern`, `high-contrast`, and
  `claude-default` palettes. Custom palettes supported.
- **Segments**: `model`, `effort`, `cwd`, `git`, `env`, `cost`, `cost-avg`,
  `context`, `limits`, `tokens`, `tokens-session`, `cache`, `duration`,
  `runtime`, `cc-version`. Each can be enabled, reordered, or split across
  multiple lines. `runtime` shells out to `node`/`python3`/`rustc`/etc. on
  every render (bounded at 0.5s each) — leave it off unless wanted.
- **Multi-line layout** — configure any number of lines via `lines` array.
- **Nerd Font icons with ASCII fallback** — one config flag flips all icons.
- **Rolling cost tracking** — session cost from Claude Code, daily and
  monthly totals aggregated locally in
  `~/.claude/plugin-data/cc-vitals/costs.json`.
- **Environment detection** — native Linux / macOS / Windows, WSL, Docker,
  Kubernetes, common VMs via `systemd-detect-virt`.
- **Git details** — branch, dirty marker, ahead/behind counts, upstream
  tracking, worktree marker, and an in-progress operation badge for
  merge / rebase (with step/total) / cherry-pick / revert / bisect.
- **Context window usage** — read from Claude Code's pre-computed
  `context_window.used_percentage`, with transcript parsing as a fallback.
- **Subscription rate limits** — `limits` segment overlays the 5-hour and
  7-day usage windows in a single bar (5-hour fill overrides 7-day where
  they overlap), each with its own gradient.
- **Token activity** — `tokens` shows last-turn `↑fresh-input ↓output
  +cache-creation`; `tokens-session` shows cumulative `Σ ↑in ↓out`.
- **Prompt-cache health** — `cache` segment shows session hit ratio, TTL
  countdown on Anthropic's prompt cache, and an estimated $ at risk if the
  cache expires before your next message. Hit ratio is rolled up across
  every assistant turn in the session (`Σcache_read / Σ(cache_read +
  input_tokens + cache_creation)`) — per-turn ratios are misleading because
  Claude Code cache-controls nearly all input, leaving `input_tokens` ≈ 0.
  Totals are aggregated incrementally from the transcript and persisted per
  session, so renders stay sub-millisecond even with `refreshInterval: 1`.
  The cache tier (5-min vs 1-hour) is auto-detected from the latest
  assistant turn's usage breakdown — Claude Code currently uses the 1-hour
  tier. To make the TTL tick live between assistant messages, set
  `"refreshInterval": 1` in the `statusLine` block of
  `~/.claude/settings.json`.
- **Zero runtime deps** beyond Python 3 standard library.

## Requirements

- Python 3.8+
- `git` on PATH (for the `git` segment — silently skipped if missing)
- A Nerd Font in your terminal for the default icon style (switch to
  `"icons": "ascii"` if unavailable)

## Install

1. Install the plugin through your marketplace.
2. In Claude Code, run `/statusline install`. This writes the `statusLine`
   command into `~/.claude/settings.json`. Restart Claude Code to pick it up.

## Configure

Run `/statusline` for the interactive wizard, or edit
`~/.claude/statusline.json` directly. Sub-commands:

- `/statusline show` — print current effective config and a live preview
- `/statusline preset <name>` — apply a shipped preset
- `/statusline toggle <segment>` — add/remove a segment from line 1
- `/statusline edit` — open the config file in your `$EDITOR`
- `/statusline uninstall` — remove the `statusLine` entry from settings

## Config Example

```json
{
  "theme": "vs-dark-modern",
  "icons": "nerd",
  "separator": " │ ",
  "lines": [
    ["model", "cwd", "git"],
    ["env", "cost", "context"]
  ],
  "segments": {
    "cwd":  { "max_length": 40 },
    "cost": { "show_session": true, "show_day": true, "show_month": true }
  },
  "colors": {
    "model": "accent",
    "git.dirty": "warning"
  }
}
```

User config is **deep-merged** over shipped defaults (nested objects like
`segments` and `colors` merge key-by-key), so you only need to include keys
you want to override.

## Themes

| Name              | Feel                                              |
|-------------------|---------------------------------------------------|
| `vs-dark-modern`  | VS Code Dark Modern (default) — cool blues/greys  |
| `high-contrast`   | Accessibility-first, bright on black              |
| `claude-default`  | Claude brand orange accent on warm neutrals       |

Override any token with a hex color:

```json
{
  "theme": { "accent": "#D97757", "primary": "#E5E5E5", "...": "..." }
}
```

## Cost Tracking Notes

- **Session**: taken directly from Claude Code's `cost.total_cost_usd`
  (cumulative for the session).
- **Day / Month**: accumulated from the delta between consecutive renders
  for each `session_id`. Safe across session resumes and restarts — never
  double-counts.
- Rolls over at local-time midnight (daily) / start of calendar month.
- Data pruned to last 90 days / 24 months / 200 sessions automatically.

## Environment Detection

Detects, in order: Docker (`/.dockerenv`), Kubernetes (env var), WSL
(`/proc/version`), virtualization (`systemd-detect-virt`), then native
OS (`platform.system()`).

## Debugging

Set `CC_VITALS_DUMP=1` in your environment to write the raw stdin
JSON Claude Code passes on each render to
`~/.claude/plugin-data/cc-vitals/last-stdin.json`. Useful for
checking which fields your Claude Code version actually exposes.

## Uninstall

```
/statusline uninstall
```

Removes the `statusLine` key from `~/.claude/settings.json`. Your plugin
config at `~/.claude/statusline.json` and cost history at
`~/.claude/plugin-data/cc-vitals/` remain; delete them manually if
you no longer want them.

## License

MIT
