"""ntfy push acceptance proof (brief section 7): an alert fires once and only
once per hot listing — across calls, runs, and failures.
"""

import responses

from sketchhound import persist, publish_page, push_alerts
from sketchhound.models import Stage

from .conftest import NOW, make_listing, persisted

TOPIC = "sketchhound-test-topic"
NTFY_URL = f"{push_alerts.NTFY_BASE_URL}/{TOPIC}"


def hot_listing(conn, **overrides):
    listing = make_listing(
        title="Edith Head original gouache sketch",
        price=450.0,
        confidence=0.85,
        attributed_artist="Edith Head",
        went_hot_at=NOW,
        **overrides,
    )
    return persisted(conn, listing, stage=Stage.VISION_SCORED)


@responses.activate
def test_alert_fires_once_and_only_once(conn):
    responses.add(responses.POST, NTFY_URL, status=200)
    listing = hot_listing(conn)

    sent, errors = push_alerts.push_hot_alerts(conn, [listing], TOPIC, NOW)
    assert (sent, errors) == (1, [])
    assert len(responses.calls) == 1

    # Same listing pushed again in the same process — guard blocks it.
    sent, _ = push_alerts.push_hot_alerts(conn, [listing], TOPIC, NOW)
    assert sent == 0

    # Re-run: fresh object from the DB — alerted_at survives persistence.
    stored = persist.get_by_source_id(conn, "ebay", listing.source_listing_id)
    assert stored.alerted_at == NOW
    sent, _ = push_alerts.push_hot_alerts(conn, [stored], TOPIC, NOW)
    assert sent == 0
    assert len(responses.calls) == 1


@responses.activate
def test_alert_payload(conn):
    responses.add(responses.POST, NTFY_URL, status=200)
    listing = hot_listing(conn)

    push_alerts.push_hot_alerts(conn, [listing], TOPIC, NOW)

    request = responses.calls[0].request
    assert request.headers["Click"] == listing.url
    assert request.headers["Priority"] == "high"
    body = request.body.decode("utf-8")
    assert "Edith Head original gouache sketch" in body
    assert "$450" in body
    assert "confidence 85%" in body


@responses.activate
def test_failed_post_retried_next_run(conn):
    for _ in range(3):  # exhaust the HTTP retry budget
        responses.add(responses.POST, NTFY_URL, status=500)
    listing = hot_listing(conn)

    sent, errors = push_alerts.push_hot_alerts(conn, [listing], TOPIC, NOW)
    assert sent == 0
    assert len(errors) == 1
    stored = persist.get_by_source_id(conn, "ebay", listing.source_listing_id)
    assert stored.alerted_at is None  # next run will retry

    responses.reset()
    responses.add(responses.POST, NTFY_URL, status=200)
    sent, errors = push_alerts.push_hot_alerts(conn, [stored], TOPIC, NOW)
    assert (sent, errors) == (1, [])


@responses.activate
def test_one_failure_does_not_block_others(conn):
    for _ in range(3):
        responses.add(responses.POST, NTFY_URL, status=500)
    responses.add(responses.POST, NTFY_URL, status=200)
    first, second = hot_listing(conn), hot_listing(conn)

    sent, errors = push_alerts.push_hot_alerts(conn, [first, second], TOPIC, NOW)
    assert sent == 1
    assert len(errors) == 1


def test_missing_topic_sends_nothing(conn):
    listing = hot_listing(conn)
    sent, errors = push_alerts.push_hot_alerts(conn, [listing], "", NOW)
    assert sent == 0
    assert errors  # surfaced in run errors instead of silently dropped


@responses.activate
def test_end_to_end_newly_hot_to_alert(conn):
    """mark_newly_hot → push: the full step-4/5 handoff alerts exactly once."""
    from datetime import timedelta

    from sketchhound.config import HotAlertConfig, Watchlist
    from sketchhound.models import VisionResult

    responses.add(responses.POST, NTFY_URL, status=200)
    watchlist = Watchlist(hot_alert=HotAlertConfig(max_price=2000, min_confidence=0.7))

    listing = make_listing(price=900.0)
    listing.end_time = NOW + timedelta(days=2)
    listing.vision = VisionResult(
        is_costume_design_sketch=True,
        confidence=0.9,
        attributed_artist="unknown",
        attribution_confidence=0.0,
        signals=["gouache on board"],
        era_estimate="1960s",
        red_flags=[],
        summary="Strong candidate.",
    )
    listing.confidence = 0.9
    persisted(conn, listing, stage=Stage.VISION_SCORED, seen_at=NOW - timedelta(hours=2))

    for _ in range(2):  # two consecutive runs
        sections = publish_page.select_sections(conn, watchlist, NOW)
        newly_hot = publish_page.mark_newly_hot(conn, sections["hot"], NOW)
        push_alerts.push_hot_alerts(conn, newly_hot, TOPIC, NOW)

    assert len(responses.calls) == 1  # once and only once
