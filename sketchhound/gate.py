"""Gating: decides which new listings earn a (paid) vision call.

Order is load-bearing (brief section 3):
1. Negative-keyword kill list — free, runs on everything first.
2. Artist-query results → Haiku text triage. RELEVANT and UNSURE proceed
   (recall over precision); IRRELEVANT is rejected.
3. Generic-query results → NO text gate, straight to vision. Their titles
   carry no signal by construction; the judgment lives in the image.

Vision must never see a negative-keyword or Haiku-rejected listing
(acceptance criterion 4).
"""

from __future__ import annotations

from .config import Watchlist
from .models import HaikuVerdict, Listing


def kill_by_negative_keywords(listings: list[Listing], negative_keywords: list[str]) -> tuple[list[Listing], list[Listing]]:
    """Case-insensitive word match against title + description snippet.

    Returns (survivors, killed). Killed get stage_reached=NEGATIVE_KEYWORD_REJECT.
    """
    raise NotImplementedError  # step 3


def haiku_triage(listing: Listing, anthropic_client) -> HaikuVerdict:
    """claude-haiku-4-5, title + snippet only. One-word verdict; anything
    unparseable is treated as UNSURE (which proceeds)."""
    raise NotImplementedError  # step 3


def gate(
    listings_by_query_kind: dict[str, list[Listing]],  # {"artist": [...], "generic": [...]}
    watchlist: Watchlist,
    anthropic_client,
) -> tuple[list[Listing], list[Listing]]:
    """Returns (survivors bound for vision, rejects with stage_reached set)."""
    raise NotImplementedError  # step 3
