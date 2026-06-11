"""ntfy.sh push for newly-Hot listings. No accounts: the friend subscribes to
one long random topic name in the ntfy app.

Idempotency is the hard requirement: alerted_at is stamped in the DB the
moment a POST succeeds, and anything with alerted_at set is never POSTed
again — re-runs and crashes can never duplicate an alert.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import persist
from .http_util import request_with_retry
from .models import Listing

NTFY_BASE_URL = "https://ntfy.sh"


def _notification(listing: Listing) -> tuple[dict, str]:
    price = (
        f"${listing.price_value:,.0f} {listing.price_currency or ''}".strip()
        if listing.price_value is not None
        else "price unknown"
    )
    confidence = f"{(listing.confidence or 0) * 100:.0f}%"
    artist = listing.attributed_artist or "unknown"
    headers = {
        "Title": "SketchHound: hot find on eBay",
        "Priority": "high",
        "Tags": "art,rotating_light",
        "Click": listing.url,
    }
    body = f"{listing.title}\n{price} Buy It Now · confidence {confidence} · {artist}"
    return headers, body


def push_hot_alerts(
    conn: sqlite3.Connection,
    newly_hot: list[Listing],
    topic: str,
    now: datetime,
) -> tuple[int, list[str]]:
    """POST one notification per newly-hot, not-yet-alerted listing.

    Payload: title, price, confidence; eBay URL as the Click action.
    Returns (alerts sent, errors). A failed POST leaves alerted_at unset so
    the next run retries; a sent alert is stamped immediately so it can
    never fire twice. One failure never blocks the other alerts.
    """
    if not topic:
        return 0, ["ntfy_topic not configured; hot alerts skipped"] if newly_hot else []
    sent = 0
    errors: list[str] = []
    for listing in newly_hot:
        if listing.alerted_at is not None:
            continue  # idempotency guard
        headers, body = _notification(listing)
        try:
            request_with_retry(
                "POST",
                f"{NTFY_BASE_URL}/{topic}",
                headers=headers,
                data=body.encode("utf-8"),
            )
        except Exception as exc:
            errors.append(f"alert {listing.source_listing_id}: {exc}")
            continue
        listing.alerted_at = now
        persist.update_listing(conn, listing)
        sent += 1
    return sent, errors
