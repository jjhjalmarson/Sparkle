"""Core data types shared across pipeline stages.

One `Listing` schema for everything (BUILD_BRIEF.md section 5). Rejects are
kept too — `stage_reached` records how far a listing got, which is the tuning
data for the gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ListingFormat(StrEnum):
    AUCTION = "auction"
    BUY_IT_NOW = "buy_it_now"


class Stage(StrEnum):
    """How far a listing made it through the pipeline (recorded on rejects too)."""

    FETCHED = "fetched"
    DUPLICATE = "duplicate"                # exact (source, source_listing_id) re-sighting
    RELISTED = "relisted"                  # pHash match to an earlier listing
    NEGATIVE_KEYWORD_REJECT = "negative_keyword_reject"
    HAIKU_REJECT = "haiku_reject"
    GATE_SURVIVOR = "gate_survivor"        # passed gating, vision pending (backfill drain queue)
    VISION_SCORED = "vision_scored"


class HaikuVerdict(StrEnum):
    RELEVANT = "RELEVANT"
    IRRELEVANT = "IRRELEVANT"
    UNSURE = "UNSURE"                      # proceeds to vision: recall over precision


@dataclass
class VisionResult:
    """Exact JSON contract from the Sonnet vision call (brief section 3)."""

    is_costume_design_sketch: bool
    confidence: float
    attributed_artist: str                 # "Edith Head" | ... | "unknown"
    attribution_confidence: float
    signals: list[str]
    era_estimate: str
    red_flags: list[str]
    summary: str


@dataclass
class Listing:
    """One eBay listing, normalized. Mirrors the `listings` table 1:1."""

    source: str                            # "ebay" (Phase 1 only source)
    source_listing_id: str
    url: str
    title: str
    description_snippet: str | None
    price_value: float | None
    price_currency: str | None
    listing_format: ListingFormat | None
    end_time: datetime | None
    image_urls: list[str] = field(default_factory=list)
    image_phash: str | None = None         # hex string; Hamming distance computed in Python
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    stage_reached: Stage = Stage.FETCHED
    haiku_verdict: HaikuVerdict | None = None
    vision: VisionResult | None = None
    confidence: float | None = None        # denormalized from vision for indexed queries
    attributed_artist: str | None = None
    relisted_from: int | None = None       # listings.id of the earlier sighting
    went_hot_at: datetime | None = None
    alerted_at: datetime | None = None     # idempotency guard: ntfy fires once, ever
    id: int | None = None                  # set by persist layer


@dataclass
class RunStats:
    """One pipeline run. Mirrors the `runs` table; feeds the page footer."""

    started_at: datetime
    finished_at: datetime | None = None
    fetched_count: int = 0
    new_count: int = 0
    vision_call_count: int = 0
    est_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)
    id: int | None = None
