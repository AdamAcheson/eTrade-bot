# Interactive Brokers: what has been verified

Findings from `scripts/ibkr_check.py`, run by the account holder on their own Mac
against TWS (paper). Newest first.

## 2026-09-24, 12:45 ET (market open), TWS paper, port 7497

| check | result |
|---|---|
| API connection | PASS -- read-only connect works on the paper account |
| account | `DUT160852`, paper, INDIVIDUAL; $5,000 cash, $5,000 available funds |
| buying power | **$20,000** -- the paper account is margin-type; the bot's own no-borrow and settled-cash rules (`config/risk.yaml`) are what keep it cash-only |
| DayTradesRemaining | not reported by IBKR |
| contract lookup | PASS -- AG resolves on NYSE |
| quote | none, live or delayed; error 354 "Requested market data is not subscribed ... Delayed market data is available" |
| 5-minute bars | 36 bars returned, but the newest started **20 minutes** before the run: **DELAYED** |

**Conclusion: without a market-data subscription IBKR's bars are 15-minute delayed
and cannot drive the bot.** A 5-minute signal acted on 15 minutes late is a
different strategy from the one backtested.

### Options for live bars

1. **IBKR paid Level 1 data.** Reported pricing for non-professional users: the
   *US Securities Snapshot and Futures Value Bundle* (about $10/month, waived at $30/month
   in commissions, which on Lite's $0 commissions never happens) plus the *US
   Equity and Options Add-On Streaming Bundle* (about $4.50/month). Confirm on IBKR's
   own subscription page before buying. Subscriptions are bought on the live
   username and then shared with paper (Settings > Paper Trading Account), which
   can take up to 24 hours.
2. **IBKR's free Cboe One + IEX real-time data.** It is free, but it is NON-consolidated:
   it covers only some exchanges. The strategy's volume rules compare today's volume
   against consolidated history, so partial-exchange volume would read as very
   low and reject most trades. Not suitable as the bot's bar source. It is also
   unverified whether it is served over the API.
3. **Keep a separate bar vendor** (Twelve Data or similar) and use IBKR for orders
   only. That needs a paid real-time plan there too: the free tier's rate limits cannot poll
   ~28 symbols every 5 minutes.

### Still open

* **Lite does not permit API trading** (IBKR's FAQ and third-party reports agree).
  The paper account connects because paper accounts are IBKR Pro (`IBKRPRO`
  header). Automating the live account therefore means switching it to Pro.

### What Pro costs this strategy (estimated 2026-09-24)

Applied to the shipped $5,000 settled-cash backtests (`settled_h` holdout, 568
sessions; `settled_t` tuning, 193 sessions), with one entry and one exit order per trade
and no partial exits in the journals. The median trade is only ~82-95 shares, so
per-order minimums dominate.

| | holdout | tuning |
|---|---|---|
| net before commissions | $2,775.91 ($4.89/session) | $1,598.00 ($8.28/session) |
| Pro **Fixed** ($0.005/sh, $1 min, 1% cap) | -$1,357.19 -> **$2.50/session** | -$575.01 -> **$5.30/session** |
| Pro **Tiered** approx. ($0.0035/sh, $0.35 min, + ~$0.0032/sh clearing and exchange, + TAF) | -$1,095.48 -> **$2.96/session** | -$426.34 -> **$6.07/session** |

Commissions come to roughly $40-50 a month, which clears the $30 waiver on the
$10 data bundle. The data cost is then only the ~$4.50 streaming add-on, about
$0.21 a session.

**Commissions take about 40-50% of the holdout profit and about 27-36% of the tuning profit.** Tiered
is the cheaper plan at this order size. Tiered's exchange fee depends on whether an order takes or adds
liquidity, so the approximation assumes every order takes (the costly case).
