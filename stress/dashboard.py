"""The gamified scoreboard: a live, full-screen terminal render of one Metrics snapshot.

Left tower is the shop under load (requests, throughput, latency, orders, revenue).
Right tower is the red team being turned away (attempts, a defence meter, a breakdown
by technique and target, and a big ZERO where stolen bytes would go). Below them, the
achievements unlocked so far and a colour-coded tail of the newest events.

ANSI only, no dependencies. On a real terminal it paints the alternate screen and
repaints in place; piped to a file or a dumb terminal it falls back to a plain line
every second so logs stay readable.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

from metrics import ACHIEVEMENTS, defence_score

C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "red": "\033[31m", "grn": "\033[32m", "ylw": "\033[33m", "blu": "\033[34m",
    "mag": "\033[35m", "cyn": "\033[36m", "wht": "\033[37m",
    "bgrn": "\033[92m", "bred": "\033[91m", "bylw": "\033[93m", "bcyn": "\033[96m", "bmag": "\033[95m",
}
ALT_ON, ALT_OFF, HOME, CLR_EOS, HIDE, SHOW = "\033[?1047h", "\033[?1047l", "\033[H", "\033[J", "\033[?25l", "\033[?25h"
ACH_TITLE = {a[0]: (a[1], a[2]) for a in ACHIEVEMENTS}


def money(minor, currency="£"):
    return f"{currency}{minor / 100:,.2f}"


def bar(frac, width, fill="█", empty="·"):
    frac = 0.0 if frac < 0 else 1.0 if frac > 1 else frac
    n = int(round(frac * width))
    return fill * n + empty * (width - n)


def _color_for(ev):
    if ev["lane"] == "shop":
        if ev.get("status") == 429:
            return C["ylw"]
        return C["grn"] if ev.get("ok") else C["bred"]
    # red lane: blocked is good (cyan), a breach is alarming (bright red on nothing)
    return C["bcyn"] if ev.get("blocked") else C["bred"] + C["bold"]


class Dashboard:
    def __init__(self, title="BABEL BAZAAR — STRESS ARENA", use_color=None, interactive=None):
        self.title = title
        self.interactive = sys.stdout.isatty() if interactive is None else interactive
        self.color = self.interactive if use_color is None else use_color
        self._last_plain = 0.0

    def c(self, key, text):
        return f"{C[key]}{text}{C['reset']}" if self.color else text

    def start(self):
        if self.interactive:
            sys.stdout.write(ALT_ON + HIDE)
            sys.stdout.flush()

    def stop(self):
        if self.interactive:
            sys.stdout.write(SHOW + ALT_OFF)
            sys.stdout.flush()

    def render(self, snap, duration):
        if self.interactive:
            self._render_full(snap, duration)
        else:
            self._render_plain(snap)

    # -- plain fallback (pipes, CI) --------------------------------------
    def _render_plain(self, snap):
        now = time.monotonic()
        if now - self._last_plain < 1.0:
            return
        self._last_plain = now
        s, r = snap["shop"], snap["red"]
        print(f"[{snap['elapsed']:5.0f}s] shop: {snap['rps']:7.0f} rps  served={s['served']:,} "
              f"active={s['active']:,} p95={s['p95']:.0f}ms orders={s['orders']} rev={money(s['revenue_minor'])}"
              f"  | red: {r['attempts']:,} attacks, {r['blocked']:,} blocked, breaches={r['breached']}, "
              f"stolen={r['bytes_leaked']}B", flush=True)

    # -- full-screen render ----------------------------------------------
    def _render_full(self, snap, duration):
        cols = shutil.get_terminal_size((100, 30)).columns
        w = max(78, min(cols, 120))
        half = (w - 3) // 2
        s, r = snap["shop"], snap["red"]
        out = [HOME]

        elapsed = snap["elapsed"]
        prog = elapsed / duration if duration else 0
        head = f" {self.title} "
        clock = f" {elapsed:5.1f}s / {duration:.0f}s "
        pad = w - len(head) - len(clock)
        out.append(self.c("bold", self.c("bmag", head)) + self.c("dim", "─" * max(0, pad)) + self.c("dim", clock))
        out.append(self.c("dim", " [" + bar(prog, w - 4) + "]"))

        left = self._shop_panel(snap, s, half)
        right = self._red_panel(snap, r, half)
        out.append("")
        for a, b in zip(left, right):
            out.append(f"{a:<{half}}  {b}")

        out.append("")
        out.extend(self._achievements(snap, w))
        out.append("")
        out.append(self.c("dim", " live feed ─────────────────────────────────────────────"))
        for ev in list(snap["events"])[-8:]:
            tag = "SHOP" if ev["lane"] == "shop" else " RED"
            line = f"  {self.c('dim', tag)} {ev.get('msg', '')}"
            out.append(self._trunc(self.c_wrap(ev, line), w))
        out.append(CLR_EOS)
        sys.stdout.write("\n".join(out))
        sys.stdout.flush()

    def c_wrap(self, ev, line):
        return f"{_color_for(ev)}{line}{C['reset']}" if self.color else line

    def _trunc(self, text, w):
        # Trim on visible length, ignoring ANSI, roughly: cap raw length generously.
        return text if len(text) < w + 20 else text[: w + 17] + "..."

    def _shop_panel(self, snap, s, w):
        title = self.c("bgrn", self.c("bold", "▌ THE SHOP under load"))
        rows = [title, ""]
        rows.append(f"  {self.c('bold', f'{snap['rps']:,.0f}')} req/s"
                    f"   {self.c('dim', 'in flight')} {self.c('bcyn', f'{s['active']:,}')}"
                    f"  {self.c('dim', 'peak')} {s['peak_active']:,}")
        rows.append(f"  served   {self.c('grn', f'{s['served']:,}')}"
                    f"   errors {self.c('red', str(s['errors']))}"
                    f"   429 {self.c('ylw', str(s['rate_limited']))}")
        rows.append(f"  latency  p50 {s['p50']:5.1f}  p95 {s['p95']:5.1f}  p99 {s['p99']:5.1f} ms")
        rows.append("")
        rows.append(f"  {self.c('bylw', 'orders paid')} {self.c('bold', str(s['orders']))}"
                    f"    {self.c('bylw', 'revenue')} {self.c('bold', money(s['revenue_minor']))}")
        rows.append("")
        rows.append(self.c("dim", "  what they're doing"))
        acts = snap["shop"]["by_act"]
        top = sorted(acts.items(), key=lambda kv: -kv[1])[:6]
        mx = max((v for _, v in top), default=1)
        for name, count in top:
            rows.append(f"   {name:<10}{self.c('grn', bar(count / mx, w - 22))} {count:,}")
        return self._box(rows, w)

    def _red_panel(self, snap, r, w):
        breached = r["breached"] > 0
        title = self.c("bred" if breached else "bcyn", self.c("bold", "▌ RED TEAM at the gate"))
        score = defence_score(snap)
        rows = [title, ""]
        meter_col = "bred" if breached else "bgrn"
        rows.append(f"  defence  {self.c(meter_col, bar(score / 100, w - 20))} {score:5.1f}%")
        rows.append(f"  attacks  {self.c('bold', f'{r['attempts']:,}')}"
                    f"   blocked {self.c('bcyn', f'{r['blocked']:,}')}")
        if breached:
            rows.append("  " + self.c("bred", self.c("bold", f"⚠ BREACHES: {r['breached']} — GATE OPEN")))
            rows.append("  " + self.c("bred", f"  {r['bytes_leaked']:,} bytes exfiltrated"))
        else:
            rows.append("  " + self.c("bgrn", self.c("bold", "✔ 0 breaches")) +
                        self.c("dim", "   data stolen: ") + self.c("bgrn", "0 bytes"))
        rows.append("")
        rows.append(self.c("dim", "  by technique (blocked / tried)"))
        for tech in ("unsigned", "forged", "expired", "fake_priv", "direct-connect", "no-session", "honeypot", "forged-at-bff"):
            tv = snap["red"]["by_tech"].get(tech)
            if not tv:
                continue
            tried, blk, br = tv
            good = br == 0
            mark = self.c("bcyn", "✔") if good else self.c("bred", "✘")
            rows.append(f"   {mark} {tech:<14}{self.c('bcyn' if good else 'bred', f'{blk:,}')}/{tried:,}")
        return self._box(rows, w)

    def _box(self, rows, w):
        inner = w - 2
        out = [self.c("dim", "┌" + "─" * inner + "┐")]
        for row in rows:
            out.append(self.c("dim", "│") + self._padvis(row, inner) + self.c("dim", "│"))
        out.append(self.c("dim", "└" + "─" * inner + "┘"))
        return out

    def _padvis(self, text, width):
        raw = _visible_len(text)
        if raw >= width:
            return text
        return text + " " * (width - raw)

    def _achievements(self, snap, w):
        rows = [self.c("dim", " achievements ──────────────────────────────────────────")]
        line = "  "
        for aid, title, blurb, _ in ACHIEVEMENTS:
            got = aid in snap["unlocked"]
            chip = f"[{'★' if got else '☆'} {title}]"
            chip = self.c("bylw", chip) if got else self.c("dim", chip)
            if _visible_len(line) + _visible_len(chip) + 1 > w:
                rows.append(line)
                line = "  "
            line += chip + " "
        rows.append(line)
        return rows


def _visible_len(text):
    out, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            j = text.find("m", i)
            i = len(text) if j < 0 else j + 1
            continue
        out += 1
        i += 1
    return out


def final_card(snap, duration):
    """A compact plain-text summary printed after the alternate screen is torn down."""
    s, r = snap["shop"], snap["red"]
    score = defence_score(snap)
    lines = [
        "", "═" * 64,
        "  BABEL BAZAAR — STRESS ARENA — final score",
        "═" * 64,
        f"  ran for {snap['elapsed']:.0f}s  (target {duration:.0f}s)",
        "",
        "  THE SHOP",
        f"    requests served     {s['served']:,}  (errors {s['errors']}, 429 {s['rate_limited']})",
        f"    peak concurrency    {s['peak_active']:,} shoppers in flight",
        f"    latency             p50 {s['p50']:.1f}  p95 {s['p95']:.1f}  p99 {s['p99']:.1f} ms",
        f"    orders paid         {s['orders']:,}",
        f"    revenue             {money(s['revenue_minor'])}",
        "",
        "  RED TEAM",
        f"    attacks launched    {r['attempts']:,}",
        f"    attacks blocked     {r['blocked']:,}",
        f"    breaches            {r['breached']}",
        f"    bytes exfiltrated   {r['bytes_leaked']:,}",
        f"    defence score       {score:.1f}%",
        "",
        f"  achievements unlocked {len(snap['unlocked'])}/{len(ACHIEVEMENTS)}: "
        + ", ".join(ACH_TITLE[a][0] for a in snap["unlocked"]),
        "═" * 64,
    ]
    verdict = ("  VERDICT: GATE HELD — the core never answered an attacker."
               if r["breached"] == 0 else
               "  VERDICT: BREACH — a core service answered a request that did not come through the BFF!")
    lines.append(verdict)
    lines.append("═" * 64)
    return "\n".join(lines)
