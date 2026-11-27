"""Settled Kalshi singles matches (all tours), kept 28 days: ITF form, who played yesterday, travel, and grading.

data/tennis/history/<YYYY-MM>.json = [{"e": event, "s": series, "d": play date, "t": tournament, "r": round,
"p": [player, player], "w": winner or null, "v": void, "ts": settlement time}, ...] (play date = settlement day, UTC).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

from tennis import config as C
from tennis import sources as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "data", "tennis", "history")


class History:
    def __init__(self, rows: list[dict] | None = None):
        self.rows = {r["e"]: r for r in (rows or [])}
        self._by_player = None

    # ---- storage ----------------------------------------------------------------------------
    @classmethod
    def load(cls, folder: str | None = None) -> "History":
        folder = folder or DIR
        rows = []
        if os.path.isdir(folder):
            for f in sorted(os.listdir(folder)):
                if f.endswith(".json"):
                    with open(os.path.join(folder, f)) as fh:
                        rows += json.load(fh)
        return cls(rows)

    def save(self, today: date, folder: str | None = None):
        folder = folder or DIR
        os.makedirs(folder, exist_ok=True)
        cut = (today - timedelta(days=C.HISTORY_DAYS)).isoformat()
        by_month = {}
        for r in self.rows.values():
            if r["d"] >= cut:
                by_month.setdefault(r["d"][:7], []).append(r)
        for f in os.listdir(folder):
            if f.endswith(".json") and f[:7] not in by_month:
                os.remove(os.path.join(folder, f))
        for mth, rows in by_month.items():
            rows.sort(key=lambda r: (r["d"], r["e"]))
            with open(os.path.join(folder, f"{mth}.json"), "w") as fh:
                fh.write("[\n" + ",\n".join(S.dumps(r) for r in rows) + "\n]\n")

    # ---- updates ----------------------------------------------------------------------------
    def add_event(self, e: dict) -> bool:
        m = S.match_from_event(e)
        if not m or not (m["winner"] or m["void"]):
            return False
        d = (m["settled_ts"] or m["start"] or "")[:10]
        if not d:
            return False
        new = m["event"] not in self.rows
        self.rows[m["event"]] = {"e": m["event"], "s": m["series"], "d": d, "t": m["tourney"], "r": m["round"],
                                 "p": [p["name"] for p in m["players"]], "w": m["winner"], "v": m["void"],
                                 "ts": m["settled_ts"]}
        self._by_player = None
        return new

    def update(self, today: date, max_pages: int = 15, log=print) -> int:
        """Pull newly settled events, newest first, until a known event or the 28-day edge."""
        cut = (today - timedelta(days=C.HISTORY_DAYS)).isoformat()
        added = 0
        for series in C.SERIES:
            seen = {"known": 0}

            def stop(e, seen=seen):
                if e.get("event_ticker") in self.rows:
                    seen["known"] += 1
                    return seen["known"] >= 25          # settled order is close to, not exactly, newest-first
                ms = e.get("markets") or [{}]
                d = (ms[0].get("settlement_ts") or ms[0].get("occurrence_datetime") or "9999")[:10]
                return d < cut
            try:
                evs = S.kalshi_events(series, status="settled", max_pages=max_pages, stop=stop)
            except S.FetchError as ex:
                log(f"  history {series}: {ex}")
                continue
            n = sum(self.add_event(e) for e in evs)
            added += n
            if n:
                log(f"  history {series}: +{n}")
        return added

    # ---- queries ----------------------------------------------------------------------------
    def matches(self, name: str) -> list[dict]:
        if self._by_player is None:
            idx = {}
            for r in self.rows.values():
                for p in r["p"]:
                    idx.setdefault(S.norm(p), []).append(r)
            for v in idx.values():
                v.sort(key=lambda r: r["d"], reverse=True)
            self._by_player = idx
        return self._by_player.get(S.norm(name), [])

    def before(self, name: str, day: str) -> list[dict]:
        """Completed (non-void) matches strictly before `day` (YYYY-MM-DD), newest first."""
        return [r for r in self.matches(name) if r["d"] < day and not r["v"]]


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
