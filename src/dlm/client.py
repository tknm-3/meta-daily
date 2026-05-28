"""Minimal stdlib HTTP client for the duellinksmeta (DLM) API.

Uses urllib (no third-party deps) with browser-like headers, which the probe
confirmed is accepted by DLM's Cloudflare from GitHub-hosted runners. Retries
transient failures (network errors, 5xx, 429) with exponential backoff; 4xx
other than 429 fail fast since retrying won't help.
"""
from __future__ import annotations

import json
import time
from urllib import error, request

BASE = "https://www.duellinksmeta.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE}/top-decks",
    "Origin": BASE,
}


class DLMError(RuntimeError):
    """Raised when a DLM API request ultimately fails."""


def _get(path: str, *, retries: int = 4, backoff: float = 2.0):
    url = BASE + path
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = request.Request(url, headers=_HEADERS, method="GET")
            with request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            last_exc = exc
            if exc.code < 500 and exc.code != 429:
                raise DLMError(f"GET {url} -> HTTP {exc.code} {exc.reason}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
        if attempt < retries - 1:
            time.sleep(backoff * (2**attempt))
    raise DLMError(f"GET {url} failed after {retries} attempts: {last_exc}")


def get_top_decks(*, limit: int = 50, page: int = 1, sort: str = "-created") -> list[dict]:
    """Fetch a page of top decks (newest first by default).

    Pagination is 1-indexed via `page` (the `from` offset param is ignored by
    the API, per probing). Returns the raw list of deck dicts.
    """
    data = _get(f"/api/v1/top-decks?sort={sort}&limit={int(limit)}&page={int(page)}")
    return data if isinstance(data, list) else []
