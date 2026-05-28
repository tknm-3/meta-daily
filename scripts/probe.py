#!/usr/bin/env python3
"""Diagnostic probe for the duellinksmeta (DLM) API (round 2: tournament filter).

Round 1 confirmed the API is reachable from Actions and captured the deck
schema. The bot must surface *tournament* placings only (the site's
#tournamentsOnly toggle), but the default top-decks feed mixes in King of Games
ladder decks. This round discovers:

  1. How "tournament only" is expressed - a query param, or a client-side
     filter on each deck's rankedType. We census the distinct rankedType values
     and try candidate filter params.
  2. How pagination works (`from` offset vs `page`).

Output -> data/probe-result.json (committed back) and the Actions log. Stdlib
only; never raises - failures are recorded.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

BASE = "https://www.duellinksmeta.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE}/top-decks",
    "Origin": BASE,
}
OUT = Path(__file__).resolve().parent.parent / "data" / "probe-result.json"


def fetch(path: str):
    url = BASE + path
    req = request.Request(url, headers=HEADERS, method="GET")
    started = time.time()
    try:
        with request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            data = json.loads(text)
            return {"status": resp.status, "data": data, "elapsed_s": round(time.time() - started, 2)}
    except error.HTTPError as exc:
        return {"status": exc.code, "error": f"HTTPError {exc.code} {exc.reason}",
                "elapsed_s": round(time.time() - started, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "elapsed_s": round(time.time() - started, 2)}


def ranked_name(deck: dict) -> str:
    rt = deck.get("rankedType") or {}
    if isinstance(rt, dict):
        return rt.get("name") or rt.get("shortName") or "?"
    return str(rt)


def census(decks):
    """Summarize a deck list: how many, and the rankedType / deckType breakdown."""
    if not isinstance(decks, list):
        return {"note": "not a list", "value": str(decks)[:200]}
    return {
        "count": len(decks),
        "rankedType_breakdown": dict(Counter(ranked_name(d) for d in decks).most_common()),
        "deckType_sample": [
            {
                "deckType": (d.get("deckType") or {}).get("name"),
                "tier": (d.get("deckType") or {}).get("tier"),
                "rankedType": ranked_name(d),
                "created": d.get("created"),
                "url": d.get("url"),
            }
            for d in decks[:8]
        ],
    }


def main() -> None:
    report = {"probed_at": datetime.now(timezone.utc).isoformat(), "base": BASE, "probes": {}}

    # 1) Big recent sample: what rankedType values actually appear?
    r = fetch("/api/v1/top-decks?sort=-created&limit=60")
    report["probes"]["recent_60"] = {
        "status": r.get("status"), "error": r.get("error"), "elapsed_s": r.get("elapsed_s"),
        "summary": census(r.get("data")) if "data" in r else None,
    }

    # 2) Candidate tournament-only filters.
    candidates = [
        "/api/v1/top-decks?sort=-created&limit=20&tournament=true",
        "/api/v1/top-decks?sort=-created&limit=20&tournaments=true",
        "/api/v1/top-decks?sort=-created&limit=20&rankedType=Tournament",
        "/api/v1/top-decks?sort=-created&limit=20&kog=false",
    ]
    report["probes"]["tournament_filters"] = {}
    for path in candidates:
        key = parse.urlparse(path).query
        r = fetch(path)
        report["probes"]["tournament_filters"][key] = {
            "status": r.get("status"), "error": r.get("error"),
            "summary": census(r.get("data")) if "data" in r else None,
        }

    # 3) Pagination: does `from` offset / `page` change the window?
    base_q = "/api/v1/top-decks?sort=-created&limit=5"
    page0 = fetch(base_q)
    page_from = fetch(base_q + "&from=5")
    page_2 = fetch(base_q + "&page=2")

    def ids(resp):
        d = resp.get("data")
        return [x.get("_id") for x in d][:5] if isinstance(d, list) else None

    report["probes"]["pagination"] = {
        "page0_ids": ids(page0),
        "from5_ids": ids(page_from),
        "page2_ids": ids(page_2),
        "from5_differs": ids(page0) != ids(page_from) if ids(page0) and ids(page_from) else None,
        "page2_differs": ids(page0) != ids(page_2) if ids(page0) and ids(page_2) else None,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
