"""ntfy.sh push for newly-Hot listings. No accounts: the friend subscribes to
one long random topic name in the ntfy app.

Idempotency is the hard requirement: alerted_at is stamped in the DB the
moment a POST succeeds, and anything with alerted_at set is never POSTed
again — re-runs and crashes can never duplicate an alert.
"""

from __future__ import annotations

import sqlite3

from .models import Listing

NTFY_BASE_URL = "https://ntfy.sh"


def push_hot_alerts(conn: sqlite3.Connection, newly_hot: list[Listing], topic: str) -> int:
    """POST one notification per newly-hot, not-yet-alerted listing.

    Payload: title, price, confidence; eBay URL as the Click action.
    Returns the number of alerts actually sent.
    """
    raise NotImplementedError  # step 5
