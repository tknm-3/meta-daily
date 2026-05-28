#!/usr/bin/env python3
"""Diagnostic probe for the duellinksmeta (DLM) API.

Purpose: this repo's bot must run somewhere with outbound internet (GitHub
Actions). Before building the deck parser/analyzer we need to confirm two
things from such an environment:

  1. Is the DLM API reachable at all (or blocked by Cloudflare / a challenge)?
  2. What is the real response shape for top-decks / tournaments / cards?

The probe never raises on HTTP or network errors - it records them, because a
403 or a Cloudflare challenge page is itself a useful result. Output is written
to data/probe-result.json (committed back by the workflow) and printed to the
Actions log. Uses only the standard library so no install step is required.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

BASE = "https://www.duellinksmeta.com"

# Best-guess endpoints. The exact params are unknown, so probe a few variants;
# one run then tells us which work and what they return.
ENDPOINTS = [
    "/api/v1/top-decks?limit=3",
    "/api/v1/top-decks?limit=3&sort=-created",
    "/api/v1/tournaments?limit=2",
    "/api/v1/cards?limit=1",
]

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

INTERESTING_HEADERS = (
    "content-type",
    "server",
    "cf-ray",
    "cf-cache-status",
    "x-cache",
    "x-deny-reason",
    "retry-after",
)


def summarize(data, depth: int = 0):
    """Compact description of a JSON value (keys + value types/lengths) so the
    committed result stays readable even for large payloads."""
    if depth > 6:
        return "<...>"
    if isinstance(data, dict):
        return {k: summarize(v, depth + 1) for k, v in list(data.items())[:60]}
    if isinstance(data, list):
        return {
            "_type": "list",
            "_len": len(data),
            "_first": summarize(data[0], depth + 1) if data else None,
        }
    if isinstance(data, str):
        return f"<str len={len(data)}>" if len(data) > 80 else data
    return data


def probe(path: str) -> dict:
    url = BASE + path
    req = request.Request(url, headers=HEADERS, method="GET")
    started = time.time()
    result: dict = {"url": url}
    try:
        with request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            text = body.decode("utf-8", errors="replace")
            result["status"] = resp.status
            result["resp_headers"] = {
                h: resp.headers.get(h) for h in INTERESTING_HEADERS if resp.headers.get(h)
            }
            ctype = resp.headers.get("content-type", "")
            if "json" in ctype or text[:1] in "[{":
                try:
                    parsed = json.loads(text)
                    result["json_summary"] = summarize(parsed)
                    # Keep one full item so we can see the exact deck schema.
                    result["sample_item"] = (
                        parsed[0] if isinstance(parsed, list) and parsed else parsed
                    )
                except json.JSONDecodeError as exc:
                    result["json_error"] = str(exc)
                    result["body_snippet"] = text[:1500]
            else:
                result["body_snippet"] = text[:1500]
    except error.HTTPError as exc:
        result["status"] = exc.code
        result["error"] = f"HTTPError {exc.code} {exc.reason}"
        result["resp_headers"] = {
            h: exc.headers.get(h) for h in INTERESTING_HEADERS if exc.headers and exc.headers.get(h)
        }
        try:
            result["body_snippet"] = exc.read().decode("utf-8", errors="replace")[:1500]
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001 - every failure mode is informative
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["elapsed_s"] = round(time.time() - started, 2)
    return result


def main() -> None:
    report = {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "base": BASE,
        "results": [probe(p) for p in ENDPOINTS],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    reachable = sorted({r.get("status") for r in report["results"] if r.get("status")})
    print(f"\n=== status codes seen: {reachable} ===")


if __name__ == "__main__":
    main()
