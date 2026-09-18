"""Tests for the daily replay driver.

The failure this guards against is specific and already happened twice: a
scheduled run that did no work reported SUCCEEDED, because the session exited
cleanly. Prose steps in an agent prompt cannot set an exit code, so the contract
here IS the exit code, and these tests pin it.
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import daily_replay as dr  # noqa: E402


def test_weekends_are_not_trading_days():
    assert not dr.is_trading_day(date(2026, 9, 19))  # Saturday
    assert not dr.is_trading_day(date(2026, 9, 20))  # Sunday
    assert dr.is_trading_day(date(2026, 9, 18))      # Friday


def test_full_day_holidays_are_excluded():
    assert not dr.is_trading_day(date(2026, 12, 25))
    assert not dr.is_trading_day(date(2026, 7, 3))
    assert dr.is_trading_day(date(2026, 12, 24))  # half day, but it DOES trade


def test_missing_key_exits_2_not_0(monkeypatch, capsys):
    """The whole point. Exit 0 here would recreate the bug that made two empty
    runs look like successful replays."""
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["daily_replay.py", "--day", "2026-09-16", "--no-commit"])
    assert dr.main() == 2
    assert "FAILURE" in capsys.readouterr().err


def test_non_trading_day_exits_3_before_checking_the_key(monkeypatch):
    """A weekend is not a failure, and must not be reported as one even when the
    key is also missing -- otherwise every Saturday pages someone."""
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["daily_replay.py", "--day", "2026-09-19", "--no-commit"])
    assert dr.main() == 3


def test_append_row_refuses_to_double_log_a_date(tmp_path, monkeypatch):
    log = tmp_path / "LOG.md"
    log.write_text("| date | x |\n|---|---|\n| 2026-09-15 | a |\n| 2026-09-16 | b |\n")
    monkeypatch.setattr(dr, "LOG", str(log))
    assert dr.append_row("2026-09-16", "| 2026-09-16 | DUPLICATE |") is False
    assert "DUPLICATE" not in log.read_text()
    assert dr.append_row("2026-09-17", "| 2026-09-17 | new |") is True
    assert "new" in log.read_text()


def test_append_row_inserts_after_the_last_row_not_at_end_of_file(tmp_path, monkeypatch):
    """The log has prose sections after the table. Appending to the end of the
    file would put the row below them, outside the table."""
    log = tmp_path / "LOG.md"
    log.write_text("| date |\n|---|\n| 2026-09-15 |\n\n## Notes\n\nprose here\n")
    monkeypatch.setattr(dr, "LOG", str(log))
    dr.append_row("2026-09-16", "| 2026-09-16 |")
    lines = log.read_text().split("\n")
    assert lines.index("| 2026-09-16 |") == lines.index("| 2026-09-15 |") + 1
    assert "## Notes" in log.read_text()


@pytest.mark.parametrize("net,expected", [(0.0, "+$0.00"), (93.53, "+$93.53"), (-88.21, "-$88.21")])
def test_row_formats_sign_and_currency(net, expected):
    s = {"signals": 10, "trades": 1, "net": net, "median_rvol": 0.5,
         "top_rejection": "benchmark confirmation", "top_count": 3, "slv": -2.23}
    assert expected in dr.row_for("2026-09-16", s, "note")


def test_row_handles_a_missing_slv_series():
    """SLV is a benchmark, not a tradeable ticker -- if its fetch lagged, the row
    still has to be well-formed rather than crashing the run after the backtest
    already succeeded."""
    s = {"signals": 0, "trades": 0, "net": 0.0, "median_rvol": float("nan"),
         "top_rejection": "n/a", "top_count": 0, "slv": None}
    row = dr.row_for("2026-09-16", s, "note")
    assert "n/a" in row and row.startswith("| 2026-09-16 |") and row.endswith("|")


# --- running tally -------------------------------------------------------------
# The tally used to be hand-written and drifted: by 2026-09-18 it claimed
# "+$110.43 across 3 trades" while the table summed to -$181.81 across 8. Stale in
# the flattering direction is the worst way for a P&L summary to be wrong.

def _log(tmp_path, rows):
    p = tmp_path / "DAILY_REPLAY_LOG.md"
    body = ["# Daily replay", "", "| date | slv | signals | trades | net | rvol | top | note |",
            "|---|---|---|---|---|---|---|---|"]
    body += rows
    body += ["", "### Running tally", "", "Stale text that must be replaced."]
    p.write_text("\n".join(body) + "\n")
    return p


def test_tally_is_recomputed_from_the_table(tmp_path, monkeypatch):
    import daily_replay
    p = _log(tmp_path, [
        "| 2026-09-11 | -1.00% | 100 | 0 | $0.00 | 0.50 | x (1) | n |",
        "| 2026-09-17 | -0.15% | 100 | 3 | -$60.19 | 0.70 | x (1) | n |",
        "| 2026-09-18 | -0.28% | 100 | 2 | -$232.05 | 0.63 | x (1) | n |",
    ])
    monkeypatch.setattr(daily_replay, "LOG", str(p))
    daily_replay.refresh_tally()
    out = p.read_text()
    assert "3 sessions logged, 2 with trades, 1 with zero" in out
    assert "**-$292.24**" in out
    assert "across 5 trades" in out
    assert "Stale text" not in out


def test_tally_handles_a_positive_total(tmp_path, monkeypatch):
    import daily_replay
    p = _log(tmp_path, [
        "| 2026-09-14 | +1.00% | 100 | 1 | +$16.90 | 0.50 | x (1) | n |",
        "| 2026-09-15 | +1.00% | 100 | 2 | +$93.53 | 0.50 | x (1) | n |",
    ])
    monkeypatch.setattr(daily_replay, "LOG", str(p))
    daily_replay.refresh_tally()
    assert "**+$110.43**" in p.read_text()


def test_appending_a_row_refreshes_the_tally(tmp_path, monkeypatch):
    """The drift happened because appending and summarising were separate steps.
    They must not be separable again."""
    import daily_replay
    p = _log(tmp_path, ["| 2026-09-14 | +1.00% | 100 | 1 | +$16.90 | 0.50 | x (1) | n |"])
    monkeypatch.setattr(daily_replay, "LOG", str(p))
    daily_replay.append_row("2026-09-15", "| 2026-09-15 | +1.00% | 100 | 2 | +$93.53 | 0.50 | x (1) | n |")
    out = p.read_text()
    assert "2 sessions logged, 2 with trades, 0 with zero" in out
    assert "**+$110.43**" in out


def test_a_refused_duplicate_does_not_change_the_tally(tmp_path, monkeypatch):
    import daily_replay
    p = _log(tmp_path, ["| 2026-09-14 | +1.00% | 100 | 1 | +$16.90 | 0.50 | x (1) | n |"])
    monkeypatch.setattr(daily_replay, "LOG", str(p))
    daily_replay.refresh_tally()
    before = p.read_text()
    assert daily_replay.append_row("2026-09-14", "| 2026-09-14 | dup |") is False
    assert p.read_text() == before


def test_an_unparseable_row_is_skipped_not_fatal(tmp_path, monkeypatch):
    """By the time the tally runs the new row is already on disk, so raising would
    fail the job after mutating the log -- worse than an approximate tally."""
    import daily_replay
    p = _log(tmp_path, [
        "| 2026-09-14 | +1.00% | 100 | 1 | +$16.90 | 0.50 | x (1) | n |",
        "| 2026-09-15 |",
        "| 2026-09-16 | +1.00% | 100 | 2 | +$93.53 | 0.50 | x (1) | n |",
    ])
    monkeypatch.setattr(daily_replay, "LOG", str(p))
    daily_replay.refresh_tally()
    out = p.read_text()
    assert "**+$110.43**" in out and "across 3 trades" in out
