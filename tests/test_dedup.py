"""Dedup acceptance proof (brief section 7):
- same listing across two runs → one row, refreshed sighting
- pHash relist under a new item ID → relisted_from set, not treated as new
"""

from datetime import timedelta

from sketchhound import dedup, persist
from sketchhound.models import Stage

from .conftest import NOW, make_image_bytes, make_listing

IMAGES = {
    "https://img.example/halves.png": make_image_bytes("halves", "PNG"),
    "https://img.example/halves-relist.jpg": make_image_bytes("halves", "JPEG", quality=60),
    "https://img.example/checker.png": make_image_bytes("checker", "PNG"),
    "https://img.example/diag.png": make_image_bytes("diag", "PNG"),
}


def fetch_image(url: str) -> bytes:
    return IMAGES[url]


def run_dedup(conn, listings, now=NOW):
    return dedup.dedup(conn, listings, now, fetch_image=fetch_image)


def count_rows(conn):
    return conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]


def test_phash_distances_behave():
    a = dedup.compute_phash(IMAGES["https://img.example/halves.png"])
    b = dedup.compute_phash(IMAGES["https://img.example/halves-relist.jpg"])
    c = dedup.compute_phash(IMAGES["https://img.example/checker.png"])
    assert dedup.hamming(a, b) <= 5, "re-encoded image must register as the same artwork"
    assert dedup.hamming(a, c) > 5, "different artwork must not match"


def test_same_listing_two_runs_one_row(conn):
    first = make_listing(item_id="v1|111|0", image_url="https://img.example/halves.png")
    assert len(run_dedup(conn, [first])) == 1
    assert count_rows(conn) == 1

    later = NOW + timedelta(hours=1)
    resight = make_listing(item_id="v1|111|0", image_url="https://img.example/halves.png", price=199.0)
    assert run_dedup(conn, [resight], now=later) == []
    assert count_rows(conn) == 1

    stored = persist.get_by_source_id(conn, "ebay", "v1|111|0")
    assert stored.last_seen_at == later
    assert stored.first_seen_at == NOW
    assert stored.price_value == 199.0  # volatile field refreshed
    assert stored.stage_reached == Stage.FETCHED  # pipeline state untouched


def test_relisting_detected_by_phash(conn):
    original = make_listing(item_id="v1|111|0", image_url="https://img.example/halves.png")
    run_dedup(conn, [original])

    relist = make_listing(item_id="v1|222|0", image_url="https://img.example/halves-relist.jpg")
    assert run_dedup(conn, [relist]) == []  # not new: vision must never see it
    assert count_rows(conn) == 2

    stored = persist.get_by_source_id(conn, "ebay", "v1|222|0")
    assert stored.stage_reached == Stage.RELISTED
    assert stored.relisted_from == original.id


def test_relisting_inherits_vision_verdict(conn):
    original = make_listing(item_id="v1|111|0", image_url="https://img.example/halves.png")
    run_dedup(conn, [original])
    conn.execute(
        "UPDATE listings SET confidence = 0.9, attributed_artist = 'Edith Head' WHERE id = ?",
        (original.id,),
    )

    relist = make_listing(item_id="v1|222|0", image_url="https://img.example/halves-relist.jpg")
    run_dedup(conn, [relist])
    stored = persist.get_by_source_id(conn, "ebay", "v1|222|0")
    assert stored.confidence == 0.9
    assert stored.attributed_artist == "Edith Head"


def test_different_artwork_is_new(conn):
    run_dedup(conn, [make_listing(item_id="v1|111|0", image_url="https://img.example/halves.png")])
    new = run_dedup(conn, [make_listing(item_id="v1|333|0", image_url="https://img.example/checker.png")])
    assert len(new) == 1
    assert new[0].stage_reached == Stage.FETCHED
    assert new[0].relisted_from is None


def test_same_item_from_two_queries_one_run(conn):
    a = make_listing(item_id="v1|444|0", image_url="https://img.example/diag.png")
    b = make_listing(item_id="v1|444|0", image_url="https://img.example/diag.png")
    assert len(run_dedup(conn, [a, b])) == 1
    assert count_rows(conn) == 1


def test_intra_run_relist_detected(conn):
    same_art = [
        make_listing(item_id="v1|555|0", image_url="https://img.example/halves.png"),
        make_listing(item_id="v1|556|0", image_url="https://img.example/halves-relist.jpg"),
    ]
    new = run_dedup(conn, same_art)
    assert [l.source_listing_id for l in new] == ["v1|555|0"]


def test_image_fetch_failure_still_new(conn):
    def broken_fetch(url):
        raise OSError("image host down")

    new = dedup.dedup(conn, [make_listing(item_id="v1|666|0")], NOW, fetch_image=broken_fetch)
    assert len(new) == 1
    assert new[0].image_phash is None
