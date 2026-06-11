"""Configuration: watchlist.yaml (the friend's tuning surface) + env secrets +
hard-coded pipeline constants (not exposed in the watchlist on purpose —
brief says the yaml is the *only* tuning surface he touches, so anything
sharper than queries/keywords/thresholds stays here in code).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# --- Pipeline constants (brief sections 3 & 6) ---
PHASH_MAX_HAMMING = 5            # ≤5 → same artwork, mark relisted_from
RELIST_PRICE_DROP = 0.20         # relistings suppressed unless price dropped >20%
BACKFILL_VISION_CAP = 150        # first-run cap; remainder drains over later runs
ABORT_NEW_SURVIVORS = 100        # >100 new survivors → skip vision, banner the feed
HOT_LISTED_WITHIN_HOURS = 24     # Hot = BIN + confidence ≥ threshold + listed <24h
ENDING_SOON_HOURS = 48           # Ending-soon section window
VISION_MAX_IMAGES = 3            # primary + up to 2 more
HTTP_RETRIES = 3                 # exponential backoff on all HTTP
HTTP_TIMEOUT_SECONDS = 30

FILTER_MODEL = "claude-haiku-4-5"
VISION_MODEL = "claude-sonnet-4-6"

DEFAULT_WATCHLIST = Path("watchlist.yaml")
DEFAULT_DB = Path("data/sketchhound.db")
DEFAULT_SITE_DIR = Path("docs")  # GitHub Pages serves from /docs


@dataclass
class HotAlertConfig:
    # Hot = high-priced: collector wants the significant pieces surfaced,
    # not budget caps. Listings at or above min_price qualify.
    min_price: float = 500.0
    min_confidence: float = 0.7


@dataclass
class Watchlist:
    artist_queries: list[str] = field(default_factory=list)
    generic_queries: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    hot_alert: HotAlertConfig = field(default_factory=HotAlertConfig)
    ntfy_topic: str = ""
    feed_url: str = ""  # public Pages URL; Click target for digest alerts


def load_watchlist(path: Path = DEFAULT_WATCHLIST) -> Watchlist:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    hot = raw.get("hot_alert", {})
    return Watchlist(
        artist_queries=raw.get("artist_queries", []),
        generic_queries=raw.get("generic_queries", []),
        negative_keywords=raw.get("negative_keywords", []),
        hot_alert=HotAlertConfig(
            min_price=float(hot.get("min_price", 500)),
            min_confidence=float(hot.get("min_confidence", 0.7)),
        ),
        ntfy_topic=raw.get("ntfy_topic", ""),
        feed_url=raw.get("feed_url", ""),
    )


@dataclass
class Secrets:
    """Repo secrets, env-only, never committed (brief section 6)."""

    ebay_client_id: str
    ebay_client_secret: str
    anthropic_api_key: str = ""

    @classmethod
    def from_env(cls, require_anthropic: bool = True) -> "Secrets":
        required = ["EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"]
        if require_anthropic:
            required.append("ANTHROPIC_API_KEY")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
        return cls(
            ebay_client_id=os.environ["EBAY_CLIENT_ID"],
            ebay_client_secret=os.environ["EBAY_CLIENT_SECRET"],
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        )
