"""Two-layer dedup (brief section 3).

1. Primary key (source, source_listing_id): seen before → refresh
   last_seen_at/price/end_time, never re-gate, never re-score.
2. Perceptual hash (pHash) on the primary image: Hamming distance ≤5 to any
   stored listing → relisting under a new item ID. Mark relisted_from,
   inherit the original's vision verdict (same artwork — no point paying for
   it twice), and let publish suppress it unless price dropped >20%.

Vision is only ever invoked on listings that clear BOTH layers — this is the
hard requirement that keeps recurring runs cheap.
"""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime
from typing import Callable

import imagehash
from PIL import Image

from . import persist
from .config import PHASH_MAX_HAMMING
from .http_util import download_bytes
from .models import Listing, Stage, VisionResult

FetchImage = Callable[[str], bytes]


def compute_phash(image_bytes: bytes) -> str:
    """pHash of the primary image as a hex string."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        return str(imagehash.phash(img))


def hamming(phash_a: str, phash_b: str) -> int:
    return (int(phash_a, 16) ^ int(phash_b, 16)).bit_count()


def find_relist_match(phash: str, stored: list[tuple[int, str]]) -> int | None:
    """Closest stored listing id within PHASH_MAX_HAMMING, else None."""
    best_id, best_distance = None, PHASH_MAX_HAMMING + 1
    for listing_id, stored_hash in stored:
        distance = hamming(phash, stored_hash)
        if distance < best_distance:
            best_id, best_distance = listing_id, distance
    return best_id


def dedup(
    conn: sqlite3.Connection,
    listings: list[Listing],
    now: datetime,
    fetch_image: FetchImage = download_bytes,
) -> list[Listing]:
    """Persist sightings/relistings; return only genuinely-new listings
    (already inserted, stage FETCHED, phash set when computable)."""
    stored_phashes = persist.all_phashes(conn)
    new: list[Listing] = []
    seen_this_batch: set[tuple[str, str]] = set()

    for listing in listings:
        key = (listing.source, listing.source_listing_id)
        if key in seen_this_batch:
            continue  # same item surfaced by multiple queries in one run
        seen_this_batch.add(key)

        existing = persist.get_by_source_id(conn, listing.source, listing.source_listing_id)
        if existing:
            persist.touch_sighting(conn, existing.id, now, listing)
            continue

        listing.first_seen_at = now
        listing.last_seen_at = now

        if listing.image_urls:
            try:
                listing.image_phash = compute_phash(fetch_image(listing.image_urls[0]))
            except Exception:
                listing.image_phash = None  # no phash → no relist detection, still a new listing

        match_id = (
            find_relist_match(listing.image_phash, stored_phashes) if listing.image_phash else None
        )
        if match_id is not None:
            original = conn.execute(
                "SELECT vision_json, confidence, attributed_artist FROM listings WHERE id = ?",
                (match_id,),
            ).fetchone()
            listing.stage_reached = Stage.RELISTED
            listing.relisted_from = match_id
            listing.confidence = original["confidence"]
            listing.attributed_artist = original["attributed_artist"]
            if original["vision_json"]:
                listing.vision = VisionResult(**json.loads(original["vision_json"]))
            persist.insert_listing(conn, listing)
            continue

        listing.stage_reached = Stage.FETCHED
        persist.insert_listing(conn, listing)
        if listing.image_phash:
            stored_phashes.append((listing.id, listing.image_phash))  # catch intra-run relists
        new.append(listing)

    return new
