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
