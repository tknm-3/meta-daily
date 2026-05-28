"""Entry point: fetch DLM top decks, notify Discord about new tournament
placings, and surface per-archetype / generic-staple analysis.

Run modes:
  python -m dlm.bot run                 # cron flow: detect new placings, notify
  python -m dlm.bot analyze "<arch>"    # print an archetype's card breakdown
  python -m dlm.bot staples             # print environment-wide generic staples
  python -m dlm.bot preview             # dump live preview.json (no Discord)

Env:
  DISCORD_WEBHOOK_URL   target webhook (unset -> dry-run, prints instead)
  DISCORD_USERNAME      webhook display name (optional)
  DLM_INCLUDE_KOG       "1" to also notify King of Games decks (default: off)
  DLM_CORPUS_PAGES      pages of 50 to pull for the analysis corpus (default 6)
  DLM_PER_PAGE          page size (default 50)
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import client
from .analyze import (
    archetype_report,
    generic_staples,
    group_by_archetype,
    staples_in_deck,
)
from .models import KOG, TOURNAMENT, Deck, parse_decks
from .notify import send_embeds
from .render import (
    archetype_report_text,
    deck_embed,
    generic_staples_text,
    startup_embed,
)
from .store import SeenStore

_MIN_DT = datetime.min.replace(tzinfo=timezone.utc)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def fetch_corpus(pages: int, per_page: int) -> list[Deck]:
    """Pull recent decks across pages (newest first), de-duplicated by id."""
    seen_ids: set[str] = set()
    raw: list[dict] = []
    for page in range(1, pages + 1):
        batch = client.get_top_decks(limit=per_page, page=page)
        if not batch:
            break
        for deck in batch:
            deck_id = deck.get("_id")
            if deck_id and deck_id not in seen_ids:
                seen_ids.add(deck_id)
                raw.append(deck)
        if len(batch) < per_page:
            break
    return parse_decks(raw)


def _resolve_archetype(groups: dict[str, list[Deck]], query: str) -> str | None:
    names = list(groups)
    for name in names:
        if name.lower() == query.lower():
            return name
    matches = [name for name in names if query.lower() in name.lower()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        print("複数一致:", ", ".join(sorted(matches)))
    return None


def _notify(webhook: str, embeds: list[dict], **kwargs) -> None:
    """Call send_embeds; log and continue on Discord errors so they never crash the run."""
    try:
        send_embeds(webhook, embeds, **kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"[Discord error - state will still be saved] {exc}")


def _group_by_tournament(decks: list[Deck]) -> list[tuple[str, list[Deck]]]:
    """Return (label, deck_list) pairs, one per tournament, oldest-first."""
    seen: dict[str, list[Deck]] = {}
    order: list[str] = []
    for deck in decks:
        key = deck.tournament_name or deck.ranked_label or "Other"
        if key not in seen:
            seen[key] = []
            order.append(key)
        seen[key].append(deck)
    return [(k, seen[k]) for k in order]


def cmd_run(_: argparse.Namespace) -> int:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    username = os.environ.get("DISCORD_USERNAME") or "DLM Tournament Tracker"
    include_kog = _flag("DLM_INCLUDE_KOG")
    pages = _env_int("DLM_CORPUS_PAGES", 6)
    per_page = _env_int("DLM_PER_PAGE", 50)

    decks = fetch_corpus(pages, per_page)
    if not decks:
        print("No decks fetched.")
        return 0

    groups = group_by_archetype(decks)
    staples = generic_staples(decks)
    wanted = {TOURNAMENT} | ({KOG} if include_kog else set())
    candidates = [d for d in decks if d.category in wanted]
    all_ids = [d.id for d in decks]

    store = SeenStore()
    if store.first_run:
        store.add_many(all_ids)
        store.save()
        latest = next((d for d in decks if d.category == TOURNAMENT), None)
        print(f"First run: seeded {len(all_ids)} decks as known.")
        if webhook:
            _notify(webhook, [startup_embed(len(all_ids), latest)], username=username)
        return 0

    new = sorted(
        (d for d in candidates if not store.has(d.id)),
        key=lambda d: d.created or _MIN_DT,
    )

    print(
        f"Fetched {len(decks)} decks; {len(candidates)} candidates "
        f"({'tournament+kog' if include_kog else 'tournament'}); {len(new)} new."
    )

    # Group by tournament so each event appears as one Discord message.
    posted = 0
    for label, event_decks in _group_by_tournament(new):
        embeds = [
            deck_embed(d, archetype_report(groups.get(d.archetype, [])), staples_in_deck(d, staples))
            for d in event_decks
        ]
        content = f"**{label}** — {len(embeds)}件の入賞構築"
        if webhook:
            _notify(webhook, embeds, content=content, username=username)
        else:
            print(f"[dry-run] {content}")
            for d in event_decks:
                print(f"  {d.placement:<12} {d.archetype}")
        posted += len(embeds)

    if posted and webhook:
        print(f"Posted {posted} embed(s) to Discord.")

    store.add_many(all_ids)
    store.save()
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    pages = _env_int("DLM_CORPUS_PAGES", 6)
    per_page = _env_int("DLM_PER_PAGE", 50)
    decks = fetch_corpus(pages, per_page)
    groups = group_by_archetype(decks)
    staples = generic_staples(decks)
    name = _resolve_archetype(groups, args.archetype)
    if not name:
        print("該当アーキタイプなし。候補:")
        for n, ds in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:30]:
            print(f"  {len(ds):>3}件  {n}")
        return 1
    report = archetype_report(groups[name])
    text = archetype_report_text(report, staples)
    print(text)
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if args.post and webhook:
        for i in range(0, len(text), 1900):
            send_embeds(webhook, [], content="```\n" + text[i : i + 1900] + "\n```")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    """Fetch live data and dump what the bot WOULD post to data/preview.json,
    without touching Discord or the seen-state. Lets us (and the user) inspect
    real rendering before wiring up the webhook."""
    pages = _env_int("DLM_CORPUS_PAGES", 6)
    per_page = _env_int("DLM_PER_PAGE", 50)
    decks = fetch_corpus(pages, per_page)
    groups = group_by_archetype(decks)
    staples = generic_staples(decks)
    recent = sorted(
        (d for d in decks if d.category == TOURNAMENT),
        key=lambda d: d.created or _MIN_DT,
        reverse=True,
    )[: args.limit]
    preview = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "decks": len(decks),
            "archetypes": len(groups),
            "by_category": dict(Counter(d.category for d in decks)),
        },
        "generic_staples": [
            {
                "name": s.name,
                "rarity": s.rarity,
                "spread": s.spread,
                "overall_adoption": round(s.overall_adoption, 3),
                "archetypes": s.archetypes,
            }
            for s in staples[:30]
        ],
        "recent_tournament_embeds": [
            deck_embed(d, archetype_report(groups.get(d.archetype, [])), staples_in_deck(d, staples))
            for d in recent
        ],
    }
    out = Path(__file__).resolve().parents[2] / "data" / "preview.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(preview, indent=2, ensure_ascii=False))
    print(
        f"Wrote {out}: {len(decks)} decks, {len(recent)} recent tournament, "
        f"{len(staples)} generic staples. categories={preview['totals']['by_category']}"
    )
    return 0


def cmd_staples(args: argparse.Namespace) -> int:
    pages = _env_int("DLM_CORPUS_PAGES", 6)
    per_page = _env_int("DLM_PER_PAGE", 50)
    decks = fetch_corpus(pages, per_page)
    staples = generic_staples(decks)
    text = generic_staples_text(staples, limit=args.limit)
    print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dlm.bot", description="DLM tournament deck tracker")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="detect new tournament placings and notify Discord")
    p_run.set_defaults(func=cmd_run)

    p_an = sub.add_parser("analyze", help="print an archetype's card breakdown")
    p_an.add_argument("archetype")
    p_an.add_argument("--post", action="store_true", help="also post the breakdown to Discord")
    p_an.set_defaults(func=cmd_analyze)

    p_st = sub.add_parser("staples", help="print environment-wide generic staples")
    p_st.add_argument("--limit", type=int, default=40)
    p_st.set_defaults(func=cmd_staples)

    p_pv = sub.add_parser("preview", help="dump live preview to data/preview.json (no Discord)")
    p_pv.add_argument("--limit", type=int, default=6)
    p_pv.set_defaults(func=cmd_preview)

    parser.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
