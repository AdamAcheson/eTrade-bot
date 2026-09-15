# Daily session replay log

One row per trading session, appended by the scheduled daily replay (17:30 ET
weekdays). The point is accumulation: a single session is noise -- 30% of holdout
sessions took zero trades -- and only a month or more of these makes the live
trade rate and rejection mix comparable to the backtest's.

Backtest baselines to compare against (19-ticker holdout, 568 sessions):

* ~30% of sessions take **zero** trades
* active sessions average **2.6** trades
* dominant rejection is benchmark confirmation
* silver's intraday direction predicts activity: zero-trade rate runs 71% when
  SLV falls >2% open-to-close, 4% when it rises >2%

| date | SLV o->c | signals | trades | net P&L | median RVOL | top rejection | note |
|---|---|---|---|---|---|---|---|
| 2026-09-11 | -1.04% | 1553 | 0 | $0 | 0.64 | benchmark confirmation (1214) | miners gapped up and faded; SIL above its VWAP on 7/78 bars |
| 2026-09-14 | +0.02% | 1606 | 1 | +$16.90 | 0.80 | low volume (810) | one HL VWAP_RECLAIM, trailing stop at +0.22R; routine fired but did no work (see below) |

## Routine reliability

**2026-09-14** -- the scheduled 17:30 ET routine fired and reported SUCCEEDED, but
finished 35 seconds after firing and committed nothing. The fetch alone takes ~4
minutes, so it cannot have run. Cause: TWELVEDATA_API_KEY lives in .env, which is
gitignored and therefore exists only in the container it was created in. A
fresh-session routine clones the repo and gets no .env, so step 1 fails
immediately and the session exits cleanly -- "succeeded" describes the session,
not the work.

This row was produced by running the same four steps by hand at 22:22 ET.

Until the key is available to fired sessions, treat a SUCCEEDED run with no new
commit as a FAILED replay. The log, not the routine's status, is the record.

