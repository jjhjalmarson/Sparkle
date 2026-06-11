"""SQLite persistence. The DB file is committed back to the repo each run —
it IS the state (brief section 2). Everything is persisted, including rejects,
with the stage they were rejected at (tuning data).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Listing, RunStats

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT NOT NULL,
    source_listing_id   TEXT NOT NULL,
    url                 TEXT NOT NULL,
    title               TEXT NOT NULL,
    description_snippet TEXT,
    price_value         REAL,
    price_currency      TEXT,
    listing_format      TEXT,                -- 'auction' | 'buy_it_now'
    end_time            TEXT,                -- ISO 8601 UTC
    image_urls          TEXT NOT NULL DEFAULT '[]',  -- JSON array
    image_phash         TEXT,                -- hex string
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    stage_reached       TEXT NOT NULL,
    haiku_verdict       TEXT,                -- RELEVANT | IRRELEVANT | UNSURE
    vision_json         TEXT,                -- raw VisionResult JSON
    confidence          REAL,
    attributed_artist   TEXT,
    relisted_from       INTEGER REFERENCES listings(id),
    went_hot_at         TEXT,
    alerted_at          TEXT,                -- ntfy idempotency guard
    UNIQUE (source, source_listing_id)
);

CREATE INDEX IF NOT EXISTS idx_listings_stage  ON listings (stage_reached);
CREATE INDEX IF NOT EXISTS idx_listings_phash  ON listings (image_phash);
CREATE INDEX IF NOT EXISTS idx_listings_hot    ON listings (went_hot_at, alerted_at);

CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    fetched_count     INTEGER NOT NULL DEFAULT 0,
    new_count         INTEGER NOT NULL DEFAULT 0,
    vision_call_count INTEGER NOT NULL DEFAULT 0,
    est_cost_usd      REAL NOT NULL DEFAULT 0.0,
    errors            TEXT NOT NULL DEFAULT '[]'   -- JSON array of strings
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating parent dirs and schema if needed) and return a connection."""
    raise NotImplementedError  # step 2


def get_by_source_id(conn: sqlite3.Connection, source: str, source_listing_id: str) -> Listing | None:
    raise NotImplementedError  # step 2


def all_phashes(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """(listing id, phash hex) for every stored listing with a phash — relist detection."""
    raise NotImplementedError  # step 2


def upsert_listing(conn: sqlite3.Connection, listing: Listing) -> Listing:
    """Insert new, or refresh last_seen_at/price/end_time on re-sighting. Returns with id set."""
    raise NotImplementedError  # step 2


def pending_vision(conn: sqlite3.Connection, limit: int) -> list[Listing]:
    """Gate survivors not yet vision-scored — the backfill drain queue."""
    raise NotImplementedError  # step 2


def record_run(conn: sqlite3.Connection, stats: RunStats) -> RunStats:
    raise NotImplementedError  # step 2


def month_spend_usd(conn: sqlite3.Connection) -> float:
    """Sum est_cost_usd over runs in the current calendar month — feed footer."""
    raise NotImplementedError  # step 2
