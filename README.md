# cc-vitals

Customizable multi-line statusline for Claude Code. Shows at a glance what
model you're using, where you are, your git state, host environment, rolling
cost (session / day / month), and context window usage — themed with a
VS Code Dark Modern palette by default.

## Features

- **Themeable colors** — ships with `vs-dark-modern`, `high-contrast`, and
  `claude-default` palettes. Custom palettes supported.
- **Segments**: `model`, `effort`, `cwd`, `git`, `env`, `cost`,
  `cost-day-forecast`, `cost-month-forecast`, `context`, `limits`, `tokens`,
  `tokens-session`, `cache`, `duration`, `runtime`, `cc-version`. Each can
  be enabled, reordered, or split across multiple lines. `runtime` shells
  out to `node`/`python3`/`rustc`/etc. on every render (bounded at 0.5s
  each) — leave it off unless wanted. (`cost-avg` is kept as an alias for
  `cost-day-forecast`.)
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
  Results are cached per-cwd in plugin-data, fingerprinted by
  `.git/HEAD` and `.git/index` mtimes — branch switches and staging
  changes invalidate immediately, while a short time TTL covers
  working-tree edits. On slow filesystems (WSL→NTFS, network mounts)
  the segment falls back to its previous result rather than blanking
  when `git status` exceeds the timeout. With no cache yet, a
  ⏳ slow marker surfaces in warning color so a misbehaving git is
  visible rather than silently dropped. Tune via
  `segments.git.timeout` (default 3.0s) and `segments.git.cache_ttl`
  (default 5.0s).
- **Context window usage** — read from Claude Code's pre-computed
  `context_window.used_percentage`, with transcript parsing as a fallback.
- **Subscription rate limits** — `limits` segment overlays the 5-hour and
  7-day usage windows in a single bar (5-hour fill overrides 7-day where
  they overlap), each with its own gradient.
- **Token activity** — `tokens` shows last-turn `↑fresh-input ↓output
  +cache-creation`; `tokens-session` shows cumulative `Σ ↑in ↓out`.
- **Prompt-cache health** — `cache` segment shows session hit ratio, the
  wall-clock expiry time of Anthropic's prompt cache (HH:mm), and an
  estimated $ at risk if the cache expires before your next message. Hit
  ratio is rolled up across every assistant turn in the session
  (`Σcache_read / Σ(cache_read + input_tokens + cache_creation)`) —
  per-turn ratios are misleading because Claude Code cache-controls nearly
  all input, leaving `input_tokens` ≈ 0. Totals are aggregated
  incrementally from the transcript and persisted per session. The cache
  tier (5-min vs 1-hour) is auto-detected from the latest assistant turn's
  usage breakdown — new Claude Code installs default to 5-min, older
  installs may still be on 1-hour. A glyph tier conveys urgency on every
  event-driven re-render: ⏳ ok → ⏰ alert → ⚠ warn → ⚠ expired.
  Thresholds scale with the detected tier (alert <60s / warn <15s on
  5-min; alert <5m / warn <1m on 1-hour) and are configurable via
  `segments.cache.ttl_alert_seconds` / `ttl_warn_seconds` (set a number
  to apply across tiers, or `{"1h": N, "5m": M}` to keep them split). The
  expiry clock follows the system timezone by default; if Claude Code
  runs in a UTC container while you read the host clock, override with
  `segments.cache.timezone` — accepts an IANA name
  (`"America/Los_Angeles"`, needs Python 3.9+ `zoneinfo`), a fixed offset
  (`"+05:30"` / `"-0800"`), `"UTC"`, or `"local"` for system default. A
  high-frequency `refreshInterval` isn't needed and is actively harmful —
  sub-second polling corrupts the CC TUI. The shipped plugin includes a `PostToolUse` hook that bumps a
  per-session marker on every tool call, so the expiry clock stays
  accurate during long agent turns when the transcript file mtime would
  otherwise lag the actual API requests.
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

### Forecast segments (pay-per-token planning)

- `cost-day-forecast` — `$X/d ↑ $Y/d`: today's projected total based on
  pace versus typical hourly progress, alongside the rolling daily average.
  Renamed from `cost-avg` (the old name still works).
- `cost-month-forecast` — `$X/mo`: month-to-date plus rolling daily
  average × days remaining. Color-coded by month-to-date pace versus
  typical. Useful for predicting your monthly bill on metered plans.

Both default to a 7-day window for the rolling average; tune via
`segments.cost_day_forecast.window` / `segments.cost_month_forecast.window`.

## Environment Detection

Detects, in order: Docker (`/.dockerenv`), Kubernetes (env var), WSL
(`/proc/version`), virtualization (`systemd-detect-virt`), then native
OS (`platform.system()`).

## Debugging

Three environment variables help when something looks off:

- `CC_VITALS_DEBUG=1` — re-raise exceptions thrown inside a segment renderer
  instead of swallowing them. Without it, a broken segment renders blank so
  the rest of the line keeps working.
- `CC_VITALS_THEME=<name>` — override the configured theme for the current
  process (e.g. `CC_VITALS_THEME=high-contrast`). Useful for quick
  comparisons without editing config.
- `CC_VITALS_DUMP=1` — write the raw stdin JSON Claude Code passes on each
  render to `~/.claude/plugin-data/cc-vitals/last-stdin.json`. Useful for
  checking which fields your Claude Code version exposes. Note that the
  dump may include session-identifying data (transcript path, session id);
  delete it when you're done debugging.

## Tests

```
python3 -m unittest discover -s tests
```

The test suite uses only the standard library — no external dependencies.

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

## 💰 Bounty Contribution

- **Task:** Cache rewrite estimate overestimates
- **Reward:** $3
- **Source:** GitHub-Paid
- **Date:** 2026-04-27

