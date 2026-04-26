"""Context window usage estimator from transcript file."""
import json
from pathlib import Path

_TAIL_BYTES = 262144  # 256 KiB


def _context_size_for(model_id):
    if not model_id:
        return 200000
    lid = model_id.lower()
    if '1m' in lid:
        return 1_000_000
    return 200_000


def get_context_usage(transcript_path, model_id=None):
    """Return fraction 0..1 of context window used, or None if unknown."""
    if not transcript_path:
        return None
    p = Path(transcript_path)
    if not p.exists():
        return None

    try:
        with open(p, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _TAIL_BYTES))
            tail = f.read().decode('utf-8', errors='replace')
    except OSError:
        return None

    last_usage = None
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidates = []
        if isinstance(obj, dict):
            candidates.append(obj.get('usage'))
            msg = obj.get('message')
            if isinstance(msg, dict):
                candidates.append(msg.get('usage'))
        for u in candidates:
            if isinstance(u, dict) and ('input_tokens' in u or 'cache_read_input_tokens' in u):
                last_usage = u
                break
        if last_usage:
            break

    if not last_usage:
        return None

    total = 0
    for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'):
        v = last_usage.get(k)
        if isinstance(v, (int, float)):
            total += v

    ctx = _context_size_for(model_id)
    if not ctx:
        return None
    return min(1.0, total / ctx)
