"""Weather pages: docs/wx/index.html (board), results.html, results.csv, summary.json (landing card)."""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from wx import config as C
from wx import paper

ROOT = paper.ROOT
DOCS = os.path.join(ROOT, "docs", "wx")
BOARD = os.path.join(DOCS, "index.html")
PT = ZoneInfo("America/Los_Angeles")
CSV_COLS = ["date", "group", "city", "kind", "event", "ticker", "bracket", "logged_utc", "hours_before", "yes_bid",
            "no_price", "contracts", "fee", "cost", "max_win", "depth", "forecast", "actual", "status", "result", "pnl", "settled_utc"]


def money(v) -> str:
    """+$1.23 / -$24.54 / $0.00"""
    v = round(v or 0, 2)
    return f"{'+' if v > 0 else ('-' if v < 0 else '')}${abs(v):,.2f}"


def _env():
    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")),
                      autoescape=select_autoescape(["html", "j2"]))
    env.filters["money"] = money
    return env


def label_pt(dt: datetime) -> str:
    return dt.astimezone(PT).strftime("%a %b %-d, %-I:%M %p PT")


def day_label(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%a %b %-d")


def next_checks(led: dict, now: datetime) -> list[dict]:
    """When each main city is checked next (Pacific time)."""
    logged = paper.logged_events(led)
    out = []
    for s, (city, _) in C.MAIN.items():
        for k in range(0, 4):
            d = now.astimezone(timezone.utc).date() + timedelta(days=k)
            when = paper.close_utc(s, d) - timedelta(hours=C.GROUPS["main"]["target_h"])
            if ("main", f"{s}-{paper.date_code(d)}") in logged or when + timedelta(hours=C.WINDOW_H) < now:
                continue
            out.append({"city": city, "when": when.astimezone(PT).strftime("%a %-I:%M %p"), "for": day_label(d.isoformat()),
                        "ts": when.isoformat()})
            break
    return sorted(out, key=lambda x: x["ts"])


def curve(bets: list[dict]) -> list[tuple[str, float]]:
    """Cumulative P&L by settled date."""
    by = {}
    for b in bets:
        if b["status"] in ("won", "lost"):
            by[b["date"]] = by.get(b["date"], 0) + b["pnl"]
    out, run = [], 0.0
    for d in sorted(by):
        run += by[d]
        out.append((d, round(run, 2)))
    return out


def svg_curve(series: dict, w=760, h=220) -> str:
    """Inline SVG line chart: {"Main rule": [(date, cum), ...], ...}. Empty string when nothing settled."""
    pts = [p for s in series.values() for p in s]
    if not pts:
        return ""
    dates = sorted({d for d, _ in pts})
    lo, hi = min(0, min(v for _, v in pts)), max(0, max(v for _, v in pts))
    if hi - lo < 1:
        hi, lo = hi + 0.5, lo - 0.5
    pl, pr, pt, pb = 52, 12, 12, 26

    def x(d):
        i = dates.index(d)
        return pl + (w - pl - pr) * (i / max(1, len(dates) - 1))

    def y(v):
        return pt + (h - pt - pb) * (hi - v) / (hi - lo)

    colors = {"Main rule": "#2FC373", "Main cities, evening": "#60A5FA", "Other high-temp cities": "#E9B23A",
              "Low-temp markets": "#C084FC"}
    g = [f'<line x1="{pl}" x2="{w - pr}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#3A4766" stroke-dasharray="3 3"/>']
    for v in (lo, hi):
        g.append(f'<text x="{pl - 6}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="11" fill="#8B95AB">${v:+.0f}</text>')
    g.append(f'<text x="{pl - 6}" y="{y(0) + 4:.1f}" text-anchor="end" font-size="11" fill="#8B95AB">$0</text>')
    for d in (dates[0], dates[-1]):
        g.append(f'<text x="{x(d):.1f}" y="{h - 6}" text-anchor="middle" font-size="11" fill="#8B95AB">{day_label(d)}</text>')
    for name, s in series.items():
        if not s:
            continue
        c = colors.get(name, "#E6EAF2")
        path = " ".join(f"{'M' if i == 0 else 'L'}{x(d):.1f},{y(v):.1f}" for i, (d, v) in enumerate(s))
        wdt = 2.6 if name == "Main rule" else 1.5
        g.append(f'<path d="{path}" fill="none" stroke="{c}" stroke-width="{wdt}" stroke-linejoin="round"/>')
        d, v = s[-1]
        g.append(f'<circle cx="{x(d):.1f}" cy="{y(v):.1f}" r="3.5" fill="{c}"/>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Cumulative paper P&amp;L">'
            + "".join(g) + "</svg>")


def by_city(bets: list[dict]) -> list[dict]:
    rows = []
    for city in sorted({b["city"] for b in bets}):
        st = paper.stats([b for b in bets if b["city"] == city])
        rows.append({"city": city, **st})
    return rows


def publish(led: dict, now: datetime):
    os.makedirs(DOCS, exist_ok=True)
    bets = led["bets"]
    now_pt = now.astimezone(PT)
    today, tomorrow = now_pt.date().isoformat(), (now_pt.date() + timedelta(days=1)).isoformat()
    groups = []
    for key, g in C.GROUPS.items():
        gb = [b for b in bets if b["group"] == key]
        groups.append({"key": key, **g, "series_n": len(g["series"]), "stats": paper.stats(gb),
                       "bt": C.BACKTEST.get(key), "curve": curve(gb)})
    main = [b for b in bets if b["group"] == "main"]
    upcoming = sorted([b for b in main if b["date"] >= today], key=lambda b: (b["date"], b["city"], b["no_price"]))
    by_date = []
    for d in sorted({b["date"] for b in upcoming}):
        bs = [b for b in upcoming if b["date"] == d]
        by_date.append({"date": d, "label": day_label(d), "bets": bs, "cost": round(sum(b["cost"] for b in bs), 2),
                        "max_win": round(sum(b["max_win"] for b in bs), 2), "cities": len({b["city"] for b in bs})})
    recent = sorted([b for b in main if b["status"] in ("won", "lost")], key=lambda b: b["settled_utc"], reverse=True)[:20]
    ctx = {
        "built": label_pt(now), "groups": groups, "main": groups[0], "by_date": by_date, "recent": recent,
        "checks": next_checks(led, now), "cfg": C, "chart": svg_curve({g["label"]: g["curve"] for g in groups}),
        "cities": by_city(main), "bets": sorted(bets, key=lambda b: (b["date"], b["logged_utc"]), reverse=True),
        "tomorrow": day_label(tomorrow), "day_label": day_label,
    }
    env = _env()
    with open(BOARD, "w") as f:
        f.write(env.get_template("wx_board.html.j2").render(**ctx))
    with open(os.path.join(DOCS, "results.html"), "w") as f:
        f.write(env.get_template("wx_results.html.j2").render(**ctx))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLS, extrasaction="ignore")
    w.writeheader()
    for b in sorted(bets, key=lambda b: (b["date"], b["group"], b["city"])):
        w.writerow(b)
    with open(os.path.join(DOCS, "results.csv"), "w") as f:
        f.write(buf.getvalue())
    nxt = [b for b in main if b["date"] == tomorrow]
    st = groups[0]["stats"]
    with open(os.path.join(DOCS, "summary.json"), "w") as f:
        json.dump({"built_utc": now.isoformat(), "built_label": label_pt(now), "tomorrow": tomorrow,
                   "tomorrow_bets": len(nxt), "tomorrow_cost": round(sum(b["cost"] for b in nxt), 2),
                   "open": st["open"], "settled": st["settled"], "won": st["won"], "lost": st["lost"],
                   "pnl": st["pnl"], "cpc": st["cpc"],
                   "next_check": (next_checks(led, now)[:1] or [{}])[0].get("when")}, f, indent=1)
