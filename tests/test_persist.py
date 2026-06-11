"""Persistence: roundtrips, the backfill drain queue ordering, monthly spend."""

from datetime import datetime, timedelta, timezone

from sketchhound import persist
from sketchhound.models import HaikuVerdict, RunStats, Stage, VisionResult

from .conftest import NOW, make_listing, persisted


def test_listing_roundtrip_with_vision(conn):
    listing = make_listing()
    listing.vision = VisionResult(
        is_costume_design_sketch=True,
        confidence=0.85,
        attributed_artist="Edith Head",
        attribution_confidence=0.6,
        signals=["signature lower right", "Paramount wardrobe stamp"],
        era_estimate="1950s",
        red_flags=[],
        summary="Probable Edith Head Paramount-era costume sketch.",
    )
    listing.confidence = 0.85
    listing.attributed_artist = "Edith Head"
    listing.haiku_verdict = HaikuVerdict.RELEVANT
    persisted(conn, listing, stage=Stage.VISION_SCORED)

    stored = persist.get_by_source_id(conn, "ebay", listing.source_listing_id)
    assert stored.vision == listing.vision
    assert stored.stage_reached == Stage.VISION_SCORED
    assert stored.haiku_verdict == HaikuVerdict.RELEVANT
    assert stored.end_time == listing.end_time
    assert stored.image_urls == listing.image_urls


def test_update_listing_writes_pipeline_fields(conn):
    listing = persisted(conn, make_listing())
    listing.stage_reached = Stage.GATE_SURVIVOR
    listing.haiku_verdict = HaikuVerdict.UNSURE
    persist.update_listing(conn, listing)

    stored = persist.get_by_source_id(conn, "ebay", listing.source_listing_id)
    assert stored.stage_reached == Stage.GATE_SURVIVOR
    assert stored.haiku_verdict == HaikuVerdict.UNSURE


def test_pending_vision_orders_by_gate_confidence(conn):
    generic = persisted(conn, make_listing(), stage=Stage.GATE_SURVIVOR, seen_at=NOW)
    unsure = persisted(
        conn,
        make_listing(haiku_verdict=HaikuVerdict.UNSURE),
        stage=Stage.GATE_SURVIVOR,
        seen_at=NOW + timedelta(minutes=1),
    )
    relevant = persisted(
        conn,
        make_listing(haiku_verdict=HaikuVerdict.RELEVANT),
        stage=Stage.GATE_SURVIVOR,
        seen_at=NOW + timedelta(minutes=2),
    )
    persisted(conn, make_listing(), stage=Stage.VISION_SCORED)  # not pending

    queue = persist.pending_vision(conn, limit=10)
    assert [l.id for l in queue] == [relevant.id, unsure.id, generic.id]
    assert len(persist.pending_vision(conn, limit=2)) == 2


def test_month_spend(conn):
    persist.record_run(conn, RunStats(started_at=NOW, finished_at=NOW, est_cost_usd=1.25))
    persist.record_run(conn, RunStats(started_at=NOW + timedelta(days=1), finished_at=NOW, est_cost_usd=0.75))
    last_month = datetime(2026, 5, 10, tzinfo=timezone.utc)
    persist.record_run(conn, RunStats(started_at=last_month, finished_at=last_month, est_cost_usd=9.99))

    assert persist.month_spend_usd(conn, NOW) == 2.0


def test_record_and_last_run(conn):
    stats = RunStats(started_at=NOW, finished_at=NOW, fetched_count=42, new_count=3, errors=["fetch 'x': boom"])
    persist.record_run(conn, stats)
    stored = persist.last_run(conn)
    assert stored.fetched_count == 42
    assert stored.errors == ["fetch 'x': boom"]
