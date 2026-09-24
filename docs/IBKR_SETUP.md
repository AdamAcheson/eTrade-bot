# Interactive Brokers: what has been verified

Findings from `scripts/ibkr_check.py`, run by the account holder on their own Mac
against TWS (paper). Newest first.

## Order connection (built and verified on paper 2026-09-24)

`src/broker/ibkr.py` (`IBKRPaperBrokerAdapter`), selected by `broker.yaml: mode:
ibkr_paper`. **Paper accounts only.** config_loader and the adapter each require a
paper port (7497/4002) on this computer, and the adapter disconnects unless every
account TWS reports starts with "DU". The shipped config stays `mode: paper` (the
simulator).

* `submit_limit_order` waits up to `fill_timeout_seconds` (10 s) for the fill, then
  cancels any remainder, so nothing rests at IBKR unwatched and the bot sees the
  final filled quantity, which can be 0, partial or full.
* Entries open a position with the shares that actually filled; an entry that
  fills nothing frees the ticker again.
* An exit that does not fully fill is re-sent at 0.25% and then 0.75% below the bid
  (`exit_retry_steps_pct`). Anything still unsold prints `EXIT INCOMPLETE` and
  must be closed in TWS by hand. Exits are booked at the real average fill.
* Reported buying power is cash, never the paper account's 4x margin figure.

Test it with `python3 scripts/ibkr_paper_order_test.py` during market hours, with
TWS's "Read-Only API" box unticked. It places three 1-share orders: one that cannot
fill and must cancel, a buy, then a sell. Afterwards it checks the account holds
what it held before.

**Verified 2026-09-24 ~13:30 ET on DUT160852** (second run): the half-price buy
came back cancelled; the buy filled at $18.69; the sell filled at $18.68. The paper
account charged $0.19 per 1-share order, which is the 1%-of-value cap.

**TWS "Order Precautions" pop-up -- answer Yes.** On the first API order TWS asks
whether to bypass its order precautions for API orders. While that dialog is open
TWS holds the orders. The adapter timed out and cancelled them, and TWS reported them
cancelled, but when the dialog was answered it sent them anyway. One buy filled
(1 AG share the bot did not know about) and the half-price buy stayed open. The
bot's reconciliation would have flagged that share and blocked AG; it could not
have prevented it. Keep "Bypass Order Precautions for API Orders" ticked on the
paper login. For the live login, set the precaution limits just above the bot's
own ($2,500 per order) rather than leave a dialog that can hold orders.

`scripts/ibkr_paper_flatten.py` cancels every open order and sells every position
on the paper account (paper-guarded, asks for YES).

**Not yet built:** a market-data feed from IBKR for the live loop
(`scripts/run_bot.py` still needs `market_data_source: etrade`). That waits on
the data subscription below. The bot's P&L also still uses the modelled costs, not
IBKR's reported commissions (captured on each order as `Order.commission`).

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
