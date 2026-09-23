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
nav{display:flex;gap:16px;align-items:baseline;margin:0 0 20px;font-size:.9rem}
nav span{color:#52514e}
nav a{color:#2a78d6}
table.index{width:100%;font-size:.9rem;margin-top:8px}
table.index th{color:#52514e;border-bottom:1px solid #e4e3df;padding-bottom:6px}
table.index td{padding:6px 14px 6px 0;border-bottom:1px solid #f0efec}
table.index td.win{color:#0ca30c}table.index td.loss{color:#d03b3b}
table.index a{color:#2a78d6}
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
 :root:not([data-theme="light"]) nav span{color:#c3c2b7}
 :root:not([data-theme="light"]) nav a,:root:not([data-theme="light"]) table.index a{color:#3987e5}
 :root:not([data-theme="light"]) table.index th{color:#c3c2b7;border-color:#33322e}
 :root:not([data-theme="light"]) table.index td{border-color:#262521}
}
"""



# ---------------------------------------------------------------- app mode ----
# One self-contained page for a whole backtest. The static mode writes ~26KB of SVG
# per trade, so a thousand trades is ~26MB across a dozen linked files -- unusable
# to hand to someone as files, since the links between them do not survive. Here the
# bars are embedded ONCE per ticker-day (946 sessions carry 1,001 holdout trades) and
# the figures are drawn in the browser on demand, which lands around 4MB in a single
# file that filters and sorts in place.
#
# VWAP is still computed in Python by the bot's own vwap_series and shipped as an
# array. Recomputing it in JavaScript would reintroduce exactly the second
# implementation this tool exists to avoid.

APP_JS = r"""
const $ = s => document.querySelector(s);
const W=900, PH=340, VH=84, ML=62, MR=96, MT=28, MB=26, GAP=2;
const esc = v => String(v).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const money = v => (v<0?'-':'')+'$'+Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const hhmm = m => String(Math.floor(m/60)).padStart(2,'0')+':'+String(m%60).padStart(2,'0');

function figure(t){
  const s = SESSIONS[t.k];
  if(!s) return `<figure class="viz-root"><figcaption><span class="tkr">${esc(t.tk)}</span>
    <span class="day">${esc(t.d)}</span></figcaption><p class="empty">No cached bars.</p></figure>`;
  const n = s.o.length;
  let lo=Math.min(...s.l), hi=Math.max(...s.h);
  for(const p of [t.sp, t.tg, t.ep, t.xp]){ lo=Math.min(lo,p); hi=Math.max(hi,p); }
  const pad=(hi-lo)*0.06 || 0.01; lo-=pad; hi+=pad;
  const step=(W-ML-MR)/n, bw=Math.max(1.6, step-GAP);
  const X=i=>ML+i*step+step/2, Y=p=>MT+(hi-p)/(hi-lo)*PH;
  const sv=[...s.v].sort((a,b)=>a-b);
  const vmax = sv[Math.min(Math.round(0.95*(n-1)), n-1)] || Math.max(...sv) || 1;
  const clipped = sv.filter(v=>v>vmax).length;
  const volTop = MT+PH+22, VY = v => volTop+VH-Math.min(v/vmax,1)*VH;
  const idx = ts => { let b=null; for(let i=0;i<n;i++){ if(s.t[i]===ts) return i; if(s.t[i]<=ts) b=i; } return b; };
  const ei=idx(t.em), xi=idx(t.xm);
  const o=[];
  if(ei!==null&&xi!==null){ const x0=X(ei)-step/2, x1=X(xi)+step/2;
    o.push(`<rect class="held" x="${x0.toFixed(1)}" y="${MT}" width="${Math.max(x1-x0,1).toFixed(1)}" height="${PH}"/>`); }
  for(let k=0;k<5;k++){ const p=lo+(hi-lo)*k/4, y=Y(p);
    o.push(`<line class="grid" x1="${ML}" y1="${y.toFixed(1)}" x2="${W-MR}" y2="${y.toFixed(1)}"/>`);
    o.push(`<text class="tick" x="${ML-8}" y="${(y+4).toFixed(1)}" text-anchor="end">${p.toFixed(2)}</text>`); }
  for(let i=0;i<n;i++){
    const cls=s.c[i]>=s.o[i]?'up':'down', cx=X(i);
    o.push(`<line class="wick ${cls}" x1="${cx.toFixed(1)}" y1="${Y(s.h[i]).toFixed(1)}" x2="${cx.toFixed(1)}" y2="${Y(s.l[i]).toFixed(1)}"/>`);
    const top=Y(Math.max(s.o[i],s.c[i])), bot=Y(Math.min(s.o[i],s.c[i]));
    o.push(`<rect class="body ${cls}" x="${(cx-bw/2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(bot-top,1).toFixed(1)}"><title>${hhmm(s.t[i])}  O ${s.o[i].toFixed(2)}  H ${s.h[i].toFixed(2)}  L ${s.l[i].toFixed(2)}  C ${s.c[i].toFixed(2)}  vol ${s.v[i].toLocaleString()}</title></rect>`);
    o.push(`<rect class="vol ${cls}" x="${(cx-bw/2).toFixed(1)}" y="${VY(s.v[i]).toFixed(1)}" width="${bw.toFixed(1)}" height="${(volTop+VH-VY(s.v[i])).toFixed(1)}"/>`);
  }
  const pts=s.w.map((v,i)=>v===null?null:`${X(i).toFixed(1)},${Y(v).toFixed(1)}`).filter(Boolean).join(' ');
  if(pts){ o.push(`<polyline class="vwap" points="${pts}"/>`);
    const last=[...s.w].reverse().find(v=>v!==null);
    o.push(`<text class="lbl vwap-lbl" x="${W-MR+6}" y="${(Y(last)+4).toFixed(1)}">VWAP ${last.toFixed(2)}</text>`); }
  for(const [lv,cls,nm] of [[t.sp,'stop','stop'],[t.tg,'target','target']]){ const y=Y(lv);
    o.push(`<line class="rail ${cls}" x1="${ML}" y1="${y.toFixed(1)}" x2="${W-MR}" y2="${y.toFixed(1)}"/>`);
    o.push(`<text class="lbl ${cls}-lbl" x="${W-MR+6}" y="${(y+4).toFixed(1)}">${nm} ${lv.toFixed(2)}</text>`); }
  const mark=(i,p,up,label,cls)=>{ const cx=X(i), cy=Y(p), d=7;
    const tri = up ? `${cx.toFixed(1)},${(cy-d).toFixed(1)} ${(cx-d).toFixed(1)},${(cy+d).toFixed(1)} ${(cx+d).toFixed(1)},${(cy+d).toFixed(1)}`
                   : `${cx.toFixed(1)},${(cy+d).toFixed(1)} ${(cx-d).toFixed(1)},${(cy-d).toFixed(1)} ${(cx+d).toFixed(1)},${(cy-d).toFixed(1)}`;
    return `<polygon class="mark ${cls}" points="${tri}"/><text class="mark-lbl" x="${cx.toFixed(1)}" y="${(up?cy-d-7:cy+d+14).toFixed(1)}" text-anchor="middle">${esc(label)}</text>`; };
  if(ei!==null) o.push(mark(ei,t.ep,true,`entry ${t.ep.toFixed(2)}`,'entry'));
  if(xi!==null) o.push(mark(xi,t.xp,false,`exit ${t.xp.toFixed(2)}`,'exit'));
  for(let i=0;i<n;i+=Math.max(1,Math.floor(n/8)))
    o.push(`<text class="tick" x="${X(i).toFixed(1)}" y="${volTop+VH+16}" text-anchor="middle">${hhmm(s.t[i])}</text>`);
  o.push(`<text class="tick axis-name" x="${ML-8}" y="${volTop+10}" text-anchor="end">vol</text>`);
  if(clipped) o.push(`<text class="tick axis-name" x="${W-MR+6}" y="${volTop+10}">p95 cap · ${clipped} over</text>`);
  const H=volTop+VH+MB, sign=t.np>0?'win':'loss';
  const rows=[['setup',t.st||'-'],['score',(t.sc||0).toFixed(1)],
    ['entry',`${t.ep.toFixed(2)} at ${hhmm(t.em)}`],['exit',`${t.xp.toFixed(2)} at ${hhmm(t.xm)} (${t.xr})`],
    ['initial stop',t.sp.toFixed(2)],['initial target',t.tg.toFixed(2)],
    ['shares',t.sh.toLocaleString()],['notional','$'+Math.round(t.ep*t.sh).toLocaleString()],
    ['net P&L',money(t.np)],['R',(t.r>=0?'+':'')+t.r.toFixed(2)]];
  return `<figure class="viz-root"><figcaption><span class="tkr">${esc(t.tk)}</span>
    <span class="day">${esc(t.d)}</span>
    <span class="res ${sign}">${t.np>0?'+':''}${money(t.np)} · ${(t.r>=0?'+':'')+t.r.toFixed(2)}R</span>
    <span class="sub">${esc(t.st||'')} · exit ${esc(t.xr)}</span></figcaption>
    <svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(t.tk)} ${esc(t.d)}: entry ${t.ep.toFixed(2)}, exit ${t.xp.toFixed(2)}, net ${t.np.toFixed(2)} dollars, ${t.r.toFixed(2)} R">${o.join('')}</svg>
    <details><summary>the numbers</summary><table>${rows.map(([k,v])=>`<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</table></details>
  </figure>`;
}

let view=[], page=0, per=25;
function apply(){
  const tk=$('#f-ticker').value, xr=$('#f-exit').value, res=$('#f-res').value, sort=$('#f-sort').value;
  view=TRADES.filter(t =>
    (!tk||t.tk===tk) && (!xr||t.xr===xr) &&
    (res==='' || (res==='win'?t.np>0:res==='loss'?t.np<0:t.r>3)));
  view.sort(sort==='r'?(a,b)=>b.r-a.r : sort==='net'?(a,b)=>b.np-a.np :
            sort==='worst'?(a,b)=>a.np-b.np : (a,b)=>a.d.localeCompare(b.d)||a.em-b.em);
  page=0; draw();
}
function draw(){
  const total=view.length, pages=Math.max(1,Math.ceil(total/per));
  page=Math.min(page,pages-1);
  const slice=view.slice(page*per,(page+1)*per);
  const net=view.reduce((a,t)=>a+t.np,0), wins=view.filter(t=>t.np>0).length;
  $('#summary').textContent = total
    ? `${total} trade(s), ${wins} winners (${(100*wins/total).toFixed(0)}%), net ${money(net)}. Showing ${page*per+1}–${page*per+slice.length}.`
    : 'No trades match these filters.';
  $('#figs').innerHTML = slice.map(figure).join('');
  $('#pg').textContent = `page ${page+1} of ${pages}`;
  $('#prev').disabled = page===0; $('#next').disabled = page>=pages-1;
  window.scrollTo({top:0});
}
function boot(){
  const opts=(el,vals)=>{ for(const v of vals){ const o=document.createElement('option'); o.value=v; o.textContent=v; el.appendChild(o);} };
  opts($('#f-ticker'), [...new Set(TRADES.map(t=>t.tk))].sort());
  opts($('#f-exit'), [...new Set(TRADES.map(t=>t.xr))].sort());
  for(const id of ['f-ticker','f-exit','f-res','f-sort']) $('#'+id).addEventListener('change',apply);
  $('#f-per').addEventListener('change',e=>{per=+e.target.value;page=0;draw();});
  $('#prev').addEventListener('click',()=>{page--;draw();});
  $('#next').addEventListener('click',()=>{page++;draw();});
  apply();
}
document.addEventListener('DOMContentLoaded',boot);
"""

APP_CSS = """
.controls{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:0 0 14px;padding:14px;
 background:#fcfcfb;border:1px solid #e4e3df;border-radius:10px}
.controls label{display:flex;flex-direction:column;gap:4px;font-size:.78rem;color:#52514e}
.controls select{font:inherit;font-size:.85rem;padding:5px 8px;border:1px solid #d6d5d0;
 border-radius:6px;background:#fff;color:#0b0b0b;min-width:9rem}
.pager{display:flex;gap:10px;align-items:center;margin:0 0 18px;font-size:.9rem}
.pager button{font:inherit;font-size:.85rem;padding:5px 12px;border:1px solid #d6d5d0;
 border-radius:6px;background:#fff;color:#0b0b0b;cursor:pointer}
.pager button:disabled{opacity:.4;cursor:default}
#summary{color:#52514e;font-size:.9rem;margin:0 0 10px}
@media (prefers-color-scheme:dark){
 :root:not([data-theme="light"]) .controls{background:#1a1a19;border-color:#33322e}
 :root:not([data-theme="light"]) .controls label,:root:not([data-theme="light"]) #summary{color:#c3c2b7}
 :root:not([data-theme="light"]) .controls select,:root:not([data-theme="light"]) .pager button{
  background:#242320;border-color:#403f3a;color:#fff}
}
"""


def build_app(trades, tag: str, out: str) -> int:
    """One self-contained page: bars once per ticker-day, figures drawn in-browser."""
    sessions, keep, skipped = {}, [], set()
    for t in trades:
        key = f'{t["ticker"]}|{t["date"]}'
        if key not in sessions:
            bars = load_bars(t["ticker"], t["date"])
            if not bars:
                sessions[key] = None
                skipped.add(key)
            else:
                open_min = bars[0].timestamp.hour * 60 + bars[0].timestamp.minute
                sessions[key] = {
                    "t": [b.timestamp.hour * 60 + b.timestamp.minute for b in bars],
                    "o": [round(b.open, 3) for b in bars], "h": [round(b.high, 3) for b in bars],
                    "l": [round(b.low, 3) for b in bars], "c": [round(b.close, 3) for b in bars],
                    "v": [int(b.volume) for b in bars],
                    # the bot's own vwap_series -- never recomputed in JS
                    "w": [None if v is None else round(v, 4) for v in vwap_series(bars)],
                }
                del open_min
        if sessions[key] is None:
            continue
        keep.append({
            "k": key, "tk": t["ticker"], "d": t["date"],
            "em": int(t["entry_time"][11:13]) * 60 + int(t["entry_time"][14:16]),
            "xm": int(t["exit_time"][11:13]) * 60 + int(t["exit_time"][14:16]),
            "ep": round(t["entry_price"], 4), "xp": round(t["exit_price"], 4),
            "sp": round(t["initial_stop"], 4), "tg": round(t["initial_target"], 4),
            "sh": t["shares"], "np": round(t["net_profit"], 2), "r": round(t["r_return"], 3),
            "xr": t["exit_reason"], "st": t.get("setup_type"), "sc": round(t.get("setup_score") or 0, 1),
        })
    sessions = {k: v for k, v in sessions.items() if v is not None}
    if not keep:
        print("no cached bars for any matching trade", file=sys.stderr)
        return 1

    # Sum the journal's own unrounded values, not the rounded ones shipped to the page:
    # 1,001 trades rounded to the cent drift 9c off the backtest's total, and a
    # verification tool must not disagree with the thing it verifies over rounding.
    kept_keys = {(t["k"], t["em"]) for t in keep}
    net = sum(t["net_profit"] for t in trades
              if (f'{t["ticker"]}|{t["date"]}',
                  int(t["entry_time"][11:13]) * 60 + int(t["entry_time"][14:16])) in kept_keys)
    note = (f'<p class="empty">No cached bars for {len(skipped)} session(s).</p>') if skipped else ""
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trade charts — {esc(tag)}</title><style>{CSS}{APP_CSS}</style></head>
<body><div class="wrap">
<h1>Trade charts — {len(keep):,} trades</h1>
<p class="lede">Every trade in <code>backtest_trades{esc(tag)}.jsonl</code>, netting
{'-' if net < 0 else ''}${abs(net):,.2f}, drawn on its own session. Blue bars closed up,
orange closed down. The dashed grey line is the session VWAP the bot computed; dotted
rails are the initial stop and target. Hover a bar for OHLCV. <strong>These are backtest
fills at bar prices, not executed orders.</strong></p>
{note}
<div class="controls">
<label>ticker<select id="f-ticker"><option value="">all</option></select></label>
<label>exit reason<select id="f-exit"><option value="">all</option></select></label>
<label>result<select id="f-res"><option value="">all</option><option value="win">winners</option>
<option value="loss">losers</option><option value="tail">the tail (&gt;3R)</option></select></label>
<label>order<select id="f-sort"><option value="time">chronological</option>
<option value="r">best R first</option><option value="net">biggest win first</option>
<option value="worst">biggest loss first</option></select></label>
<label>per page<select id="f-per"><option>10</option><option selected>25</option>
<option>50</option><option>100</option></select></label>
</div>
<p id="summary"></p>
<div class="pager"><button id="prev">← previous</button><span id="pg"></span><button id="next">next →</button></div>
<div id="figs"></div>
<div class="pager"><button id="prev2" hidden></button></div>
</div>
<script>const SESSIONS={json.dumps(sessions, separators=(",", ":"))};
const TRADES={json.dumps(keep, separators=(",", ":"))};
{APP_JS}</script></body></html>"""
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as fh:
        fh.write(doc)
    print(f"wrote {out}  ({len(keep):,} trades, {len(sessions):,} sessions, "
          f"{os.path.getsize(out) / 1e6:.1f} MB)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tag", default="_daily", help="journal suffix: reports/backtest_trades<TAG>.jsonl")
    ap.add_argument("--day", help="only this session (YYYY-MM-DD)")
    ap.add_argument("--ticker", help="only this ticker")
    ap.add_argument("--limit", type=int, default=40,
                    help="cap the number of trades drawn (default 40; 0 for all)")
    ap.add_argument("--chunk", type=int, default=0,
                    help="split across files of N figures each, with an index. A figure is "
                         "~26KB, so a thousand trades in one file is ~26MB and no browser "
                         "will scroll it comfortably. 100 is a reasonable page.")
    ap.add_argument("--sort", choices=("time", "r", "net"), default="time",
                    help="order: chronological (default), or by R / net P&L descending")
    ap.add_argument("--app", action="store_true",
                    help="one self-contained interactive page instead of static files: bars "
                         "embedded once per session, figures drawn in-browser, with filters "
                         "and paging. The right mode for a whole backtest.")
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
    if args.sort == "r":
        trades.sort(key=lambda t: -t["r_return"])
    elif args.sort == "net":
        trades.sort(key=lambda t: -t["net_profit"])
    else:
        trades.sort(key=lambda t: (t["date"], t["entry_time"]))
    if args.limit and len(trades) > args.limit:
        kept = trades[-args.limit:] if args.sort == "time" else trades[:args.limit]
        print(f"{len(trades)} trades match; drawing {args.limit} "
              f"({'most recent' if args.sort == 'time' else 'top by ' + args.sort}). "
              f"--limit 0 for all", file=sys.stderr)
        trades = kept
    if not trades:
        print("no trades matched", file=sys.stderr)
        return 1

    if args.app:
        return build_app(trades, args.tag,
                         args.out or os.path.join(REPORTS, f"trades{args.tag}-app.html"))

    cache: Dict[tuple, List[Bar]] = {}
    figs, meta, skipped = [], [], []
    for t in trades:
        key = (t["ticker"], t["date"])
        if key not in cache:
            cache[key] = load_bars(*key)
        bars = cache[key]
        if not bars:
            skipped.append(f'{t["ticker"]} {t["date"]}')
            continue
        figs.append(figure(t, bars))
        meta.append({"date": t["date"], "ticker": t["ticker"],
                     "net": t["net_profit"], "r": t["r_return"]})

    if not figs:
        print("no cached bars for any matching trade", file=sys.stderr)
        return 1

    net = sum(t["net_profit"] for t in trades)
    note = ""
    if skipped:
        note = (f'<p class="empty">No cached bars for {len(skipped)} trade(s): '
                f'{esc(", ".join(sorted(set(skipped))[:6]))}{"…" if len(set(skipped)) > 6 else ""}</p>')

    out = args.out or os.path.join(REPORTS, f"trades{args.tag}.html")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    stem, ext = os.path.splitext(out)
    size = args.chunk if args.chunk and args.chunk > 0 else len(figs)
    pages = [figs[i:i + size] for i in range(0, len(figs), size)] or [[]]
    metas = [meta[i:i + size] for i in range(0, len(meta), size)] or [[]]
    single = len(pages) == 1

    def page_path(k: int) -> str:
        return out if single else f"{stem}-{k + 1:03d}{ext}"

    order = {"time": "chronological", "r": "by R, descending", "net": "by net P&L, descending"}[args.sort]
    written = []
    for k, page in enumerate(pages):
        first, last = metas[k][0], metas[k][-1]
        head = (f"{first['date']} to {last['date']}" if first["date"] != last["date"] else first["date"]) \
            if args.sort == "time" else f"{first['r']:+.2f}R to {last['r']:+.2f}R"
        nav = ""
        if not single:
            bits = []
            if k > 0:
                bits.append(f'<a href="{esc(os.path.basename(page_path(k - 1)))}">← previous</a>')
            bits.append(f'<span>page {k + 1} of {len(pages)}</span>')
            if k < len(pages) - 1:
                bits.append(f'<a href="{esc(os.path.basename(page_path(k + 1)))}">next →</a>')
            nav = f'<nav>{" ".join(bits)}</nav>'
        page_net = sum(m["net"] for m in metas[k])
        doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trade charts {esc(head)}</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>Trade charts — {esc(head)}</h1>
<p class="lede">{len(page)} of {len(figs)} simulated trades from
<code>backtest_trades{esc(args.tag)}.jsonl</code>, {esc(order)}. This page nets
${page_net:,.2f}; the full selection nets ${net:,.2f}. Blue bars closed up, orange
closed down. The dashed grey line is the session VWAP the bot computed; dotted rails
are the initial stop and target. These are backtest fills at bar prices, not executed
orders.</p>
{nav}
{note if k == 0 else ""}
{''.join(page)}
{nav}
</div></body></html>"""
        with open(page_path(k), "w") as fh:
            fh.write(doc)
        written.append(page_path(k))

    if not single:
        rows = []
        for k, ms in enumerate(metas):
            pnet = sum(m["net"] for m in ms)
            wins = sum(1 for m in ms if m["net"] > 0)
            rows.append(
                f'<tr><td><a href="{esc(os.path.basename(page_path(k)))}">page {k + 1}</a></td>'
                f'<td>{esc(ms[0]["ticker"])} {esc(ms[0]["date"])} → {esc(ms[-1]["ticker"])} {esc(ms[-1]["date"])}</td>'
                f'<td>{len(ms)}</td><td>{wins}</td>'
                f'<td class="{"win" if pnet > 0 else "loss"}">${pnet:,.2f}</td>'
                f'<td>{max(m["r"] for m in ms):+.2f}R</td></tr>')
        idx = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trade charts index</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>Trade charts — {len(figs)} trades, {len(pages)} pages</h1>
<p class="lede">From <code>backtest_trades{esc(args.tag)}.jsonl</code>, {esc(order)},
netting ${net:,.2f}. Split into pages of {size} because one file of every figure is
about {len(figs) * 26 // 1000}MB. These are backtest fills, not executed orders.</p>
{note}
<table class="index"><thead><tr><th>page</th><th>span</th><th>trades</th><th>wins</th>
<th>net</th><th>best R</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</div></body></html>"""
        index_path = f"{stem}-index{ext}"
        with open(index_path, "w") as fh:
            fh.write(idx)
        written.insert(0, index_path)

    for w in written[:4]:
        print(f"wrote {w}")
    if len(written) > 4:
        print(f"... and {len(written) - 4} more ({len(figs)} figures total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
