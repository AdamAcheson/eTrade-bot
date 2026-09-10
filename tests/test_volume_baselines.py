"""build_volume_baselines was rewritten from the obvious O(days^2) form to running
totals, because a backtest over 750 days of cached history took ~35 minutes --
long enough that parameter sweeps stopped being practical. These tests pin the
behavior that rewrite had to preserve: identical numbers, and no lookahead."""

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from backtest import build_volume_baselines  # noqa: E402

from models.bar import Bar  # noqa: E402


def day_bars(day, volumes):
    base = datetime(2026, 3, day, 9, 30)
    return [Bar(timestamp=base + timedelta(minutes=5 * i), open=10.0, high=10.0,
                low=10.0, close=10.0, volume=v) for i, v in enumerate(volumes)]


def reference(bars_by_day):
    """The original implementation, kept here as the oracle."""
    days_sorted = sorted(bars_by_day.keys())
    cum_by_day_index = {}
    for day in days_sorted:
        cum, cums = 0.0, []
        for b in bars_by_day[day]:
            cum += b.volume
            cums.append(cum)
        cum_by_day_index[day] = cums

    out = {}
    for idx, day in enumerate(days_sorted):
        prior = days_sorted[:idx]
        if not prior:
            out[day] = {}
            continue
        max_len = max(len(cum_by_day_index[d]) for d in prior)
        baseline = {}
        for i in range(max_len):
            values = [cum_by_day_index[d][i] for d in prior if i < len(cum_by_day_index[d])]
            if values:
                baseline[i] = sum(values) / len(values)
        out[day] = baseline
    return out


def test_first_day_has_no_baseline():
    """A real bot's cold start: nothing to compare the first session against."""
    assert build_volume_baselines({"2026-03-02": day_bars(2, [100, 200])})["2026-03-02"] == {}


def test_baseline_averages_only_strictly_earlier_days():
    bars = {
        "2026-03-02": day_bars(2, [100, 100]),
        "2026-03-03": day_bars(3, [300, 300]),
        "2026-03-04": day_bars(4, [999, 999]),   # must not influence 03-03
    }
    out = build_volume_baselines(bars)
    assert out["2026-03-03"] == {0: 100.0, 1: 200.0}
    # 03-04 sees 03-02 and 03-03: cumulative index 0 -> (100+300)/2
    assert out["2026-03-04"][0] == 200.0


def test_ragged_days_average_over_the_days_that_reach_that_index():
    # A half day (early close) has fewer bars; later indices average only the
    # full sessions that actually reached them.
    bars = {
        "2026-03-02": day_bars(2, [100, 100, 100]),
        "2026-03-03": day_bars(3, [200]),          # early close
        "2026-03-04": day_bars(4, [0]),
    }
    out = build_volume_baselines(bars)
    assert out["2026-03-04"][0] == 150.0           # both days have index 0
    assert out["2026-03-04"][2] == 300.0           # only 03-02 reaches index 2


def test_matches_the_original_implementation_on_ragged_input():
    bars = {}
    for d in range(2, 22):
        length = 3 if d % 5 else 1                 # sprinkle in short sessions
        bars[f"2026-03-{d:02d}"] = day_bars(d, [10.0 * d * (i + 1) for i in range(length)])
    assert build_volume_baselines(bars) == reference(bars)
