"""Persistent cost aggregation: session + daily (with hourly buckets) + monthly totals."""
import json
import os
from datetime import datetime
from pathlib import Path

DATA_DIR = Path.home() / '.claude' / 'plugin-data' / 'cc-vitals'
COSTS_FILE = DATA_DIR / 'costs.json'

_MAX_SESSIONS = 200
_MAX_DAYS = 90
_MAX_MONTHS = 24


def _load():
    if not COSTS_FILE.exists():
        return {}
    try:
        with open(COSTS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = COSTS_FILE.with_suffix('.tmp')
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, COSTS_FILE)
    except OSError:
        pass


def _normalize_day(v):
    """Days are stored as {total, hours:{0..23}}. Older entries are bare floats."""
    if isinstance(v, dict):
        return {
            'total': float(v.get('total', 0.0)),
            'hours': {int(k): float(val) for k, val in (v.get('hours') or {}).items()},
        }
    return {'total': float(v or 0.0), 'hours': {}}


def _prune(data):
    sessions = data.get('sessions', {})
    days = data.get('days', {})
    months = data.get('months', {})
    if len(days) > _MAX_DAYS:
        for k in sorted(days)[:-_MAX_DAYS]:
            del days[k]
    if len(months) > _MAX_MONTHS:
        for k in sorted(months)[:-_MAX_MONTHS]:
            del months[k]
    if len(sessions) > _MAX_SESSIONS:
        ordered = sorted(sessions.items(), key=lambda kv: kv[1].get('last_seen', ''))
        for k, _ in ordered[:-_MAX_SESSIONS]:
            del sessions[k]


def update_and_get(session_id, session_cost):
    """Record current session cost, roll daily/hourly/monthly totals using a delta
    against the last recorded value for this session. Returns
    (session_cost, day_total, month_total)."""
    now = datetime.now()
    day_key = now.strftime('%Y-%m-%d')
    month_key = now.strftime('%Y-%m')
    hour = now.hour

    data = _load()
    sessions = data.setdefault('sessions', {})
    days = data.setdefault('days', {})
    months = data.setdefault('months', {})

    for k in list(days.keys()):
        days[k] = _normalize_day(days[k])

    prev = sessions.get(session_id, {}).get('last_cost', 0.0)
    delta = max(0.0, float(session_cost) - float(prev))

    day = days.setdefault(day_key, {'total': 0.0, 'hours': {}})
    if delta:
        day['total'] += delta
        day['hours'][hour] = day['hours'].get(hour, 0.0) + delta
        months[month_key] = months.get(month_key, 0.0) + delta

    sessions[session_id] = {
        'last_cost': float(session_cost),
        'last_seen': now.isoformat(timespec='seconds'),
    }

    _prune(data)
    _save(data)

    return (
        float(session_cost),
        float(day.get('total', 0.0)),
        float(months.get(month_key, 0.0)),
    )


def get_projection(window=7, min_expected=0.05, min_days=3):
    """Compute rolling daily average (excluding today) and pace ratio.

    `ratio` answers: has today's spend-so-far already exceeded what you'd
    *typically* have spent by this hour on prior days? Uses per-hour cumulative
    means over the past `window` days that have hourly data.

    Returns None if no past days are recorded.
    """
    now = datetime.now()
    today_key = now.strftime('%Y-%m-%d')
    current_hour = now.hour

    data = _load()
    days = {k: _normalize_day(v) for k, v in data.get('days', {}).items()}

    past_keys = sorted(k for k in days if k < today_key)[-window:]
    if not past_keys:
        return None

    avg = sum(days[k]['total'] for k in past_keys) / len(past_keys)

    past_with_hours = [days[k] for k in past_keys if days[k]['hours']]
    if past_with_hours:
        cum = [
            sum(v for h, v in d['hours'].items() if h <= current_hour)
            for d in past_with_hours
        ]
        expected_by_now = sum(cum) / len(cum)
    else:
        expected_by_now = 0.0

    today = days.get(today_key)
    today_so_far = today['total'] if today else 0.0

    enough = len(past_with_hours) >= min_days and expected_by_now >= min_expected
    ratio = today_so_far / expected_by_now if expected_by_now > 0 else None

    return {
        'avg': avg,
        'today_so_far': today_so_far,
        'expected_by_now': expected_by_now,
        'ratio': ratio,
        'enough': enough,
        'days_sampled': len(past_keys),
        'hour_days_sampled': len(past_with_hours),
    }
