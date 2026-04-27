"""Event-driven state mutation, shared by every CC `statusLine` entrypoint.

A single CC turn must advance:
  - cost totals (session_id → last_cost; rolls into day / hour / month)
  - cache hit/tier state (transcript tail → session totals + tier seconds)

Both writes used to happen lazily inside the render path. That worked when
each render *was* a CC event (native mode) but breaks once a second renderer
(tmux's 1 Hz status bar) starts calling the same code paths — deltas would
be applied repeatedly or race the writer.

`ingest()` runs the mutating steps exactly once per CC event. The render
path then becomes a pure read of persisted state, safe to invoke at any
frequency. Native mode calls `ingest()` from statusline.py before rendering;
tmux mode (PR 2) wires it as the CC `statusLine` command.
"""
from cache import get_session_cache_state
from cost import ingest_cost


def ingest(data):
    """Apply a CC stdin payload to persisted state. Idempotent on a no-op
    payload (same session_cost, same transcript size).

    Errors are swallowed inside the underlying writers (they already use
    atomic-write + best-effort patterns) so a partial mutation never blocks
    the render that follows."""
    if not isinstance(data, dict):
        return
    session_id = data.get('session_id') or 'unknown'
    cost_info = data.get('cost') or {}
    session_cost = cost_info.get('total_cost_usd', 0.0) or 0.0
    ingest_cost(session_id, session_cost)

    transcript = data.get('transcript_path')
    if transcript and session_id:
        get_session_cache_state(transcript, session_id)
