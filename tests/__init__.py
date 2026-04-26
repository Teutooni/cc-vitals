"""Test suite for cc-vitals.

Add `scripts/lib/` to sys.path so tests can `import cache, cost, ...` the
same way segments.py does. Imports here run once at package load time —
each test module relies on this happening first.
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / 'scripts' / 'lib'
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
