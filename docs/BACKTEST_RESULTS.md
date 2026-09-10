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

## Holdout, 19 tickers

| config | trades | net | return | max DD | win% | flat | ret/DD | t |
|---|---|---|---|---|---|---|---|---|
| pre-change (1 slot, no trailing) | 579 | $23,659 | 23.7% | 4.6% | 20.9% | 145 | 5.2 | 2.95 |
| shipped (5 slots + trailing 1.0xATR) | 1082 | $44,811 | 44.8% | 3.9% | 46.4% | 7 | 11.4 | 4.36 |

On $100k, the shipped config returns **~17.8%/yr at 3.9% maximum drawdown**.
95% CI on total P&L (day-block bootstrap): **[$25,170, $65,540]**.

## Universe width changes the shape of the result, not just its size

The same holdout run on only the four tickers that had deep history earlier:

| universe | config | trades | days with a trade | net | top-5 share of profit | t |
|---|---|---|---|---|---|---|
| 4 tickers | pre-change | 201 | 172 | $353 | 1426% | 0.10 |
| 4 tickers | shipped | 227 | 173 | $5,265 | 88% | 1.50 |
| 19 tickers | pre-change | 579 | 407 | $23,659 | — | 2.95 |
| 19 tickers | shipped | 1082 | 413 | $44,811 | **15.7%** | 4.36 |

The recurring finding through this project's history -- that a handful of trades
carry all the profit, so nothing is measurable -- was substantially an artifact
of too narrow a universe. At four tickers the top five trades are 88% of profit
and the result is indistinguishable from zero. At nineteen they are 15.7% and
the result is significant at t=4.36.

Widening also bought independent observations rather than merely stacking more
correlated trades into the same sessions: days carrying at least one trade went
173 -> 413.

## What is still unproven

The **improvement from the config change** is not statistically separable from
zero: 95% CI on (shipped - pre-change) is **[-$4,434, +$46,683]**.

Its direction is consistent in every cut of the data and the trailing stop's
mechanism is understood -- it removes trades that closed at exactly $0.00 after a
median 1.12% favorable excursion (flat trades 145 -> 7, win rate 20.9% -> 46.4%)
-- but the magnitude is not established by this data. Treat the trailing stop as
justified by mechanism, and the 5-slot change as the weaker half: out of sample
at four tickers it made drawdown worse on its own.

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
