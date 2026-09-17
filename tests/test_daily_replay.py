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
