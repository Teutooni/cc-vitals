"""Persistent cost aggregation: session + daily (with hourly buckets) + monthly totals."""
from calendar import monthrange
from datetime import datetime

from state import (
    DATA_DIR,
    MAX_SESSIONS,
    load_json,
    prune_sessions_lru,
    save_json_atomic,
)

COSTS_FILE = DATA_DIR / 'costs.json'

_MAX_DAYS = 90
_MAX_MONTHS = 24

# A fresh process per statusline render, so this cache is effectively per-render
# — it lets update_and_get and get_projection share one disk read.
_DATA_CACHE = None


def _load():
    global _DATA_CACHE
    if _DATA_CACHE is None:
        _DATA_CACHE = load_json(COSTS_FILE) or {}
    return _DATA_CACHE


def _save(data):
    save_json_atomic(COSTS_FILE, data)


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
    prune_sessions_lru(sessions, MAX_SESSIONS, key='last_seen')


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

    prev_entry = sessions.get(session_id, {})
    prev = float(prev_entry.get('last_cost', 0.0))
    delta = max(0.0, float(session_cost) - prev)

    raw_today = days.get(day_key)
    day = _normalize_day(raw_today) if raw_today is not None else {'total': 0.0, 'hours': {}}
    if delta:
        day['total'] += delta
        day['hours'][hour] = day['hours'].get(hour, 0.0) + delta
        # months entries are bare floats; coerce in case of legacy ints/strings.
        months[month_key] = float(months.get(month_key, 0.0) or 0.0) + delta
        days[day_key] = day

    if delta or prev_entry.get('last_cost') != float(session_cost):
        sessions[session_id] = {
            'last_cost': float(session_cost),
            'last_seen': now.isoformat(timespec='seconds'),
        }
        _prune(data)
        _save(data)

    return (
        float(session_cost),
        float(day.get('total', 0.0)),
        float(months.get(month_key, 0.0) or 0.0),
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

    raw_days = _load().get('days', {})
    past_keys = sorted(k for k in raw_days if k < today_key)[-window:]
    if not past_keys:
        return None

    past = {k: _normalize_day(raw_days[k]) for k in past_keys}
    avg = sum(past[k]['total'] for k in past_keys) / len(past_keys)

    past_with_hours = [past[k] for k in past_keys if past[k]['hours']]
    if past_with_hours:
        cum = [
            sum(v for h, v in d['hours'].items() if h <= current_hour)
            for d in past_with_hours
        ]
        expected_by_now = sum(cum) / len(cum)
    else:
        expected_by_now = 0.0

    today_raw = raw_days.get(today_key)
    today_so_far = _normalize_day(today_raw)['total'] if today_raw is not None else 0.0

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


def get_month_projection(window=7):
    """Project the calendar-month total from month-to-date plus a rolling
    daily average for the days remaining.

    `forecast = month_so_far + avg_daily * days_remaining` (today's partial
    spend is already inside `month_so_far`; we don't double-count the
    remainder of today).

    `ratio` compares month-to-date against what you'd typically have spent
    by this point in the month — full historical days for the prior days,
    plus today's hourly cumulative — so it's directly comparable to the
    same-named field on `get_projection`.

    Returns None if there's no usable history for an avg.
    """
    proj = get_projection(window=window)
    if not proj:
        return None

    now = datetime.now()
    month_key = now.strftime('%Y-%m')
    days_in_month = monthrange(now.year, now.month)[1]
    day_of_month = now.day
    days_remaining = max(0, days_in_month - day_of_month)

    months = _load().get('months', {})
    month_so_far = float(months.get(month_key, 0.0) or 0.0)

    avg = proj['avg']
    forecast = month_so_far + avg * days_remaining

    expected_to_date = avg * (day_of_month - 1) + (proj.get('expected_by_now') or 0.0)
    ratio = month_so_far / expected_to_date if expected_to_date > 0 else None

    return {
        'forecast': forecast,
        'month_so_far': month_so_far,
        'avg_daily': avg,
        'days_in_month': days_in_month,
        'day_of_month': day_of_month,
        'days_remaining': days_remaining,
        'ratio': ratio,
        'enough': proj.get('enough', False),
    }
