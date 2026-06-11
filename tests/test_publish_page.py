"""Feed page: section selection, relist suppression, hot stamping, render."""

from datetime import timedelta

from sketchhound import persist, publish_page
from sketchhound.config import HotAlertConfig, Watchlist
from sketchhound.models import ListingFormat, RunStats, Stage, VisionResult

from .conftest import NOW, make_listing, persisted

WATCHLIST = Watchlist(hot_alert=HotAlertConfig(min_price=500, min_confidence=0.7))


def scored(
    conn,
    *,
    conf=0.85,
    artist="unknown",
    attr_conf=0.0,
    fmt=ListingFormat.BUY_IT_NOW,
    price=500.0,
    seen_hours_ago=1,
    ends_in_hours=72.0,
    is_sketch=True,
    stage=Stage.VISION_SCORED,
    **overrides,
):
    listing = make_listing(price=price, fmt=fmt, **overrides)
    listing.end_time = NOW + timedelta(hours=ends_in_hours)
    listing.vision = VisionResult(
        is_costume_design_sketch=is_sketch,
        confidence=conf,
        attributed_artist=artist,
        attribution_confidence=attr_conf,
        signals=["signature lower right"],
        era_estimate="1950s",
        red_flags=[],
        summary="Test listing.",
    )
    listing.confidence = conf
    listing.attributed_artist = artist
    return persisted(conn, listing, stage=stage, seen_at=NOW - timedelta(hours=seen_hours_ago))


def section_of(sections, listing):
    for name, items in sections.items():
        if any(l.id == listing.id for l in items):
            return name
    return None


def test_hot_criteria(conn):
    """Hot = high-priced: BIN, confident, at or above min_price, fresh."""
    hot = scored(conn, conf=0.8, price=1200.0)
    too_old = scored(conn, conf=0.8, price=1200.0, seen_hours_ago=30)
    too_cheap = scored(conn, conf=0.9, price=100.0)
    low_conf = scored(conn, conf=0.6, price=1200.0)
    auction = scored(conn, conf=0.9, price=1200.0, fmt=ListingFormat.AUCTION)

    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    assert section_of(sections, hot) == "hot"
    assert section_of(sections, too_old) == "probable"
    assert section_of(sections, too_cheap) == "probable"
    assert section_of(sections, low_conf) == "probable"
    assert section_of(sections, auction) == "probable"


def test_each_listing_appears_once(conn):
    star = scored(conn, conf=0.9, price=1500.0, artist="Edith Head", attr_conf=0.8)  # hot AND attributable
    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    assert section_of(sections, star) == "hot"
    assert sum(len(v) for v in sections.values()) == 1


def test_attribution_split(conn):
    attributed = scored(conn, conf=0.8, artist="Bob Mackie", attr_conf=0.7, fmt=ListingFormat.AUCTION)
    weak_attr = scored(conn, conf=0.6, artist="Bob Mackie", attr_conf=0.3, fmt=ListingFormat.AUCTION)
    unknown = scored(conn, conf=0.6, fmt=ListingFormat.AUCTION)

    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    assert section_of(sections, attributed) == "attributed"
    assert section_of(sections, weak_attr) == "probable"
    assert section_of(sections, unknown) == "probable"


def test_below_floor_and_non_sketch_hidden(conn):
    low = scored(conn, conf=0.4)
    not_sketch = scored(conn, conf=0.9, is_sketch=False)

    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    assert section_of(sections, low) is None
    assert section_of(sections, not_sketch) is None


def test_ending_soon_claims_qualifying_auctions(conn):
    closing = scored(conn, conf=0.8, artist="Travilla", attr_conf=0.8, fmt=ListingFormat.AUCTION, ends_in_hours=24)
    not_closing = scored(conn, conf=0.8, artist="Travilla", attr_conf=0.8, fmt=ListingFormat.AUCTION, ends_in_hours=60)
    junk_closing = scored(conn, conf=0.3, fmt=ListingFormat.AUCTION, ends_in_hours=24)  # never surfaced

    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    assert section_of(sections, closing) == "ending_soon"
    assert section_of(sections, not_closing) == "attributed"
    assert section_of(sections, junk_closing) is None


def test_ended_listings_leave_the_feed(conn):
    ended = scored(conn, conf=0.9, ends_in_hours=-2)
    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    assert section_of(sections, ended) is None


def test_relist_suppressed_unless_price_dropped(conn):
    original = scored(conn, conf=0.9, price=2000.0)
    big_drop = scored(conn, conf=0.9, price=1400.0, stage=Stage.RELISTED, relisted_from=original.id)
    small_drop = scored(conn, conf=0.9, price=1800.0, stage=Stage.RELISTED, relisted_from=original.id)

    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    assert section_of(sections, big_drop) == "hot"      # >20% drop resurfaces
    assert section_of(sections, small_drop) is None     # 10% drop stays hidden


def test_mark_newly_hot_stamps_once(conn):
    listing = scored(conn, conf=0.8, price=900.0)
    sections = publish_page.select_sections(conn, WATCHLIST, NOW)

    first = publish_page.mark_newly_hot(conn, sections["hot"], NOW)
    assert [l.id for l in first] == [listing.id]

    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    assert publish_page.mark_newly_hot(conn, sections["hot"], NOW) == []  # already stamped

    stored = persist.get_by_source_id(conn, "ebay", listing.source_listing_id)
    assert stored.went_hot_at == NOW


def test_render_full_page(conn):
    hot = scored(conn, conf=0.8, price=1450.0, artist="Edith Head", attr_conf=0.9, title="Edith Head Paramount gouache")
    stats = RunStats(started_at=NOW, finished_at=NOW, fetched_count=120, new_count=4, vision_call_count=3)

    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    html = publish_page.render(sections, stats, month_spend=1.23, banner=None)

    assert "Edith Head Paramount gouache" in html
    assert hot.url in html
    assert hot.image_urls[0] in html
    assert "Sketch confidence 80%" in html
    assert "signature lower right" in html
    assert "$1.23" in html
    assert "120 fetched" in html
    assert "viewport" in html  # mobile meta present
    assert "class=\"banner\"" not in html  # no banner when none passed


def test_render_sort_filter_controls(conn):
    scored(conn, conf=0.8, price=1450.0, artist="Edith Head", attr_conf=0.9)
    scored(conn, conf=0.6, price=85.0, fmt=ListingFormat.AUCTION)
    stats = RunStats(started_at=NOW, finished_at=NOW)

    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    html = publish_page.render(sections, stats, month_spend=0.0, banner=None)

    # Controls present with all four sort modes and the artist filter.
    assert 'id="sort"' in html and 'id="artist-filter"' in html
    assert 'value="newest"' in html
    assert 'value="price"' in html
    assert 'value="artist"' in html
    assert '<option value="edith head">Edith Head</option>' in html
    # Cards carry the data attributes the script sorts on.
    assert 'data-price="1450.0"' in html
    assert 'data-artist="edith head"' in html
    assert 'data-artist="unknown"' in html
    assert f'data-seen="{(NOW - timedelta(hours=1)).isoformat()}"' in html


def test_render_banner_and_empty_sections(conn):
    stats = RunStats(started_at=NOW, finished_at=NOW)
    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    html = publish_page.render(sections, stats, month_spend=0.0, banner="Run aborted: filter regression")

    assert "Run aborted: filter regression" in html
    assert html.count("Nothing right now.") == 4


def test_publish_writes_site(tmp_path):
    out = publish_page.publish("<html>x</html>", tmp_path / "docs")
    assert out.read_text(encoding="utf-8") == "<html>x</html>"
    assert (tmp_path / "docs" / ".nojekyll").exists()
