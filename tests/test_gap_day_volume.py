"""Gap-conditioned relative-volume threshold.

relative_volume compares today's cumulative volume against the same clock time on
an average day. After a large overnight gap that comparison is unfair in one
direction: the repricing happened while the market was shut, so the session opens
with the work already done and trades quietly for the rest of the day. The gate
then reads "no interest" when what actually happened is "interest already spent".

The motivating case is SVM on 2026-09-17: gapped +5.19%, drifted +3.53% intraday,
closed +8.91%, and its RVOL decayed from 0.77 to 0.36 -- never within reach of the
1.10 gate.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from factories import base_stock_snapshot  # noqa: E402

from strategy.setups import basic_eligibility  # noqa: E402

GATES = dict(max_spread_pct=0.50, min_relative_volume=1.10)


def _svm_on_the_17th(rvol=0.56, gap=5.19):
    """SVM as it actually traded: comfortably above VWAP and its 9EMA, priced well
    clear of the tick screen, and rejected purely on volume."""
    return base_stock_snapshot(last_price=12.19, bid=12.18, ask=12.19, vwap=12.08,
                               ema_9=12.15, swing_low=11.71, relative_volume=rvol,
                               overnight_gap_pct=gap)


def test_the_motivating_case_is_admitted_once_the_gap_is_accounted_for():
    assert basic_eligibility(_svm_on_the_17th(), **GATES).reason == "REJECTED_LOW_VOLUME"
    assert basic_eligibility(_svm_on_the_17th(), **GATES,
                             gap_day_min_gap_pct=3.0,
                             gap_day_min_relative_volume=0.50).eligible


def test_a_quiet_day_still_needs_the_full_volume_threshold():
    """The relaxation must apply ONLY to gap days. A normal session at RVOL 0.56 is
    exactly what the gate exists to refuse."""
    normal = _svm_on_the_17th(gap=0.4)
    assert basic_eligibility(normal, **GATES, gap_day_min_gap_pct=3.0,
                             gap_day_min_relative_volume=0.50).reason == "REJECTED_LOW_VOLUME"


def test_a_gap_day_that_is_still_too_quiet_is_refused():
    """Relaxed is not disabled -- the relaxed threshold is a real gate."""
    assert basic_eligibility(_svm_on_the_17th(rvol=0.30), **GATES, gap_day_min_gap_pct=3.0,
                             gap_day_min_relative_volume=0.50).reason == "REJECTED_LOW_VOLUME"


def test_a_gap_DOWN_counts_too():
    """The mechanism is about the size of the overnight repricing, not its
    direction -- a big gap down leaves the same quiet session behind it."""
    assert basic_eligibility(_svm_on_the_17th(gap=-5.19), **GATES, gap_day_min_gap_pct=3.0,
                             gap_day_min_relative_volume=0.50).eligible


def test_disabled_by_default_and_by_zero_threshold():
    """Omitting the parameters, or a zero gap threshold, must reproduce the old
    behaviour exactly."""
    assert basic_eligibility(_svm_on_the_17th(), **GATES).reason == "REJECTED_LOW_VOLUME"
    assert basic_eligibility(_svm_on_the_17th(), **GATES, gap_day_min_gap_pct=0.0,
                             gap_day_min_relative_volume=0.50).reason == "REJECTED_LOW_VOLUME"


def test_missing_gap_data_falls_back_to_the_standard_gate():
    """First session of a cold start has no prior close, so the gap is unknown. An
    unknown gap must not be treated as a gap day."""
    snap = _svm_on_the_17th()
    snap.overnight_gap_pct = None
    assert basic_eligibility(snap, **GATES, gap_day_min_gap_pct=3.0,
                             gap_day_min_relative_volume=0.50).reason == "REJECTED_LOW_VOLUME"
