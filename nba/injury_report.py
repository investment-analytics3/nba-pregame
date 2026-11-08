"""Official NBA injury report (PDF published by the league through the day).

The PDF is a fixed-column table: Game Date | Game Time | Matchup | Team |
Player Name | Current Status | Reason. Values carry down until they change,
and long reasons wrap onto extra lines. We read words by x-position.
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .http import get, FetchError

ET = ZoneInfo("America/New_York")
BASE = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_"
HEADERS = ["GameDate", "GameTime", "Matchup", "Team", "PlayerName", "CurrentStatus", "Reason"]
KEYS = ["date", "time", "matchup", "team", "player", "status", "reason"]
STATUSES = {"Out", "Doubtful", "Questionable", "Probable", "Available"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z0-9]", "", s.encode("ascii", "ignore").decode().lower())


def player_key(pdf_name: str) -> str:
    """'Jackson Jr.,Jaren' -> 'jarenjacksonjr' (matches ESPN 'Jaren Jackson Jr.')."""
    if "," in pdf_name:
        last, first = pdf_name.split(",", 1)
        return norm(first) + norm(last)
    return norm(pdf_name)


def latest_report(now: datetime | None = None, hours_back: int = 30):
    """Newest PDF within `hours_back` hours. Returns (url, pdf_bytes) or (None, None)."""
    t = (now or datetime.now(ET)).astimezone(ET)
    t = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)
    for _ in range(hours_back * 4):
        names = [f"{t:%Y-%m-%d}_{t:%I}_{t:%M}{t:%p}.pdf"]
        if t.minute == 0:
            names.append(f"{t:%Y-%m-%d}_{t:%I}{t:%p}.pdf")
        for n in names:
            try:
                body = get(BASE + n, want="bytes", retries=0, timeout=15, pace=0.1)
            except FetchError as e:
                if "HTTP 403" in str(e):
                    body = None  # S3-style "missing file" answer
                else:
                    raise
            if body and body[:4] == b"%PDF":
                return BASE + n, body
        t -= timedelta(minutes=15)
    return None, None


def parse(pdf_bytes: bytes) -> dict:
    import pdfplumber

    rows, not_submitted = [], []
    report_time = None
    cols = None
    cur = {k: "" for k in KEYS}
    fragments = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            if report_time is None:
                head = " ".join(w["text"] for w in words[:8])
                m = re.search(r"(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2})\s*(AM|PM)", head)
                if m:
                    report_time = datetime.strptime(" ".join(m.groups()), "%m/%d/%y %I:%M %p").replace(tzinfo=ET)
            hdr = {w["text"]: w for w in words if w["text"] in HEADERS}
            if len(hdr) == len(HEADERS):
                cols = [hdr[h]["x0"] for h in HEADERS]
                header_bottom = max(hdr[h]["bottom"] for h in HEADERS)
            elif cols is None:
                continue
            else:
                header_bottom = 0
            # group words into lines
            lines: dict[int, list] = {}
            for w in words:
                if w["top"] <= header_bottom + 1 or re.match(r"Page\d+of\d+", w["text"]):
                    continue
                key = round(w["top"] / 3)
                lines.setdefault(key, []).append(w)
            for key in sorted(lines):
                line_text = " ".join(w["text"] for w in sorted(lines[key], key=lambda w: w["x0"]))
                if line_text.startswith("Injury Report:") or set(line_text.split()) & set(HEADERS):
                    continue  # page title or repeated column header
                cells = {k: [] for k in KEYS}
                for w in sorted(lines[key], key=lambda w: w["x0"]):
                    idx = max(i for i, x in enumerate(cols) if w["x0"] >= x - 6) if w["x0"] >= cols[0] - 6 else 0
                    cells[KEYS[idx]].append(w["text"])
                text = {k: " ".join(v) for k, v in cells.items()}
                for k in ("date", "time", "matchup", "team"):
                    if text[k]:
                        cur[k] = text[k]
                        if k in ("date", "time", "matchup"):
                            pass
                if "NOTYETSUBMITTED" in text["player"].replace(" ", "").upper():
                    not_submitted.append({"matchup": cur["matchup"], "team": cur["team"]})
                    continue
                top = min(w["top"] for w in lines[key])
                if text["player"] and "," in text["player"] and text["status"] in STATUSES:
                    rows.append({"game_date": cur["date"], "game_time": cur["time"],
                                 "matchup": cur["matchup"], "team": cur["team"],
                                 "team_key": norm(cur["team"]), "player": text["player"],
                                 "player_key": player_key(text["player"]),
                                 "status": text["status"], "_parts": [(top, text["reason"])] if text["reason"] else [],
                                 "_page": page.page_number, "_top": top})
                elif text["reason"] and not text["player"]:
                    fragments.append((page.page_number, top, text["reason"]))
    # wrapped reason lines belong to the nearest player row on the same page
    for pg, top, txt in fragments:
        near = [r for r in rows if r["_page"] == pg and abs(r["_top"] - top) <= 20]
        if near:
            min(near, key=lambda r: abs(r["_top"] - top))["_parts"].append((top, txt))
    for r in rows:
        raw = "".join(t for _, t in sorted(r.pop("_parts")))
        r["reason"] = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw).replace("-", " - ").replace("/", " / ")
        r.pop("_page"); r.pop("_top")
    return {"report_time": report_time.isoformat() if report_time else None,
            "rows": rows, "not_submitted": not_submitted}
