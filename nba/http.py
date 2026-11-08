"""Small HTTP helper: browser-like headers, retries with backoff, polite pacing."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

_last_call = 0.0


class FetchError(RuntimeError):
    pass


def get(url: str, *, want: str = "json", timeout: int = 25, retries: int = 3,
        pace: float = 0.25, headers: dict | None = None):
    """GET a URL. Returns parsed JSON (want="json") or bytes. Raises FetchError.

    A 404 returns None (useful for probing files that may not exist yet).
    """
    global _last_call
    hdrs = {"User-Agent": UA, "Accept": "application/json, text/html, */*"}
    if headers:
        hdrs.update(headers)
    err = None
    for attempt in range(retries + 1):
        wait = pace - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            return json.loads(raw.decode("utf-8")) if want == "json" else raw
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = f"HTTP {e.code}"
            if e.code in (401, 403) and attempt >= 1:
                break
        except Exception as e:  # timeouts, connection resets, bad JSON
            err = f"{type(e).__name__}: {e}"
        time.sleep(2 * (attempt + 1))
    raise FetchError(f"{url} -> {err}")
