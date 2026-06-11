"""ntfy.sh push for Hot listings. No accounts: the friend subscribes to one
long random topic name in the ntfy app.

Idempotency is the hard requirement: alerted_at is stamped in the DB the
moment a POST succeeds, and anything with alerted_at set is never POSTed
again. The queue is driven off the DB (went_hot_at set, alerted_at null),
so a failed or rate-limited POST retries on the next run.

Storm control: more than ALERT_DIGEST_THRESHOLD pending alerts collapse into
ONE digest notification (first live backfill went 71-for-71 into ntfy's rate
limiter and would have buzzed a phone 71 times). Individual alerts are spaced
a second apart to stay under ntfy's burst limit.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime

from . import persist
from .http_util import request_with_retry
from .models import Listing

NTFY_BASE_URL = "https://ntfy.sh"
ALERT_DIGEST_THRESHOLD = 5
SECONDS_BETWEEN_ALERTS = 1.0


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


def _digest_notification(count: int, feed_url: str | None) -> tuple[dict, str]:
    headers = {
        "Title": f"SketchHound: {count} hot finds",
        "Priority": "high",
        "Tags": "art,sparkles",
    }
    if feed_url:
        headers["Click"] = feed_url
    body = f"{count} hot Buy It Now finds are on the feed right now — too many to ping one by one."
    return headers, body


def push_hot_alerts(
    conn: sqlite3.Connection,
    topic: str,
    now: datetime,
    feed_url: str | None = None,
) -> tuple[int, list[str]]:
    """Alert every hot, not-yet-alerted listing (from the DB, not the caller).

    ≤ALERT_DIGEST_THRESHOLD pending → one notification each (title, price,
    confidence, eBay URL as Click action). More → a single digest pointing at
    the feed. Returns (notifications sent, errors). alerted_at is stamped per
    listing on success only, so nothing can ever alert twice and failures
    retry next run.
    """
    pending = persist.unalerted_hot(conn)
    if not pending:
        return 0, []
    if not topic:
        return 0, ["ntfy_topic not configured; hot alerts skipped"]

    url = f"{NTFY_BASE_URL}/{topic}"

    if len(pending) > ALERT_DIGEST_THRESHOLD:
        headers, body = _digest_notification(len(pending), feed_url)
        try:
            request_with_retry("POST", url, headers=headers, data=body.encode("utf-8"))
        except Exception as exc:
            return 0, [f"digest alert: {exc}"]
        for listing in pending:
            listing.alerted_at = now
            persist.update_listing(conn, listing)
        return 1, []

    sent = 0
    errors: list[str] = []
    for i, listing in enumerate(pending):
        if i > 0:
            time.sleep(SECONDS_BETWEEN_ALERTS)
        headers, body = _notification(listing)
        try:
            request_with_retry("POST", url, headers=headers, data=body.encode("utf-8"))
        except Exception as exc:
            errors.append(f"alert {listing.source_listing_id}: {exc}")
            continue
        listing.alerted_at = now
        persist.update_listing(conn, listing)
        sent += 1
    return sent, errors
