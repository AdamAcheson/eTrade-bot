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
| 2026-09-15 | +0.07% | 1823 | 2 | +$93.53 | 0.49 | benchmark confirmation (841) | AUGO stopped -1.00R, CDE trailing stop +2.10R; first session on the 24-ticker universe |
| 2026-09-16 | **-2.23%** | 1837 | 0 | $0 | 0.60 | benchmark confirmation (1172) | SIL closed above its VWAP on **0 of 78 bars**; GDX 2/78, SILJ 1/78 |

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


## 2026-09-16: the cleanest zero-trade session yet

Silver fell 2.23% open-to-close and the miners fell harder -- SIL -4.06%, SILJ
-3.98%, GDX -3.28%, each down 5-6% peak-to-trough. It was a straight-down session
with no reclaim at any point:

| benchmark | open->close | peak->trough | bars closing ABOVE VWAP |
|---|---|---|---|
| SIL | -4.06% | -5.95% | **0 of 78** |
| SILJ | -3.98% | -6.07% | 1 of 78 |
| GDX | -3.28% | -4.99% | 2 of 78 |
| GDXJ | -3.68% | -5.77% | 6 of 78 |
| SLV | -2.23% | -4.02% | 25 of 78 |

Every benchmark closed below its own VWAP on the first bar and stayed there.
`benchmark_confirmation.require_price_above_vwap` therefore failed all day, giving
1172 of 1837 rejections and zero entries.

This is the gate working, not failing. The holdout says a >2% open-to-close drop
in silver produces a zero-trade session 71% of the time, and average P&L on those
days is POSITIVE (+$34) precisely because the bot declines to trade rather than
buying a falling tape. A long-only intraday momentum strategy has no business
being long on a day when its entire sector loses 4%.

Compare 09-11, the other zero-trade session logged here: that one was a gap-up
that faded, SIL above VWAP on 7 of 78 bars. Today needed no such subtlety -- the
tape was never up.

### Running tally

Five sessions logged, 3 with trades, 2 with zero. Cumulative P&L **+$110.43**
across 3 trades. Far too small a sample to compare against the backtest's ~30%
zero-trade rate and 2.6 trades per active session; the point remains accumulation.
