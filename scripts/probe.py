#!/usr/bin/env python3
"""Diagnostic probe for the duellinksmeta (DLM) API (round 3: tournament schema).

Rounds 1-2 established: API reachable from Actions; deck schema known; there is
no tournament-only query param (filter client-side); pagination uses `page`.
King of Games decks have rankedType.name == "King of Games" and url under
/king-of-games/; tournament decks have a null rankedType and url under
/community-tournaments/ (or similar).

This round dumps the FULL object of a few non-KoG (tournament) decks so we can
see how the tournament name / placement is represented, which the Discord
notification needs ("X placed Top 4 at tournament Y").

Output -> data/probe-result.json. Stdlib only; never raises.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

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
    req = request.Request(BASE + path, headers=HEADERS, method="GET")
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (error.HTTPError, Exception) as exc:  # noqa: BLE001
        return {"_probe_error": f"{type(exc).__name__}: {exc}"}


def strip_cards(deck: dict) -> dict:
    """Drop the bulky main/extra/side card arrays so the dumped object is small
    and the tournament/placement metadata is easy to read."""
    out = {}
    for k, v in deck.items():
        if k in ("main", "extra", "side"):
            out[k] = f"<{len(v)} cards>" if isinstance(v, list) else v
        else:
            out[k] = v
    return out


def main() -> None:
    decks = fetch("/api/v1/top-decks?sort=-created&limit=60")
    report = {"probed_at": datetime.now(timezone.utc).isoformat(), "base": BASE}

    if not isinstance(decks, list):
        report["error"] = decks
    else:
        non_kog = [
            d for d in decks
            if not str(d.get("url", "")).startswith("/king-of-games/")
        ]
        report["top_level_keys_kog"] = sorted(
            next((d.keys() for d in decks if str(d.get("url", "")).startswith("/king-of-games/")), [])
        )
        report["top_level_keys_non_kog"] = sorted(non_kog[0].keys()) if non_kog else []
        report["non_kog_count_in_60"] = len(non_kog)
        # Full (card-stripped) objects of a few tournament decks.
        report["non_kog_samples"] = [strip_cards(d) for d in non_kog[:4]]
        # Also show the distinct url path prefixes present, to map categories.
        prefixes = {}
        for d in decks:
            parts = str(d.get("url", "")).split("/")
            prefix = "/" + parts[1] if len(parts) > 1 else d.get("url")
            prefixes.setdefault(prefix, 0)
            prefixes[prefix] += 1
        report["url_prefix_breakdown"] = prefixes

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
