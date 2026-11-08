#!/usr/bin/env python3
"""Developer probe for live mode: ESPN live play-by-play, Kalshi NBA markets, and
whether a browser page on GitHub Pages may call them directly (CORS).

Writes raw responses to samples/ (pushed to the dev-live-samples branch by the
"Probe live sources" workflow). Never touches main.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

OUT = "samples"
ORIGIN = "https://investment-analytics3.github.io"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
KALSHI_HOSTS = ["https://external-api.kalshi.com/trade-api/v2",
                "https://api.elections.kalshi.com/trade-api/v2"]
ET = ZoneInfo("America/New_York")
report: list[str] = []


def call(url: str, origin: bool = True, ua: str | None = UA):
    hdrs = {"Accept": "application/json"}
    if ua:
        hdrs["User-Agent"] = ua
    if origin:
        hdrs["Origin"] = ORIGIN
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=25) as r:
            body = r.read()
            h = {k.lower(): v for k, v in r.headers.items()}
            return r.status, body, h
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}
    except Exception as e:  # noqa: BLE001
        return None, str(e).encode(), {}


def save(name: str, url: str, origin: bool = True, ua: str | None = UA):
    st, body, h = call(url, origin, ua)
    cors = h.get("access-control-allow-origin", "-")
    cache = h.get("cache-control", "-")
    extra = " ".join(f"{k}={h[k]}" for k in ("server", "cf-ray", "x-amzn-errortype", "content-type") if k in h)
    report.append(f"{name}: HTTP {st}  CORS={cors}  cache={cache}  bytes={len(body)}  {extra}  {url}")
    with open(os.path.join(OUT, name), "wb") as f:
        f.write(body)
    try:
        return json.loads(body)
    except Exception:  # noqa: BLE001
        return None


def main():
    os.makedirs(OUT, exist_ok=True)
    today = datetime.now(ET).date()
    sb = save("scoreboard_today.json", f"{ESPN}/scoreboard?dates={today:%Y%m%d}") or {}
    live = [e for e in sb.get("events", []) if e["status"]["type"]["state"] == "in"]
    pick = live[0] if live else (sb.get("events") or [None])[0]
    if pick:
        save("summary_live_or_next.json", f"{ESPN}/summary?event={pick['id']}")
    # a finished game for the replay/backtest shape
    y = today - timedelta(days=1)
    sby = save("scoreboard_yesterday.json", f"{ESPN}/scoreboard?dates={y:%Y%m%d}") or {}
    if sby.get("events"):
        save("summary_finished.json", f"{ESPN}/summary?event={sby['events'][0]['id']}")

    for i, base in enumerate(KALSHI_HOSTS):
        tag = f"k{i}"
        save(f"{tag}_series_sports.json", f"{base}/series?category=Sports")
        for s in ("KXNBAGAME", "KXNBA"):
            save(f"{tag}_events_{s}.json",
                 f"{base}/events?series_ticker={s}&status=open&with_nested_markets=true&limit=50")
        save(f"{tag}_markets_KXNBAGAME.json", f"{base}/markets?series_ticker=KXNBAGAME&status=open&limit=100")

    # Kalshi variants: no Origin header; plain client name; no User-Agent
    k = KALSHI_HOSTS[1]
    save("kv_no_origin.json", f"{k}/markets?series_ticker=KXNBAGAME&status=open&limit=5", origin=False)
    save("kv_plain_ua.json", f"{k}/markets?series_ticker=KXNBAGAME&status=open&limit=5", origin=False,
         ua="nba-pregame-dashboard/1.0")
    save("kv_no_ua.json", f"{k}/exchange/status", origin=False, ua=None)
    save("kv_status_origin.json", f"{k}/exchange/status")
    save("kv_ext_status.json", f"{KALSHI_HOSTS[0]}/exchange/status", origin=False, ua="nba-pregame-dashboard/1.0")
    import subprocess
    r = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", f"{k}/exchange/status"],
                       capture_output=True, text=True)
    report.append(f"curl default UA exchange/status: HTTP {r.stdout} {r.stderr.strip()}")
    r = subprocess.run(["curl", "-sS", "https://api.ipify.org"], capture_output=True, text=True)
    report.append(f"runner public IP: {r.stdout.strip()[:3]}.x (Azure)")

    with open(os.path.join(OUT, "REPORT.txt"), "w") as f:
        f.write("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
