"""Two-layer dedup (brief section 3).

1. Primary key (source, source_listing_id): seen before → refresh
   last_seen_at/price/end_time, never re-gate, never re-score.
2. Perceptual hash (pHash) on the primary image: Hamming distance ≤5 to any
   stored listing → relisting under a new item ID. Mark relisted_from and
   suppress from the feed unless price dropped >20% vs the original.

Vision is only ever invoked on listings that clear BOTH layers — this is the
hard requirement that makes hourly cadence cheap.
"""

from __future__ import annotations

import sqlite3

from .models import Listing


def compute_phash(image_bytes: bytes) -> str:
    """pHash of the primary image as a hex string (imagehash.phash)."""
    raise NotImplementedError  # step 2


def hamming(phash_a: str, phash_b: str) -> int:
    raise NotImplementedError  # step 2


def dedup(conn: sqlite3.Connection, listings: list[Listing]) -> list[Listing]:
    """Return only genuinely-new listings; persist sightings/relist links for the rest."""
    raise NotImplementedError  # step 2
