# Backtest results

> **READ THIS FIRST.** Every section below dated before "Modelling transaction
> costs" was produced by a backtest that filled at bar prices with NO commission
> and NO slippage (`gross_profit == net_profit` on every trade). Those numbers
> remain valid as COMPARISONS BETWEEN CONFIGURATIONS -- both sides paid nothing --
> and must not be read as returns. The strategy turns ~$92M of notional on $100k
> of equity over the holdout; costs are not a rounding error on that, they are the
> dominant term. See the final section for the re-run with costs modelled.

Numbers here come from `scripts/backtest.py` against the cached 5-minute bars in
`data_cache/historical/`. Journals are gitignored, so this file is the record.

Two periods, split at 2025-12-17:

* **Tuning period** — the most recent 183 trading days. Every parameter in
  `config/*.yaml` was chosen while looking at this window.
* **Holdout** — 2023-09-11 to 2025-12-16 (~568 trading days, 2.26 years).
  Nothing was tuned on it.

19 of 21 tickers can trade the holdout; CRML (listed 2024-03) and USAR (listed
2025-03) did not exist for most of it.

## Both periods, same 19 tickers

| period | config | trades | net | return | annualised | max DD | win% | flat | t |
|---|---|---|---|---|---|---|---|---|---|
| Holdout (2.26y) | pre-change | 579 | $23,659 | 23.7% | 9.9% | 4.6% | 20.9% | 145 | 2.95 |
| Holdout (2.26y) | shipped | 1082 | $44,811 | 44.8% | **17.8%** | 3.9% | 46.4% | 7 | 4.36 |
| Tuning (0.73y) | pre-change | 176 | $9,076 | 9.1% | 12.6% | 2.6% | 20.5% | 49 | 2.12 |
| Tuning (0.73y) | shipped | 319 | $15,967 | 16.0% | **22.5%** | 3.2% | 49.5% | 2 | 2.48 |

All four are significant on their own by a day-block bootstrap.

Annualised, the out-of-sample period (17.8%) and the tuned period (22.5%) are
close. The earlier reading -- that the holdout earned ~2.3%/yr against ~9.9% on
the tuning period, so the strategy was mostly a period artifact -- was a
UNIVERSE artifact. Measured on the same 19 tickers the periods differ modestly,
and in the direction you would expect from a window that parameters were chosen
on.

## Universe width changes the shape of the result, not just its size

The same holdout on only the four tickers that had deep history earlier:

| universe | config | trades | days with a trade | net | top-5 share of profit | t |
|---|---|---|---|---|---|---|
| 4 tickers | pre-change | 201 | 172 | $353 | 1426% | 0.10 |
| 4 tickers | shipped | 227 | 173 | $5,265 | 88% | 1.50 |
| 19 tickers | pre-change | 579 | 407 | $23,659 | - | 2.95 |
| 19 tickers | shipped | 1082 | 413 | $44,811 | **15.7%** | 4.36 |

The recurring finding through this project's history -- that a handful of trades
carry all the profit, so nothing is measurable -- was substantially an artifact
of too narrow a universe. At four tickers the top five trades are 88% of profit
and the result is indistinguishable from zero. At nineteen they are 15.7% and
the result is significant at t=4.36.

Days carrying at least one trade went 173 -> 413, so widening bought independent
observations rather than merely stacking correlated trades into the same sessions.

## Does the config change itself help?

Comparing two strategies over the same market days is a PAIRED question. Testing
them as independent samples throws away the pairing and lets day-to-day market
variance -- which both configs face identically -- swamp the difference:

| test | improvement | 95% CI | |
|---|---|---|---|
| holdout, unpaired | $21,151 | [-$3,853, +$46,767] | includes 0 |
| tuning, unpaired | $6,891 | [-$7,907, +$22,538] | includes 0 |
| pooled 2.99y, unpaired | $28,043 | [-$1,407, +$57,738] | includes 0 |
| **pooled 2.99y, paired by day** | **$28,043** | **[+$11,150, +$46,460]** | **significant** |

Robustness of the paired result:

* Shipped config is better on **353 days, worse on 182**, identical on 12. Sign
  test on the 535 days they differ: **p = 1.2e-13**. Distribution-free, so the
  right tail cannot manufacture it.
* Median daily difference is **+$3.96** -- positive, not a mean dragged by outliers.
* Trimming the best and worst 1% of days still leaves **+$43/day**, $23,235 total.

The caveat: split by time, the second half carries it ($26,697, significant)
while the first half is flat ($1,346, includes zero). Direction is consistent
throughout -- the sign test spans every day -- but the dollar magnitude is
concentrated in the later period. Read the edge as real and consistent in
direction, with a size that scales with how much opportunity the tape offers.

Note also that several tests appear above. The paired test is the correct one on
principle rather than the one that happened to come out favourable: both configs
trade the same market days, and ignoring that is simply the wrong model. The
sign test is an independent, distribution-free confirmation.

## Caveats that apply to every number above

* Bid/ask is synthesized from each ticker's configured max spread, not real
  historical quotes. NEXA in particular trades a median 37 five-minute bars a
  session against 78 for a liquid name, so its fills are optimistic.
* These tickers are highly correlated -- on 2026-02-26 every one of them fell
  32-41% together. Five concurrent positions is closer to one bet than five, and
  the drawdown figures reflect an intraday strategy that is rarely exposed
  overnight.
* Parameters were selected over 13 swept configurations on the tuning period.
  The holdout is genuinely out of sample, but the config being tested was not
  chosen blind.

## Silver direction predicts how much the bot trades

Measured across all 570 holdout sessions, bucketed by SLV's OPEN-TO-CLOSE move
(the intraday move, not close-to-close -- this is an intraday strategy):

| SLV open->close | sessions | % taking zero trades | avg trades | avg P&L |
|---|---|---|---|---|
| < -2% | 21 | **71%** | 0.81 | $34 |
| -2% to -0.5% | 148 | 39% | 1.28 | -$12 |
| -0.5% to +0.5% | 231 | 24% | 1.90 | $77 |
| +0.5% to +2% | 142 | 20% | 2.51 | $144 |
| > +2% | 28 | **4%** | 2.86 | $273 |

Monotonic in all three columns. Correlation of SLV's intraday move with trades
taken is **+0.272**; with daily P&L, **+0.144**.

Read that gap carefully. Silver direction predicts how ACTIVE the strategy is far
better than whether its trades win. This does not contradict the earlier finding
that silver failed as a predictor of trade OUTCOME -- both are true, and they are
different questions. The strategy is structurally long intraday sector momentum,
so its activity scales with sector direction, while its per-trade edge does not.

Note also that average P&L on the worst silver days is POSITIVE (+$34). The bot
does not lose money when silver falls; it declines to trade. That is the
benchmark-confirmation gate doing its job.

### The mechanism, from 2026-09-11

Silver fell 5.32% on 2026-09-10. On the 11th the miners gapped up -- SIL opened
98.31 against a 96.15 prior close, +2.2% -- and then sold off all session, closing
96.33 (-2.01% open-to-close, -3.25% peak-to-trough).

That gap-up-then-fade shape is the worst case for a VWAP gate: VWAP anchors near
the high open, price falls under it early and never recovers. SIL and GDX each
closed above their own VWAP on just **7 of 78 bars**, dropping below at 10:05.
benchmark_confirmation.require_price_above_vwap therefore failed nearly all day,
producing 1214 of 1553 rejections and zero trades.

Judging that session by close-to-close numbers (SLV +1.06%, SIL +0.19%) makes it
look flat and the zero-trade outcome unexplained. Those closes are measured
against 09-10's post-crash low. The intraday move is what the strategy actually
experiences, and it was firmly negative.

## The do-not-chase rule costs roughly 60% of P&L

`chase_rule` refuses entries into names that have already moved. Since 97% of
profit comes from the 6.7% of trades exceeding +3R, it was worth asking whether
it declines the trades that pay. It does.

| period | variant | trades | net | max DD | ret/DD | >3R |
|---|---|---|---|---|---|---|
| holdout (490d) | chase ON | 1091 | $44,019 | 3.9% | 11.2 | 73 |
| holdout (490d) | chase OFF | 1899 | **$72,101** | 5.1% | **14.2** | **127** |
| tuning (152d) | chase ON | 313 | $16,770 | 3.2% | 5.3 | 24 |
| tuning (152d) | chase OFF | 538 | **$26,132** | 4.5% | **5.8** | **45** |

Pooled over 642 trading days (~3 years): **+$37,445, 95% CI [+$14,555, +$60,846]**,
still +$33,812 after trimming the best and worst 1% of days.

### It is one sub-rule, not three

Of 85,309 chase rejections across 797,956 signals:

| sub-rule | rejections | share |
|---|---|---|
| `extended_above_vwap` (>1 ATR above VWAP) | 85,284 | **100.0%** |
| `consecutive_large_green_candles` | 24 | 0.0% |
| `extended_above_open_no_consolidation` | 1 | 0.0% |

The other two are effectively dead code. "1 ATR above VWAP" is close to the
definition of a strong intraday mover, so the rule as configured rejects strength
rather than over-extension.

Relaxing sub-rule 2 alone is monotonic in P&L on the holdout: 1.0 ATR $44,019 ->
2.0 ATR $60,381 -> 3.0 ATR $66,434 -> off $72,101.

### The mechanism replicates; the daily experience does not improve

Runner count (>3R) rises by almost the same multiple in both periods, which is
the strongest part of the evidence:

  holdout  73 -> 127  (1.74x)
  tuning   24 ->  45  (1.88x)

But the gain is **entirely** in the tail. Pooled sign test: better on 314 days,
worse on 322, **p = 0.78**. Median daily difference is **-$0.10**; the mean is
+$58.33. Drawdown rises in both periods (3.9% -> 5.1%, 3.2% -> 4.5%).

Contrast the trailing stop, which improved 353 days against 182 with sign test
p = 1.2e-13 and a positive median. That was a broad improvement. This is not: it
is a deliberate trade of higher drawdown and a flat-to-slightly-worse typical day
for roughly twice as many large winners. It increases the strategy's dependence
on the right tail, which is already its main fragility.

## Runner clustering is contemporaneous, not predictive

Runners do cluster: 24 holdout days carried 2+ runners against 13.6 expected under
Poisson arrival, permutation p = 0.0006. That looked like exploitable regime
structure, so it was tested directly -- can a cluster day be recognised in advance?

Features measured at 10:00, with the target restricted to trades ENTERED at or
after 10:00 so nothing peeks:

| feature | vs runners/day | vs day P&L |
|---|---|---|
| sector breadth at 10:00 (share of names above own VWAP) | **-0.070** | +0.007 |
| SIL opening gap | +0.053 | +0.080 |
| prior day's runner count | **-0.063** | -0.013 |

All null, and two are the wrong sign. Days following a 2+ runner day produce
FEWER runner days (12%) than days following a quiet one (19%). Breadth above 75%
gives 15% runner-days against 22% when breadth is under 25%.

So the clustering does not persist across days. It is contemporaneous: when one
miner runs, others run THE SAME DAY. That is the +0.267 mean pairwise correlation
and the effective breadth of 3.2 showing up in a different projection -- not a
regime that can be traded, because a cluster day is only identifiable once its
runners have already run.

This corrects the reading recorded when the clustering was first found. Temporal
structure was the obvious inference and it is wrong; the structure is
cross-sectional.

Predictors tested and failed to date: setup score, RVOL, planned R, ATR%, spread,
benchmark strength, setup type, silver (three definitions), Dow, stop width, hold
time, concurrency, hour-of-day, entry price, day-of-week, sector breadth, runner
persistence. Nineteen.


## Sector comparison: the bot vs "Metals & Mining is up 21% YTD"

The screener figure (127 companies, +21.43% YTD) is an average of constituent
returns, not an investable return. XME -- the actual SPDR S&P Metals & Mining
ETF, in our own cache -- is **+4.69%** over the same 1/02-9/14 window. Our own
20-stock universe bought and held equal-weight is +11.80% mean / +13.32% median.
Nobody earns the 21% without buying all 127 names in equal size and rebalancing.

Bot vs sector on identical windows, shipped config, $100k start:

| window | bot net | bot return | XME | universe equal-wt |
|---|---|---|---|---|
| YTD 2026 (1/02-9/14, 175d) | $29,319 | **+29.3%** (5.2% DD) | +4.69% | +11.80% |
| Jun 1 - Sep 14 (the 7.9% run) | ~$7,791 | **+7.8%** | -11.15% | -7.68% |
| January 2026 alone | $14,798 | +14.8% | +12.88% | +21.40% |

The bot beat the sector in both windows. The apparent shortfall came from
comparing the bot's Jun-Sep number against the sector's YTD number.

CAVEAT: the YTD window sits almost entirely inside the tuning period (which
starts 2025-12-17), so +29.3% is IN-SAMPLE. The honest forward number is the
holdout's 17.8%/yr at 3.9% max drawdown.

### Why the returns feel thin outside January

| month | net |
|---|---|
| 2026-01 | $14,798 |
| 2026-02 | $4,008 |
| 2026-03 | $308 |
| 2026-04 | -$1,094 |
| 2026-05 | $3,508 |
| 2026-06 | $2,351 |
| 2026-07 | $3,157 |
| 2026-08 | $2,736 |
| 2026-09 (partial) | -$453 |

January is 50% of the year's profit. Consistent with the concentration finding:
36 of 503 trades (7.2%) carry 100% of net; the top 5% of trades carry 80%.

### Structural reason the bot cannot track a sector trend

* Time-weighted capital deployed: **22.5%** of equity. Flat the other 77.5%.
* Average hold 123 minutes; **zero** overnight positions in 175 days.
* 31 of 175 days (18%) have no trade at all.

A sector's YTD gain accrues continuously, including overnight and on days the
bot sits out. A long-only intraday strategy holding ~1/5 of its capital for ~2
hours a day can only ever capture a slice of it -- and in exchange carries 5.2%
max drawdown against XME's own much larger swings.

## Uncorrelated universe test

The question: the strategy's remaining upside was argued to be breadth, not
selection (nineteen predictors tested, nineteen null). IR = IC x sqrt(breadth),
and mining names move together, so the effective number of independent bets is
far below the ticker count. Does adding names that do NOT move with silver raise
the information ratio?

Setup. Sixteen cross-sector names, two each from eight sectors, each with its own
sector-ETF benchmark: AMD/MU (SMH), PLTR/CRWD (XLK), OXY/HAL (XLE), SCHW/COIN
(XLF), MRNA (XBI), HIMS (XLV), TSLA/CHWY (XLY), BA/GE (XLI), VST/NRG (XLU).
Three years of 5-minute bars backfilled for all 25 symbols.

Two things keep this from being a re-tune in disguise:

* `config_experiments/<name>/` symlinks strategy.yaml, risk.yaml, broker.yaml and
  schedule.yaml back to `config/`, so every parameter is byte-identical to the
  mining book and the universe is the only variable. `--config-dir` selects it;
  the experiment tickers are never added to `config/tickers.yaml`, because
  run_bot.py reads the same universe the backtest does.
* `profit_target_pct` is derived, not chosen: [0.58, 1.11] x each name's own
  median daily range, those being the median ratios the shipped mining config
  already embodies. `volatility_category` buckets on the same measurement.
  Nothing was picked by looking at P&L.

### Holdout (568 days, out-of-sample for mining, never tuned at all for the rest)

| universe | trades | net | return | max DD | return/DD | Sharpe |
|---|---|---|---|---|---|---|
| mining (20 names) | 1,838 | $71,199 | 71.2% | 5.2% | 13.7 | 3.99 |
| cross-sector (16 names) | 1,086 | $46,982 | 47.0% | 3.4% | 13.7 | 3.69 |
| **combined (36), 5 slots** | **2,635** | **$110,002** | **110.0%** | **4.3%** | **25.6** | **4.70** |
| combined (36), 10 slots | 2,828 | $100,477 | 100.5% | 7.0% | 14.4 | 4.09 |

Read the first two rows first: run alone, the cross-sector book is not better
than the mining book. Same return/DD to one decimal, slightly lower Sharpe,
fewer trades. The strategy travels -- it is not a silver artifact -- but
swapping one universe for the other buys nothing.

Run TOGETHER is where the gain is. Combined earns more than mining alone while
drawing down LESS, which is diversification and nothing else: the two books'
bad days are not the same days.

### Effective breadth

N_eff = N / (1 + (N-1) * rho_bar), on per-ticker daily P&L. Two correlations,
because they answer different questions -- "portfolio" fills no-trade days with
zero and so includes co-activity; "conditional" uses only days both names traded
and so isolates the signals.

| universe | rho (portfolio) | N_eff | rho (conditional) | N_eff | Sharpe |
|---|---|---|---|---|---|
| mining (20) | +0.035 | 12.0 | +0.115 | 6.3 | 3.99 |
| cross-sector (16) | +0.023 | 12.0 | +0.108 | 6.1 | 3.69 |
| combined (36) | +0.019 | 21.7 | +0.077 | 9.8 | 4.70 |

The law roughly holds. Mining's IC proxy is 3.99/sqrt(6.3) = 1.59; applied to the
combined book's breadth it predicts 1.59*sqrt(9.8) = 4.98 against an observed
4.70. Breadth, not a better signal, is what moved.

NOTE on an earlier number: a previous ad-hoc measurement put mining's effective
breadth at 3.2 with mean pairwise correlation +0.267. Measured by
`scripts/analyze_breadth.py` on per-ticker daily P&L it is 6.3 at +0.115. The
old figure came from a different projection and should not be compared against
these; the direction of the finding is unchanged but the magnitude was overstated.

### Is the combined book's advantage real?

Paired by day -- both books face the same market, so an unpaired test would throw
that away and let market variance swamp the difference.

| period | combined - mining, per active day | t | 5-day-block bootstrap 95% CI |
|---|---|---|---|
| holdout (529 active days) | +$75.15 | 4.65 | +$46.52 .. +$106.98 |
| tuning (179 active days) | +$63.35 | 2.38 | +$18.76 .. +$112.21 |

Both periods, same direction, bootstrap CI clear of zero in both.

### More slots is not more breadth

Ten concurrent positions instead of five: 193 more trades, $9,525 LESS profit,
and drawdown up from 4.3% to 7.0%. Slot contention is doing real work -- with 36
names feeding 5 slots the bot takes the best five candidates of a bigger pool,
and loosening the constraint just lets the marginal ones in. Widen the universe,
not the slot count.

### What this does NOT establish

* Every caveat from the $5,000-account analysis still binds and gets WORSE here:
  no buying-power check exists in the code, commissions are unmodelled, and the
  PDT rule already made ~2.4 trades/day illegal under $25k. The combined book
  runs ~4.6 trades/day.
* 36 names at 5-minute resolution is 36 live quote subscriptions and 36x the
  data-feed cost. Nothing has been checked about whether the feed sustains it.
* The cross-sector names carry event risk the mining book does not -- earnings,
  FDA dates, index rebalances. None of that is modelled.

### How the cross-sector names were chosen, and what that costs

Stated plainly: they were picked by hand, from knowledge, not from a screen.
There was no pre-registered filter run over all US equities. The criteria applied
were:

1. **A liquid sector ETF must exist to benchmark against.** This is a hard
   constraint, not a preference -- benchmark confirmation is an entry gate
   (REJECTED_BENCHMARK_CONFIRMATION is the single largest rejection bucket), so a
   name with no tradeable sector proxy cannot generate a signal at all. That alone
   restricts the field to the GICS sectors with a real ETF: SMH, XLK, XLE, XLF,
   XBI, XLV, XLY, XLI, XLU.
2. **Two names per sector, eight sectors.** Pairs are deliberate: if within-sector
   correlation reappears between AMD/MU the way it does between AG/EXK, that is
   visible, and it means sector count -- not ticker count -- is what sets breadth.
3. **Daily range comparable to the mining book** (mining spans 1.7%-8.8%; these
   span 2.1%-6.7%), so the shipped ATR-based stops and R-targets transfer without
   re-tuning.
4. **Liquid enough for the 0.15% spread gate** -- all sixteen clear $160M median
   daily dollar volume.

Criteria 1, 3 and 4 are mechanical. Criterion 2 is a design choice. What is NOT
mechanical is which two names per sector: those came from memory, which means
names that were memorable for having trended are over-represented. That is
hindsight bias and it should be assumed present until measured.

Measuring it. Two adversarial holdout runs, same 568 days, same parameters:

| universe | net | max DD | return/DD |
|---|---|---|---|
| mining alone (20) | $71,199 | 5.2% | 13.7 |
| combined, all 36 | $110,002 | 4.3% | **25.6** |
| combined minus the 3 best cross-sector names (33) | $96,253 | 5.8% | 16.5 |
| mining + only the 8 WORST cross-sector names (28) | $84,319 | 5.1% | 16.5 |

The direction survives everything. Strip HIMS, COIN and PLTR -- 46% of the
cross-sector book's profit -- and the combined book still beats mining alone by
$25k. Keep only the eight names that performed WORST, chosen after the fact to be
maximally unflattering, and it still beats mining alone by $13k. Diversification
is doing real work that stock-picking luck cannot explain away.

But the MAGNITUDE was inflated. The headline 25.6 return/DD drops to 16.5 under
either adversarial cut, against mining's 13.7. So roughly half the improvement in
return/DD rode on three fortunate names, and about half is the structural
diversification benefit. 16.5 is the number to plan against, not 25.6.

The honest next step, not yet done, would be to rebuild the universe from a
mechanical screen -- rank every optionable US name with a sector-ETF benchmark by
median daily range and dollar volume, take the top N per sector, and re-run. That
removes the hand from the selection entirely.

## Nine mining names added by request (2026-09-16)

RMCO, CTGO, EMAT, NB, PPTA, GFI, HMY, TRX, AUGO. All nine are now in
`config/tickers.yaml`. Four trade; five are `strategy: excluded` on a liquidity
screen, the same switch NEXA carries.

### Why a liquidity screen and not just a backtest

`scripts/backtest.py` synthesizes bid/ask as 30% of each ticker's own configured
`max_spread_pct`, so the spread gate in risk_manager.py can NEVER reject anything
in a backtest, and a five-minute interval that never traded is simply absent from
the file rather than appearing as an unfillable moment. A name too thin to trade
therefore backtests as though it were liquid. TRX at $0.40 carries a one-cent real
spread -- 2.5% -- against the ~0.045% a backtest would assume, a 55x understatement
of the cost to cross. The screen has to come from the bars, before the backtest.

`scripts/screen_liquidity.py` encodes it: median >= 74 of 78 five-minute bars per
session, and median daily dollar volume >= $5M. Both thresholds are anchored to
decisions already in the config rather than chosen -- VZLA is the weakest ENABLED
name (78 bars, $5.7M), NEXA was EXCLUDED at 37 bars, $0.3M. Run on NEXA the screen
reproduces that exclusion independently.

| ticker | what it is | benchmark | bars/day | median $vol | close | verdict |
|---|---|---|---|---|---|---|
| GFI | Gold Fields, large-cap producer | GDX | 78 (100%) | $67.9M | $20.04 | trade |
| HMY | Harmony Gold, producer | GDX | 78 (100%) | $47.3M | $12.16 | trade |
| AUGO | Aura Minerals, mid-tier gold/copper | GDX | 78 (100%) | $35.6M | $58.22 | trade* |
| PPTA | Perpetua Resources, Stibnite development | GDXJ | 78 (100%) | $9.4M | $11.86 | trade |
| CTGO | Contango Ore, Alaska gold | GDXJ | 61 (78%) | $1.8M | $19.57 | excluded |
| NB | NioCorp, niobium/scandium/REE | REMX | 69 (88%) | $0.7M | $3.08 | excluded |
| TRX | TRX Gold, Buckreef Tanzania | GDXJ | 59 (76%) | $0.2M | $0.40 | excluded |
| EMAT | Evolution Metals & Technologies | REMX | 62 (79%) | $0.9M | $7.10 | excluded |
| RMCO | Royalty Management, critical minerals | REMX | 16 (21%) | $0.02M | $1.32 | excluded |

*AUGO listed 2025-07-16 and EMAT 2026-01-06, so neither has holdout history.

GDXJ was backfilled as a junior benchmark: PPTA, CTGO and TRX are developers and
explorers, and benchmarking them against GDX's large-cap producers would gate them
on the wrong tape. Precedent is VZLA -> SILJ.

### What the four additions do

| period | universe | trades | net | max DD |
|---|---|---|---|---|
| holdout | mining 20 | 1,838 | $71,199 | 5.2% |
| holdout | mining 24 | 1,991 | **$77,347** | 4.9% |
| tuning | mining 20 | 569 | $32,283 | 5.2% |
| tuning | mining 24 | 630 | **$36,880** | 5.6% |

Positive in both periods (+$6,148 and +$4,597), Sharpe up in both (3.99 -> 4.15
holdout, 3.81 -> 4.06 tuning).

The additions are NOT statistically significant on their own:

| period | per active day | t | bootstrap 95% CI | P(<=0) |
|---|---|---|---|---|
| holdout | +$12.68 | 1.55 | -$3.19 .. +$30.86 | 0.063 |
| tuning | +$28.20 | 1.64 | -$4.49 .. +$63.14 | 0.050 |

Both CIs straddle zero. It is not a demonstrated edge and should not be quoted as
one.

#### Per-period split, which the book-level numbers hide

The four names do NOT agree with each other across the two periods. Only the
AGGREGATE is consistent:

| ticker | holdout trades | holdout net | tuning trades | tuning net | agrees? |
|---|---|---|---|---|---|
| PPTA | 109 | +$5,193 | 22 | +$1,990 | yes |
| AUGO | 9 | +$776 | 24 | +$990 | yes, but 9 trades |
| HMY | 58 | +$1,255 | 13 | **-$271** | **no** |
| GFI | 38 | +$888 | 21 | **-$632** | **no** |
| subtotal | 214 | +$8,112 | 80 | +$2,077 | |

Two of four flip sign. GFI and HMY are large-cap producers at 2.9-3.2% median
daily range -- the least volatile names in the book -- and they trade rarely
(38 and 58 times in 2.25 years) because they seldom clear the setup thresholds.
On that trade count neither period's result means anything individually.

AUGO's holdout figure rests on 9 trades: it listed 2025-07-16, so it exists for
only the last ~5 months of a 2.25-year window. Treat the tuning period as its
only real measurement.

PPTA is the one name that stands on its own -- positive in both periods, 131
trades, and 6th best in the entire 24-name book over the holdout.

So the earlier phrasing here, "same direction in two independent periods", is
true of the BOOK and not of the NAMES. The defensible claim is narrower: PPTA
earned its place, and the other three are cheap to carry at 9-58 trades apiece
while more evidence accumulates.

#### Effective breadth moved in opposite directions

| period | mining 20 | mining 24 | rho (conditional) |
|---|---|---|---|
| holdout | 6.3 of 20 | **6.9 of 24** | +0.115 -> +0.109 |
| tuning | 6.1 of 20 | **5.4 of 24** | +0.121 -> +0.151 |

The holdout says the additions bought independent bets. The tuning period says
they bought correlated ones -- four gold names benchmarked to GDX/GDXJ, over 193
days where the gold complex moved together. Sharpe still rose on the tuning
period, but via added return outweighing added correlation, not via
diversification. Do not cite the 6.3 -> 6.9 number without this one beside it.

### The excluded five, measured

Lifting `strategy: excluded` on all five (config_experiments/illiquid/) over the
same holdout:

| universe | trades | net | max DD |
|---|---|---|---|
| mining 24 | 1,991 | $77,347 | 4.9% |
| mining 24 + the 5 excluded | 2,119 | **$66,178** | 5.4% |

Adding them COSTS $11,169. Their own contributions: NB -$3,846, RMCO -$383,
CTGO +$49, TRX +$703 -- a net -$3,477 between them, and the remaining -$7,692 is
opportunity cost, the five consuming position slots that would otherwise have gone
to better candidates.

Worth stating because the prediction was wrong: the expectation was that these
names would show inflated PHANTOM profits from the optimistic synthetic spread.
They do not. They lose money even in the backtest's forgiving world, before any
real spread is charged. The exclusion does not depend on the spread argument --
though the spread argument is still why the screen must run before the backtest
rather than after.

### Dropping GFI and HMY (2026-09-16, by request)

Both are `strategy: excluded`. Both PASS `scripts/screen_liquidity.py` -- this is
not a liquidity call, and the config says so in place so the next person
re-deriving the universe does not assume it was.

| period | universe | trades | net | max DD | return/DD | Sharpe | breadth |
|---|---|---|---|---|---|---|---|
| holdout | 24 names | 1,991 | $77,347 | 4.93% | **15.7** | 4.15 | 6.9 of 24 |
| holdout | 22 names | 1,920 | **$77,809** | 5.41% | 14.4 | 4.12 | 6.5 of 22 |
| tuning | 24 names | 630 | **$36,880** | 5.61% | 6.6 | 4.06 | 5.4 of 24 |
| tuning | 22 names | 606 | $35,096 | 5.38% | 6.5 | 3.98 | 5.9 of 22 |

Paired by day: holdout +$0.96/active day (t=+0.21), tuning -$11.01/active day
(t=-0.91). Both CIs straddle zero comfortably. The drop is not measurable either
way, which is the same verdict the addition got.

Two things are worth recording because they contradict the reasoning that
motivated the drop.

**The tuning period got WORSE.** That is the period GFI and HMY lost money in
(-$632 and -$271, -$903 between them). Removing them should have returned roughly
that much. Instead the book fell $1,784. The slots they had been occupying were
filled by trades that did worse than the losses removed -- so on the evidence that
prompted this change, the two names were crowding out something worse, not
something better.

**The holdout's drawdown rose**, 4.93% -> 5.41%, while net barely moved. Return
per unit of drawdown went 15.7 -> 14.4. The 24-name book is the better
risk-adjusted one on the out-of-sample period.

The methodological problem, stated plainly: the case for dropping these two came
from their TUNING-period P&L. That is the window every parameter in config/ was
fitted on, and the holdout -- the only honest evidence available -- had them both
positive (+$888 and +$1,255). Selecting names on the fitted window is the same
error as fitting a parameter on it, one level up. Thirteen and twenty-one trades
in that window is also far too few to read a sign from.

**RESTORED the same day.** Both are enabled again and the universe is back to 24
tradeable names. The measurements above are why: out-of-sample the 22-name book
was worse on every risk-adjusted measure (return/DD 14.4 vs 15.7, breadth 6.5 vs
6.9), and the tuning period -- the only evidence that had pointed toward dropping
them -- moved AGAINST the drop once measured rather than assumed.

The general lesson, which applies beyond these two names: a ticker's P&L is not
its contribution. With five concurrent position slots, removing a name frees
capacity that something else fills, and that replacement can be better or worse
than what it displaced. NEXA's removal worked because the replacements were
better (it gained more than NEXA's own -$1,057). Removing GFI and HMY did not,
because they were displacing trades worse than their own small losses. Neither
outcome is predictable from the name's own P&L column, so the only way to know is
to run the book both ways.

The config records the round trip in place rather than reverting silently, so the
question does not get re-opened from scratch later.

## Why 2026-09-17's big movers were missed (and the RVOL gate re-examined)

SVM closed +8.91%, VZLA +8.67%, FSM +3.74% on 2026-09-17. The bot took three
trades in HL and CDE and finished -$60.19. Asked how those were missed:

### Most of the move happened before the opening bell

| ticker | prev close -> open (GAP) | open -> close (INTRADAY) | share unreachable |
|---|---|---|---|
| FSM | +3.74% | **0.00%** | **100%** |
| SVM | +5.19% | +3.53% | 59% |
| VZLA | +4.34% | +4.16% | 52% |

FSM opened at 11.93 and closed at 11.93. Its entire quoted gain was the overnight
gap; there was no intraday move to capture at all. A long-only intraday strategy
that is flat at every close cannot participate in a gap, by construction.

### The intraday remainder came on BELOW-average volume

| ticker | session volume | trailing-20 median | ratio |
|---|---|---|---|
| SVM | 1,804,571 | 1,948,361 | **0.93x** |
| VZLA | 4,323,956 | 5,407,356 | **0.80x** |
| FSM | 2,645,254 | 4,716,474 | **0.56x** |

All three drifted up on lighter-than-normal participation. `min_relative_volume`
refused them, which is the gate doing its job -- a move without volume behind it
is what it exists to decline. HL and CDE, the two that traded, ran median RVOL
2.31 and 2.35.

### A real design flaw that is NOT the cause

`build_volume_baselines` computes the RVOL baseline as the mean cumulative volume
at each bar index across ALL strictly earlier days -- up to 750 sessions, with no
recency window. Conventional relative volume uses a trailing 20-30 days. A
multi-year mean is dragged by old spikes and structurally penalises names whose
liquidity has declined.

That looked like the explanation. It is not. Recomputing 2026-09-17 with a
trailing-20 median baseline instead:

| ticker | RVOL (all-history mean) | RVOL (trailing-20 median) | gate 1.1 |
|---|---|---|---|
| SVM | 0.39 | 0.93 | still fails |
| VZLA | 1.09 | 0.80 | **worse** |
| FSM | 0.31 | 0.56 | still fails |

Across the 24-name universe exactly ONE ticker changes gate status on the day
(EQX, 1.13 -> 0.92, and in the wrong direction). The baseline is worth fixing on
its own merits but it would not have caught any of these.

### Loosening the gate: measured, not adopted

`--min-relative-volume` added to scripts/backtest.py. Holdout, 568 days:

| gate | trades | net | max DD | return/DD |
|---|---|---|---|---|
| 0.7 | 2,604 | $76,128 | 4.20% | 18.1 |
| 0.9 | 2,318 | **$82,880** | 4.30% | **19.3** |
| 1.10 (shipped) | 1,991 | $77,347 | 4.93% | 15.7 |
| 1.3 | 1,641 | $68,165 | 5.01% | 13.6 |

0.9 beats the shipped value on both net AND drawdown out-of-sample. It does not
survive the second period:

| period | net at 0.9 vs 1.10 | max DD | paired t | bootstrap 95% CI |
|---|---|---|---|---|
| holdout | +$5,533 | 4.93% -> **4.30%** | +0.97 | -$12.88 .. +$32.55 |
| tuning | +$2,647 | 5.61% -> **7.59%** | +0.61 | -$23.98 .. +$57.53 |

Net improves in both periods but drawdown moves in OPPOSITE directions, and
neither paired test comes close to significance. NOT adopted. The gate stays at
1.10.

### What would actually capture a gap

Nothing in the intraday parameter space. Capturing FSM's move requires holding
overnight into the catalyst -- a different strategy with a different risk profile
(overnight gap risk cuts both ways, and every drawdown figure in this document
assumes a book that is flat at every close). The config already carries
`overnight_category` per ticker, so the machinery exists; whether an overnight
variant earns its risk is an unanswered, testable question, not a tuning fix.

## Trailing-stop multiplier: the mechanism is real, the fix is not

Prompted by 2026-09-17, where the day's only winner peaked at +1.71R and exited
at +0.73R. The mechanism looked like a genuine defect: the initial stop is
0.85 x ATR but the trailing stop trails by 1.0 x ATR, so the trail is WIDER than
the risk unit and can never give back less than ~1.2R from the high-water mark.

Holdout, 568 days, sweeping `--trailing-atr`:

| trail (ATR) | trades | net | max DD | return/DD |
|---|---|---|---|---|
| 0.3 | 2,019 | $82,968 | 4.80% | 17.3 |
| **0.4** | 2,017 | **$83,252** | 4.59% | **18.2** |
| 0.5 | 2,015 | $81,460 | 4.60% | 17.7 |
| 0.6 | 2,010 | $77,094 | 4.58% | 16.8 |
| 0.85 | 2,006 | $77,402 | 4.72% | 16.4 |
| 1.0 (shipped) | 1,991 | $77,347 | 4.93% | 15.7 |

0.3-0.5 is a plateau, not a spike -- three adjacent values all land near $82-83k
while 0.6 and above sit near $77k. A region with a cliff at 0.6 is the shape of a
real effect rather than a fitted point, and it is roughly where the trail stops
exceeding the 0.85 ATR risk unit. Both periods improve:

| period | net at 1.0 | net at 0.4 | max DD 1.0 -> 0.4 |
|---|---|---|---|
| holdout | $77,347 | $83,252 | 4.93% -> **4.59%** |
| tuning | $36,198 | $37,298 | 5.61% -> **2.94%** |

### Why it is NOT adopted

Two reasons, and the second is decisive.

**It is not significant.** Paired by day: holdout +$12.20/day, t=+1.21, bootstrap
CI -$9.16..+$33.23. Tuning +$6.79/day, t=+0.22, CI -$57.84..+$68.05. The holdout
splits 243 better days against 237 worse -- a coin flip.

**It eats the tail that the strategy lives on.**

| | trail 1.0 | trail 0.4 |
|---|---|---|
| trades > +3R | 140 | **91** (-35%) |
| trades > +5R | 41 | **23** (-44%) |
| mean R | +0.321 | **+0.292** |

96% of holdout profit comes from the 7% of trades above +3R. Tightening the trail
cuts that population by more than a third and lowers mean R. The dollar gain shows
up despite a WORSE per-trade edge, which means it is coming from the middle of the
distribution while the right tail is being amputated -- exactly the trade this
strategy must not make.

The hypothesis was mechanically correct and strategically wrong: yes, a 1.0 ATR
trail gives back more than 1R on every winner, and yes, fixing that captures more
of the small winners. It does so by exiting the big ones early. Left at 1.0.

Worth keeping from the diagnosis: on 2026-09-17 the stops were 0.22-0.40% of
price against a 5.05% median daily range for those names -- CDE was stopped by a
0.42% adverse move. That is the 14-period ATR being computed on FIVE-MINUTE bars,
so it measures the last ~70 minutes and collapses in a quiet afternoon. Whether
the stop should reference a longer horizon is a separate, untested question, and a
more promising one than the trail multiplier.

## Stop floor: sizing stops off real volatility instead of a 70-minute ATR

The lead from the 2026-09-17 diagnosis. The stop is 0.85 x ATR where ATR is a
14-period average of FIVE-MINUTE bars -- it measures about the last 70 minutes and
collapses in a quiet afternoon. CDE was entered at 15:05 with a stop 0.22% below
entry against a 5.09% median daily range, and a 0.42% adverse move took it out.

Implemented as a FLOOR (`stops.min_stop_pct_of_price`, `--min-stop-pct`) rather
than by redefining `atr`: the chase rule's `max_atr_above_vwap: 3.0` is separately
calibrated against the 5-minute ATR and would have shifted meaning silently. The
floor is a third candidate alongside the ATR and structure stops, and the widest
still wins -- it can never tighten a stop.

These names average ~5% daily range, so a 0.5% floor is roughly 0.1x daily ATR.

### Holdout, 568 days

| floor | trades | net | max DD | return/DD | >3R | >5R | mean R | full-stop rate |
|---|---|---|---|---|---|---|---|---|
| none (shipped) | 1,991 | $77,347 | 4.93% | 15.7 | 140 | 41 | +0.321 | 50% |
| **0.5%** | 1,989 | **$90,543** | 5.29% | **17.1** | 129 | 37 | +0.286 | 46% |
| 1.0% | 1,895 | $82,389 | 6.85% | 12.0 | 43 | 1 | +0.161 | 35% |
| 1.5% | 1,755 | $85,233 | 9.16% | 9.3 | 8 | 0 | +0.129 | 27% |
| 2.0% | 1,684 | $71,515 | **17.01%** | 4.2 | - | - | - | - |

Drawdown degrades monotonically and then catastrophically -- 17% at a 2% floor.
Past 0.5% the runner population is destroyed: >5R goes 41 -> 1 -> 0. Only 0.5%
both improves net AND leaves the tail broadly intact, which is the constraint that
killed the trailing-stop change.

### Both periods, floor 0.5%

| period | net | vs baseline | max DD | paired t | P(<=0) | better/worse days |
|---|---|---|---|---|---|---|
| holdout | $90,543 | +$13,196 | 4.93% -> 5.29% | **+2.36** | **0.009** | 193/289 |
| tuning | $38,648 | +$2,450 | 5.61% -> 6.00% | +1.07 | 0.103 | 40/79 |

**The holdout result is the first change tested in this project to clear
significance** (P(<=0) = 0.009, bootstrap CI +$4.09 .. +$52.53 per active day).

### The wrinkle, which matters

In BOTH periods MORE days are worse than better -- 193/289 and 40/79, over 2:1
against on the tuning period. The mean is positive only because the winning days
win much bigger.

That is mechanically exactly what a wider stop should do, and it is the same shape
as the chase-rule relaxation that was adopted earlier: most days get slightly
worse because small losses become slightly larger, while trades that would have
been noise-stopped survive to become runners. For a strategy where 96% of profit
comes from 7% of trades, trading many small day-level losses for a fatter right
tail is the correct direction -- but it means the typical day gets WORSE, and
anyone watching daily P&L will feel it before the tail pays.

NOT adopted unilaterally. It costs drawdown in both periods, it makes the median
day worse, and one of the two periods is not significant.

### ADOPTED: min_stop_pct_of_price = 0.5

Set in `config/risk.yaml` and re-verified with NO override flags, so both runs
exercise the shipped config path end to end. Both periods reproduced the flag-run
net P&L to the cent, which is the check that the config actually reaches the stop
calculation rather than only the `--min-stop-pct` path.

| period | net before | net after | change | max DD | paired t | P(<=0) |
|---|---|---|---|---|---|---|
| holdout (568d) | $77,347 | **$90,543** | +17.1% | 4.93% -> 5.29% | +2.36 | **0.009** |
| tuning (193d) | $36,198 | **$38,648** | +6.8% | 5.61% -> 6.00% | +1.07 | 0.103 |

Runner tail survives, which is what 1.0% and above failed:

| | >3R | >5R | mean R |
|---|---|---|---|
| holdout | 140 -> 129 | 41 -> 37 | +0.321 -> +0.286 |
| tuning | 47 -> **48** | 20 -> 17 | +0.361 -> +0.347 |

Known costs, accepted deliberately:
- Drawdown is ~0.4pp worse in both periods.
- The MEDIAN DAY IS WORSE in both periods (193 better / 289 worse, and 40/79).
  All of the gain is in the right tail. Expect more small red days than before;
  the payback arrives in the rare large winners.
- Mean R per trade drops slightly. Dollars rise anyway because a floored stop
  lets more of the risk budget be deployed -- with a 0.22% stop, risk-based sizing
  hits the position cap and the trade carries less than its intended risk.
- The tuning period is not significant on its own (P = 0.103).

The usable band is narrow -- 1.0% cuts >5R trades from 41 to 1, and 2.0% takes
drawdown to 17% -- so `test_shipped_stop_floor_is_half_a_percent` pins the value.

## Versus SPY buy & hold -- and the transaction-cost problem it exposed

`scripts/benchmark_compare.py` compares a backtest tag against buy-and-hold over
the SAME trading days, using ADJUSTED closes so the benchmark gets its dividends.

| holdout, 2023-09-01 -> 2025-12-05 | return | CAGR | max DD | ret/DD | Sharpe |
|---|---|---|---|---|---|
| bot (zero cost) | 90.6% | 33.1% | 3.45% | 26.2 | 3.88 |
| SPY buy & hold | 56.5% | 22.0% | 18.76% | 3.0 | 1.32 |
| QQQ buy & hold | 67.9% | 25.9% | 22.77% | 3.0 | 1.23 |

| tuning, 2025-12-10 -> 2026-09-17 | return | CAGR | max DD | ret/DD | Sharpe |
|---|---|---|---|---|---|
| bot (zero cost) | 38.6% | 53.2% | 4.49% | 8.6 | 3.77 |
| SPY buy & hold | 11.8% | 15.7% | 8.88% | 1.3 | 1.19 |

A Sharpe near 4 is not a plausible number for a real intraday strategy, and
chasing down why produced the most important finding in this file.

### The backtest assumes free trading

`gross_profit == net_profit` on every trade in every run recorded here. The
backtest fills at bar prices with no commission and no slippage. That is harmless
when comparing two configurations against each other -- both pay nothing -- and
badly misleading when comparing against buy-and-hold, which pays its cost ONCE.

The holdout does 1,989 round trips moving **$92.3M of notional on $100k of
equity**. Against $90.5k of profit:

| cost per side | holdout return | Sharpe | % of profit left |
|---|---|---|---|
| 0 bps | 90.6% | 3.88 | 100% |
| 2 bps | 72.2% | 3.15 | 80% |
| 5 bps | 44.6% | 1.99 | 49% |
| **10 bps** | **-1.5%** | **-0.01** | **-2%** |

**The strategy breaks even at 9.8 bps per side** (12.5 on the tuning period).

### Why that is not a comfortable margin

A US equity quote cannot be tighter than one cent, so crossing a perfectly tight
market costs half a cent per side. Weighted by this strategy's actual traded
notional, that floor is **7.3 bps per side** -- because 54% of the notional is in
sub-$10 stocks where a penny is enormous in relative terms:

| ticker | avg price | min half-spread | trades | net |
|---|---|---|---|---|
| SVM | $4.05 | 12.4 bps | 107 | $5,117 |
| EXK | $4.37 | 11.4 bps | 140 | $4,358 |
| FSM | $5.41 | 9.2 bps | 141 | $5,001 |
| HL | $6.70 | 7.5 bps | 127 | -$206 |
| SIL | $39.62 | 1.3 bps | 109 | $2,299 |
| SCCO | $94.61 | 0.5 bps | 99 | $2,906 |

So the edge (9.8 bps) exceeds the theoretical minimum cost of trading these names
(7.3 bps) by 2.5 bps -- and that floor assumes every market is exactly one cent
wide, that $25k orders in $4 miners have zero market impact, and zero commission.
SVM and EXK are individually underwater against their own tick floor.

Exits make this concrete rather than theoretical: `submit_exit_order` sends a
MARKETABLE limit, so every exit crosses the spread by construction. Entries use a
"wait for a good fill" limit, which either crosses too or invites adverse
selection -- resting orders fill on the names that come back to you and miss the
ones that run, which preferentially discards the right-tail winners the whole
strategy depends on.

### What this means

The bot beats SPY on a zero-cost backtest by a wide margin on both return and
drawdown, and the drawdown advantage is real -- it is flat overnight and its
equity curve is genuinely smoother. But the return advantage is smaller than the
cost of the trading required to produce it. On these names, at this trade
frequency, the edge and the spread are the same size.

Nothing here is a reason to trust the zero-cost numbers less as a COMPARISON
between configs (the stop floor's +17% is still a real relative improvement). It
is a reason not to read any of them as an expected return.

The obvious directions, none of them tested yet:
1. Model costs in the backtest properly, and re-run every result in this file.
2. Bias the universe toward higher-priced names, where the tick floor is 1-2 bps
   instead of 12 -- SCCO and SIL clear their floor comfortably.
3. Trade less. 1,989 round trips for $90k is a thin margin per trade; a higher
   score threshold would cut notional faster than it cuts profit.

## Modelling transaction costs, and re-running everything

`src/execution/costs.py` now prices every fill and `PositionManager` charges it on
close, so `gross_profit - net_profit` is a real cost. Costs are modelled per SHARE,
not in basis points, because the binding constraint is the one-cent minimum tick:
crossing a penny-wide market costs half a cent per share whatever the price, which
is 0.5 bps on a $95 stock and 12 bps on a $4 one.

Shipped settings are a FLOOR on real cost, not an estimate:
`spread_ticks: 1.0` (tightest a US quote can legally be), `impact_bps: 0.0` (a $25k
order moves the book not at all), `commission_per_order: 0.0` (true at E*TRADE).
Real costs can only be higher.

### Holdout, 568 days, costs on

| floor | trades | gross | cost | **net** | max DD | ret/DD | >3R | >5R |
|---|---|---|---|---|---|---|---|---|
| none | 1,945 | $72,161 | $61,649 | **$10,512** | 13.85% | 0.8 | 131 | 39 |
| **0.5%** | 1,956 | $85,593 | $65,233 | **$20,360** | 11.88% | **1.7** | 125 | 36 |
| 1.0% | 1,878 | $81,478 | $65,056 | **$16,422** | 14.61% | 1.1 | 43 | 1 |
| 1.5% | 1,744 | $85,315 | $62,473 | **$22,843** | 28.34% | 0.8 | 8 | 0 |

### Tuning, 193 days, costs on

| floor | trades | gross | cost | **net** | max DD | ret/DD | >3R | >5R |
|---|---|---|---|---|---|---|---|---|
| none | 624 | $35,973 | $10,064 | **$25,910** | 5.64% | 4.6 | 47 | 20 |
| **0.5%** | 614 | $38,496 | $10,052 | **$28,444** | 5.77% | **4.9** | 48 | 17 |
| 1.0% | 608 | $43,335 | $10,021 | **$33,314** | 7.95% | 4.2 | 27 | 0 |

**Costs consume 85% of gross profit on the holdout** ($61.6k of $72.2k with no
floor). The stop floor survives this and matters MORE than it did for free: it
roughly doubles holdout net, because a wider stop means fewer noise stop-outs,
fewer round trips, and less spread paid. 0.5% has the best return/drawdown in BOTH
periods, so the adopted value stands. 1.5% posts the highest holdout net but a
28.34% drawdown with zero >5R trades -- it is a different, worse strategy that
happens to print a number.

Significance weakens once costs are real: holdout none -> 0.5% is t=+1.73,
P(<=0)=0.052, against t=+2.36 and P=0.009 for free. Marginal, not significant.

### Versus SPY, the honest version

| holdout 2023-09 -> 2025-12 | return | CAGR | max DD | Sharpe |
|---|---|---|---|---|
| bot, costs on | 20.5% | 8.6% | 11.88% | 0.92 |
| SPY buy & hold | **56.5%** | **22.0%** | 18.76% | **1.32** |

| tuning 2025-12 -> 2026-09 | return | CAGR | max DD | Sharpe |
|---|---|---|---|---|
| bot, costs on | **28.4%** | **38.7%** | **5.77%** | **2.87** |
| SPY buy & hold | 11.8% | 15.7% | 8.88% | 1.19 |

**On the holdout the bot loses decisively to buying SPY and holding it** -- less
than half the return, lower Sharpe, for 1,956 round trips and daily babysitting.
On the tuning period it wins clearly. The periods disagree completely, and the
reason is not the strategy.

### Why the periods disagree: the universe got more expensive

| | notional-weighted avg price | sub-$10 notional | cost |
|---|---|---|---|
| holdout | $19.27 | 57% | 7.3 bps/side |
| tuning | $38.05 | 15% | 3.3 bps/side |

The same names, in the two windows:

| ticker | holdout | tuning | change |
|---|---|---|---|
| HL | $6.71 | $19.39 | +189% |
| AG | $7.49 | $20.98 | +180% |
| SVM | $4.07 | $10.70 | +163% |
| SIL | $39.46 | $98.78 | +150% |
| EXK | $4.37 | $10.84 | +148% |

The metals rally roughly doubled to tripled this universe. A penny tick on a $4
stock is 12 bps; on a $12 stock it is 4. **The strategy's cost burden halved for
reasons that have nothing to do with the strategy.** The tuning period is not
evidence that the bot got better -- it is substantially evidence that its universe
got more expensive.

Forward-looking, prices today resemble the tuning period, so the ~3.3 bps regime is
the relevant one for live trading. That is contingent: a metals selloff that halves
these names restores the holdout's cost structure, in which the strategy does not
beat SPY.

### What this changes

1. The stop floor at 0.5% is confirmed, and for a second independent reason.
2. No figure in this file above the banner is a return.
3. The strategy is viable only while its universe stays expensive. Screening the
   universe on PRICE (or on spread as a fraction of price) is now the single
   highest-value untested change -- SCCO at $94 pays 0.5 bps, SVM at $4 pays 12.
4. Trade frequency is the other lever. 1,956 round trips to net $20k on the holdout
   is $10 a trade against a $33 average cost.

## Screening the universe on price (tick-cost screen)

`eligibility.min_price` rejects entries below a share price. This is a TRANSACTION
COST screen, not a quality judgement: the one-cent minimum tick is a fixed cost per
share, so the threshold maps directly to a cost ceiling -- $10 caps tick cost at 5
bps per side, $20 at 2.5. It is deliberately separate from `max_spread_pct`, which
asks whether a quote is unusually wide FOR THAT NAME; a $4 stock with a perfectly
tight one-cent quote passes that gate and still cannot afford to be traded.

The screen is evaluated per signal on the live price, so a name that rallies
becomes eligible on its own -- EXK is excluded at $4.37 and admitted at $10.84.

### Holdout, 568 days, costs on, 0.5% stop floor

| min_price | trades | gross | cost | **net** | max DD | ret/DD | cost bps | >5R |
|---|---|---|---|---|---|---|---|---|
| none | 1,956 | $85,593 | $65,233 | $20,360 | 11.88% | 1.7 | 7.3 | 36 |
| **$5** | 1,654 | $75,810 | $36,212 | **$39,598** | 4.92% | 8.0 | 4.6 | 30 |
| **$10** | 976 | $49,174 | $10,727 | **$38,446** | **3.23%** | **11.9** | 2.2 | 16 |
| $15 | 748 | $38,694 | $5,668 | $33,026 | 2.17% | 15.2 | 1.5 | 11 |
| $20 | 622 | $28,176 | $3,704 | $24,472 | 2.02% | 12.1 | 1.2 | 8 |

### Tuning, 193 days

| min_price | trades | gross | cost | **net** | max DD | ret/DD | >5R |
|---|---|---|---|---|---|---|---|
| none | 614 | $38,496 | $10,052 | $28,444 | 5.77% | 4.9 | 17 |
| **$5** | 579 | $37,571 | $6,856 | **$30,714** | 4.43% | 6.9 | 15 |
| **$10** | 551 | $35,281 | $5,535 | $29,746 | 4.27% | **7.0** | 11 |
| $20 | 388 | $16,128 | $2,463 | $13,665 | 5.34% | 2.6 | 3 |

**This is the largest effect found in the project.** On the holdout a $5 screen
nearly doubles net ($20,360 -> $39,598) while more than halving drawdown (11.88%
-> 4.92%). Both periods improve on net AND drawdown at $5 and $10; $20 is too
aggressive in both.

Paired by day, costs on:

| comparison | per day | t | P(<=0) | better/worse days |
|---|---|---|---|---|
| holdout none -> $5 | +$39.75 | **+2.64** | **0.002** | **248/211** |
| holdout none -> $10 | +$37.37 | +1.63 | 0.063 | 284/188 |
| holdout $5 -> $10 | -$2.49 | -0.14 | 0.536 | 238/187 |
| tuning none -> $5 | +$14.01 | +1.09 | 0.117 | 32/14 |
| tuning none -> $10 | +$8.04 | +0.33 | 0.387 | 54/30 |

Note the holdout's better/worse split: 248/211 at $5 and 284/188 at $10, both
majority-better. Unlike the stop floor, this change improves the TYPICAL day rather
than only the tail -- it removes trades that were losing money to the spread.

$5 and $10 are statistically indistinguishable from each other (t=-0.14).

### Versus SPY, with the screen and costs

| holdout | return | CAGR | max DD | ret/DD | Sharpe |
|---|---|---|---|---|---|
| bot, no screen | 20.5% | 8.6% | 11.88% | 1.7 | 0.92 |
| bot, $5 screen | 39.7% | 16.0% | **4.92%** | **8.1** | **2.06** |
| SPY buy & hold | **56.5%** | **22.0%** | 18.76% | 3.0 | 1.32 |

| tuning | return | CAGR | max DD | ret/DD | Sharpe |
|---|---|---|---|---|---|
| bot, $5 screen | **30.7%** | **41.9%** | **4.43%** | **6.9** | **3.17** |
| SPY buy & hold | 11.8% | 15.7% | 8.88% | 1.3 | 1.19 |

The screen does not make the bot beat SPY's raw holdout return -- 39.7% against
56.5%. It does take it from losing on every measure to winning decisively on
risk-adjusted ones: Sharpe 2.06 vs 1.32, return per unit of drawdown 8.1 vs 3.0.
Whether that trade is worth making is a judgement about leverage and temperament,
not something the backtest decides.

### ADOPTED: min_price = 10.0

Re-verified with NO override flags, so both runs exercise the shipped config path
end to end. Both reproduced the flag runs to the cent: holdout $38,446.26, tuning
$29,745.69.

$10 was chosen over the marginally higher-netting $5 on three grounds: the two are
statistically indistinguishable (t=-0.14); $10 halves holdout drawdown (4.92% ->
3.23%) for ~3% less net; and $10 caps tick cost at 5 bps per side against a ~10 bps
breakeven, leaving headroom if real spreads exceed the one-cent floor the cost
model assumes -- which they do on the cheaper names. The cost model is deliberately
optimistic, so the more conservative screen is the safer read of it.

### Where the shipped config now stands (holdout, costs on)

| configuration | net | max DD | ret/DD |
|---|---|---|---|
| no stop floor, no price screen | $10,512 | 13.85% | 0.8 |
| + stop floor 0.5% | $20,360 | 11.88% | 1.7 |
| **+ price screen $10** | **$38,446** | **3.23%** | **11.9** |

Two config values, both adopted today, take the holdout from $10.5k on a 13.85%
drawdown to $38.4k on a 3.23% one -- 3.7x the net for a quarter of the drawdown.
Neither changed a single line of strategy logic; both are about the cost of
trading rather than about what to trade.

The honest caveat remains that the holdout still returns less than SPY over the
same window (39.7% at the $5 screen, 38.4k/38.4% at $10, against SPY's 56.5%),
while beating it substantially on every risk-adjusted measure.

## Gap-conditioned volume baseline: hypothesis refuted

Prompted by 2026-09-17, where SVM closed +8.91% and VZLA +8.67% and the bot took
neither. Decomposing those days showed most of the move happened before the bell:

| | prev close -> open -> close | total | overnight gap | intraday |
|---|---|---|---|---|
| SVM | 11.17 -> 11.75 -> 12.16 | +8.91% | **+5.19%** | +3.53% |
| VZLA | 3.69 -> 3.85 -> 4.01 | +8.67% | **+4.34%** | +4.16% |
| CDE | 19.00 -> 19.75 -> 20.00 | +5.24% | +3.95% | +1.24% |

The bot is flat overnight, so half to three-quarters of those moves were never
available to it. The intraday remainder was refused on volume: SVM's RVOL decayed
0.77 -> 0.36 against a 1.10 gate.

The hypothesis: RVOL compares today's cumulative volume to the same clock time on
an AVERAGE day, and that comparison is structurally unfair after a gap, because the
repricing already happened while the market was shut. So `eligibility.gap_day`
applies a relaxed RVOL threshold when |open vs prior close| clears a bar.

### It is worse in every configuration tested

| period | shipped | gap 3.0 / rvol 0.70 | gap 3.0 / rvol 0.50 | gap 2.0 / rvol 0.70 |
|---|---|---|---|---|
| holdout | **$38,446** | $33,549 | $33,189 | $34,025 |
| tuning | **$29,746** | $28,886 | $28,456 | - |

### Why: low volume on a gap day is real information, not an artifact

Isolating the trades the relaxation ADDS on the holdout:

| cohort | trades | net | win rate | mean R | >3R |
|---|---|---|---|---|---|
| shipped baseline | 976 | $38,446 | 49% | +0.291 | 62 |
| **trades added by the relaxation** | **35** | **-$3,395** | **26%** | **-0.472** | **0** |
| trades present in both | 971 | $36,943 | 49% | +0.294 | 62 |
| trades displaced | 5 | -$292 | 40% | -0.270 | 0 |

The added cohort is not marginally worse, it is a losing population: a 26% win
rate against 49%, mean R of -0.472, NOT ONE trade above +3R, and 25 of 35 exiting
on a stop. Day-level, 21 of 29 affected days are negative.

The premise was wrong. A stock that gaps up and then trades on light volume is not
a good setup whose volume reads low for technical reasons -- it is a move nobody is
confirming, and momentum entries into it get stopped out. The RVOL gate was
already doing exactly the job it exists to do.

Note the second-order cost: the shared 971 trades earn $36,943 in the
gap-conditioned run against $38,446 shipped, because the extra trades consume risk
budget and position slots that the better trades would otherwise have used. A bad
filter costs more than the bad trades it admits.

SVM on 2026-09-17 was therefore a correct refusal that happened to be wrong on the
day. The gate is right on average, which is the only sense in which a gate can be
right.

`eligibility.gap_day.min_gap_pct` ships at 0.0 (disabled). The mechanism is kept,
tested and off, so the question does not have to be re-litigated from scratch.

### SI=F is cached, and the backtest does not need it

`scripts/fetch_reference_data.py` caches COMEX silver futures to
`data_cache/reference/` -- deliberately NOT `data_cache/historical/`, because that
directory defines the backtest's trading-day universe and SI=F trades nearly 24
hours including Sunday evenings, which would silently shift every backtest window.

Overnight futures moves predict the next morning's SLV gap with 0.974 correlation
over 22 days, and on 2026-09-17 futures were +3.52% overnight -- the gap was
knowable at 9:29am. But that only matters for trading the gap, which this strategy
does not do. By the time it evaluates anything, the gap is already visible in the
stock's own open versus its prior close, which is what the (rejected) experiment
above used and which has the full 761-day history. Yahoo serves only ~60 days of
5-minute SI=F, and Twelve Data cannot fill the gap on the current plan (XAG/USD
needs a paid tier; the bare symbol SI is an unrelated NYSE listing).
