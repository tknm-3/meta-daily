"""DLM image/asset URL helpers.

The bot wants to render a *glanceable* digest with artwork, but the top-deck
JSON only carries a card's `_id`, `name`, `rarity` (no image URL). DLM serves
card art from its S3 CDN under a stable, width-suffixed path, so we can derive
the image URL from the id alone:

    https://s3.duellinksmeta.com/cards/<cardId>_w<width>.webp

Tournament logos, on the other hand, arrive *in* the payload as a site-relative
path (e.g. tournamentType.icon = "/img/tournaments/logos/…webp"); those just
need the site origin prefixed. Prefer payload-provided assets when available
since they can't drift.

All helpers return None for missing input so callers can omit the image cleanly
(Discord simply renders no thumbnail rather than a broken one).
"""
from __future__ import annotations

from urllib.parse import quote

from .client import BASE

# DLM's public CDN for card crops. Width variants exist (e.g. _w60/_w100/_w140);
# w140 is a good balance for a Discord thumbnail (max displayed ~80px wide).
CARD_CDN = "https://s3.duellinksmeta.com/cards"
DEFAULT_CARD_WIDTH = 140


def card_image_url(card_id: str | None, width: int = DEFAULT_CARD_WIDTH) -> str | None:
    """CDN URL for a card's cropped artwork, or None when the id is missing."""
    if not card_id:
        return None
    return f"{CARD_CDN}/{quote(card_id, safe='')}_w{width}.webp"


def site_asset_url(path: str | None) -> str | None:
    """Absolute URL for a site-relative asset path from the API payload
    (e.g. a tournamentType icon). Returns absolute URLs unchanged."""
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return BASE + (path if path.startswith("/") else "/" + path)
