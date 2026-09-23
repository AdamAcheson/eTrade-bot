"""Draw each trade on its own session chart, so a human can look at what the bot did.

Everything else in this project is numbers. A rule can pass 300 unit tests and still
fire on a bar that plainly is not the pattern it claims to be, and no amount of
aggregate P&L will say so. This renders the bars the bot actually saw, with the
entry, the stop, the target and the exit marked, and leaves the judging to a person.

The VWAP drawn here comes from the bot's OWN `vwap_series`, not a reimplementation:
a verification tool that computes its reference line differently from the thing it
is verifying would agree with a broken bot and disagree with a correct one.

Output is a standalone HTML file with inline SVG -- no plotting library, so nothing
is added to requirements.txt, and it opens in any browser.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from data.indicators import vwap_series  # noqa: E402
from models.bar import Bar  # noqa: E402

CACHE = os.path.join(ROOT, "data_cache", "historical")
REPORTS = os.path.join(ROOT, "reports")

# Candle up/down is the only categorical pair on the chart. Validated all-pairs in
# both modes (worst normal-vision dE 33.6 light / 31.8 dark, CVD 24.7 / 26.8) --
# green/red would fail that outright for red-green colour blindness. VWAP is drawn
# in recessive ink rather than a third hue: it is a reference line, not a series,
# and the violet that would otherwise fit collides with the blue in dark mode.
W, PRICE_H, VOL_H = 900, 340, 84
ML, MR, MT, MB = 62, 96, 28, 26
GAP = 2.0  # surface gap between adjacent marks


def load_bars(ticker: str, day: str) -> List[Bar]:
    path = os.path.join(CACHE, f"{ticker}.csv")
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            if not row["timestamp"].startswith(day):
                continue
            out.append(Bar(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=float(row["volume"]),
            ))
    out.sort(key=lambda b: b.timestamp)
    return out


def bar_index(bars: Sequence[Bar], iso: str) -> Optional[int]:
    """Index of the bar a timestamp belongs to -- exact match, else the last bar
    at or before it (an exit can land mid-bar)."""
    if not bars:
        return None
    ts = datetime.fromisoformat(iso)
    best = None
    for i, b in enumerate(bars):
        if b.timestamp == ts:
            return i
        if b.timestamp <= ts:
            best = i
    return best


def esc(v) -> str:
    return html.escape(str(v), quote=True)


def figure(trade: dict, bars: List[Bar]) -> str:
    """One trade, one SVG: candles, VWAP, stop/target rails, entry and exit."""
    n = len(bars)
    vwaps = vwap_series(bars)

    lo = min(b.low for b in bars)
    hi = max(b.high for b in bars)
    for level in (trade["initial_stop"], trade["initial_target"], trade["entry_price"], trade["exit_price"]):
        lo, hi = min(lo, level), max(hi, level)
    pad = (hi - lo) * 0.06 or 0.01
    lo, hi = lo - pad, hi + pad

    plot_w = W - ML - MR
    step = plot_w / n
    bw = max(1.6, step - GAP)

    def x(i: float) -> float:
        return ML + i * step + step / 2.0

    def y(p: float) -> float:
        return MT + (hi - p) / (hi - lo) * PRICE_H

    # Volume scales to the 95th percentile, not the max: an opening-auction bar
    # routinely runs 30x the session median (DRD 2026-09-22: 61,138 against 1,738),
    # and scaling to it flattens every other bar to nothing. Bars above the cap are
    # drawn full height and the axis says so, rather than silently misreading.
    vols = sorted(b.volume for b in bars)
    # nearest-rank on n-1, not int(0.95*n): the latter indexes the MAXIMUM whenever
    # n <= 20, so the cap silently never bit on a short session.
    vmax = vols[min(round(0.95 * (len(vols) - 1)), len(vols) - 1)] or max(vols) or 1.0
    clipped = sum(1 for v in vols if v > vmax)
    vol_top = MT + PRICE_H + 22

    def vy(v: float) -> float:
        return vol_top + VOL_H - min(v / vmax, 1.0) * VOL_H

    ei = bar_index(bars, trade["entry_time"])
    xi = bar_index(bars, trade["exit_time"])
    parts: List[str] = []

    # held-position band, behind everything
    if ei is not None and xi is not None:
        x0, x1 = x(ei) - step / 2, x(xi) + step / 2
        parts.append(f'<rect class="held" x="{x0:.1f}" y="{MT}" width="{max(x1 - x0, 1):.1f}" height="{PRICE_H}"/>')

    # price gridlines + axis labels
    for k in range(5):
        p = lo + (hi - lo) * k / 4.0
        yy = y(p)
        parts.append(f'<line class="grid" x1="{ML}" y1="{yy:.1f}" x2="{W - MR}" y2="{yy:.1f}"/>')
        parts.append(f'<text class="tick" x="{ML - 8}" y="{yy + 4:.1f}" text-anchor="end">{p:,.2f}</text>')

    # candles
    for i, b in enumerate(bars):
        cls = "up" if b.close >= b.open else "down"
        cx = x(i)
        parts.append(f'<line class="wick {cls}" x1="{cx:.1f}" y1="{y(b.high):.1f}" x2="{cx:.1f}" y2="{y(b.low):.1f}"/>')
        top, bot = y(max(b.open, b.close)), y(min(b.open, b.close))
        parts.append(
            f'<rect class="body {cls}" x="{cx - bw / 2:.1f}" y="{top:.1f}" width="{bw:.1f}" '
            f'height="{max(bot - top, 1.0):.1f}"><title>{b.timestamp:%H:%M}  O {b.open:,.2f}  H {b.high:,.2f}  '
            f'L {b.low:,.2f}  C {b.close:,.2f}  vol {b.volume:,.0f}</title></rect>'
        )
        parts.append(f'<rect class="vol {cls}" x="{cx - bw / 2:.1f}" y="{vy(b.volume):.1f}" '
                     f'width="{bw:.1f}" height="{vol_top + VOL_H - vy(b.volume):.1f}"/>')

    # VWAP
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vwaps) if v is not None)
    if pts:
        parts.append(f'<polyline class="vwap" points="{pts}"/>')
        last = next(v for v in reversed(vwaps) if v is not None)
        parts.append(f'<text class="lbl vwap-lbl" x="{W - MR + 6}" y="{y(last) + 4:.1f}">VWAP {last:,.2f}</text>')

    # stop and target rails -- status colours, always with a written label beside them
    for level, cls, name in ((trade["initial_stop"], "stop", "stop"),
                             (trade["initial_target"], "target", "target")):
        yy = y(level)
        parts.append(f'<line class="rail {cls}" x1="{ML}" y1="{yy:.1f}" x2="{W - MR}" y2="{yy:.1f}"/>')
        parts.append(f'<text class="lbl {cls}-lbl" x="{W - MR + 6}" y="{yy + 4:.1f}">{name} {level:,.2f}</text>')

    # entry and exit
    def marker(i: int, price: float, up: bool, label: str, cls: str) -> str:
        cx, cy = x(i), y(price)
        d = 7
        tri = (f"{cx:.1f},{cy - d:.1f} {cx - d:.1f},{cy + d:.1f} {cx + d:.1f},{cy + d:.1f}" if up
               else f"{cx:.1f},{cy + d:.1f} {cx - d:.1f},{cy - d:.1f} {cx + d:.1f},{cy - d:.1f}")
        ty = cy - d - 7 if up else cy + d + 14
        return (f'<polygon class="mark {cls}" points="{tri}"/>'
                f'<text class="mark-lbl" x="{cx:.1f}" y="{ty:.1f}" text-anchor="middle">{esc(label)}</text>')

    if ei is not None:
        parts.append(marker(ei, trade["entry_price"], True, f'entry {trade["entry_price"]:,.2f}', "entry"))
    if xi is not None:
        parts.append(marker(xi, trade["exit_price"], False, f'exit {trade["exit_price"]:,.2f}', "exit"))

    # time axis
    for i in range(0, n, max(1, n // 8)):
        parts.append(f'<text class="tick" x="{x(i):.1f}" y="{vol_top + VOL_H + 16}" '
                     f'text-anchor="middle">{bars[i].timestamp:%H:%M}</text>')
    parts.append(f'<text class="tick axis-name" x="{ML - 8}" y="{vol_top + 10}" text-anchor="end">vol</text>')
    if clipped:
        # in the right gutter with the other rail labels -- the left margin only fits "vol"
        parts.append(f'<text class="tick axis-name" x="{W - MR + 6}" y="{vol_top + 10}">p95 cap · {clipped} over</text>')

    height = vol_top + VOL_H + MB
    net, r = trade["net_profit"], trade["r_return"]
    sign = "win" if net > 0 else "loss"
    rows = [
        ("setup", trade.get("setup_type") or "-"), ("score", f'{trade.get("setup_score", 0):.1f}'),
        ("entry", f'{trade["entry_price"]:,.2f} at {trade["entry_time"][11:16]}'),
        ("exit", f'{trade["exit_price"]:,.2f} at {trade["exit_time"][11:16]} ({trade["exit_reason"]})'),
        ("initial stop", f'{trade["initial_stop"]:,.2f}'), ("initial target", f'{trade["initial_target"]:,.2f}'),
        ("shares", f'{trade["shares"]:,}'), ("notional", f'${trade["entry_price"] * trade["shares"]:,.0f}'),
        ("net P&L", f"${net:,.2f}"), ("R", f"{r:+.2f}"),
        ("max favourable", f'{trade.get("maximum_favorable_excursion", 0):,.2f}'),
        ("max adverse", f'{trade.get("maximum_adverse_excursion", 0):,.2f}'),
    ]
    table = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in rows)

    return f"""<figure class="viz-root">
<figcaption><span class="tkr">{esc(trade["ticker"])}</span> <span class="day">{esc(trade["date"])}</span>
<span class="res {sign}">{'+' if net > 0 else ''}${net:,.2f} · {r:+.2f}R</span>
<span class="sub">{esc(trade.get("setup_type") or "")} · exit {esc(trade["exit_reason"])}</span></figcaption>
<svg viewBox="0 0 {W} {height}" width="100%" role="img"
     aria-label="{esc(trade['ticker'])} {esc(trade['date'])}: entry {trade['entry_price']:.2f}, exit {trade['exit_price']:.2f}, net {net:.2f} dollars, {r:+.2f} R">
{''.join(parts)}
</svg>
<details><summary>the numbers</summary><table>{table}</table></details>
</figure>"""


CSS = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;padding:24px 16px 48px;background:#f9f9f7;color:#0b0b0b;
 font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
h1{font-size:1.25rem;margin:0 0 4px}
.lede{color:#52514e;margin:0 0 24px;max-width:70ch}
.wrap{max-width:960px;margin:0 auto}
figure{margin:0 0 28px;padding:16px;background:#fcfcfb;border:1px solid #e4e3df;border-radius:10px}
figcaption{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;margin-bottom:10px}
.tkr{font-weight:650;font-size:1.05rem}
.day{color:#52514e}
.res{font-variant-numeric:tabular-nums;font-weight:650}
.res.win{color:#0ca30c}.res.loss{color:#d03b3b}
.sub{color:#52514e;font-size:.85rem;margin-left:auto}
svg{display:block;overflow:visible}
.grid{stroke:#e4e3df;stroke-width:1}
.tick{fill:#52514e;font-size:11px;font-variant-numeric:tabular-nums}
.axis-name{fill:#8a8984}
.body.up,.vol.up,.wick.up{fill:#2a78d6;stroke:#2a78d6}
.body.down,.vol.down,.wick.down{fill:#eb6834;stroke:#eb6834}
.wick{stroke-width:1.4}
.vol{opacity:.42}
.vwap{fill:none;stroke:#52514e;stroke-width:2;stroke-dasharray:5 4}
.rail{stroke-width:2;stroke-dasharray:2 5}
.rail.stop{stroke:#d03b3b}.rail.target{stroke:#0ca30c}
.lbl{font-size:11px;font-variant-numeric:tabular-nums;dominant-baseline:middle}
.vwap-lbl{fill:#52514e}.stop-lbl{fill:#d03b3b}.target-lbl{fill:#0ca30c}
.mark{stroke:#fcfcfb;stroke-width:2}
.mark.entry{fill:#0b0b0b}.mark.exit{fill:#52514e}
.mark-lbl{fill:#0b0b0b;font-size:11px;font-weight:600;font-variant-numeric:tabular-nums}
.held{fill:#0b0b0b;opacity:.05}
details{margin-top:10px}
summary{cursor:pointer;color:#52514e;font-size:.85rem}
table{border-collapse:collapse;margin-top:8px;font-size:.85rem}
th,td{text-align:left;padding:3px 14px 3px 0;font-weight:400}
th{color:#52514e;font-weight:400}
td{font-variant-numeric:tabular-nums}
.empty{color:#52514e}
@media (prefers-color-scheme:dark){
 :root:not([data-theme="light"]) body{background:#0d0d0d;color:#fff}
 :root:not([data-theme="light"]) .lede,:root:not([data-theme="light"]) .day,
 :root:not([data-theme="light"]) .sub,:root:not([data-theme="light"]) .tick,
 :root:not([data-theme="light"]) summary,:root:not([data-theme="light"]) th,
 :root:not([data-theme="light"]) .empty{color:#c3c2b7}
 :root:not([data-theme="light"]) figure{background:#1a1a19;border-color:#33322e}
 :root:not([data-theme="light"]) .grid{stroke:#33322e}
 :root:not([data-theme="light"]) .tick{fill:#c3c2b7}
 :root:not([data-theme="light"]) .axis-name{fill:#8a8984}
 :root:not([data-theme="light"]) .body.up,:root:not([data-theme="light"]) .vol.up,
 :root:not([data-theme="light"]) .wick.up{fill:#3987e5;stroke:#3987e5}
 :root:not([data-theme="light"]) .body.down,:root:not([data-theme="light"]) .vol.down,
 :root:not([data-theme="light"]) .wick.down{fill:#d95926;stroke:#d95926}
 :root:not([data-theme="light"]) .vwap{stroke:#c3c2b7}
 :root:not([data-theme="light"]) .vwap-lbl{fill:#c3c2b7}
 :root:not([data-theme="light"]) .mark{stroke:#1a1a19}
 :root:not([data-theme="light"]) .mark.entry{fill:#fff}
 :root:not([data-theme="light"]) .mark.exit{fill:#c3c2b7}
 :root:not([data-theme="light"]) .mark-lbl{fill:#fff}
 :root:not([data-theme="light"]) .held{fill:#fff;opacity:.07}
}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tag", default="_daily", help="journal suffix: reports/backtest_trades<TAG>.jsonl")
    ap.add_argument("--day", help="only this session (YYYY-MM-DD)")
    ap.add_argument("--ticker", help="only this ticker")
    ap.add_argument("--limit", type=int, default=40, help="most recent N trades (default 40)")
    ap.add_argument("--out", help="output HTML (default reports/trades<TAG>.html)")
    args = ap.parse_args()

    path = os.path.join(REPORTS, f"backtest_trades{args.tag}.jsonl")
    if not os.path.exists(path):
        print(f"no journal at {path}", file=sys.stderr)
        return 1
    trades = [json.loads(l) for l in open(path)]
    if args.day:
        trades = [t for t in trades if t["date"] == args.day]
    if args.ticker:
        trades = [t for t in trades if t["ticker"].upper() == args.ticker.upper()]
    trades.sort(key=lambda t: (t["date"], t["entry_time"]))
    if args.limit and len(trades) > args.limit:
        print(f"{len(trades)} trades match; drawing the most recent {args.limit} "
              f"(raise with --limit)", file=sys.stderr)
        trades = trades[-args.limit:]
    if not trades:
        print("no trades matched", file=sys.stderr)
        return 1

    cache: Dict[tuple, List[Bar]] = {}
    figs, skipped = [], []
    for t in trades:
        key = (t["ticker"], t["date"])
        if key not in cache:
            cache[key] = load_bars(*key)
        bars = cache[key]
        if not bars:
            skipped.append(f'{t["ticker"]} {t["date"]}')
            continue
        figs.append(figure(t, bars))

    if not figs:
        print("no cached bars for any matching trade", file=sys.stderr)
        return 1

    span = f"{trades[0]['date']} to {trades[-1]['date']}" if trades[0]["date"] != trades[-1]["date"] else trades[0]["date"]
    net = sum(t["net_profit"] for t in trades)
    note = ""
    if skipped:
        note = (f'<p class="empty">No cached bars for {len(skipped)} trade(s): '
                f'{esc(", ".join(sorted(set(skipped))[:6]))}{"…" if len(set(skipped)) > 6 else ""}</p>')

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trade charts {esc(span)}</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>Trade charts — {esc(span)}</h1>
<p class="lede">{len(figs)} simulated trade(s) from <code>backtest_trades{esc(args.tag)}.jsonl</code>,
net ${net:,.2f}. Blue bars closed up, orange closed down. The dashed grey line is the
session VWAP the bot computed; dotted rails are the initial stop and target. These are
backtest fills at bar prices, not executed orders.</p>
{note}
{''.join(figs)}
</div></body></html>"""

    out = args.out or os.path.join(REPORTS, f"trades{args.tag}.html")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as fh:
        fh.write(doc)
    print(f"wrote {out}  ({len(figs)} figures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
