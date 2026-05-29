"""Deck analysis: per-archetype card adoption, copy counts, and cross-archetype
generic-staple detection.

Two questions this answers, both asked for explicitly:
  - For a given archetype, which cards are core (確定枠) vs flex (選択枠) vs tech,
    and how many copies are typically run.
  - Which cards are "generic staples" (汎用札) - cards adopted across MANY
    different archetypes (handtraps, generic spells/traps, generic extra-deck
    monsters), as opposed to archetype engine pieces.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .models import Deck

CORE_THRESHOLD = 0.80
FLEX_THRESHOLD = 0.35


@dataclass
class CardStat:
    name: str
    rarity: str
    decks_running: int
    sample_size: int
    card_id: str = ""  # representative id, for resolving artwork
    copies_hist: dict[int, int] = field(default_factory=dict)  # copies -> #decks

    @property
    def adoption(self) -> float:
        return self.decks_running / self.sample_size if self.sample_size else 0.0

    @property
    def avg_copies(self) -> float:
        total = sum(copies * decks for copies, decks in self.copies_hist.items())
        return total / self.decks_running if self.decks_running else 0.0

    @property
    def mode_copies(self) -> int:
        if not self.copies_hist:
            return 0
        return max(self.copies_hist.items(), key=lambda kv: (kv[1], kv[0]))[0]

    @property
    def role(self) -> str:
        a = self.adoption
        if a >= CORE_THRESHOLD:
            return "core"
        if a >= FLEX_THRESHOLD:
            return "flex"
        return "tech"


@dataclass
class ArchetypeReport:
    archetype: str
    sample_size: int
    stats: list[CardStat]  # sorted by adoption desc

    def by_role(self, role: str) -> list[CardStat]:
        return [s for s in self.stats if s.role == role]


@dataclass
class GenericStaple:
    name: str
    rarity: str
    archetypes: list[str]
    overall_decks_running: int
    overall_sample: int
    card_id: str = ""  # representative id, for resolving artwork

    @property
    def spread(self) -> int:
        return len(self.archetypes)

    @property
    def overall_adoption(self) -> float:
        return self.overall_decks_running / self.overall_sample if self.overall_sample else 0.0


def group_by_archetype(decks: list[Deck]) -> dict[str, list[Deck]]:
    groups: dict[str, list[Deck]] = defaultdict(list)
    for deck in decks:
        groups[deck.archetype].append(deck)
    return dict(groups)


def card_stats(decks: list[Deck], zone: str = "main") -> list[CardStat]:
    """Adoption / copy stats for every card seen in `zone` across `decks`
    (assumed same archetype). Sorted by adoption, then average copies."""
    n = len(decks)
    running: dict[str, int] = defaultdict(int)
    hist: dict[str, Counter] = defaultdict(Counter)
    rarity: dict[str, str] = {}
    card_id: dict[str, str] = {}
    for deck in decks:
        # Sum copies per name within the deck first: a card can appear as two
        # entries (e.g. alternate printings sharing a name), but it's still one
        # adopting deck, so adoption never exceeds 100%.
        per_deck: dict[str, int] = defaultdict(int)
        for card in getattr(deck, zone):
            per_deck[card.name] += card.amount
            rarity[card.name] = card.rarity
            if card.card_id and not card_id.get(card.name):
                card_id[card.name] = card.card_id
        for name, amount in per_deck.items():
            running[name] += 1
            hist[name][amount] += 1
    stats = [
        CardStat(name, rarity[name], running[name], n, card_id.get(name, ""), dict(hist[name]))
        for name in running
    ]
    stats.sort(key=lambda s: (-s.adoption, -s.avg_copies, s.name))
    return stats


def archetype_report(decks: list[Deck], zone: str = "main") -> ArchetypeReport | None:
    if not decks:
        return None
    return ArchetypeReport(decks[0].archetype, len(decks), card_stats(decks, zone))


def generic_staples(
    decks: list[Deck],
    zone: str = "main",
    *,
    min_archetypes: int = 3,
    arch_adoption: float = 0.25,
    min_arch_size: int = 3,
) -> list[GenericStaple]:
    """Cards adopted (>= arch_adoption) in at least `min_archetypes` distinct
    archetypes. Only archetypes with >= min_arch_size sampled decks count toward
    the spread, so a card splashed in one fringe list isn't called generic."""
    groups = group_by_archetype(decks)
    card_archetypes: dict[str, set[str]] = defaultdict(set)
    rarity: dict[str, str] = {}
    card_id: dict[str, str] = {}
    for archetype, archetype_decks in groups.items():
        if len(archetype_decks) < min_arch_size:
            continue
        for stat in card_stats(archetype_decks, zone):
            rarity[stat.name] = stat.rarity
            if stat.card_id and not card_id.get(stat.name):
                card_id[stat.name] = stat.card_id
            if stat.adoption >= arch_adoption:
                card_archetypes[stat.name].add(archetype)

    overall_running: dict[str, int] = defaultdict(int)
    for deck in decks:
        for name in {card.name for card in getattr(deck, zone)}:
            overall_running[name] += 1

    n_total = len(decks)
    staples = [
        GenericStaple(
            name=name,
            rarity=rarity.get(name, ""),
            archetypes=sorted(archetypes),
            overall_decks_running=overall_running[name],
            overall_sample=n_total,
            card_id=card_id.get(name, ""),
        )
        for name, archetypes in card_archetypes.items()
        if len(archetypes) >= min_archetypes
    ]
    staples.sort(key=lambda g: (-g.spread, -g.overall_adoption, g.name))
    return staples


def staples_in_deck(deck: Deck, staples: list[GenericStaple], zone: str = "main") -> list[GenericStaple]:
    names = {card.name for card in getattr(deck, zone)}
    return [s for s in staples if s.name in names]


def archetype_staples(
    decks: list[Deck],
    staples: list[GenericStaple],
    zone: str = "main",
    *,
    min_adoption: float = 0.0,
    top: int | None = None,
) -> list[CardStat]:
    """How heavily each detected generic staple is run *within* a single
    archetype's decks. Reuses `card_stats` (adoption / copies inside the
    archetype) but keeps only cards already flagged as environment-wide
    staples, so engine pieces stay out. Sorted by in-archetype adoption."""
    staple_names = {s.name for s in staples}
    stats = [
        s
        for s in card_stats(decks, zone)
        if s.name in staple_names and s.adoption >= min_adoption
    ]
    return stats[:top] if top is not None else stats
