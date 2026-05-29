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


def _all_keys(obj, out=None) -> set:
    """Every dict key appearing anywhere in a nested JSON structure."""
    if out is None:
        out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _all_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _all_keys(v, out)
    return out


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

    tonamel_code = None
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

        # Q1: read the report ARTICLE and inspect its STRUCTURE. We confirmed
        # `?_id=` returns JSON; now dump the article object's keys, look for any
        # inline match/result structures, and extract the Tonamel link.
        art_rec = fetch(f"{DLM}/api/v1/articles?_id={art_id}",
                        accept="*/*", referer=f"{DLM}{art_url}")
        art_text = art_rec.pop("text", "") or ""
        art_obj = None
        try:
            parsed = json.loads(art_text)
            art_obj = parsed[0] if isinstance(parsed, list) and parsed else parsed
        except Exception:  # noqa: BLE001
            pass
        if isinstance(art_obj, dict):
            art_rec["article_top_keys"] = sorted(art_obj.keys())
            # Which keys (anywhere) hint at structured match data?
            hint = re.compile(r"match|result|bracket|standing|round|winner|score",
                              re.I)
            art_rec["match_like_keys"] = sorted({
                k for k in _all_keys(art_obj) if hint.search(k)
            })
            # The prose body, if any, and the bracket link within it.
            body = art_obj.get("content") or art_obj.get("body") or ""
            art_rec["content_field_len"] = len(body) if isinstance(body, str) else None
        art_rec["scan"] = scan_text(art_text)
        # Extract a Tonamel competition code (e.g. .../competition/VA23z/...).
        m = re.search(r"tonamel\.com/competition/([A-Za-z0-9]+)", art_text)
        tonamel_code = m.group(1) if m else None
        art_rec["tonamel_code"] = tonamel_code
        report["report_article"] = art_rec

    # --- Q2: Tonamel — find the API that serves bracket/match data ------------
    code = tonamel_code or "VA23z"
    report["tonamel_competition_code"] = code
    tonamel_targets = [
        f"https://tonamel.com/competition/{code}",
        f"https://tonamel.com/competition/{code}/tournament",
        # Candidate JSON/API shapes to discover (most will 404; that's data too).
        f"https://tonamel.com/api/competition/{code}",
        f"https://tonamel.com/api/v1/competition/{code}",
        f"https://tonamel.com/competition/{code}/tournament.json",
    ]
    out_tonamel = []
    for url in tonamel_targets:
        rec = fetch(url, accept="text/html,application/json,*/*")
        text = rec.pop("text", "") or ""
        rec["has_next_data"] = "__NEXT_DATA__" in text
        rec["has_nuxt_data"] = "__NUXT__" in text
        rec["mentions_graphql"] = "graphql" in text.lower()
        # Pull referenced API paths so we can chase the real data endpoint.
        api_paths = sorted(set(
            re.findall(r"https?://[a-z0-9.\-]+/[^\s\"'<>\\)]*api[^\s\"'<>\\)]*", text, re.I)
            + re.findall(r"[\"'](/api/[^\"'\s]+)", text)
        ))[:25]
        rec["api_paths_referenced"] = api_paths
        rec["scan"] = scan_text(text)
        rec["snippet"] = text[:500]
        out_tonamel.append(rec)
    report["tonamel_probes"] = out_tonamel

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
