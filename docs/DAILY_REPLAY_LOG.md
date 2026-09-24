# Daily session replay log

One row per trading session, appended by `scripts/daily_replay.py`. **Run it by
hand after the close** -- the GitHub schedule was removed on 2026-09-18 because it
never fired (see "Routine reliability" below). The point is accumulation: a single
session is noise -- 30% of holdout sessions took zero trades -- and only a month or
more of these makes the live trade rate and rejection mix comparable to the
backtest's.

**Equity basis of the `net P&L` column changed on 2026-09-23.**

* **Rows up to and including 2026-09-22** are on the old default of $100,000 paper
  equity with $25,000 positions. Divide by 12.5 for a $5,000 / $2,000-per-trade basis
  (2026-09-22: $810.83 here is $64.72 there).
* **Rows from 2026-09-23 on** use the shipped config for the live account: $5,000
  cash, $2,500 per trade, two at a time, never more than the account is worth. No
  conversion needed -- these are the account's own numbers.

All rows are SIMULATED replays of a finished session, not executed trades.

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
| 2026-09-21 | -0.83% | 2171 | 3 | +$35.01 | 0.67 | benchmark confirmation (1098) | 3 trade(s) |
| 2026-09-22 | +2.41% | 2122 | 5 | +$810.83 | 0.69 | low volume (1176) | 5 trade(s) |
| 2026-09-23 | -1.42% | 2194 | 2 | -$38.49 | 0.83 | benchmark confirmation (1044) | BHP and SSRM both stopped at -1.00R; first row on the $5,000 cash config, other entry candidates refused by the two-slot and settled-cash limits |

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

`.github/workflows/daily-replay.yml` runs it. CI rather than an agent session
because of where the key can safely live: `.env` is gitignored, and the cloud
environment's Environment variables box warns in its own UI that values are
visible to anyone using that environment. A GitHub Actions secret is the only
place in this project's infrastructure built to hold a credential.

### The schedule was removed on 2026-09-18, because GitHub never ran it

The workflow carried `cron: 30 22 * * 1-5`, and after the first miss `37 22 * * 1-5`
on the theory that `:30` is a congested slot. **GitHub fired neither**, on 09-17 or
09-18. Everything that would explain it was checked and ruled out:

| check | result |
|---|---|
| scheduled runs recorded | none -- every run is `workflow_dispatch` |
| cron syntax on the default branch | valid, and the file IS on the default branch |
| workflow state | active; manual runs go green in ~6 minutes |
| fork? (forks disable schedules) | no |
| archived / disabled? | no |
| Actions minutes | public repo, unmetered |
| repo activity (60-day rule) | pushed to the same day |

The configuration was never wrong. GitHub documents scheduled workflows as best
effort, warns they may be delayed under load, and a delayed schedule is DROPPED
rather than run late; this is widely reported on low-traffic repositories.

Rather than keep a `schedule:` block that implies a guarantee it does not provide,
the trigger was removed. **The replay is now run by hand:** Actions -> Daily
session replay -> Run workflow. Nothing is lost by a missed day, because
`daily_replay.py --day YYYY-MM-DD` replays any past session from cached data.

If a schedule is ever restored: 22:37 UTC is 18:37 ET under EDT and 17:37 ET under
EST, both after the close and after Twelve Data consolidates volume (~80 minutes
post-close), so no DST handling is needed at that hour. That also retires a bug
never hit in production -- the original `30 21 * * 1-5` was 17:30 ET only during
EDT and would have fired at 16:30 ET under EST, before the close.

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

**$5,000 cash basis (from 2026-09-23):** 1 sessions logged, 1 with trades, 0 with zero. Cumulative P&L **-$38.49** across 2 trades.

**Old $100,000 basis (before 2026-09-23; divide by 12.5):** 8 sessions logged, 6 with trades, 2 with zero. Cumulative P&L **+$664.03** across 16 trades.

Far too small a sample to compare against the backtest's ~30% zero-trade rate and 2.6 trades per active session; the point remains accumulation.
