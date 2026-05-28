"""Parsing of DLM top-deck JSON into typed objects.

Schema (confirmed by scripts/probe.py):
  - Every deck: _id, created, author (dict|str), skill (dict|str|null),
    deckType {name, tier}, main/extra/side lists of {card:{_id,name,rarity},
    amount}, url (relative), rush (bool).
  - King of Games decks carry `rankedType` {name: "King of Games", ...}.
  - Tournament decks carry `tournamentType` {name, ...}, `tournamentPlacement`
    ("1st Place"/"Top 4"/...), `tournamentNumber`, `customTournamentName`, and
    `linkedArticle` {title, url} pointing at the tournament report.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .client import BASE

KOG = "kog"
TOURNAMENT = "tournament"
FEATURED = "featured"
OTHER = "other"


@dataclass(frozen=True)
class CardCount:
    card_id: str
    name: str
    rarity: str
    amount: int


@dataclass
class Deck:
    id: str
    created: datetime | None
    created_raw: str
    author: str
    archetype: str
    tier: int | None
    skill: str | None
    is_rush: bool
    url: str
    category: str
    main: list[CardCount]
    extra: list[CardCount]
    side: list[CardCount]
    # Tournament-only metadata (None for KoG/featured):
    tournament_name: str | None = None
    placement: str | None = None
    tournament_category: str | None = None
    tournament_icon: str | None = None  # site-relative logo path from the payload
    report_url: str | None = None
    # KoG/featured label, e.g. "King of Games" / "Spicy Win Streaks":
    ranked_label: str | None = None

    @property
    def full_url(self) -> str | None:
        return BASE + self.url if self.url else None

    @property
    def report_full_url(self) -> str | None:
        return BASE + self.report_url if self.report_url else None


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _name_field(value, key: str = "name") -> str | None:
    if isinstance(value, dict):
        return value.get(key)
    if isinstance(value, str):
        return value
    return None


def _cards(raw_list) -> list[CardCount]:
    out: list[CardCount] = []
    if not isinstance(raw_list, list):
        return out
    for entry in raw_list:
        card = entry.get("card") or {}
        if not isinstance(card, dict):
            continue
        out.append(
            CardCount(
                card_id=card.get("_id", ""),
                name=card.get("name", "?"),
                rarity=card.get("rarity", ""),
                amount=int(entry.get("amount", 0) or 0),
            )
        )
    return out


def _category(raw: dict) -> tuple[str, str | None]:
    """Return (category, ranked_label)."""
    if raw.get("tournamentType"):
        return TOURNAMENT, None
    ranked = _name_field(raw.get("rankedType"))
    if ranked == "King of Games":
        return KOG, ranked
    if ranked:
        return FEATURED, ranked
    url = raw.get("url", "") or ""
    if url.startswith("/community-tournaments") or url.startswith("/tournaments"):
        return TOURNAMENT, None
    if url.startswith("/king-of-games"):
        return KOG, "King of Games"
    return OTHER, ranked


def _tournament_name(raw: dict) -> str | None:
    name = raw.get("customTournamentName") or _name_field(raw.get("tournamentType"))
    number = raw.get("tournamentNumber")
    if name and number:
        return f"{name} #{number}"
    return name


def parse_deck(raw: dict) -> Deck:
    deck_type = raw.get("deckType") or {}
    category, ranked_label = _category(raw)
    linked = raw.get("linkedArticle") or {}
    ttype = raw.get("tournamentType") or {}
    return Deck(
        id=raw.get("_id", ""),
        created=_parse_dt(raw.get("created")),
        created_raw=raw.get("created", ""),
        author=_name_field(raw.get("author"), "username") or "Unknown",
        archetype=deck_type.get("name") or "Unknown",
        tier=deck_type.get("tier"),
        skill=_name_field(raw.get("skill")),
        is_rush=bool(raw.get("rush")),
        url=raw.get("url", ""),
        category=category,
        main=_cards(raw.get("main")),
        extra=_cards(raw.get("extra")),
        side=_cards(raw.get("side")),
        tournament_name=_tournament_name(raw) if category == TOURNAMENT else None,
        placement=raw.get("tournamentPlacement") if category == TOURNAMENT else None,
        tournament_category=_name_field(raw.get("tournamentType")),
        tournament_icon=(ttype.get("icon") if isinstance(ttype, dict) else None),
        report_url=(linked.get("url") if isinstance(linked, dict) else None),
        ranked_label=ranked_label,
    )


def parse_decks(raw_list: list[dict]) -> list[Deck]:
    return [parse_deck(d) for d in raw_list if isinstance(d, dict) and d.get("_id")]
