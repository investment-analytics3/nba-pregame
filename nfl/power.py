"""NFL power rankings: ESPN FPI (daily) + NFL.com (weekly), and their consensus.

Owner decision Nov 10 2026 (mirrors the NBA board):
- consensus = average of the ESPN FPI rank and the NFL.com rank, re-ranked 1-32
  (either source alone if the other is unavailable)
- the board shows each team's consensus rank as a pill
- opponent power gate: an underdog top 10 in ANY ranking (consensus, ESPN FPI,
  NFL.com) means No track; the note names the ranking(s)
Information only when nothing is available: the gate then passes.
"""
from __future__ import annotations

import json
import re

from nba.http import get, FetchError
from . import sources

FPI_URL = "https://site.web.api.espn.com/apis/fitt/v3/sports/football/nfl/powerindex?region=us&lang=en"
NFLCOM_INDEX = "https://www.nfl.com/news/series/power-rankings-news"
SOURCES = (("consensus", "consensus"), ("ESPN FPI", "fpi"), ("NFL.com", "nfl_com"))
TOP = 10

# NFL.com writes team names; map nicknames (and city names) to nflverse codes.
NICK = {"cardinals": "ARI", "falcons": "ATL", "ravens": "BAL", "bills": "BUF", "panthers": "CAR", "bears": "CHI",
        "bengals": "CIN", "browns": "CLE", "cowboys": "DAL", "broncos": "DEN", "lions": "DET", "packers": "GB",
        "texans": "HOU", "colts": "IND", "jaguars": "JAX", "chiefs": "KC", "raiders": "LV", "chargers": "LAC",
        "rams": "LA", "dolphins": "MIA", "vikings": "MIN", "patriots": "NE", "saints": "NO", "giants": "NYG",
        "jets": "NYJ", "eagles": "PHI", "steelers": "PIT", "49ers": "SF", "niners": "SF", "seahawks": "SEA",
        "buccaneers": "TB", "titans": "TEN", "commanders": "WAS"}


def fpi_ranks(body: dict | None = None) -> dict:
    """{nflverse code: FPI rank} from ESPN's Football Power Index."""
    body = body if body is not None else (get(FPI_URL) or {})
    names = next((c.get("names") or [] for c in body.get("categories", []) if c.get("name") == "fpi"), [])
    i = names.index("fpirank") if "fpirank" in names else None
    out = {}
    for t in body.get("teams", []):
        abbr = (t.get("team") or {}).get("abbreviation")
        for c in t.get("categories", []):
            v = c.get("values") or []
            if c.get("name") == "fpi" and i is not None and len(v) > i and v[i] is not None and abbr:
                out[sources.nfl_code(abbr)] = int(v[i])
    return out


def team_code(name: str) -> str | None:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    for w in reversed(words):
        if w in NICK:
            return NICK[w]
    return None


def parse_nflcom(html: bytes) -> dict:
    """{nflverse code: rank} from an NFL.com power rankings article.

    The page embeds the article as (escaped) JSON: one "rankedCards" part per team with
    team.displayName and rankedItemSecondLabel = the rank (sample: Week 10 2026).
    """
    text = html.decode("utf-8", "replace").replace('\\\\"', '"').replace('\\"', '"')
    out = {}
    for m in re.finditer(r'"storyPartId":"rankedCards","content":\{"template":"team","team":\{"displayName":"([^"]+)"'
                         r'.*?"rankedItemSecondLabel":"(\d{1,2})"', text):
        code, r = team_code(m.group(1)), int(m.group(2))
        if code and 1 <= r <= 32 and code not in out:
            out[code] = r
    return out


def nflcom_ranks() -> dict:
    """Latest NFL.com weekly power rankings: {"url", "ranks": {code: rank}} (empty if unavailable)."""
    idx = get(NFLCOM_INDEX, want="bytes") or b""
    # article URLs are predictable: /news/nfl-power-rankings-week-<N>-<season>-nfl-season; newest first
    weeks = sorted({(int(y), int(w)) for w, y in re.findall(rb"nfl-power-rankings-week-(\d+)-(\d{4})-nfl-season", idx)},
                   reverse=True)
    for season, week in weeks[:3]:
        url = f"https://www.nfl.com/news/nfl-power-rankings-week-{week}-{season}-nfl-season"
        ranks = parse_nflcom(get(url, want="bytes") or b"")
        if len(ranks) >= 28:
            return {"url": url, "ranks": ranks}
    return {}


def consensus(fpi: dict, nfl: dict) -> dict:
    """{code: {"consensus", "fpi", "nfl_com"}}: average of the available ranks, re-ranked 1-32."""
    teams = set(fpi) | set(nfl)
    avg = {t: sum(v) / len(v) for t in teams for v in [[x for x in (fpi.get(t), nfl.get(t)) if x is not None]] if v}
    # ties: the better FPI rank first
    order = sorted(avg, key=lambda t: (avg[t], fpi.get(t, 99), t))
    return {t: {"consensus": i + 1, "fpi": fpi.get(t), "nfl_com": nfl.get(t)} for i, t in enumerate(order)}


def safe(fn, default):
    try:
        return fn()
    except (FetchError, ValueError, KeyError, json.JSONDecodeError):
        return default


def load(notes: list | None = None) -> dict:
    """Everything the board needs: {"teams": {code: {...}}, "nfl_com_url"}. Never raises."""
    fpi = safe(fpi_ranks, {})
    nfl = safe(nflcom_ranks, {})
    if notes is not None:
        if not fpi:
            notes.append("ESPN FPI power ranking unavailable.")
        if not nfl:
            notes.append("NFL.com power rankings unavailable; power rank uses ESPN FPI only.")
    return {"teams": consensus(fpi, nfl.get("ranks", {})), "nfl_com_url": nfl.get("url")}


def gate(dog_power: dict | None) -> list[str]:
    """Rankings that put the underdog in the top 10, e.g. ["ESPN FPI #9", "NFL.com #5"]."""
    p = dog_power or {}
    return [f"{label} #{p[k]}" for label, k in SOURCES if p.get(k) is not None and p[k] <= TOP]
