"""Shared fixtures: in-memory DB, deterministic test images, listing factory.
No live eBay or Anthropic calls anywhere in the suite.
"""

from __future__ import annotations

import io
import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from sketchhound import persist
from sketchhound.models import Listing, ListingFormat, Stage

NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    """Retry backoff is real in prod, instant in tests."""
    monkeypatch.setattr("sketchhound.http_util.time.sleep", lambda _: None)


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


class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, text: str, input_tokens: int = 1000, output_tokens: int = 200):
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _FakeMessages:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("unexpected Anthropic API call")
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return FakeResponse(item)
        return item


class FakeAnthropic:
    """Scripted stand-in for anthropic.Anthropic — no live calls in CI.
    Each scripted item is a response text, a FakeResponse, or an Exception."""

    def __init__(self, scripted=()):
        self.messages = _FakeMessages(scripted)

    @property
    def calls(self) -> list[dict]:
        return self.messages.calls


def vision_json(confidence: float = 0.85, artist: str = "Edith Head", **overrides) -> str:
    data = {
        "is_costume_design_sketch": True,
        "confidence": confidence,
        "attributed_artist": artist,
        "attribution_confidence": 0.6,
        "signals": ["signature lower right", "gouache on board"],
        "era_estimate": "1950s",
        "red_flags": [],
        "summary": "Probable original costume design sketch.",
    }
    data.update(overrides)
    return json.dumps(data)


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
