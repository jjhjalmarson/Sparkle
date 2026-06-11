"""Raw eBay item_summary dicts → the single Listing schema."""

from __future__ import annotations

from datetime import datetime

from .models import Listing, ListingFormat


def _parse_end_time(raw: dict) -> datetime | None:
    value = raw.get("itemEndDate")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_format(raw: dict) -> ListingFormat | None:
    # FIXED_PRICE wins when both present: it's actionable right now, which is
    # what the Hot section cares about.
    options = raw.get("buyingOptions") or []
    if "FIXED_PRICE" in options:
        return ListingFormat.BUY_IT_NOW
    if "AUCTION" in options:
        return ListingFormat.AUCTION
    return None


def _parse_price(raw: dict) -> tuple[float | None, str | None]:
    # Auctions report currentBidPrice; fixed-price listings report price.
    price = raw.get("currentBidPrice") or raw.get("price") or {}
    try:
        return float(price["value"]), price.get("currency")
    except (KeyError, TypeError, ValueError):
        return None, None


def _parse_images(raw: dict) -> list[str]:
    urls: list[str] = []
    primary = (raw.get("image") or {}).get("imageUrl")
    if primary:
        urls.append(primary)
    for extra in raw.get("additionalImages") or []:
        url = extra.get("imageUrl")
        if url and url not in urls:
            urls.append(url)
    return urls


def normalize_ebay_item(raw: dict) -> Listing:
    """Map one Browse API item_summary to a Listing. first_seen_at/last_seen_at
    are stamped by dedup — provenance, not parse time."""
    price_value, price_currency = _parse_price(raw)
    return Listing(
        source="ebay",
        source_listing_id=raw["itemId"],
        url=raw["itemWebUrl"],
        title=raw["title"],
        description_snippet=raw.get("shortDescription"),
        price_value=price_value,
        price_currency=price_currency,
        listing_format=_parse_format(raw),
        end_time=_parse_end_time(raw),
        image_urls=_parse_images(raw),
    )
