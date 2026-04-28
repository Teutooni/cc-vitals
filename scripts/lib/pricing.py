"""Per-model pricing constants for prompt-cache cost estimates.

Prices are USD per million tokens. Update when Anthropic changes prices or
releases new models. Match is longest-prefix-first against `model.id`.

The standard Anthropic ratios are:
- cache_read     = 10%  of input
- cache_write_5m = 125% of input
- cache_write_1h = 200% of input
"""

# Opus 4.5+ dropped to a new lower tier; Opus 4 and 4.1 remain on the legacy
# high tier. `lookup` matches longest prefix first, so `claude-opus-4-7-...`
# resolves to the new tier while bare `claude-opus-4-...` / `claude-opus-4-1-...`
# fall through to the legacy entry.
_OPUS_4_5_PLUS = {
    'input':           5.0,
    'output':         25.0,
    'cache_read':      0.50,
    'cache_write_5m':  6.25,
    'cache_write_1h': 10.0,
}

PRICING = {
    'claude-opus-4': {
        'input':          15.0,
        'output':         75.0,
        'cache_read':      1.5,
        'cache_write_5m': 18.75,
        'cache_write_1h': 30.0,
    },
    'claude-opus-4-5': _OPUS_4_5_PLUS,
    'claude-opus-4-6': _OPUS_4_5_PLUS,
    'claude-opus-4-7': _OPUS_4_5_PLUS,
    'claude-sonnet-4': {
        'input':           3.0,
        'output':         15.0,
        'cache_read':      0.30,
        'cache_write_5m':  3.75,
        'cache_write_1h':  6.0,
    },
    'claude-haiku-4': {
        'input':           1.0,
        'output':          5.0,
        'cache_read':      0.10,
        'cache_write_5m':  1.25,
        'cache_write_1h':  2.0,
    },
}

# Sonnet-tier as the safe middle when a model isn't recognized.
_DEFAULT = PRICING['claude-sonnet-4']


def lookup(model_id):
    """Return a pricing dict for `model_id`, falling back to Sonnet-tier."""
    if not model_id:
        return _DEFAULT
    mid = model_id.lower()
    for prefix in sorted(PRICING.keys(), key=len, reverse=True):
        if mid.startswith(prefix):
            return PRICING[prefix]
    return _DEFAULT


def at_risk_cost(cached_tokens, model_id, ttl='5m'):
    """Estimated extra cost on the next request if the prompt cache expires.

    Anthropic bills any given token in exactly one of input / cache_read /
    cache_creation per request — they're mutually exclusive buckets. So:
      Cache hit:  pays cache_read_price for those tokens.
      Cache miss: pays cache_write_price (rebuild) — not input + write.
    Difference per token = cache_write - cache_read.
    """
    if not cached_tokens or cached_tokens <= 0:
        return 0.0
    p = lookup(model_id)
    write_key = 'cache_write_1h' if ttl == '1h' else 'cache_write_5m'
    delta_per_mtok = p[write_key] - p['cache_read']
    return cached_tokens * delta_per_mtok / 1_000_000
