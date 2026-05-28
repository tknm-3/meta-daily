"""Entry point: fetch DLM top decks and post a glanceable *meta digest* to
Discord — winning deck types and trending generic staples, drawn solely from
tournament placement builds (大会の入賞構築) over a rolling window — instead of
dumping full decklists (those live on the site, which the digest links to).

Run modes:
  python -m dlm.bot digest               # build the digest and post to Discord
  python -m dlm.bot analyze "<arch>"     # print an archetype's card breakdown
  python -m dlm.bot staples              # print environment-wide generic staples
  python -m dlm.bot preview              # dump the digest to data/preview.json (no Discord)

Env:
  DISCORD_WEBHOOK_URL   target webhook (unset -> dry-run, prints instead)
  DISCORD_USERNAME      webhook display name (optional)
  DLM_WINDOW_DAYS       digest window in days (default 5)
  DLM_MAX_PAGES         max pages of 50 to pull when building the corpus (default 20)
  DLM_PER_PAGE          page size (default 50)
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import client
from .analyze import archetype_report, generic_staples, group_by_archetype
from .models import TOURNAMENT, Deck, parse_decks
from .notify import send_embeds
from .render import (
    archetype_report_text,
    digest_content,
    digest_embeds,
    generic_staples_text,
)
from .trends import _STAPLE_MIN_ARCH_SIZE, _STAPLE_MIN_ARCHETYPES, build_digest


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def fetch_corpus(*, max_pages: int, per_page: int, until: datetime | None = None) -> list[Deck]:
    """Pull recent decks newest-first, de-duplicated by id.

    Stops early once a fetched page's oldest deck predates `until` (so we don't
    page forever), but always honours `max_pages` as a hard cap.
    """
    seen_ids: set[str] = set()
    raw: list[dict] = []
    for page in range(1, max_pages + 1):
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
        if until is not None:
            oldest = min((d.created for d in parse_decks(batch) if d.created), default=None)
            if oldest and oldest < until:
                break
    return parse_decks(raw)


def _corpus_for_window(window_days: int) -> tuple[list[Deck], datetime]:
    now = datetime.now(timezone.utc)
    max_pages = _env_int("DLM_MAX_PAGES", 20)
    per_page = _env_int("DLM_PER_PAGE", 50)
    # Need both the current and previous window populated -> reach back 2x the
    # window, plus a day of slack so the boundary isn't starved.
    until = now - timedelta(days=2 * window_days + 1)
    decks = fetch_corpus(max_pages=max_pages, per_page=per_page, until=until)
    return decks, now


def _tournament_only(decks: list[Deck]) -> list[Deck]:
    """Restrict a corpus to tournament placement builds (大会の入賞構築)."""
    return [d for d in decks if d.category == TOURNAMENT]


def _staples(decks: list[Deck]):
    """Generic staples over the placement corpus, using the digest's relaxed
    spread/size thresholds (placements are a smaller sample than the ladder)."""
    return generic_staples(
        decks,
        min_archetypes=_STAPLE_MIN_ARCHETYPES,
        min_arch_size=_STAPLE_MIN_ARCH_SIZE,
    )


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
        print(f"[Discord error] {exc}")


def cmd_digest(_: argparse.Namespace) -> int:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    username = os.environ.get("DISCORD_USERNAME") or "DLM Meta Digest"
    window_days = _env_int("DLM_WINDOW_DAYS", 5)

    decks, now = _corpus_for_window(window_days)
    if not decks:
        print("No decks fetched.")
        return 0

    digest = build_digest(decks, now=now, window_days=window_days)
    print(
        f"Digest: {digest.total_decks} decks in window "
        f"({digest.tournament_decks} tournament), "
        f"{len(digest.winning_decks)} winning archetypes, {len(digest.staples)} staples."
    )
    if not digest.has_data:
        print("Window has no usable data — skipping post.")
        return 0

    embeds = digest_embeds(digest)
    if webhook:
        _notify(webhook, embeds, content=digest_content(digest), username=username)
        print(f"Posted digest ({len(embeds)} embeds) to Discord.")
    else:
        print(f"[dry-run] would post {len(embeds)} embeds:")
        print(json.dumps(embeds, indent=2, ensure_ascii=False))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    window_days = _env_int("DLM_WINDOW_DAYS", 5)
    decks, _ = _corpus_for_window(window_days)
    decks = _tournament_only(decks)
    groups = group_by_archetype(decks)
    staples = _staples(decks)
    name = _resolve_archetype(groups, args.archetype)
    if not name:
        print("該当アーキタイプなし。候補:")
        for n, ds in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:30]:
            print(f"  {len(ds):>3}件  {n}")
        return 1
    report = archetype_report(groups[name])
    print(archetype_report_text(report, staples))
    return 0


def cmd_staples(args: argparse.Namespace) -> int:
    window_days = _env_int("DLM_WINDOW_DAYS", 5)
    decks, _ = _corpus_for_window(window_days)
    staples = _staples(_tournament_only(decks))
    print(generic_staples_text(staples, limit=args.limit))
    return 0


def cmd_preview(_: argparse.Namespace) -> int:
    """Fetch live data and dump exactly what the bot WOULD post to
    data/preview.json, without touching Discord. Lets us inspect the rendered
    digest (and verify image URLs) against real data before wiring the webhook."""
    window_days = _env_int("DLM_WINDOW_DAYS", 5)
    decks, now = _corpus_for_window(window_days)
    digest = build_digest(decks, now=now, window_days=window_days)
    preview = {
        "generated_at": now.isoformat(),
        "window_days": window_days,
        "corpus_size": len(decks),
        "totals": {
            "decks_in_window": digest.total_decks,
            "tournament_in_window": digest.tournament_decks,
        },
        "content": digest_content(digest),
        "embeds": digest_embeds(digest),
    }
    out = Path(__file__).resolve().parents[2] / "data" / "preview.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(preview, indent=2, ensure_ascii=False))
    print(
        f"Wrote {out}: corpus={len(decks)}, window={digest.total_decks} decks "
        f"({digest.tournament_decks} tournament), embeds={len(preview['embeds'])}."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dlm.bot", description="DLM meta digest bot")
    sub = parser.add_subparsers(dest="command")

    p_dg = sub.add_parser("digest", help="build the meta digest and post to Discord")
    p_dg.set_defaults(func=cmd_digest)

    p_an = sub.add_parser("analyze", help="print an archetype's card breakdown")
    p_an.add_argument("archetype")
    p_an.set_defaults(func=cmd_analyze)

    p_st = sub.add_parser("staples", help="print environment-wide generic staples")
    p_st.add_argument("--limit", type=int, default=40)
    p_st.set_defaults(func=cmd_staples)

    p_pv = sub.add_parser("preview", help="dump the digest to data/preview.json (no Discord)")
    p_pv.set_defaults(func=cmd_preview)

    parser.set_defaults(func=cmd_digest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
