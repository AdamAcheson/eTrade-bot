# Mining Stock Trading Bot

A rules-based, long-only intraday momentum/pullback trading bot for a fixed universe
of mining and commodity-related securities. **Paper trading by default; E*TRADE
SANDBOX only beyond that -- production trading is not implemented.** See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design (architecture,
file tree, config schemas, state machine, main-loop pseudocode) and the exact
safeguards that keep live/production trading disabled.

## Status: Phase 1 + Phase 2 (paper and E*TRADE sandbox)

* **Phase 1 -- Market data + signal engine**: implemented. Indicators (VWAP, EMA,
  ATR, relative volume, opening range, spread, swing highs/lows), benchmark
  confirmation, the "do not chase" rule, the two preferred setups (opening-range
  breakout + pullback, VWAP reclaim), and 0-100 setup scoring all have unit and
  scenario test coverage.
* **Phase 2 -- Paper trading**: the simulated broker (`broker/paper.py`), order
  manager (duplicate-order protection + pre-submit rechecks), position manager
  (state machine + breakeven/partial-exit trade management), and overnight-holding
  evaluation are implemented and wired together in `src/main.py: TradingBot`. This
  is still the default (`config/broker.yaml: mode: paper`).
* **Phase 2b -- E*TRADE sandbox connection**: `broker/etrade.py` (`ETradeBrokerAdapter`)
  makes real HTTP calls to E*TRADE's documented sandbox API (accounts, orders,
  quotes) using OAuth1 (`broker/etrade_auth.py`). Since E*TRADE's API has no
  historical-bar endpoint, `data/etrade_market_data.py` builds 5-minute bars itself
  by polling quotes. **This has not been exercised against a real sandbox account**
  -- see "Connecting to E*TRADE sandbox" below before you trust it.
* **Phase 3 -- Strategy evaluation**: `reporting/daily_report.py` produces the daily
  report (win rate, R multiples, drawdown, missed-opportunity analysis) and
  read-only feedback-loop suggestions.
* **Phase 4 -- Live/production trading**: **not implemented, anywhere.**
  `ETradeBrokerAdapter` raises `ETradeSafetyError` for any `environment` other than
  `"sandbox"`, and `config_loader.load_config()` separately raises unless
  `broker.yaml: mode` is `"paper"` or `"sandbox"`. See `docs/ARCHITECTURE.md`
  section 11 for the full safety chain.

## Connecting to E*TRADE sandbox

1. Register a sandbox app on E*TRADE's developer portal to get a consumer key/secret.
2. `cp .env.example .env` and fill in `ETRADE_SANDBOX_CONSUMER_KEY` /
   `ETRADE_SANDBOX_CONSUMER_SECRET`. Never commit `.env`.
3. `set -a; source .env; set +a` (or otherwise export those two vars), then run
   `python scripts/etrade_authorize.py` **locally, in a real terminal with a
   browser** -- it walks you through E*TRADE's 3-legged OAuth flow and prints an
   access token/secret for you to add to `.env`. This can't be done from inside an
   AI assistant session; the flow requires you to log in and read a verifier code.
4. Set `config/broker.yaml: mode: sandbox`.
5. Run `python scripts/etrade_sandbox_check.py` -- read-only (lists your account,
   gets one quote), places no orders. Confirm the output looks right before doing
   anything else with `ETradeBrokerAdapter`.
6. E*TRADE access tokens expire at midnight US Eastern or after 2 hours idle --
   re-run step 3 when that happens.

`src/main.py` still doesn't start a live loop when run directly, in either mode --
running `python src/main.py` prints a status message and exits.

## Project layout

See `docs/ARCHITECTURE.md` for the annotated file tree. In short:

```
config/     tickers.yaml, strategy.yaml, risk.yaml, broker.yaml, schedule.yaml
src/        broker/ data/ strategy/ risk/ positions/ execution/ reporting/ models/
tests/      unit tests + scenario tests
logs/       signal log (jsonl)
reports/    trade journal (jsonl)
```

## Running the tests

```
pip install -r requirements.txt
pytest
```

## Trading universe & rules

The approved symbols, benchmark ETF mappings, spread limits, overnight categories,
and profit-target ranges live in `config/tickers.yaml` (spec sections 3-4, 13,
16, 18-21) -- nothing ticker-specific is hard-coded in strategy source. Strategy
thresholds (scoring weights, chase-rule limits, reward/risk minimums) live in
`config/strategy.yaml`. Money-management and daily safety limits live in
`config/risk.yaml`, including the kill switch
(`emergency.kill_switch_enabled` + presence of `logs/KILL_SWITCH`).

`AQN` is present in `tickers.yaml` only so it is never silently miscategorized --
it is marked `strategy: excluded` and is never traded by the mining strategy.
`NZAUF` and `AAGAF` are `manual_only: true`: the bot evaluates and scores them but
never submits an order for them.

## Safety

* `config_loader.load_config()` only accepts `broker.yaml: mode: paper` or
  `sandbox` -- anything else (including `live`) raises at startup.
* `ETradeBrokerAdapter` independently refuses to construct unless
  `etrade.environment: sandbox` -- two separate checks have to agree before any
  network call to E*TRADE happens at all, and neither can reach production.
* Credentials are read only from environment variables named in `broker.yaml` --
  never hard-coded, never logged, never committed (`.env` is gitignored).
* No short selling, no averaging down, no pyramiding (all hard-disabled, not just
  defaulted).
* A stopped-out ticker enters a configurable cooldown before it can be re-entered
  (no revenge trading).
* Every evaluated signal (entry or rejection, with reason) and every completed trade
  is logged (`logs/signals.jsonl`, `reports/trades.jsonl`).
