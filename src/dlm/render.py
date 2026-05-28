"""Format analysis into Discord webhook embeds / text.

The headline output is the *meta digest* (see trends.py): a compact, glanceable
embed pair — winning deck types + rising archetypes, and trending generic
staples with card art — rather than full decklists. Detailed lists live on
duellinksmeta.com, which every digest links to.

Discord limits respected: embed title <=256, description <=4096, field value
<=1024, <=25 fields, <=10 embeds per message.
"""
from __future__ import annotations

from .analyze import ArchetypeReport, GenericStaple
from .assets import card_image_url, site_asset_url
from .client import BASE
from .trends import ArchetypeTrend, Digest, StapleTrend, WinningDeck

FIELD_LIMIT = 1024

_GOLD = 0xFFD700
_BLUE = 0x5865F2

TOP_DECKS_URL = f"{BASE}/top-decks"
TOURNAMENTS_URL = f"{BASE}/tournaments"

_ARROW = {"new": "🆕", "up": "📈", "down": "📉", "flat": "➖"}


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _copies(stat) -> str:
    """Compact copy summary: mode count, and average if it differs."""
    if abs(stat.avg_copies - stat.mode_copies) < 0.25:
        return f"{stat.mode_copies}枚"
    return f"{stat.mode_copies}枚 (avg {stat.avg_copies:.1f})"


def _arrow(direction: str) -> str:
    return _ARROW.get(direction, "")


def _join_budget(lines: list[str], budget: int = FIELD_LIMIT) -> str:
    """Join lines with newlines, stopping (with an ellipsis) before `budget`."""
    out: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > budget:
            out.append("…")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out) if out else "—"


# --------------------------------------------------------------------------- #
# Meta digest                                                                  #
# --------------------------------------------------------------------------- #

def _winning_line(w: WinningDeck) -> str:
    arrow = _arrow(w.trend)
    if w.firsts:
        body = f"🏆{w.firsts} ・ 入賞{w.entries}件"
    else:
        body = f"入賞{w.entries}件"
    return f"**{w.archetype}** — {body} {arrow}".rstrip()


def _archetype_line(t: ArchetypeTrend) -> str:
    delta = t.count - t.prev_count
    sign = f"+{delta}" if delta > 0 else str(delta)
    return f"{_arrow(t.trend)} **{t.archetype}** {t.count}件 ({_pct(t.share)}・前比{sign})"


def _staple_line(s: StapleTrend) -> str:
    g = s.staple
    rarity = f" [{g.rarity}]" if g.rarity else ""
    return f"{_arrow(s.trend)} **{g.name}**{rarity} — {g.spread}デッキ採用・全体{_pct(g.overall_adoption)}"


def summary_embed(
    digest: Digest,
    *,
    top_winners: int = 8,
    top_archetypes: int = 6,
) -> dict:
    span = f"{digest.window_start:%m/%d} 〜 {digest.generated_at:%m/%d}"
    desc = [
        f"🗓️ **直近{digest.window_days}日間**（{span}）の大会・環境まとめ",
        f"📦 集計対象 {digest.total_decks} 構築（うち大会 {digest.tournament_decks} 件）",
        f"🔗 細かいレシピは [DuelLinksMeta の TOP DECKS]({TOP_DECKS_URL}) でチェック",
    ]
    embed: dict = {
        "title": f"📊 Duel Links 環境まとめ ｜ 直近{digest.window_days}日",
        "url": TOP_DECKS_URL,
        "color": _GOLD,
        "description": "\n".join(desc)[:4096],
        "fields": [],
        "footer": {"text": "📈上昇 📉下降 🆕新顔 ➖横ばい（前の同期間との比較）"},
    }
    icon = site_asset_url(digest.headline_icon)
    if icon:
        embed["thumbnail"] = {"url": icon}

    winners = digest.winning_decks[:top_winners]
    if winners:
        embed["fields"].append(
            {
                "name": "🏆 大会で勝っているデッキ",
                "value": _join_budget([_winning_line(w) for w in winners]),
                "inline": False,
            }
        )

    # Highlight movers: things rising or newly appearing, biggest delta first.
    movers = [t for t in digest.rising_archetypes if t.trend in ("up", "new") and t.count >= 2]
    if movers:
        embed["fields"].append(
            {
                "name": "📈 ここ数日で伸びているデッキ",
                "value": _join_budget([_archetype_line(t) for t in movers[:top_archetypes]]),
                "inline": False,
            }
        )
    return embed


def staples_embed(digest: Digest, *, top: int = 12) -> dict | None:
    staples = digest.staples[:top]
    if not staples:
        return None
    embed: dict = {
        "title": "🃏 流行りの汎用札（複数デッキで採用）",
        "url": TOP_DECKS_URL,
        "color": _BLUE,
        "description": _join_budget([_staple_line(s) for s in staples], budget=4096),
    }
    # Lead card's art as a thumbnail to make the post visual at a glance.
    art = next((card_image_url(s.staple.card_id) for s in staples if s.staple.card_id), None)
    if art:
        embed["thumbnail"] = {"url": art}
    return embed


def digest_embeds(digest: Digest) -> list[dict]:
    """The full digest as a list of embeds (one Discord message)."""
    embeds = [summary_embed(digest)]
    staples = staples_embed(digest)
    if staples:
        embeds.append(staples)
    return embeds


def digest_content(digest: Digest) -> str:
    """Short message text shown above the embeds (mobile push preview)."""
    return f"📊 直近{digest.window_days}日間のDuel Links環境まとめ"


# --------------------------------------------------------------------------- #
# CLI text helpers (analyze / staples commands)                                #
# --------------------------------------------------------------------------- #

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
