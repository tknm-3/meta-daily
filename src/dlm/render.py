"""Format decks and analysis into Discord webhook embeds / text.

Discord limits respected: embed title <=256, description <=4096, field value
<=1024, <=25 fields, <=10 embeds per message.
"""
from __future__ import annotations

from .analyze import ArchetypeReport, GenericStaple
from .models import Deck

FIELD_LIMIT = 1024

_PLACEMENT_COLOR = {
    "1st place": 0xFFD700,
    "2nd place": 0xC0C0C0,
    "3rd place": 0xCD7F32,
}
_DEFAULT_COLOR = 0x5865F2


def _color(deck: Deck) -> int:
    if deck.placement:
        return _PLACEMENT_COLOR.get(deck.placement.lower(), _DEFAULT_COLOR)
    return _DEFAULT_COLOR


def _total(cards) -> int:
    return sum(c.amount for c in cards)


def _card_lines(cards, *, budget: int = FIELD_LIMIT) -> str:
    lines: list[str] = []
    used = 0
    ordered = sorted(cards, key=lambda c: (-c.amount, c.name))
    for c in ordered:
        line = f"`{c.amount}` {c.name}"
        if used + len(line) + 1 > budget:
            lines.append("…")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if lines else "—"


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _copies(stat) -> str:
    """Compact copy summary: mode count, and average if it differs."""
    if abs(stat.avg_copies - stat.mode_copies) < 0.25:
        return f"{stat.mode_copies}枚"
    return f"{stat.mode_copies}枚 (avg {stat.avg_copies:.1f})"


def _tier(deck: Deck) -> str:
    return f"Tier {deck.tier}" if deck.tier else "Tier -"


def deck_embed(
    deck: Deck,
    report: ArchetypeReport | None = None,
    staples_here: list[GenericStaple] | None = None,
) -> dict:
    header_bits = []
    if deck.placement:
        header_bits.append(f"**{deck.placement}**")
    if deck.tournament_name:
        header_bits.append(f"@ {deck.tournament_name}")
    desc_lines = [" ".join(header_bits)] if header_bits else []
    meta = f"by {deck.author} ・ {_tier(deck)}"
    if deck.skill:
        meta += f" ・ Skill: {deck.skill}"
    if deck.created:
        meta += f" ・ {deck.created:%Y-%m-%d}"
    desc_lines.append(meta)
    if deck.report_full_url:
        desc_lines.append(f"[大会レポート]({deck.report_full_url})")

    title = deck.archetype + (" (Rush)" if deck.is_rush else "")
    embed = {
        "title": title[:256],
        "url": deck.full_url,
        "color": _color(deck),
        "description": "\n".join(desc_lines)[:4096],
        "fields": [
            {
                "name": f"メインデッキ ({_total(deck.main)}枚)",
                "value": _card_lines(deck.main),
                "inline": False,
            }
        ],
    }
    if deck.extra:
        embed["fields"].append(
            {
                "name": f"エクストラ ({_total(deck.extra)}枚)",
                "value": _card_lines(deck.extra),
                "inline": False,
            }
        )
    if staples_here:
        names = "、".join(s.name for s in staples_here[:20])
        embed["fields"].append(
            {"name": "このリストの汎用札", "value": names[:FIELD_LIMIT], "inline": False}
        )
    if report and report.sample_size >= 3:
        core = report.by_role("core")[:12]
        if core:
            lines = [f"`{_copies(s)}` {s.name} ({_pct(s.adoption)})" for s in core]
            value = "\n".join(lines)[:FIELD_LIMIT]
            embed["fields"].append(
                {
                    "name": f"{report.archetype} の確定枠 (直近{report.sample_size}件)",
                    "value": value,
                    "inline": False,
                }
            )
    return embed


def startup_embed(total_tracked: int, latest: Deck | None) -> dict:
    desc = f"入賞構築の追跡を開始しました。現在 {total_tracked} 件を既知として記録。"
    if latest and latest.tournament_name:
        desc += f"\n最新の大会: {latest.tournament_name} — {latest.archetype}（{latest.placement}）"
    return {"title": "Duel Links 大会構築トラッカー 起動", "color": _DEFAULT_COLOR, "description": desc[:4096]}


def archetype_report_text(report: ArchetypeReport, staples: list[GenericStaple]) -> str:
    """Plain-text breakdown for the CLI `analyze` command."""
    lines = [f"# {report.archetype}  (直近 {report.sample_size} 件)"]
    for role, label in (("core", "確定枠"), ("flex", "選択枠"), ("tech", "少数採用")):
        stats = report.by_role(role)
        if not stats:
            continue
        lines.append(f"\n## {label}")
        for s in stats:
            lines.append(f"  {_copies(s):<14} {s.name:<32} {_pct(s.adoption)}")
    arch_staples = [s for s in staples if report.archetype in s.archetypes]
    if arch_staples:
        lines.append("\n## このアーカイプに含まれる汎用札 (環境横断)")
        for s in arch_staples:
            lines.append(f"  {s.name:<32} {s.spread}アーキタイプ / 全体{_pct(s.overall_adoption)}")
    return "\n".join(lines)


def generic_staples_text(staples: list[GenericStaple], limit: int = 40) -> str:
    lines = ["# 環境全体の汎用札 (複数アーカイプで採用)"]
    for s in staples[:limit]:
        lines.append(
            f"  {s.name:<34} {s.spread:>2}アーキ  全体{_pct(s.overall_adoption):>4}  [{s.rarity}]"
        )
    return "\n".join(lines)
