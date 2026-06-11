"""Static feed page generator → docs/index.html, served by GitHub Pages.

Mobile-first, zero client-side JS required to read it. Sections in order
(brief section 3 "Publish"):
1. Hot — BIN, confidence ≥ hot_alert.min_confidence, first seen <24h ago
2. High-confidence attributions — any format
3. Probable sketches, unattributed
4. Ending soon — previously surfaced auctions ending <48h

Card: thumbnail, title, price + format, end time, confidence, attributed
artist, signals one-liner, direct eBay link, first-seen timestamp.
Footer: last run time, run stats, estimated model spend this month.

Relistings are suppressed unless price dropped >20% vs the original.
If the abort guard tripped (>100 new survivors), a warning banner renders
at the top instead of fresh vision results.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import Watchlist
from .models import Listing, RunStats


def select_sections(conn: sqlite3.Connection, watchlist: Watchlist) -> dict[str, list[Listing]]:
    """Query the DB into the four feed sections; a listing appears in its
    highest-priority section only."""
    raise NotImplementedError  # step 4


def mark_newly_hot(conn: sqlite3.Connection, hot: list[Listing]) -> list[Listing]:
    """Stamp went_hot_at on first entry into Hot; returns the newly-hot subset
    (the only listings push_alerts may notify about)."""
    raise NotImplementedError  # step 4


def render(sections: dict[str, list[Listing]], stats: RunStats, month_spend: float, banner: str | None) -> str:
    raise NotImplementedError  # step 4


def publish(html: str, site_dir: Path) -> Path:
    """Write docs/index.html (+ .nojekyll). The Actions workflow commits it."""
    raise NotImplementedError  # step 4
