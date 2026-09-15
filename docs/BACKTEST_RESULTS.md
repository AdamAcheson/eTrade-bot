# Backtest results

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
