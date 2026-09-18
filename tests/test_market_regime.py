"""Day-level market regime filter.

The reason this module needs careful tests is lookahead. "Trade only on days the
Dow and silver were up" read literally needs today's close, which is not known when
the trade is placed; backtesting it that way would trade only on days already known
to be good and produce a spectacular fake result. Both supported modes must be
decidable before the session's first entry window, and these tests pin that.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from data.market_regime import qualifying_days  # noqa: E402


def _write(tmp_path, symbol, rows):
    path = tmp_path / f"daily_{symbol}.json"
    path.write_text(json.dumps({d: {"open": o, "close": c} for d, o, c in rows}))


# day       open close      prior close   -> prior_day?        open>prev close -> open_gap?
ROWS = [
    ("2026-01-02", 100.0, 100.0),
    ("2026-01-05", 100.0, 101.0),   # closed UP vs 100
    ("2026-01-06", 102.0, 100.0),   # prior_day: yes (Jan5 up). open_gap: 102 > 101 yes
    ("2026-01-07",  99.0, 103.0),   # prior_day: no  (Jan6 closed down). open_gap: 99 < 100 no
    ("2026-01-08", 104.0,  98.0),   # prior_day: yes (Jan7 up). open_gap: 104 > 103 yes
]


def test_prior_day_uses_only_settled_history(tmp_path):
    _write(tmp_path, "AAA", ROWS)
    good = qualifying_days(["AAA"], "prior_day", cache_dir=str(tmp_path))
    assert good == {"2026-01-06", "2026-01-08"}
    # Jan 7 closed UP (103 vs 100) yet is excluded -- proof the mode reads
    # yesterday's move, not today's outcome.
    assert "2026-01-07" not in good


def test_open_gap_uses_only_the_open_and_yesterdays_close(tmp_path):
    _write(tmp_path, "AAA", ROWS)
    good = qualifying_days(["AAA"], "open_gap", cache_dir=str(tmp_path))
    assert good == {"2026-01-06", "2026-01-08"}
    # Jan 7 again: it closed up big but GAPPED DOWN, so it is not tradeable under
    # this mode. A rule that looked at the close would have included it.
    assert "2026-01-07" not in good


def test_every_symbol_must_qualify(tmp_path):
    """The rule is 'both were up', so it is an intersection, not a union."""
    _write(tmp_path, "AAA", ROWS)
    _write(tmp_path, "BBB", [
        ("2026-01-02", 50.0, 50.0),
        ("2026-01-05", 50.0, 49.0),   # DOWN
        ("2026-01-06", 50.0, 51.0),
        ("2026-01-07", 50.0, 52.0),
        ("2026-01-08", 50.0, 53.0),
    ])
    both = qualifying_days(["AAA", "BBB"], "prior_day", cache_dir=str(tmp_path))
    # Jan 6 qualifies for AAA but not BBB (BBB fell on Jan 5), so it is out.
    assert "2026-01-06" not in both
    assert both == {"2026-01-08"}


def test_first_days_are_excluded_for_want_of_history(tmp_path):
    """No prior close means no decision -- an unknown regime is not a tradeable one."""
    _write(tmp_path, "AAA", ROWS)
    good = qualifying_days(["AAA"], "prior_day", cache_dir=str(tmp_path))
    assert "2026-01-02" not in good and "2026-01-05" not in good


def test_unknown_mode_is_rejected(tmp_path):
    _write(tmp_path, "AAA", ROWS)
    with pytest.raises(ValueError):
        qualifying_days(["AAA"], "same_day_close", cache_dir=str(tmp_path))
