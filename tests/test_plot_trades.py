"""The plotter is a verification tool, so its own failure mode matters: a chart that
silently draws the wrong thing is worse than no chart. These pin the parts that
could mislead -- which bar a timestamp maps to, and whether any coordinate can
escape its panel."""

import importlib.util
import os
import re
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("plot_trades", os.path.join(ROOT, "scripts", "plot_trades.py"))
plot_trades = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plot_trades)

from models.bar import Bar


def _bars(n=20, base=10.0, vols=None):
    t0 = datetime.fromisoformat("2026-09-22T09:30:00-04:00")
    out = []
    for i in range(n):
        c = base + (i % 5) * 0.05
        out.append(Bar(timestamp=t0 + timedelta(minutes=5 * i), open=c, high=c + 0.05,
                       low=c - 0.05, close=c + 0.02,
                       volume=(vols[i] if vols else 1000.0)))
    return out


def _trade(bars, **over):
    t = dict(ticker="TEST", date="2026-09-22",
             entry_time=bars[4].timestamp.isoformat(), entry_price=10.10,
             exit_time=bars[12].timestamp.isoformat(), exit_price=10.30,
             initial_stop=10.00, initial_target=10.50, shares=100,
             net_profit=20.0, r_return=2.0, exit_reason="TRAILING_STOP",
             setup_type="VWAP_RECLAIM", setup_score=80.0,
             maximum_favorable_excursion=0.3, maximum_adverse_excursion=-0.05)
    t.update(over)
    return t


def test_bar_index_exact_match():
    bars = _bars()
    assert plot_trades.bar_index(bars, bars[7].timestamp.isoformat()) == 7


def test_bar_index_falls_back_to_the_preceding_bar():
    # an exit can land mid-bar; it belongs to the bar it is inside, not the next one
    bars = _bars()
    mid = (bars[7].timestamp + timedelta(minutes=2)).isoformat()
    assert plot_trades.bar_index(bars, mid) == 7


def test_bar_index_before_the_session_is_none_not_zero():
    bars = _bars()
    early = (bars[0].timestamp - timedelta(minutes=30)).isoformat()
    assert plot_trades.bar_index(bars, early) is None


def test_bar_index_on_empty_bars():
    assert plot_trades.bar_index([], "2026-09-22T10:00:00-04:00") is None


def _coords(svg):
    return [float(v) for v in re.findall(r'(?:x|y|x1|y1|x2|y2)="(-?[\d.]+)"', svg)]


def test_no_coordinate_is_nan_or_infinite():
    svg = plot_trades.figure(_trade(_bars()), _bars())
    assert "nan" not in svg.lower() and "inf" not in svg.lower()
    assert all(v == v and abs(v) < 1e6 for v in _coords(svg))


def test_a_dominant_opening_bar_does_not_push_volume_off_the_panel():
    # the real case this guards: DRD 2026-09-22 opened at 35x its session median,
    # which flattened every other bar. The p95 cap must clip, not overflow.
    vols = [61138.0] + [1738.0] * 19
    bars = _bars(vols=vols)
    svg = plot_trades.figure(_trade(bars), bars)
    assert "p95 cap" in svg
    assert all(v > -1.0 for v in _coords(svg))


def test_entry_and_exit_are_both_drawn():
    svg = plot_trades.figure(_trade(_bars()), _bars())
    assert "entry 10.10" in svg and "exit 10.30" in svg
    assert 'class="mark entry"' in svg and 'class="mark exit"' in svg


def test_stop_and_target_are_labelled_not_colour_alone():
    svg = plot_trades.figure(_trade(_bars()), _bars())
    assert "stop 10.00" in svg and "target 10.50" in svg


def test_price_scale_stretches_to_include_stop_and_target_outside_the_bars():
    # a stop far below every bar must still be on the chart
    bars = _bars()
    svg = plot_trades.figure(_trade(bars, initial_stop=5.0), bars)
    assert "stop 5.00" in svg
    assert all(v > -1.0 for v in _coords(svg))


def test_ticker_is_escaped_into_the_caption():
    bars = _bars()
    svg = plot_trades.figure(_trade(bars, ticker="A&B"), bars)
    assert "A&amp;B" in svg and "<span class=\"tkr\">A&B" not in svg


def test_zero_volume_session_does_not_divide_by_zero():
    bars = _bars(vols=[0.0] * 20)
    svg = plot_trades.figure(_trade(bars), bars)
    assert all(v == v for v in _coords(svg))


def test_app_headline_total_matches_the_journal_not_rounded_values(tmp_path):
    """The page rounds each trade to the cent for size; summing those rounded values
    drifts from the backtest's own total (9c across the 1,001 holdout trades). A
    verification tool disagreeing with the thing it verifies is the one thing it
    must not do."""
    bars = _bars()
    day = bars[0].timestamp.strftime("%Y-%m-%d")
    trades = []
    for i in range(12):
        t = _trade(bars, net_profit=0.004 + i * 0.001)
        t["entry_time"] = bars[i].timestamp.isoformat()
        trades.append(t)
    exact = sum(t["net_profit"] for t in trades)

    out = tmp_path / "app.html"
    monkey = plot_trades.load_bars
    plot_trades.load_bars = lambda tk, d: bars
    try:
        assert plot_trades.build_app(trades, "_t", str(out)) == 0
    finally:
        plot_trades.load_bars = monkey

    html_text = out.read_text()
    assert f"${exact:,.2f}" in html_text
    # and the naive rounded sum, if different, must NOT be what is printed
    rounded = sum(round(t["net_profit"], 2) for t in trades)
    if abs(rounded - exact) >= 0.005:
        assert f"${rounded:,.2f}" not in html_text
