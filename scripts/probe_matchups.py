#!/usr/bin/env python3
"""Feasibility probe for a DECK MATCHUP TABLE (round 4).

The matchup table idea needs MATCH-LEVEL data ("deck A beat deck B"), which the
DLM top-decks API does NOT carry (it only has final placements). This probe runs
from a GitHub-hosted runner (which, unlike the sandbox, can reach DLM/Tonamel)
and answers two concrete feasibility questions:

  Q1. Does the DLM tournament REPORT contain bracket / head-to-head data (or a
      link out to an external bracket)? We try the report article via several
      candidate API shapes AND the report HTML page, then scan for bracket
      keywords and external bracket-host links (tonamel / challonge / etc).

  Q2. Is Tonamel reachable from CI at all, and does it expose machine-readable
      bracket/match data? We probe the homepage and look for an embedded JSON /
      Next.js data blob and any API hints.

Output -> data/probe-matchups.json. Stdlib only; never raises.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

DLM = "https://www.duellinksmeta.com"
OUT = Path(__file__).resolve().parent.parent / "data" / "probe-matchups.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Keywords that would indicate match/bracket data is present in a body of text.
BRACKET_KEYWORDS = [
    "bracket", "round 1", "round 2", "quarterfinal", "semifinal", "finals",
    "defeated", "beat ", " vs ", "vs.", "match ", "winners", "losers",
    "double elim", "single elim", "swiss", "head-to-head", "matchup",
]
# External bracket-hosting platforms a report might link to.
BRACKET_HOSTS = [
    "tonamel.com", "challonge.com", "toornament.com", "start.gg",
    "smash.gg", "battlefy.com", "matcherino.com",
]


def fetch(url: str, *, accept: str = "application/json, text/plain, */*",
          referer: str | None = None) -> dict:
    """Return a small dict describing the response (never raises)."""
    headers = {
        "User-Agent": UA,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    rec: dict = {"url": url}
    try:
        req = request.Request(url, headers=headers, method="GET")
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            rec["status"] = resp.status
            rec["content_type"] = resp.headers.get("Content-Type", "")
            rec["bytes"] = len(raw)
            text = raw.decode("utf-8", errors="replace")
            rec["text"] = text  # caller trims/analyzes
    except error.HTTPError as exc:
        rec["status"] = exc.code
        rec["error"] = f"HTTPError {exc.code} {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def scan_text(text: str) -> dict:
    low = text.lower()
    found_kw = sorted({k.strip() for k in BRACKET_KEYWORDS if k in low})
    found_hosts = sorted({h for h in BRACKET_HOSTS if h in low})
    # Pull any explicit external bracket URLs.
    urls = re.findall(r"https?://[^\s\"'<>\\)]+", text)
    bracket_urls = sorted({
        u for u in urls if any(h in u for h in BRACKET_HOSTS)
    })[:20]
    return {
        "bracket_keywords_found": found_kw,
        "bracket_hosts_mentioned": found_hosts,
        "external_bracket_urls": bracket_urls,
    }


def analyze(rec: dict, *, keep_snippet: int = 600) -> dict:
    """Replace bulky `text` with a scan summary + short snippet."""
    text = rec.pop("text", None)
    if text is not None:
        rec["scan"] = scan_text(text)
        rec["snippet"] = text[:keep_snippet]
    return rec


def main() -> None:
    report: dict = {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "question": "Is match-level (head-to-head) tournament data obtainable?",
    }

    # --- Find a current tournament deck + its linked report --------------------
    decks_rec = fetch(f"{DLM}/api/v1/top-decks?sort=-created&limit=60",
                      referer=f"{DLM}/top-decks")
    sample = None
    try:
        decks = json.loads(decks_rec.get("text", "null"))
    except Exception:  # noqa: BLE001
        decks = None
    if isinstance(decks, list):
        for d in decks:
            if d.get("linkedArticle") and d.get("tournamentType"):
                sample = d
                break
    report["found_tournament_deck"] = bool(sample)

    if sample:
        art = sample["linkedArticle"]
        art_id = art.get("_id")
        art_url = art.get("url", "")
        report["sample_tournament"] = {
            "customTournamentName": sample.get("customTournamentName"),
            "tournamentNumber": sample.get("tournamentNumber"),
            "article_id": art_id,
            "article_url": art_url,
        }

        # Q1: probe candidate ways to read the report ARTICLE content.
        candidates = [
            f"{DLM}/api/v1/articles/{art_id}",
            f"{DLM}/api/v1/articles?_id={art_id}",
            f"{DLM}/api/v1/articles?url={art_url}",
            f"{DLM}/api/v1/article/{art_id}",
            f"{DLM}{art_url}",  # the HTML report page (Nuxt blob)
        ]
        report["report_probes"] = [
            analyze(fetch(u, accept="*/*", referer=f"{DLM}{art_url}"))
            for u in candidates
        ]

    # --- Q2: Tonamel reachability + data shape from CI ------------------------
    tonamel_probes = [
        fetch("https://tonamel.com/", accept="text/html,*/*"),
        # A competition page pattern (id is illustrative; we mostly want to know
        # whether the host responds and whether pages carry an embedded JSON
        # blob we could parse).
        fetch("https://tonamel.com/competition/", accept="text/html,*/*"),
    ]
    out_tonamel = []
    for rec in tonamel_probes:
        text = rec.get("text", "") or ""
        rec.pop("text", None)
        rec["has_next_data"] = "__NEXT_DATA__" in text
        rec["has_apollo_state"] = "APOLLO_STATE" in text or "apollo" in text.lower()
        rec["mentions_graphql"] = "graphql" in text.lower()
        rec["snippet"] = text[:400]
        out_tonamel.append(rec)
    report["tonamel_probes"] = out_tonamel

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
