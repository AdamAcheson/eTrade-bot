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
| 2026-09-17 | -0.15% | 1831 | 3 | -$60.19 | 0.70 | low volume (797) | 3 trade(s) |
| 2026-09-18 | -0.28% | 1774 | 2 | -$232.05 | 0.63 | benchmark confirmation (751) | 2 trade(s) |

## Routine reliability

**The diagnosis recorded here on 2026-09-14 was WRONG.** It blamed the missing
API key. Corrected 2026-09-17 after measuring instead of inferring:

* A session created fresh in this cloud environment DOES receive
  `TWELVEDATA_API_KEY` from the environment config. Verified by probe: "present
  (32 chars)". The key was fixed days ago and nobody confirmed it.
* What a fired session does NOT get is **the repository**. The routine's stored
  `session_request.config.sources` is `[]`, so nothing is cloned. The probe
  session came up in `/home/user` with no `eTrade-bot` directory at all.

So `cd /home/user/eTrade-bot` failed on the first line, every step after it was a
no-op, and the session exited cleanly in ~30 seconds. Runs on 09-14 (35s) and
09-16 (29s) have the same signature. `last_run.status` describes whether the
SESSION exited cleanly, never whether the work happened -- so it read SUCCEEDED
both times.

The lesson worth keeping: a scheduled job whose steps live in prose cannot fail.
Only a process with an exit code can.

### The fix

`scripts/daily_replay.py` does the whole replay in one process with a real exit
code contract -- 0 ran, 2 no key, 3 not a trading session, 4 data unusable (row
still appended), 5 subprocess failed -- and refuses to log the same date twice, so
a retrying scheduler cannot double-count a session.

`.github/workflows/daily-replay.yml` runs it on schedule. CI rather than an agent
session because of where the key can safely live: `.env` is gitignored, and the
cloud environment's Environment variables box warns in its own UI that values are
visible to anyone using that environment. A GitHub Actions secret is the only
place in this project's infrastructure built to hold a credential.

The workflow runs at **22:30 UTC**, which also retires a bug never hit in
production: the old cron `30 21 * * 1-5` was 17:30 ET only during EDT, and would
have fired at 16:30 ET under EST -- half an hour before the close, and two hours
before Twelve Data finishes consolidating volume. 22:30 UTC is 18:30 ET in summer
and 17:30 ET in winter. Both are after the close and after consolidation, so no
DST handling is needed at all.

**One manual step remains:** add `TWELVEDATA_API_KEY` under the repo's
Settings -> Secrets and variables -> Actions. Until then the workflow fails loudly
on its first step, which is the intended behaviour.

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
