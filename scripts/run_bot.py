#!/usr/bin/env python3
"""Runs the trading bot's live event loop: polls quotes for the approved universe
and their benchmarks, builds bars, and calls TradingBot.run_cycle() continuously.
Prints new signals and trades as they happen. Press Ctrl+C to stop safely.

Interactive Brokers PAPER account (orders and prices through TWS on this computer):

    python3 scripts/run_bot.py --ibkr-paper

That sets broker.yaml mode: ibkr_paper and market_data_source: ibkr for this run
only. TWS must be open, logged into Paper Trading, with "Read-Only API" unticked.
Without a live-data subscription IBKR sends 15-minute-DELAYED prices; the startup
summary says which arrived, and delayed prices only test the plumbing.

Otherwise it runs whatever config/broker.yaml says, which needs market_data_source:
etrade or ibkr. With mode: paper, orders/fills/P&L stay in the local simulator
even though the prices are real.

Relative-volume baselines are seeded from the local historical cache
(data_cache/historical, populated by scripts/fetch_historical_data_twelvedata.py).
Without them every signal rejects on REJECTED_LOW_VOLUME -- min_relative_volume is
1.10 and RVOL reads as unavailable -- so the loop would run all day and never take a
trade. The baseline for each five-minute slot is that slot's average cumulative
volume over the last --baseline-days sessions, refreshed as the session advances,
matching how scripts/backtest.py computes it.

If a symbol has no cached history the loop still runs, but that symbol will not
produce entries; the startup summary says which.

Usage:
    python3 scripts/run_bot.py [poll_interval_seconds] [--baseline-days N] [--ibkr-paper]
"""

import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from config_loader import load_config, use_ibkr_paper, ConfigError  # noqa: E402
from data.historical_data import HistoricalDataError, get_bars, group_bars_by_day  # noqa: E402
from main import build_default_bot  # noqa: E402
from models.signal import Decision  # noqa: E402



BAR_MINUTES = 5

_SESSIONS = {}


def cached_sessions(symbol):
    """The local cache's bars for `symbol`, grouped by session, loaded once per run:
    each file holds years of 5-minute bars and takes ~0.5 s to parse, and both the
    RVOL baselines and the IBKR volume check read it."""
    if symbol not in _SESSIONS:
        try:
            _SESSIONS[symbol] = group_bars_by_day(get_bars(symbol))
        except HistoricalDataError:
            _SESSIONS[symbol] = {}
    return _SESSIONS[symbol]


def volume_baselines_from_cache(symbol, lookback_days):
    """Average cumulative volume at each five-minute slot of the session, over the
    most recent `lookback_days` cached sessions.

    RVOL compares today's volume-so-far against a normal day's volume-by-this-time,
    so the baseline has to be per slot, not a single daily average -- comparing
    10:05's cumulative volume against a full day's would reject every morning
    signal. Recent sessions only: a three-year average would understate a name whose
    volume has since doubled.
    """
    by_day = cached_sessions(symbol)
    recent = [by_day[d] for d in sorted(by_day)[-lookback_days:]]
    if not recent:
        return {}

    sums, counts = {}, {}
    for day_bars in recent:
        cumulative = 0.0
        for i, bar in enumerate(day_bars):
            cumulative += bar.volume
            sums[i] = sums.get(i, 0.0) + cumulative
            counts[i] = counts.get(i, 0) + 1
    return {i: sums[i] / counts[i] for i in sums}


def session_bar_index(now, market_open_hhmm):
    """Which five-minute slot of the session `now` falls in. Negative before the
    open, which callers treat as "no baseline yet" rather than clamping to slot 0 --
    a pre-open quote is not the first bar of the day."""
    hh, mm = (int(x) for x in market_open_hhmm.split(":"))
    open_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return int((now - open_dt).total_seconds() // (BAR_MINUTES * 60))


def volume_scales(provider, symbols, today):
    """Per symbol: IBKR's volume divided by the cached history's, summed over completed
    sessions both sources have (today's partial session is left out).

    The RVOL baselines come from the cache (Twelve Data), while today's volume comes
    from IBKR. On 2026-09-24, IBKR's volume was 0.78x the cache's (median over 38
    symbols). Unscaled, every RVOL reading would be 22% low: min_relative_volume
    1.10 would behave like ~1.41, and most trades would be rejected. Scaling the baseline by
    this ratio puts both sides of RVOL on IBKR's scale, as the backtest had both on
    Twelve Data's."""
    scales = {}
    for sym in symbols:
        cached = cached_sessions(sym)
        ib_total = cache_total = 0.0
        for day, total in provider.session_volumes(sym).items():
            if day == today:
                continue
            c = sum(b.volume for b in cached.get(day, []))
            if total > 0 and c > 0:
                ib_total += total
                cache_total += c
        if cache_total > 0:
            scales[sym] = ib_total / cache_total
    return scales


def apply_volume_scales(baselines, scales):
    """Scale each symbol's baseline. A symbol with no overlap takes the median of the
    rest; with no overlap anywhere, nothing is scaled. Returns (median, how many
    symbols were measured)."""
    if not scales:
        return None, 0
    ordered = sorted(scales.values())
    median = ordered[len(ordered) // 2]
    for sym, slots in baselines.items():
        k = scales.get(sym, median)
        baselines[sym] = {i: v * k for i, v in slots.items()}
    return median, len(scales)


def ibkr_data_report(provider, scale_summary=None):
    """Startup lines on what IBKR is actually sending. Empty for other providers."""
    if not hasattr(provider, "data_delayed"):
        return []
    lines = []
    delayed = provider.data_delayed()
    if delayed is None:
        lines.append("  Prices:            none received yet (market closed?)")
    elif delayed:
        lines.append("  Prices:            DELAYED ~15 min -- no live-data subscription. Good for "
                     "testing the plumbing only;")
        lines.append("                     entries and exits are decided on stale prices.")
    else:
        lines.append("  Prices:            LIVE")
    if provider.failed:
        lines.append(f"  NO DATA:           {', '.join(f'{s} ({e})' for s, e in sorted(provider.failed.items()))}")
    if scale_summary is not None:
        median, n = scale_summary
        if n:
            lines.append(f"  Volume scale:      IBKR = {median:.2f} x cached history (median of {n} "
                         f"symbols); RVOL baselines scaled to match")
        else:
            lines.append("  Volume scale:      no session in both IBKR and the cache (cache out of "
                         "date?) -- RVOL baselines NOT scaled, may be biased")
    return lines


def health_line(provider, symbols, now, staleness_limit):
    """One line: can the bot actually evaluate its symbols right now, and if not, why not."""
    if not hasattr(provider, "health"):
        return None
    h = provider.health(symbols, now, staleness_limit)
    n = h["symbols"]
    from_bars = f" + {h['quotes_from_bars']} from bars" if h["quotes_from_bars"] else ""
    return (f"data: bars today {h['bars_today']}/{n} (streaming {h['streaming']}), "
            f"quotes {h['quotes']}/{n}{from_bars}, fresh {h['fresh']}/{n}")


def main() -> int:
    args = [a for a in sys.argv[1:]]
    baseline_days = 20
    if "--baseline-days" in args:
        i = args.index("--baseline-days")
        baseline_days = int(args[i + 1])
        del args[i:i + 2]
    ibkr_paper = "--ibkr-paper" in args
    if ibkr_paper:
        args.remove("--ibkr-paper")
    poll_interval = int(args[0]) if args else 30

    try:
        config = load_config()
        if ibkr_paper:
            use_ibkr_paper(config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    source = config.broker.get("market_data_source", "memory")
    if source not in ("etrade", "ibkr"):
        print(
            "No live prices configured: run with --ibkr-paper, or set config/broker.yaml "
            f"market_data_source to 'ibkr' or 'etrade' (it is '{source}', which never "
            "receives any data unless a caller pushes it explicitly).",
            file=sys.stderr,
        )
        return 1

    if ibkr_paper:
        import logging
        logging.getLogger("ib_async").setLevel(logging.CRITICAL)
    try:
        bot = build_default_bot(config)
    except Exception as e:  # TWS not running, API off, live account (IBKRSafetyError)
        print(f"Could not start: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    provider = bot.data_provider
    wait = getattr(provider, "wait", time.sleep)

    tz = ZoneInfo(config.schedule.get("timezone", "America/New_York"))
    universe = config.auto_tradeable_universe()
    benchmarks = sorted({config.benchmark_of(t) for t in universe})
    all_symbols = sorted(set(universe) | set(benchmarks))

    baselines = {sym: volume_baselines_from_cache(sym, baseline_days) for sym in all_symbols}
    missing = sorted(s for s, b in baselines.items() if not b)
    market_open = config.schedule.get("market_open", "09:30")

    scale_summary = None
    if hasattr(provider, "subscribe"):
        print(f"Subscribing to {len(all_symbols)} symbols (about a second each) ...")
        provider.subscribe(all_symbols)
        wait(3)
        today = datetime.now(tz).strftime("%Y-%m-%d")
        scale_summary = apply_volume_scales(baselines, volume_scales(provider, all_symbols, today))
        for symbol in all_symbols:
            provider.poll(symbol, datetime.now(tz))
    staleness_limit = config.risk["safety"]["data_staleness_limit_seconds"]

    print("=" * 70)
    print("STARTING LIVE BOT LOOP")
    print("=" * 70)
    print(f"  Broker:            {type(bot.broker).__name__}")
    print(f"  Market data:       {type(provider).__name__}")
    for line in ibkr_data_report(provider, scale_summary):
        print(line)
    health = health_line(provider, all_symbols, datetime.now(tz), staleness_limit)
    if health:
        print(f"  Data now:          {health}")
    print(f"  Universe ({len(universe)}):      {', '.join(universe)}")
    print(f"  Benchmarks ({len(benchmarks)}):    {', '.join(benchmarks)}")
    print(f"  RVOL baselines:    {len(all_symbols) - len(missing)}/{len(all_symbols)} symbols "
          f"from the last {baseline_days} cached sessions")
    if missing:
        print(f"  NO BASELINE:       {', '.join(missing)} -- these cannot produce entries")
    print(f"  Poll interval:     {poll_interval}s")
    print(f"  Timezone:          {tz.key}")
    print("  Press Ctrl+C to stop.")
    print()

    seen_signals = 0
    seen_trades = 0
    cycles = 0
    current_day = datetime.now(tz).date()

    try:
        while True:
            now = datetime.now(tz)
            cycles += 1

            if now.date() != current_day:
                # Without this, RiskManager's daily counters (trades_today,
                # consecutive losses, cooldowns) never reset if this process is
                # left running across midnight -- confirmed in scripts/backtest.py:
                # max_trades_per_day silently blocked every signal after the 3rd
                # trade of the ENTIRE run, not just the 3rd trade of each day.
                bot.risk_manager.reset_daily_counters()
                current_day = now.date()
                print(f"[{now:%H:%M:%S}] new trading day -- daily risk counters reset")

            slot = session_bar_index(now, market_open)
            for symbol in all_symbols:
                try:
                    bot.data_provider.poll(symbol, now)
                except Exception as e:
                    print(f"[{now:%H:%M:%S}] quote poll failed for {symbol}: {e}")
                # Refresh the volume baseline for the slot the session is currently
                # in. Left unset it stays 0.0, RVOL reads as unavailable, and every
                # signal rejects on REJECTED_LOW_VOLUME.
                if slot >= 0:
                    bot.data_provider.set_average_volume_baseline(
                        symbol, baselines.get(symbol, {}).get(slot, 0.0)
                    )

            bot.run_cycle(now)

            new_signals = bot.signal_journal.signals[seen_signals:]
            seen_signals = len(bot.signal_journal.signals)
            for s in new_signals:
                if s.decision == Decision.ENTRY_CANDIDATE:
                    print(
                        f"[{now:%H:%M:%S}] ENTRY CANDIDATE  {s.ticker:<6} "
                        f"setup={s.setup_type} score={s.setup_score:.1f} "
                        f"entry={s.entry_price:.2f} stop={s.stop:.2f} target={s.target:.2f} R={s.r_ratio:.2f}"
                    )
                else:
                    reason = s.rejection_reason.value if s.rejection_reason else "?"
                    print(f"[{now:%H:%M:%S}] reject  {s.ticker:<6} {reason}")

            new_trades = bot.trade_journal.trades[seen_trades:]
            seen_trades = len(bot.trade_journal.trades)
            for t in new_trades:
                print(
                    f"[{now:%H:%M:%S}] TRADE CLOSED  {t.ticker}  entry={t.entry_price:.2f} "
                    f"exit={t.exit_price:.2f} reason={t.exit_reason.value} "
                    f"net_profit=${t.net_profit:,.2f} R={t.r_return:.2f}"
                )

            if cycles % 10 == 0:
                open_positions = len(bot.position_manager.open_positions())
                print(f"[{now:%H:%M:%S}] heartbeat -- cycle {cycles}, open positions: {open_positions}")
                health = health_line(provider, all_symbols, now, staleness_limit)
                if health:
                    print(f"[{now:%H:%M:%S}] {health}")

            wait(poll_interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        print(f"Total cycles: {cycles}, signals logged: {seen_signals}, trades closed: {seen_trades}")
        open_now = [p.ticker for p in bot.position_manager.open_positions()]
        if open_now:
            print(f"STILL OPEN: {', '.join(open_now)} -- the bot is no longer managing these. "
                  f"Restart it, or close them in TWS (scripts/ibkr_paper_flatten.py on paper).")
    finally:
        for thing in (provider, bot.broker):
            for method in ("close", "disconnect"):
                if hasattr(thing, method):
                    try:
                        getattr(thing, method)()
                    except Exception:  # noqa: BLE001 -- best effort on the way out
                        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
