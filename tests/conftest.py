"""Shared fixtures: in-memory DB, deterministic test images, listing factory.
No live eBay or Anthropic calls anywhere in the suite.
"""

from __future__ import annotations

import io
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from sketchhound import persist
from sketchhound.models import Listing, ListingFormat, Stage

NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = persist.connect(":memory:")
    yield c
    c.close()


def make_image_bytes(pattern: str, fmt: str = "PNG", quality: int = 95) -> bytes:
    """Deterministic 128x128 test images. 'halves' and its JPEG re-encode are
    pHash-near (a relisting); 'checker' is pHash-far (a different artwork)."""
    img = Image.new("RGB", (128, 128), "white")
    px = img.load()
    for x in range(128):
        for y in range(128):
            if pattern == "halves":
                px[x, y] = (0, 0, 0) if x < 64 else (255, 255, 255)
            elif pattern == "checker":
                px[x, y] = (0, 0, 0) if (x // 16 + y // 16) % 2 else (255, 255, 255)
            elif pattern == "diag":
                px[x, y] = (0, 0, 0) if x > y else (255, 255, 255)
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    return buf.getvalue()


_counter = itertools.count(1)


def make_listing(
    item_id: str | None = None,
    title: str = "Vintage costume sketch",
    image_url: str = "https://img.example/a.png",
    price: float = 250.0,
    fmt: ListingFormat = ListingFormat.BUY_IT_NOW,
    **overrides,
) -> Listing:
    n = next(_counter)
    listing = Listing(
        source="ebay",
        source_listing_id=item_id or f"v1|{1000 + n}|0",
        url=f"https://www.ebay.com/itm/{1000 + n}",
        title=title,
        description_snippet="Original gouache on board",
        price_value=price,
        price_currency="USD",
        listing_format=fmt,
        end_time=NOW + timedelta(days=3),
        image_urls=[image_url] if image_url else [],
    )
    for key, value in overrides.items():
        setattr(listing, key, value)
    return listing


def persisted(conn, listing: Listing, stage: Stage = Stage.FETCHED, seen_at: datetime = NOW) -> Listing:
    listing.stage_reached = stage
    listing.first_seen_at = listing.first_seen_at or seen_at
    listing.last_seen_at = listing.last_seen_at or seen_at
    return persist.insert_listing(conn, listing)
