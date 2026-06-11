"""SQLite persistence. The DB file is committed back to the repo each run —
it IS the state (brief section 2). Everything is persisted, including rejects,
with the stage they were rejected at (tuning data).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import HaikuVerdict, Listing, ListingFormat, RunStats, Stage, VisionResult

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


def connect(db_path: Path | str) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_listing(row: sqlite3.Row) -> Listing:
    vision = None
    if row["vision_json"]:
        vision = VisionResult(**json.loads(row["vision_json"]))
    return Listing(
        id=row["id"],
        source=row["source"],
        source_listing_id=row["source_listing_id"],
        url=row["url"],
        title=row["title"],
        description_snippet=row["description_snippet"],
        price_value=row["price_value"],
        price_currency=row["price_currency"],
        listing_format=ListingFormat(row["listing_format"]) if row["listing_format"] else None,
        end_time=_dt(row["end_time"]),
        image_urls=json.loads(row["image_urls"]),
        image_phash=row["image_phash"],
        first_seen_at=_dt(row["first_seen_at"]),
        last_seen_at=_dt(row["last_seen_at"]),
        stage_reached=Stage(row["stage_reached"]),
        haiku_verdict=HaikuVerdict(row["haiku_verdict"]) if row["haiku_verdict"] else None,
        vision=vision,
        confidence=row["confidence"],
        attributed_artist=row["attributed_artist"],
        relisted_from=row["relisted_from"],
        went_hot_at=_dt(row["went_hot_at"]),
        alerted_at=_dt(row["alerted_at"]),
    )


def _listing_params(listing: Listing) -> dict:
    return {
        "source": listing.source,
        "source_listing_id": listing.source_listing_id,
        "url": listing.url,
        "title": listing.title,
        "description_snippet": listing.description_snippet,
        "price_value": listing.price_value,
        "price_currency": listing.price_currency,
        "listing_format": listing.listing_format.value if listing.listing_format else None,
        "end_time": _iso(listing.end_time),
        "image_urls": json.dumps(listing.image_urls),
        "image_phash": listing.image_phash,
        "first_seen_at": _iso(listing.first_seen_at),
        "last_seen_at": _iso(listing.last_seen_at),
        "stage_reached": listing.stage_reached.value,
        "haiku_verdict": listing.haiku_verdict.value if listing.haiku_verdict else None,
        "vision_json": json.dumps(asdict(listing.vision)) if listing.vision else None,
        "confidence": listing.confidence,
        "attributed_artist": listing.attributed_artist,
        "relisted_from": listing.relisted_from,
        "went_hot_at": _iso(listing.went_hot_at),
        "alerted_at": _iso(listing.alerted_at),
    }


def get_by_source_id(conn: sqlite3.Connection, source: str, source_listing_id: str) -> Listing | None:
    row = conn.execute(
        "SELECT * FROM listings WHERE source = ? AND source_listing_id = ?",
        (source, source_listing_id),
    ).fetchone()
    return _row_to_listing(row) if row else None


def all_phashes(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """(listing id, phash hex) for every stored listing with a phash — relist detection."""
    return [
        (row["id"], row["image_phash"])
        for row in conn.execute("SELECT id, image_phash FROM listings WHERE image_phash IS NOT NULL")
    ]


def insert_listing(conn: sqlite3.Connection, listing: Listing) -> Listing:
    params = _listing_params(listing)
    columns = ", ".join(params)
    placeholders = ", ".join(f":{k}" for k in params)
    cur = conn.execute(f"INSERT INTO listings ({columns}) VALUES ({placeholders})", params)
    conn.commit()
    listing.id = cur.lastrowid
    return listing


def touch_sighting(conn: sqlite3.Connection, listing_id: int, seen_at: datetime, fresh: Listing) -> None:
    """Re-sighting of a known listing: refresh volatile fields only. Stage,
    verdicts, and vision results are never overwritten by a re-fetch."""
    conn.execute(
        """UPDATE listings
           SET last_seen_at = ?, price_value = ?, price_currency = ?, end_time = ?, url = ?
           WHERE id = ?""",
        (
            _iso(seen_at),
            fresh.price_value,
            fresh.price_currency,
            _iso(fresh.end_time),
            fresh.url,
            listing_id,
        ),
    )
    conn.commit()


def update_listing(conn: sqlite3.Connection, listing: Listing) -> None:
    """Write back pipeline-mutable fields after gating/vision/hot/alert stamps."""
    if listing.id is None:
        raise ValueError("update_listing requires a persisted listing (id set)")
    conn.execute(
        """UPDATE listings
           SET stage_reached = ?, haiku_verdict = ?, vision_json = ?, confidence = ?,
               attributed_artist = ?, relisted_from = ?, went_hot_at = ?, alerted_at = ?,
               image_phash = ?
           WHERE id = ?""",
        (
            listing.stage_reached.value,
            listing.haiku_verdict.value if listing.haiku_verdict else None,
            json.dumps(asdict(listing.vision)) if listing.vision else None,
            listing.confidence,
            listing.attributed_artist,
            listing.relisted_from,
            _iso(listing.went_hot_at),
            _iso(listing.alerted_at),
            listing.image_phash,
            listing.id,
        ),
    )
    conn.commit()


def pending_vision(conn: sqlite3.Connection, limit: int) -> list[Listing]:
    """Gate survivors not yet vision-scored — the backfill drain queue.

    Highest-confidence-gate first (brief section 6): Haiku RELEVANT, then
    UNSURE, then generic-query survivors (no verdict), oldest first within
    each band.
    """
    rows = conn.execute(
        """SELECT * FROM listings
           WHERE stage_reached = ?
           ORDER BY CASE haiku_verdict
                        WHEN 'RELEVANT' THEN 0
                        WHEN 'UNSURE' THEN 1
                        ELSE 2
                    END,
                    first_seen_at
           LIMIT ?""",
        (Stage.GATE_SURVIVOR.value, limit),
    ).fetchall()
    return [_row_to_listing(r) for r in rows]


def feed_candidates(conn: sqlite3.Connection) -> list[Listing]:
    """Everything that could appear on the page: vision-scored listings plus
    relistings (which inherited their original's verdict)."""
    rows = conn.execute(
        "SELECT * FROM listings WHERE stage_reached IN (?, ?) AND confidence IS NOT NULL",
        (Stage.VISION_SCORED.value, Stage.RELISTED.value),
    ).fetchall()
    return [_row_to_listing(r) for r in rows]


def original_price(conn: sqlite3.Connection, listing_id: int) -> float | None:
    row = conn.execute("SELECT price_value FROM listings WHERE id = ?", (listing_id,)).fetchone()
    return row["price_value"] if row else None


def record_run(conn: sqlite3.Connection, stats: RunStats) -> RunStats:
    cur = conn.execute(
        """INSERT INTO runs (started_at, finished_at, fetched_count, new_count,
                             vision_call_count, est_cost_usd, errors)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            _iso(stats.started_at),
            _iso(stats.finished_at),
            stats.fetched_count,
            stats.new_count,
            stats.vision_call_count,
            stats.est_cost_usd,
            json.dumps(stats.errors),
        ),
    )
    conn.commit()
    stats.id = cur.lastrowid
    return stats


def last_run(conn: sqlite3.Connection) -> RunStats | None:
    row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    return RunStats(
        id=row["id"],
        started_at=_dt(row["started_at"]),
        finished_at=_dt(row["finished_at"]),
        fetched_count=row["fetched_count"],
        new_count=row["new_count"],
        vision_call_count=row["vision_call_count"],
        est_cost_usd=row["est_cost_usd"],
        errors=json.loads(row["errors"]),
    )


def total_vision_calls(conn: sqlite3.Connection) -> int:
    """Vision calls across all runs ever. Zero → still in initial backfill."""
    row = conn.execute("SELECT COALESCE(SUM(vision_call_count), 0) FROM runs").fetchone()
    return int(row[0])


def month_spend_usd(conn: sqlite3.Connection, now: datetime) -> float:
    """Sum est_cost_usd over runs in `now`'s calendar month — feed footer."""
    prefix = now.strftime("%Y-%m")
    row = conn.execute(
        "SELECT COALESCE(SUM(est_cost_usd), 0) FROM runs WHERE started_at LIKE ?",
        (f"{prefix}%",),
    ).fetchone()
    return float(row[0])
