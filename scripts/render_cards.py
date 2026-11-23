#!/usr/bin/env python3
"""Render the profile README as a stack of animated SVG cards.

  python scripts/render_cards.py          -> cards/*.svg, and rewrites the README block
  STATIC=1 python scripts/render_cards.py -> cards-static/*.svg + preview.html (final frame, no motion)
  python scripts/render_cards.py probe URL -> print colours/fonts found on your site + current README

Needs: fonttools (pyftsubset). Optional: pillow (portrait resize).
Everything is meant for <img> use: inline <style> + CSS keyframes, SMIL, base64 WOFF fonts,
base64 portrait. No external requests, no scripts.
"""
from __future__ import annotations

import base64, datetime as dt, html, io, json, os, re, shutil, subprocess, sys, tempfile
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
STATIC = os.environ.get("STATIC") == "1"
OUT = ROOT / ("cards-static" if STATIC else "cards")
DATA_FILE = ROOT / "data" / "contributions.json"
README = ROOT / "README.md"
W = 830  # width of README column; every full-width card is drawn at this size

# ───────────────────────── CONFIG ─────────────────────────
PROFILE = {
    "login": "Navtesh00",
    "name": "Navtesh Gophane",
    "tagline": "Building thoughtful web products with React, TypeScript & Node.js",
    "portrait": "assets/portrait.jpg",
    "hero_url": "https://github.com/Navtesh00",
    "activity_url": "https://github.com/Navtesh00?tab=overview",
}
THEME = {
    "bg": "#0d1117",
    "surface": "#111a24",
    "border": "#263445",
    "text": "#e6edf3",
    "muted": "#91a4b7",
    "brand": "#58a6ff",
}
FONTS = {
    "display": {"file": "assets/fonts/display.ttf", "weight": 700},
    "body":    {"file": "assets/fonts/body.ttf",    "weight": 400},
    "mono":    {"file": "assets/fonts/mono.ttf",    "weight": 400},
}
FALLBACK = {
    "display": "system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif",
    "body":    "system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif",
    "mono":    "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace",
}
TILES = [
    {"label": "PROFILE", "title": "GitHub", "sub": "Explore my repositories", "url": "https://github.com/Navtesh00"},
    {"label": "CONNECT", "title": "LinkedIn", "sub": "Let's connect professionally", "url": "https://www.linkedin.com/in/navtesh-gophane/"},
    {"label": "LIVE BUILD", "title": "AgentCart", "sub": "Human-approved AI commerce", "url": "https://agentcart-orpin.vercel.app"},
]
FEATURED_REPOS = [
    {
        "name": "agentcart",
        "desc": "Agentic commerce with bounded spending and human-approved payments.",
        "url": "https://github.com/Navtesh00/agentcart",
    },
    {
        "name": "url-shortner",
        "desc": "Full-stack shortener with Redis-first redirects and BullMQ analytics.",
        "url": "https://github.com/Navtesh00/url-shortner",
    },
]
# ──────────────────────────────────────────────────────────────────────────────────────────────────────────

T = THEME


# ───────────────────────── helpers ─────────────────────────
def esc(s) -> str:
    return html.escape(str(s), quote=True)


def rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(rgb(a), rgb(b)))


def fmt_date(d):
    return f"{d.day} {d:%b} {d.year}"


class Meter:
    """Text width from the real font's advance widths (so wrapping matches what the browser draws)."""
    def __init__(self, path, factor):
        self.factor, self.cmap = factor, None
        if path:
            try:
                from fontTools.ttLib import TTFont
                f = TTFont(str(path), lazy=True)
                self.cmap, self.hmtx, self.upm = f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm
            except Exception as e:  # noqa
                print(f"warn: cannot measure {path}: {e}")

    def width(self, s, size, spacing=0.0):
        if self.cmap is None:
            return len(s) * (self.factor * size + spacing)
        tot = 0
        for ch in s:
            g = self.cmap.get(ord(ch))
            tot += self.hmtx[g][0] if g else self.upm * self.factor
        return tot * size / self.upm + spacing * len(s)


class Font:
    def __init__(self, role, path, weight):
        self.path, self.weight = path, weight
        self.meter = Meter(path, {"display": .58, "body": .52, "mono": .6}[role])


FONT: dict[str, Font] = {}


def setup_fonts():
    avail = {r: ROOT / c["file"] for r, c in FONTS.items() if (ROOT / c["file"]).exists()}
    for r, c in FONTS.items():
        src = r if r in avail else ("body" if "body" in avail else None)
        if r not in avail:
            print(f"warn: {c['file']} missing -> {r} falls back to {src or 'system fonts'}")
        FONT[r] = Font(r, avail.get(src), FONTS[src]["weight"] if src else c["weight"])


_subset_cache: dict = {}


def subset_woff(path: Path, chars: str) -> bytes:
    key = (str(path), chars)
    if key not in _subset_cache:
        cmd = [shutil.which("pyftsubset")] if shutil.which("pyftsubset") else [sys.executable, "-m", "fontTools.subset"]
        with tempfile.TemporaryDirectory() as td:
            txt, out = Path(td) / "chars.txt", Path(td) / "out.woff"
            txt.write_text(chars, encoding="utf-8")
            subprocess.run(cmd + [str(path), f"--text-file={txt}", f"--output-file={out}", "--flavor=woff",
                                  "--layout-features=*", "--no-hinting"], check=True)
            _subset_cache[key] = out.read_bytes()
    return _subset_cache[key]


def ellipsize(role, s, size, maxw, spacing=0):
    m = FONT[role].meter
    if m.width(s, size, spacing) <= maxw:
        return s
    while s and m.width(s + "…", size, spacing) > maxw:
        s = s[:-1]
    return s.rstrip() + "…"


def wrap(role, s, size, maxw, maxlines):
    m, lines, cur = FONT[role].meter, [], ""
    for word in s.split():
        t = f"{cur} {word}".strip()
        if cur and m.width(t, size) > maxw:
            lines.append(cur)
            cur = word
        else:
            cur = t
    if cur:
        lines.append(cur)
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        lines[-1] += "…"
    return [ellipsize(role, l, size, maxw) for l in lines]


# ───────────────────────── card scaffold ─────────────────────────
ANIM_CSS = """
@keyframes rise{from{opacity:0;transform:translateY(12px)}}
@keyframes fade{from{opacity:0}}
@keyframes growy{from{transform:scaleY(0)}}
@keyframes growx{from{transform:scaleX(0)}}
@keyframes pop{from{opacity:0;transform:scale(.7)}}
.rise{animation:rise .7s cubic-bezier(.2,.7,.2,1) both}
.fade{animation:fade .6s ease-out both}
.growy{transform-box:fill-box;transform-origin:50% 100%;animation:growy .9s cubic-bezier(.2,.7,.2,1) both}
.growx{transform-box:fill-box;transform-origin:0 50%;animation:growx .9s cubic-bezier(.2,.7,.2,1) both}
.pop{transform-box:fill-box;transform-origin:50% 50%;animation:pop .8s cubic-bezier(.2,.9,.3,1.1) both}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.smil{display:none}}
"""


class Card:
    """Every element's *base* style is its final frame; animations only define the `from` state,
    so removing them (STATIC=1 or prefers-reduced-motion) leaves the finished card."""

    def __init__(self, w, h, t0, label):
        self.w, self.h, self.t0, self.label = w, h, t0, label
        self.parts, self.chars = [], defaultdict(set)

    def add(self, s):
        self.parts.append(s)

    def attr(self, cls="", anim=None):
        classes, style = ([cls] if cls else []), ""
        if anim and not STATIC:
            classes.append(anim[0])
            style = f' style="animation-delay:{self.t0 + anim[1]:.2f}s"'
        return (f' class="{" ".join(classes)}"' if classes else "") + style

    def smil(self, markup):  # decorative loops only; hidden under reduced motion, omitted when STATIC
        return "" if STATIC else f'<g class="smil">{markup}</g>'

    def at(self, off):
        return f"{self.t0 + off:.2f}s"

    def frame(self):
        w, h = self.w, self.h
        self.add(f'<defs><clipPath id="clip"><rect width="{w}" height="{h}" rx="14"/></clipPath></defs>')
        self.add(f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="14" fill="{T["surface"]}" '
                 f'stroke="{T["border"]}"{self.attr(anim=("fade", 0))}/>')

    def text(self, x, y, s, role, size, fill, anchor="start", anim=None, spacing=0):
        self.chars[role].update(s)
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        sp = f' letter-spacing="{spacing}"' if spacing else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}"{a}{sp}'
                 f'{self.attr("f-" + role, anim)}>{esc(s)}</text>')
        return FONT[role].meter.width(s, size, spacing)

    def svg(self):
        css = ""
        for role, chars in self.chars.items():
            f = FONT[role]
            if f.path:
                b64 = base64.b64encode(subset_woff(f.path, "".join(sorted(chars | {" "})))).decode()
                css += (f"@font-face{{font-family:'pf-{role}';font-weight:{f.weight};font-style:normal;"
                        f"src:url(data:font/woff;base64,{b64}) format('woff')}}\n")
            fam = (f"'pf-{role}'," if f.path else "") + FALLBACK[role]
            css += f".f-{role}{{font-family:{fam};font-weight:{f.weight}}}\n"
        if not STATIC:
            css += ANIM_CSS
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.label)}">\n'
                f'<title>{esc(self.label)}</title>\n<style>{css}</style>\n' + "\n".join(self.parts) + "\n</svg>\n")


# ───────────────────────── data ─────────────────────────
def load():
    if not DATA_FILE.exists():
        sys.exit(f"missing {DATA_FILE}: run the fetch step (or the workflow) first")
    raw = json.loads(DATA_FILE.read_text("utf-8"))
    root = raw if isinstance(raw, dict) else {}
    api_data = root.get("data") or {}
    user = api_data.get("user") or root.get("user") or {}
    cal = (user.get("contributionsCollection") or {}).get("contributionCalendar")
    if cal:
        entries = [d for wk in cal["weeks"] for d in wk["contributionDays"]]
    else:
        entries = raw if isinstance(raw, list) else root.get("days", [])
    days = {
        dt.date.fromisoformat(e["date"][:10]): int(e.get("contributionCount", e.get("count", 0)))
        for e in entries
    }
    if not days:
        sys.exit("no contribution days found in the JSON")

    candidates = [
        api_data.get("featuredAgentCart"),
        api_data.get("featuredUrlShortener"),
        *(root.get("featuredRepos") or []),
        *((user.get("pinnedItems") or {}).get("nodes") or []),
        *(root.get("repos") or []),
    ]
    by_name = {
        n.get("name"): n for n in candidates
        if isinstance(n, dict) and n.get("name")
    }
    repos = []
    selected = set()
    for cfg in FEATURED_REPOS:
        n = by_name.get(cfg["name"], {})
        lang = n.get("primaryLanguage") or {}
        repos.append(dict(
            name=cfg["name"],
            desc=" ".join((n.get("description") or cfg["desc"]).split()),
            url=n.get("url") or cfg["url"],
            stars=n.get("stargazerCount", n.get("stars", 0)),
            forks=n.get("forkCount", n.get("forks", 0)),
            lang=lang.get("name") or n.get("language"),
            color=lang.get("color"),
        ))
        selected.add(cfg["name"])

    for n in (user.get("pinnedItems") or {}).get("nodes") or []:
        if not isinstance(n, dict) or not n.get("name") or n["name"] in selected:
            continue
        lang = n.get("primaryLanguage") or {}
        repos.append(dict(
            name=n["name"],
            desc=" ".join((n.get("description") or "").split()),
            url=n.get("url", ""),
            stars=n.get("stargazerCount", 0),
            forks=n.get("forkCount", 0),
            lang=lang.get("name") or n.get("language"),
            color=lang.get("color"),
        ))
    return days, repos, user


def compute(days):
    first, last = min(days), max(days)
    y, m, months = last.year, last.month, []
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    start = max(first, dt.date(months[0][0], months[0][1], 1))  # bars + active days + busiest share this window
    window = sorted((d, c) for d, c in days.items() if d >= start)
    per_month = Counter()
    for d, c in window:
        per_month[(d.year, d.month)] += c
    one = dt.timedelta(days=1)
    run = longest = 0
    d = first
    while d <= last:
        run = run + 1 if days.get(d, 0) > 0 else 0
        longest = max(longest, run)
        d += one
    d = last if days.get(last, 0) > 0 else last - one  # today may still be empty
    cur = 0
    while d >= first and days.get(d, 0) > 0:
        cur += 1
        d -= one
    busy = max(window, key=lambda x: (x[1], x[0]))
    return dict(months=months, vals=[per_month[mm] for mm in months], total=sum(per_month.values()),
                active=sum(1 for _, c in window if c > 0), span=(last - start).days + 1,
                cur=cur, longest=longest, busy=busy)


def portrait_uri():
    p = ROOT / PROFILE["portrait"]
    if not p.exists():
        return None
    try:
        from PIL import Image
        im = Image.open(p).convert("RGB")
        s = min(im.size)
        l, t = (im.width - s) // 2, (im.height - s) // 2
        im = im.crop((l, t, l + s, t + s)).resize((320, 320))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=84, optimize=True)
        data, mime = buf.getvalue(), "image/jpeg"
    except ImportError:
        data, mime = p.read_bytes(), "image/" + p.suffix.lstrip(".").lower().replace("jpg", "jpeg")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


# ───────────────────────── cards ─────────────────────────
def hero(t0, name, tagline):
    H, pad = 250, 48
    c = Card(W, H, t0, f"{name}: {tagline}")
    c.frame()
    pic = portrait_uri()
    r, cx, cy = 64, W - 56 - 64, H / 2
    maxw = (cx - r - 32 - pad) if pic else W - 2 * pad
    c.add(f'<defs><radialGradient id="glow" cx="85%" cy="15%" r="75%"><stop offset="0" stop-color="{T["brand"]}" '
          f'stop-opacity=".22"/><stop offset="1" stop-color="{T["brand"]}" stop-opacity="0"/></radialGradient></defs>'
          f'<g clip-path="url(#clip)"><rect width="{W}" height="{H}" fill="url(#glow)"/></g>')
    c.text(pad, 64, "@" + PROFILE["login"], "mono", 14, T["brand"], anim=("rise", .1))
    size = min(56, 56 * maxw / max(1, FONT["display"].meter.width(name, 56)))
    c.text(pad, 124, name, "display", round(size, 1), T["text"], anim=("rise", .2))
    c.add(f'<rect x="{pad}" y="142" width="56" height="4" rx="2" fill="{T["brand"]}"{c.attr(anim=("growx", .5))}/>')
    lines = wrap("body", tagline, 19, maxw, 2)
    for i, ln in enumerate(lines):
        c.text(pad, 182 + i * 28, ln, "body", 19, T["muted"], anim=("rise", .45 + i * .1))
    if lines:  # blinking cursor (SMIL)
        x = pad + FONT["body"].meter.width(lines[-1], 19) + 6
        y = 182 + (len(lines) - 1) * 28 - 17
        c.add(c.smil(f'<rect x="{x:.1f}" y="{y}" width="9" height="21" rx="1.5" fill="{T["brand"]}" opacity="0">'
                     f'<animate attributeName="opacity" values="1;0" calcMode="discrete" dur="1.1s" '
                     f'begin="{c.at(1.0)}" repeatCount="indefinite"/></rect>'))
    if pic:
        c.add(f'<defs><clipPath id="pc"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath></defs>'
              f'<g{c.attr(anim=("pop", .3))}><image href="{pic}" x="{cx - r}" y="{cy - r}" width="{2 * r}" height="{2 * r}" '
              f'preserveAspectRatio="xMidYMid slice" clip-path="url(#pc)"/>'
              f'<circle cx="{cx}" cy="{cy}" r="{r + 1}" fill="none" stroke="{T["brand"]}" stroke-width="2.5"/></g>')
    return c


def tile(t0, cfg):
    tw, th, pad = 270, 112, 24  # 830 * 32.5% = 270, so text keeps the same size as the full-width cards
    c = Card(tw, th, t0, f'{cfg["label"]}: {cfg["title"]}')
    c.frame()
    c.text(pad, 32, cfg["label"].upper(), "mono", 11, T["brand"], spacing=1.2, anim=("rise", .1))
    c.text(pad, 64, ellipsize("display", cfg["title"], 20, tw - pad - 44), "display", 20, T["text"], anim=("rise", .18))
    c.text(pad, 88, ellipsize("body", cfg["sub"], 13, tw - 2 * pad), "body", 13, T["muted"], anim=("rise", .26))
    x, y = tw - 34, 20
    c.add(f'<path d="M{x} {y + 10}L{x + 10} {y}M{x + 2} {y}H{x + 10}V{y + 8}" fill="none" stroke="{T["brand"]}" '
          f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"{c.attr(anim=("fade", .3))}/>')
    c.add(f'<g clip-path="url(#clip)"><rect x="0" y="{th - 3}" width="{tw}" height="3" fill="{T["brand"]}"'
          f'{c.attr(anim=("growx", .35))}/></g>')
    return c


def chart(t0, st):
    H, pad = 342, 32
    busy_d, busy_n = st["busy"]
    c = Card(W, H, t0, (f'Contributions per month, last 12 months: {st["total"]:,} total. Current streak {st["cur"]} days, '
                        f'{st["active"]} active days, busiest day {fmt_date(busy_d)} with {busy_n} contributions.'))
    c.frame()
    c.text(pad, 46, "Contributions per month", "display", 18, T["text"], anim=("rise", .1))
    c.text(W - pad, 46, f'{st["total"]:,} in the last 12 months', "body", 13, T["muted"], anchor="end", anim=("rise", .15))
    colw = (W - 2 * pad) / 3
    stats = [("Current streak", f'{st["cur"]}', "day" if st["cur"] == 1 else "days", f'Longest: {st["longest"]} days'),
             ("Active days", f'{st["active"]}', "days", f'{round(100 * st["active"] / st["span"])}% of the last {st["span"]} days'),
             ("Busiest day", f"{busy_n:,}", "contributions", fmt_date(busy_d))]
    for i, (label, val, unit, sub) in enumerate(stats):
        x, off = pad + i * colw, .15 + i * .1
        lw = c.text(x, 78, label.upper(), "mono", 11, T["muted"], spacing=1.2, anim=("rise", off))
        if i == 0 and st["cur"] > 0:  # live dot with pulsing ring (SMIL)
            dx, dy, b = x + lw + 12, 74, c.at(off + .5)
            c.add(f'<circle cx="{dx:.1f}" cy="{dy}" r="3.5" fill="{T["brand"]}"{c.attr(anim=("fade", off))}/>')
            c.add(c.smil(f'<circle cx="{dx:.1f}" cy="{dy}" r="3.5" fill="none" stroke="{T["brand"]}" stroke-width="1.5" opacity="0">'
                         f'<animate attributeName="r" values="3.5;12" dur="2s" begin="{b}" repeatCount="indefinite"/>'
                         f'<animate attributeName="opacity" values=".7;0" dur="2s" begin="{b}" repeatCount="indefinite"/></circle>'))
        vw = c.text(x, 116, val, "display", 34, T["text"], anim=("rise", off + .05))
        c.text(x + vw + 8, 116, unit, "body", 14, T["muted"], anim=("rise", off + .05))
        c.text(x, 138, sub, "body", 13, T["muted"], anim=("rise", off + .1))
    c.add(f'<rect x="{pad}" y="162" width="{W - 2 * pad}" height="1" fill="{T["border"]}"{c.attr(anim=("fade", .4))}/>')
    base, maxh, vals = 292, 96, st["vals"]
    vmax, slot = max(vals) or 1, (W - 2 * pad) / 12
    peak = vals.index(max(vals)) if max(vals) else -1
    c.add(f'<rect x="{pad}" y="{base}" width="{W - 2 * pad}" height="1" fill="{T["border"]}"/>')
    for i, ((yy, mm), v) in enumerate(zip(st["months"], vals)):
        bw = slot * .56
        x = pad + i * slot + (slot - bw) / 2
        h = max(3, v / vmax * maxh) if v else 3
        fill = T["brand"] if i == peak else (mix(T["surface"], T["brand"], .5) if v else T["border"])
        d = .45 + i * .06
        c.add(f'<rect x="{x:.1f}" y="{base - h:.1f}" width="{bw:.1f}" height="{h:.1f}" rx="4" fill="{fill}"{c.attr(anim=("growy", d))}/>')
        mid = x + bw / 2
        if v:
            c.text(mid, base - h - 7, f"{v:,}", "mono", 11, T["text"] if i == peak else T["muted"], anchor="middle", anim=("fade", d + .5))
        c.text(mid, 312, dt.date(yy, mm, 1).strftime("%b"), "mono", 11, T["muted"], anchor="middle", anim=("fade", d + .2))
        if i == 0 or mm == 1:
            c.text(mid, 326, str(yy), "mono", 10, mix(T["surface"], T["muted"], .6), anchor="middle", anim=("fade", d + .2))
    return c


def graph(t0, days):
    pad, gy, H = 28, 84, 232
    first, last = min(days), max(days)
    total = sum(days.values())
    c = Card(W, H, t0, f"Contribution graph: {total:,} contributions in the last year")
    c.frame()
    c.text(pad, 44, "Contribution graph", "display", 18, T["text"], anim=("rise", .1))
    c.text(W - pad, 44, f"{total:,} contributions in the last year", "body", 13, T["muted"], anchor="end", anim=("rise", .15))
    sun0 = first - dt.timedelta(days=(first.weekday() + 1) % 7)
    ncols = (last - sun0).days // 7 + 1
    gx = pad + 30
    step = (W - pad - gx) / ncols
    cell = step - 3
    nz = sorted(v for v in days.values() if v > 0)
    q = [nz[min(len(nz) - 1, int(p * len(nz)))] for p in (.25, .5, .75)] if nz else [1, 2, 3]
    lv = [mix(T["surface"], T["text"], .07)] + [mix(T["surface"], T["brand"], t) for t in (.3, .52, .76, 1)]

    def level(v):
        return 0 if v == 0 else 1 if v <= q[0] else 2 if v <= q[1] else 3 if v <= q[2] else 4

    cols = defaultdict(list)
    for d, v in sorted(days.items()):
        col, row = (d - sun0).days // 7, (d.weekday() + 1) % 7
        cols[col].append((row, v, d))
    for col in range(ncols):
        rects = "".join(f'<rect x="{gx + col * step:.2f}" y="{gy + row * step:.2f}" width="{cell:.2f}" height="{cell:.2f}" '
                        f'rx="2.5" fill="{lv[level(v)]}"/>' for row, v, _ in cols[col])
        c.add(f'<g{c.attr(anim=("fade", .25 + col * .018))}>{rects}</g>')
    marks, prev = [], None  # month labels (week's Thursday decides the month)
    for col in range(ncols):
        m = (sun0 + dt.timedelta(days=7 * col + 4)).month
        if m != prev:
            marks.append((col, m))
        prev = m
    if len(marks) > 1 and marks[1][0] - marks[0][0] < 3:
        marks.pop(0)
    for col, m in marks:
        c.text(gx + col * step, gy - 9, dt.date(2000, m, 1).strftime("%b"), "mono", 10, T["muted"], anim=("fade", .3))
    for row, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.text(pad, gy + row * step + cell - 1, lab, "mono", 10, T["muted"], anim=("fade", .3))
    end = .25 + ncols * .018
    lr, lc = (last - sun0).days // 7, (last.weekday() + 1) % 7  # "today" ring (SMIL)
    c.add(c.smil(f'<rect x="{gx + lr * step - 1:.2f}" y="{gy + lc * step - 1:.2f}" width="{cell + 2:.2f}" height="{cell + 2:.2f}" '
                 f'rx="3.5" fill="none" stroke="{T["text"]}" stroke-width="1.4" opacity="0"><animate attributeName="opacity" '
                 f'values="0;1;0" dur="2.4s" begin="{c.at(end + .4)}" repeatCount="indefinite"/></rect>'))
    ly, xe = 212, W - pad
    mw = c.text(xe, ly, "More", "mono", 10, T["muted"], anchor="end", anim=("fade", end))
    sx = xe - mw - 8 - (5 * 14 - 3)
    for i in range(5):
        c.add(f'<rect x="{sx + i * 14:.1f}" y="{ly - 10}" width="11" height="11" rx="2.5" fill="{lv[i]}"{c.attr(anim=("fade", end))}/>')
    c.text(sx - 8, ly, "Less", "mono", 10, T["muted"], anchor="end", anim=("fade", end))
    return c


def repo_card(t0, r):
    H, pad = 136, 32
    c = Card(W, H, t0, f'{r["name"]}: {r["desc"] or "no description"}')
    c.frame()
    c.add(f'<g clip-path="url(#clip)"><rect x="0" y="0" width="5" height="{H}" fill="{T["brand"]}"{c.attr(anim=("growy", .2))}/></g>')
    c.text(pad, 46, ellipsize("display", r["name"], 20, W - 2 * pad - 40), "display", 20, T["text"], anim=("rise", .1))
    x, y = W - pad - 10, 30
    c.add(f'<path d="M{x} {y + 10}L{x + 10} {y}M{x + 2} {y}H{x + 10}V{y + 8}" fill="none" stroke="{T["brand"]}" '
          f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"{c.attr(anim=("fade", .25))}/>')
    for i, ln in enumerate(wrap("body", r["desc"] or "No description yet.", 14, W - 2 * pad - 20, 2)):
        c.text(pad, 74 + i * 21, ln, "body", 14, T["muted"], anim=("rise", .2 + i * .06))
    my, x = H - 26, pad
    if r["lang"]:
        col = r["color"] or T["brand"]
        c.add(f'<circle cx="{x + 5}" cy="{my - 4}" r="5" fill="{col}"{c.attr(anim=("fade", .35))}/>')
        x += 16 + c.text(x + 16, my, r["lang"], "mono", 12, T["muted"], anim=("fade", .35)) + 22
    c.add(f'<path transform="translate({x} {my - 12})" d="M6 .5l1.76 3.8 4.14.5-3.06 2.84.8 4.1L6 9.7 2.36 11.74l.8-4.1L.1 4.8l4.14-.5z" '
          f'fill="none" stroke="{T["muted"]}" stroke-width="1.2" stroke-linejoin="round"{c.attr(anim=("fade", .4))}/>')
    x += 18 + c.text(x + 18, my, f'{r["stars"]:,}', "mono", 12, T["muted"], anim=("fade", .4)) + 22
    c.add(f'<g transform="translate({x} {my - 12})"><g fill="none" stroke="{T["muted"]}" stroke-width="1.2" '
          f'stroke-linecap="round"{c.attr(anim=("fade", .45))}><circle cx="3" cy="2.5" r="1.7"/><circle cx="9" cy="2.5" r="1.7"/>'
          f'<circle cx="6" cy="11" r="1.7"/><path d="M3 4.2v1.1c0 1 .8 1.8 1.8 1.8h2.4c1 0 1.8-.8 1.8-1.8V4.2M6 7.1v2.2"/></g></g>')
    c.text(x + 18, my, f'{r["forks"]:,}', "mono", 12, T["muted"], anim=("fade", .45))
    c.text(W - pad, my, "VIEW REPOSITORY ↗", "mono", 10, T["brand"], anchor="end", anim=("fade", .5))
    return c


# ───────────────────────── output ─────────────────────────
def block(prefix, items):
    rows = []
    for it in items:
        img = f'<img src="{prefix}{it["file"]}" width="{it["width"]}" alt="{esc(it["alt"])}">'
        rows.append(f'<a href="{esc(it["href"])}">{img}</a>' if it["href"] else img)
    return '<div align="center">\n' + "\n".join(rows) + "\n</div>"


def probe(site):
    import urllib.parse, urllib.request

    def get(u):
        return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "card-probe"}), timeout=20).read().decode("utf-8", "replace")

    page = get(site)
    blob = [page]
    for tag in re.findall(r"<link[^>]+rel=[\"']stylesheet[\"'][^>]*>", page):
        m = re.search(r"href=[\"']([^\"']+)", tag)
        if m:
            try:
                blob.append(get(urllib.parse.urljoin(site, m.group(1))))
            except Exception as e:  # noqa
                print("could not fetch", m.group(1), e)
    css = "\n".join(blob)
    hexes = r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b"
    print("\n== CSS custom properties ==")
    for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;}{]+)", css)[:40]:
        print(f"  {k}: {v.strip()}")
    print("\n== most used hex colours ==")
    for col, n in Counter(x.lower() for x in re.findall(hexes, css)).most_common(12):
        print(f"  {col}  x{n}")
    print("\n== font-family stacks ==")
    for fam, n in Counter(x.strip() for x in re.findall(r"font-family\s*:\s*([^;}{]+)", css)).most_common(6):
        print(f"  {fam}  x{n}")
    print("\n== font files / google fonts ==")
    for f in sorted(set(re.findall(r"url\(([^)]+\.(?:woff2?|ttf|otf)[^)]*)\)", css))):
        print("  ", f.strip("'\""))
    for f in sorted(set(re.findall(r"fonts\.googleapis\.com/[^\"')\s]+", css))):
        print("  ", f)
    login = PROFILE["login"]
    try:
        rd = get(f"https://raw.githubusercontent.com/{login}/{login}/HEAD/README.md")
        print(f"\n== current README ({len(rd.splitlines())} lines), colours used: "
              f"{sorted(set(x.lower() for x in re.findall(hexes, rd)))} ==")
        print("\n".join(rd.splitlines()[:40]))
    except Exception as e:  # noqa
        print("could not fetch README:", e)
    print("\nPick: surface/bg/text/muted/brand for THEME; drop static TTFs of the fonts into assets/fonts/.")


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "probe":
        return probe(sys.argv[2])
    if len(TILES) != 3:
        sys.exit("TILES must contain exactly three entries")
    setup_fonts()
    days, repos, user = load()
    st = compute(days)
    name = PROFILE["name"] or user.get("name") or PROFILE["login"]
    tagline = PROFILE["tagline"] or " ".join((user.get("bio") or "").split()) or "TODO: set PROFILE['tagline']"

    # timeline: each card starts shortly after the previous one, so the page plays top to bottom
    clock, plan = 0.0, []
    h = hero(clock, name, tagline)
    plan.append(("hero.svg", h, PROFILE["hero_url"], "100%"))
    clock += .55
    for i, cfg in enumerate(TILES):
        plan.append((f"tile-{i + 1}.svg", tile(clock + i * .12, cfg), cfg["url"], "32.5%"))
    clock += .6
    ch = chart(clock, st)
    plan.append(("contributions-monthly.svg", ch, PROFILE["activity_url"], "100%"))
    clock += .7
    for i, r in enumerate(repos):
        plan.append((f"repo-{i + 1}.svg", repo_card(clock + i * .3, r), r["url"], "100%"))
    clock += len(repos) * .3 + .2
    plan.append(("contribution-graph.svg", graph(clock, days), PROFILE["activity_url"], "100%"))

    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    items = []
    for fn, card, href, width in plan:
        (OUT / fn).write_text(card.svg(), encoding="utf-8")
        items.append(dict(file=fn, alt=card.label, href=href, width=width))
        print(f"{OUT.name}/{fn}  {len(card.svg()) / 1024:.0f} KB")

    if STATIC:
        page = (f'<!doctype html><meta charset="utf-8"><title>Card preview</title>'
                f'<body style="margin:0;background:{T["bg"]}"><div style="max-width:{W}px;margin:0 auto;padding:32px 0">'
                f'{block("", items)}</div>')
        (OUT / "preview.html").write_text(page, encoding="utf-8")
        print(f"\nStatic frames written. Open {OUT / 'preview.html'} and screenshot it. (Don't commit {OUT.name}/.)")
        return
    md = block("cards/", items)
    text = README.read_text("utf-8") if README.exists() else ""
    pat = re.compile(r"(<!-- cards:start -->).*?(<!-- cards:end -->)", re.S)
    if pat.search(text):
        README.write_text(pat.sub(lambda m: f"{m.group(1)}\n{md}\n{m.group(2)}", text), encoding="utf-8")
    else:
        print("\nREADME.md has no <!-- cards:start/end --> markers; paste this where you want the cards:\n" + md)


if __name__ == "__main__":
    main()
