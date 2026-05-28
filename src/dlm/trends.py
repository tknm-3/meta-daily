"""Build a glanceable, periodic *meta digest* instead of dumping full deck lists.

The premise (per the user): individual card-by-card decklists are best read on
duellinksmeta.com itself, so the bot's job is the at-a-glance summary —

  1. 大会で優勝したデッキタイプ   → which archetypes are actually winning events
  2. 流行ってた汎用札            → generic staples seen across the environment
  3. ここ5日間の流行り          → what's rising / falling versus the prior period

Everything is computed over a rolling time window (default 5 days) and compared
against the immediately-preceding window of equal length to derive a trend
arrow (🆕 new / 📈 up / 📉 down / ➖ flat). Pure data → no I/O here; rendering
and delivery live in render.py / notify.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .analyze import GenericStaple, generic_staples, group_by_archetype
from .models import TOURNAMENT, Deck

# Placement → score, so "actually winning" outranks "merely Top 16". Anything
# unrecognised (Top 4/8/16, "5th-8th", …) counts as a podium-adjacent finish.
_FIRST = 5
_SECOND = 3
_THIRD = 2
_OTHER = 1

# Relative change needed to call a trend up/down rather than flat (±15%).
_TREND_BAND = 0.15


def _placement_score(placement: str | None) -> int:
    p = (placement or "").lower()
    if p.startswith("1st"):
        return _FIRST
    if p.startswith("2nd"):
        return _SECOND
    if p.startswith("3rd"):
        return _THIRD
    return _OTHER


def _is_win(placement: str | None) -> bool:
    return (placement or "").lower().startswith("1st")


def direction(current: float, previous: float) -> str:
    """Trend bucket from a current vs previous magnitude."""
    if previous <= 0:
        return "new" if current > 0 else "flat"
    if current <= 0:
        return "down"
    change = (current - previous) / previous
    if change > _TREND_BAND:
        return "up"
    if change < -_TREND_BAND:
        return "down"
    return "flat"


@dataclass
class WinningDeck:
    archetype: str
    firsts: int          # 1st-place finishes in the current window
    entries: int         # total tournament placings in the current window
    score: float         # weighted placement score, current window
    prev_score: float    # same score in the previous window
    tier: int | None = None

    @property
    def trend(self) -> str:
        return direction(self.score, self.prev_score)


@dataclass
class ArchetypeTrend:
    archetype: str
    count: int           # decks of this archetype in the current window
    prev_count: int      # ditto, previous window
    share: float         # count / total decks in current window

    @property
    def trend(self) -> str:
        return direction(self.count, self.prev_count)


@dataclass
class StapleTrend:
    staple: GenericStaple
    prev_adoption: float

    @property
    def trend(self) -> str:
        return direction(self.staple.overall_adoption, self.prev_adoption)


@dataclass
class Digest:
    generated_at: datetime
    window_days: int
    window_start: datetime
    prev_start: datetime
    total_decks: int            # decks in current window (all categories)
    tournament_decks: int       # tournament-category decks in current window
    winning_decks: list[WinningDeck] = field(default_factory=list)
    rising_archetypes: list[ArchetypeTrend] = field(default_factory=list)
    staples: list[StapleTrend] = field(default_factory=list)
    headline_icon: str | None = None  # site-relative tournament logo, if any

    @property
    def has_data(self) -> bool:
        return bool(self.winning_decks or self.rising_archetypes or self.staples)


def _in_window(decks: list[Deck], start: datetime, end: datetime) -> list[Deck]:
    return [d for d in decks if d.created and start <= d.created < end]


def _winning_decks(current: list[Deck], previous: list[Deck]) -> list[WinningDeck]:
    cur_t = [d for d in current if d.category == TOURNAMENT]
    prev_t = [d for d in previous if d.category == TOURNAMENT]

    prev_score: dict[str, float] = {}
    for d in prev_t:
        prev_score[d.archetype] = prev_score.get(d.archetype, 0) + _placement_score(d.placement)

    firsts: dict[str, int] = {}
    entries: dict[str, int] = {}
    score: dict[str, float] = {}
    tier: dict[str, int | None] = {}
    for d in cur_t:
        a = d.archetype
        entries[a] = entries.get(a, 0) + 1
        firsts[a] = firsts.get(a, 0) + (1 if _is_win(d.placement) else 0)
        score[a] = score.get(a, 0) + _placement_score(d.placement)
        tier.setdefault(a, d.tier)

    decks = [
        WinningDeck(
            archetype=a,
            firsts=firsts[a],
            entries=entries[a],
            score=score[a],
            prev_score=prev_score.get(a, 0.0),
            tier=tier.get(a),
        )
        for a in entries
    ]
    # Rank by 1st-place finishes first (the literal "優勝"), then weighted score.
    decks.sort(key=lambda w: (-w.firsts, -w.score, -w.entries, w.archetype))
    return decks


def _archetype_trends(current: list[Deck], previous: list[Deck]) -> list[ArchetypeTrend]:
    cur_groups = group_by_archetype(current)
    prev_groups = group_by_archetype(previous)
    total = len(current) or 1
    trends = [
        ArchetypeTrend(
            archetype=a,
            count=len(ds),
            prev_count=len(prev_groups.get(a, [])),
            share=len(ds) / total,
        )
        for a, ds in cur_groups.items()
    ]
    # Surface movers: biggest absolute climbers first, then by current volume.
    trends.sort(key=lambda t: (-(t.count - t.prev_count), -t.count, t.archetype))
    return trends


def _staple_trends(current: list[Deck], previous: list[Deck]) -> list[StapleTrend]:
    cur_staples = generic_staples(current)
    prev_staples = {s.name: s.overall_adoption for s in generic_staples(previous)}
    return [StapleTrend(staple=s, prev_adoption=prev_staples.get(s.name, 0.0)) for s in cur_staples]


def build_digest(decks: list[Deck], *, now: datetime, window_days: int = 5) -> Digest:
    """Compute the full digest from a corpus that should already span at least
    `2 * window_days` of `created` history (so the previous window is populated)."""
    decks = [d for d in decks if not d.is_rush]
    window_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=2 * window_days)
    current = _in_window(decks, window_start, now)
    previous = _in_window(decks, prev_start, window_start)

    winning = _winning_decks(current, previous)
    headline_icon = next(
        (d.tournament_icon for d in current if d.category == TOURNAMENT and d.tournament_icon),
        None,
    )

    return Digest(
        generated_at=now,
        window_days=window_days,
        window_start=window_start,
        prev_start=prev_start,
        total_decks=len(current),
        tournament_decks=sum(1 for d in current if d.category == TOURNAMENT),
        winning_decks=winning,
        rising_archetypes=_archetype_trends(current, previous),
        staples=_staple_trends(current, previous),
        headline_icon=headline_icon,
    )
