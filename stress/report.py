"""Turn a run's JSONL log into a standalone, gamified HTML scorecard.

Reads stress/reports/run-<ts>.jsonl (every event the arena recorded) and writes a
matching .html plus latest.html. The page is self-contained -- the storefront's own
colour tokens inline, one SVG throughput sparkline, no scripts, no network -- so it
opens from disk and drops straight onto the Pages demo if wanted.

    python3 stress/report.py stress/reports/run-20260101-120000.jsonl
"""

from __future__ import annotations

import html
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from metrics import ACHIEVEMENTS, _pct

ACH = {a[0]: (a[1], a[2]) for a in ACHIEVEMENTS}


def aggregate(events):
    shop = dict(requests=0, served=0, errors=0, rate_limited=0, orders=0, revenue_minor=0)
    by_act = Counter()
    lat = []
    red = dict(attempts=0, blocked=0, breached=0, bytes_leaked=0)
    by_tech = defaultdict(lambda: [0, 0, 0])
    by_target = defaultdict(lambda: [0, 0])
    t0 = t1 = None
    buckets = defaultdict(lambda: [0, 0.0])   # second -> [served, sum_ms] for the sparkline

    for ev in events:
        t = ev.get("t")
        if t is not None:
            t0 = t if t0 is None else min(t0, t)
            t1 = t if t1 is None else max(t1, t)
        if ev["lane"] == "shop":
            shop["requests"] += 1
            by_act[ev.get("act", "?")] += 1
            if ev.get("ms") is not None:
                lat.append(ev["ms"])
            if ev.get("status") == 429:
                shop["rate_limited"] += 1
            elif ev.get("ok"):
                shop["served"] += 1
                if t is not None:
                    b = buckets[int(t)]
                    b[0] += 1
                    b[1] += ev.get("ms") or 0.0
                if ev.get("act") == "pay" and ev.get("amount_minor"):
                    shop["orders"] += 1
                    shop["revenue_minor"] += ev["amount_minor"]
            else:
                shop["errors"] += 1
        else:
            red["attempts"] += 1
            tech, target = ev.get("technique", "?"), ev.get("target", "?")
            by_tech[tech][0] += 1
            by_target[target][0] += 1
            if ev.get("blocked"):
                red["blocked"] += 1
                by_tech[tech][1] += 1
                by_target[target][1] += 1
            else:
                red["breached"] += 1
                by_tech[tech][2] += 1
                red["bytes_leaked"] += int(ev.get("leaked", 0))

    lat.sort()
    duration = (t1 - t0) if (t0 is not None and t1 is not None) else 0
    snap = {
        "duration": duration,
        "shop": {**shop, "p50": _pct(lat, 50), "p95": _pct(lat, 95), "p99": _pct(lat, 99),
                 "by_act": dict(by_act), "peak_rps": max((b[0] for b in buckets.values()), default=0)},
        "red": {**red, "by_tech": {k: v for k, v in by_tech.items()},
                "by_target": {k: v for k, v in by_target.items()}},
        "series": [[int(sec - (t0 or 0)), b[0]] for sec, b in sorted(buckets.items())],
    }
    earned = []
    for aid, title, blurb, pred in ACHIEVEMENTS:
        probe = {"shop": {**snap["shop"], "peak_active": 0}, "red": snap["red"]}
        try:
            if pred(probe):
                earned.append(aid)
        except (KeyError, ZeroDivisionError):
            pass
    snap["unlocked"] = earned
    return snap


def _sparkline(series, w=720, h=120):
    if not series:
        return '<p class="muted">no throughput recorded</p>'
    xs = [p[0] for p in series]
    ys = [p[1] for p in series]
    xmax = max(xs) or 1
    ymax = max(ys) or 1
    pts = " ".join(f"{10 + x / xmax * (w - 20):.1f},{h - 10 - y / ymax * (h - 20):.1f}" for x, y in series)
    area = f"10,{h - 10} " + pts + f" {10 + (w - 20):.1f},{h - 10}"
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" aria-label="requests per second over time">'
            f'<polygon points="{area}" fill="var(--accent)" opacity="0.14"/>'
            f'<polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="2.5"/>'
            f'<text x="12" y="18" fill="var(--ink-3)" font-size="12">peak {ymax:,}/s</text></svg>')


def render_html(snap, title="Babel Bazaar — Stress Arena"):
    s, r = snap["shop"], snap["red"]
    held = r["breached"] == 0
    verdict_cls = "held" if held else "breach"
    verdict = ("GATE HELD — the core never answered an attacker"
               if held else "BREACH — a core service answered without the BFF")
    e = html.escape

    def stat(label, value, sub=""):
        sub = f'<span class="sub">{sub}</span>' if sub else ""
        return f'<div class="stat"><b>{value}</b><span>{e(label)}</span>{sub}</div>'

    tech_rows = "".join(
        f'<tr><td>{e(k)}</td><td>{v[1]:,}</td><td>{v[0]:,}</td>'
        f'<td class="{"ok" if v[2] == 0 else "bad"}">{v[2]:,}</td></tr>'
        for k, v in sorted(r["by_tech"].items(), key=lambda kv: -kv[1][0]))
    target_rows = "".join(
        f'<tr><td>{e(k)}</td><td>{v[1]:,}</td><td>{v[0]:,}</td></tr>'
        for k, v in sorted(r["by_target"].items(), key=lambda kv: -kv[1][0]))
    act_rows = "".join(
        f'<tr><td>{e(k)}</td><td>{v:,}</td></tr>'
        for k, v in sorted(s["by_act"].items(), key=lambda kv: -kv[1]))
    chips = "".join(
        f'<span class="chip {"got" if a[0] in snap["unlocked"] else ""}" title="{e(a[2])}">'
        f'{"★" if a[0] in snap["unlocked"] else "☆"} {e(a[1])}</span>'
        for a in ACHIEVEMENTS)
    defence = 100.0 if r["attempts"] == 0 else 100.0 * r["blocked"] / r["attempts"]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<style>
:root {{ color-scheme: light dark;
  --ink:#17130f; --ink-2:#4a433b; --ink-3:#7a7067; --paper:#fbf8f3; --paper-2:#f3eee6; --card:#fff;
  --line:#e7e0d5; --brand:#d4482b; --accent:#0f6b62; --gold:#b7791f; --good:#0f6b62; --bad:#c0392b;
  --font:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; --serif:ui-serif,Georgia,serif;
  --mono:ui-monospace,Menlo,Consolas,monospace; }}
@media (prefers-color-scheme:dark){{:root{{--ink:#f4efe8;--ink-2:#c9c0b4;--ink-3:#958b80;--paper:#14110e;
  --paper-2:#1c1814;--card:#211c17;--line:#322b24;--brand:#ef6a4c;--accent:#4fc1b2;--good:#4fc1b2;--bad:#ef6a4c;}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 var(--font)}}
.wrap{{width:min(100% - 32px,1000px);margin:0 auto;padding:40px 0 72px}}
h1{{font-family:var(--serif);font-size:clamp(2rem,1.4rem+2vw,3rem);margin:0 0 4px;letter-spacing:-.02em}}
h2{{font-family:var(--serif);margin:40px 0 12px}}
.muted{{color:var(--ink-3)}}
.verdict{{margin:20px 0;padding:18px 22px;border-radius:16px;font-weight:650;border:1px solid var(--line)}}
.verdict.held{{background:color-mix(in srgb,var(--good) 14%,transparent);color:var(--good);border-color:color-mix(in srgb,var(--good) 40%,transparent)}}
.verdict.breach{{background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad);border-color:var(--bad)}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.stat{{padding:18px;border-radius:14px;background:var(--card);border:1px solid var(--line)}}
.stat b{{display:block;font:700 1.9rem/1.1 var(--font);letter-spacing:-.02em}}
.stat span{{color:var(--ink-3);font-size:.85rem}} .stat .sub{{display:block;color:var(--accent);font-size:.8rem;margin-top:2px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
.panel{{padding:20px;border-radius:16px;background:var(--card);border:1px solid var(--line)}}
.meter{{height:12px;border-radius:999px;background:var(--paper-2);overflow:hidden;margin:8px 0 16px}}
.meter>i{{display:block;height:100%;background:var(--good)}}
table{{border-collapse:collapse;width:100%;font-size:.92rem}} th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}}
th{{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3)}}
td.ok{{color:var(--good);font-weight:650}} td.bad{{color:var(--bad);font-weight:700}}
.chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}}
.chip{{padding:6px 12px;border-radius:999px;border:1px solid var(--line);color:var(--ink-3);font-size:.85rem}}
.chip.got{{color:var(--gold);border-color:color-mix(in srgb,var(--gold) 45%,transparent);background:color-mix(in srgb,var(--gold) 10%,transparent);font-weight:650}}
.spark{{padding:12px;border-radius:16px;background:var(--card);border:1px solid var(--line);margin-top:12px}}
footer{{margin-top:48px;color:var(--ink-3);font-size:.85rem;border-top:1px solid var(--line);padding-top:16px}}
@media(max-width:760px){{.grid{{grid-template-columns:repeat(2,1fr)}}.two{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<p class="muted" style="text-transform:uppercase;letter-spacing:.14em;font-size:.75rem;color:var(--brand);font-weight:700">Stress Arena · scorecard</p>
<h1>Babel Bazaar held the line</h1>
<p class="muted">{s['served']:,} requests served and {r['attempts']:,} attacks turned away in {snap['duration']:.0f} seconds.</p>
<div class="verdict {verdict_cls}">{"✔" if held else "⚠"} {e(verdict)}</div>

<h2>The shop under load</h2>
<div class="grid">
{stat('requests served', f"{s['served']:,}", f"{s['errors']:,} errors · {s['rate_limited']:,} rate-limited")}
{stat('peak throughput', f"{s['peak_rps']:,}/s")}
{stat('p95 latency', f"{s['p95']:.0f} ms", f"p50 {s['p50']:.0f} · p99 {s['p99']:.0f}")}
{stat('orders paid', f"{s['orders']:,}", f"revenue £{s['revenue_minor']/100:,.0f}")}
</div>
<div class="spark">{_sparkline(snap['series'])}</div>

<h2>The red team at the gate</h2>
<div class="two">
<div class="panel">
<strong>Defence score {defence:.1f}%</strong>
<div class="meter"><i style="width:{defence:.1f}%;background:{'var(--good)' if held else 'var(--bad)'}"></i></div>
<table><thead><tr><th>technique</th><th>blocked</th><th>tried</th><th>breached</th></tr></thead>
<tbody>{tech_rows}</tbody></table>
</div>
<div class="panel">
<strong>Targets attacked directly</strong>
<p class="muted" style="font-size:.85rem">Each is a core service hit without the BFF. Blocked = refused (403 or no route to the host).</p>
<table><thead><tr><th>service</th><th>blocked</th><th>attempts</th></tr></thead><tbody>{target_rows}</tbody></table>
<p style="margin-top:14px"><strong>Bytes exfiltrated:</strong> <span class="{'ok' if held else 'bad'}" style="font-size:1.3rem;font-weight:700">{r['bytes_leaked']:,}</span></p>
</div>
</div>

<h2>What the shoppers did</h2>
<div class="panel"><table><thead><tr><th>action</th><th>count</th></tr></thead><tbody>{act_rows}</tbody></table></div>

<h2>Achievements · {len(snap['unlocked'])}/{len(ACHIEVEMENTS)}</h2>
<div class="chips">{chips}</div>

<footer>Generated by the Babel Bazaar stress arena (<code>stress/arena.py</code>). The gate guards every core
service with a per-request HMAC signature the BFF alone can produce; see <code>docs/request-signing.md</code>.</footer>
</div></body></html>"""


def write_report(jsonl_path, out_dir=None):
    jsonl_path = Path(jsonl_path)
    events = []
    with open(jsonl_path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
    snap = aggregate(events)
    out_dir = Path(out_dir) if out_dir else jsonl_path.parent
    out = out_dir / (jsonl_path.stem + ".html")
    out.write_text(render_html(snap))
    (out_dir / "latest.html").write_text(render_html(snap))
    return out, snap


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    path, snap = write_report(sys.argv[1])
    print(f"wrote {path}")
