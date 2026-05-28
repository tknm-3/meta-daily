#!/usr/bin/env python3
"""Offline self-test for the meta-digest pipeline.

No network: builds a synthetic corpus spanning two windows and asserts that
trend computation and embed rendering behave (winning-deck ranking, rising/new
detection, image URLs, Discord size limits). Run: PYTHONPATH=src python scripts/selftest.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from dlm.models import KOG, TOURNAMENT, CardCount, Deck
from dlm.render import digest_embeds
from dlm.trends import build_digest

NOW = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)
ICON = "/img/tournaments/logos/community-tournaments-logo.webp"


def card(name: str, amount: int, cid: str = "") -> CardCount:
    return CardCount(card_id=cid or f"id_{name}", name=name, rarity="UR", amount=amount)


def deck(archetype, created, *, category=TOURNAMENT, placement=None, staples=()) -> Deck:
    main = [card(f"{archetype} Engine A", 3), card(f"{archetype} Engine B", 2)]
    main += [card(name, 2, f"cid_{name}") for name in staples]
    return Deck(
        id=f"{archetype}-{created.isoformat()}-{placement}",
        created=created,
        created_raw=created.isoformat(),
        author="tester",
        archetype=archetype,
        tier=1,
        skill=None,
        is_rush=False,
        url=f"/x/{archetype}",
        category=category,
        main=main,
        extra=[],
        side=[],
        tournament_name="Test Cup #1" if category == TOURNAMENT else None,
        placement=placement,
        tournament_icon=ICON if category == TOURNAMENT else None,
    )


def build_corpus() -> list[Deck]:
    d = lambda days: NOW - timedelta(days=days)  # noqa: E731
    decks: list[Deck] = []
    STAPLES = ["Forbidden Droplet", "Effect Veiler", "D.D. Crow"]

    # CURRENT window (0–5 days ago): Stardust dominates (2 firsts), Swordsoul rising.
    decks += [
        deck("Stardust / Synchron", d(1), placement="1st Place", staples=STAPLES),
        deck("Stardust / Synchron", d(2), placement="1st Place", staples=STAPLES),
        deck("Stardust / Synchron", d(3), placement="Top 4", staples=STAPLES[:2]),
        deck("Swordsoul", d(1), placement="1st Place", staples=STAPLES),
        deck("Swordsoul", d(2), placement="2nd Place", staples=STAPLES),
        deck("Swordsoul", d(4), placement="Top 4", staples=STAPLES[:2]),
        deck("Traptrix", d(2), placement="Top 8", staples=STAPLES[:1]),
    ]
    # current-window ladder/KoG decks (count toward popularity + staples spread)
    decks += [deck("Swordsoul", d(i % 5), category=KOG, staples=STAPLES) for i in range(6)]
    decks += [deck("Branded", d(i % 5), category=KOG, staples=STAPLES[:2]) for i in range(4)]

    # PREVIOUS window (5–10 days ago): Traptrix was the big winner; Swordsoul small.
    decks += [
        deck("Traptrix", d(6), placement="1st Place", staples=STAPLES[:2]),
        deck("Traptrix", d(7), placement="1st Place", staples=STAPLES[:2]),
        deck("Traptrix", d(8), placement="2nd Place", staples=STAPLES[:1]),
        deck("Swordsoul", d(9), placement="Top 8", staples=STAPLES[:1]),
        deck("Stardust / Synchron", d(7), placement="Top 4", staples=STAPLES[:2]),
    ]
    decks += [deck("Traptrix", d(6 + i % 4), category=KOG, staples=STAPLES[:2]) for i in range(5)]
    return decks


def main() -> int:
    digest = build_digest(build_corpus(), now=NOW, window_days=5)
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)

    # --- winning decks ---
    winners = {w.archetype: w for w in digest.winning_decks}
    check("Stardust / Synchron" in winners, "Stardust should be a winning deck")
    top = digest.winning_decks[0]
    check(top.archetype == "Stardust / Synchron", f"top winner should be Stardust, got {top.archetype}")
    check(top.firsts == 2, f"Stardust should have 2 firsts, got {top.firsts}")
    # Swordsoul went 0 firsts -> 1 first this window from ~tiny prev: should trend up/new.
    check(winners["Swordsoul"].trend in ("up", "new"), "Swordsoul should be trending up")
    # Traptrix won a lot last window, little this window -> down.
    check(winners["Traptrix"].trend == "down", f"Traptrix should be down, got {winners['Traptrix'].trend}")

    # --- staples ---
    names = [s.staple.name for s in digest.staples]
    check("Forbidden Droplet" in names, "Forbidden Droplet should be a staple")
    lead = next(s for s in digest.staples if s.staple.card_id)
    check(lead.staple.card_id.startswith("cid_"), "staple should carry a card_id for art")

    # --- headline icon resolved from payload ---
    check(digest.headline_icon == ICON, "headline icon should come from tournament payload")

    # --- embeds + Discord limits ---
    embeds = digest_embeds(digest)
    check(1 <= len(embeds) <= 10, f"embed count out of range: {len(embeds)}")
    summary = embeds[0]
    check(summary["thumbnail"]["url"].startswith("https://www.duellinksmeta.com"),
          "summary thumbnail should be an absolute site URL")
    has_card_art = any(
        "thumbnail" in e and "s3.duellinksmeta.com/cards/" in e["thumbnail"]["url"]
        for e in embeds
    )
    check(has_card_art, "a digest embed should carry card artwork")
    for e in embeds:
        check(len(e.get("title", "")) <= 256, "title over 256")
        check(len(e.get("description", "")) <= 4096, "description over 4096")
        for f in e.get("fields", []):
            check(len(f["value"]) <= 1024, f"field value over 1024: {f['name']}")
            check(len(f["name"]) <= 256, "field name over 256")

    if failures:
        print("SELFTEST FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print(f"SELFTEST OK — {len(embeds)} embeds, {len(digest.winning_decks)} winners, "
          f"{len(digest.staples)} staples, top={top.archetype}({top.firsts}🏆)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
