"""Raw Browse API item_summary → Listing mapping."""

from datetime import datetime, timezone

from sketchhound.models import ListingFormat
from sketchhound.normalize import normalize_ebay_item

FULL_ITEM = {
    "itemId": "v1|123456|0",
    "title": "Original Edith Head costume sketch gouache",
    "shortDescription": "Signed lower right, Paramount stamp verso.",
    "price": {"value": "450.00", "currency": "USD"},
    "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"],
    "itemEndDate": "2026-06-15T18:30:00.000Z",
    "itemWebUrl": "https://www.ebay.com/itm/123456",
    "image": {"imageUrl": "https://i.ebayimg.com/images/g/abc/s-l500.jpg"},
    "additionalImages": [
        {"imageUrl": "https://i.ebayimg.com/images/g/def/s-l500.jpg"},
        {"imageUrl": "https://i.ebayimg.com/images/g/abc/s-l500.jpg"},  # dup of primary
    ],
}


def test_full_item():
    listing = normalize_ebay_item(FULL_ITEM)
    assert listing.source == "ebay"
    assert listing.source_listing_id == "v1|123456|0"
    assert listing.title.startswith("Original Edith Head")
    assert listing.price_value == 450.0
    assert listing.price_currency == "USD"
    assert listing.listing_format == ListingFormat.BUY_IT_NOW
    assert listing.end_time == datetime(2026, 6, 15, 18, 30, tzinfo=timezone.utc)
    assert listing.image_urls == [
        "https://i.ebayimg.com/images/g/abc/s-l500.jpg",
        "https://i.ebayimg.com/images/g/def/s-l500.jpg",
    ]
    assert listing.first_seen_at is None  # stamped by dedup, not normalize


def test_auction_uses_current_bid():
    item = dict(FULL_ITEM, buyingOptions=["AUCTION"], currentBidPrice={"value": "26.00", "currency": "USD"})
    listing = normalize_ebay_item(item)
    assert listing.listing_format == ListingFormat.AUCTION
    assert listing.price_value == 26.0


def test_dual_format_classifies_as_bin():
    item = dict(FULL_ITEM, buyingOptions=["AUCTION", "FIXED_PRICE"])
    assert normalize_ebay_item(item).listing_format == ListingFormat.BUY_IT_NOW


def test_minimal_item():
    item = {"itemId": "v1|9|0", "title": "sketch", "itemWebUrl": "https://www.ebay.com/itm/9"}
    listing = normalize_ebay_item(item)
    assert listing.price_value is None
    assert listing.listing_format is None
    assert listing.end_time is None
    assert listing.image_urls == []
