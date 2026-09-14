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
