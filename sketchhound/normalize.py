"""Raw eBay item_summary dicts → the single Listing schema."""

from __future__ import annotations

from .models import Listing


def normalize_ebay_item(raw: dict) -> Listing:
    """Map one Browse API item_summary to a Listing.

    - buyingOptions containing FIXED_PRICE → buy_it_now, AUCTION → auction
      (FIXED_PRICE wins when both present: it's actionable now, which is what
      the Hot section cares about)
    - itemEndDate → end_time (UTC)
    - image.imageUrl first, then additionalImages, deduped, order preserved
    """
    raise NotImplementedError  # step 2
