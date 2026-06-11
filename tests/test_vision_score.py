"""Vision stage: exact JSON contract, retry-on-malformed, cost accounting,
backfill cap, abort guard. All Anthropic responses mocked.
"""

import pytest

from sketchhound import persist, vision_score
from sketchhound.config import ABORT_NEW_SURVIVORS, BACKFILL_VISION_CAP, Watchlist
from sketchhound.models import RunStats, Stage
from sketchhound.run import gate_and_score
from sketchhound.vision_score import ScoringError, score_batch, score_listing

from .conftest import NOW, FakeAnthropic, FakeResponse, make_image_bytes, make_listing, persisted, vision_json

PNG = make_image_bytes("halves", "PNG")


def fetch_image(url: str) -> bytes:
    return PNG


def test_score_listing_happy_path():
    client = FakeAnthropic([FakeResponse(vision_json(), input_tokens=2000, output_tokens=300)])
    listing = make_listing(image_url="https://img.example/a.png")
    listing.image_urls.append("https://img.example/b.png")

    result, cost, calls = score_listing(listing, client, fetch_image=fetch_image)

    assert result.is_costume_design_sketch is True
    assert result.confidence == 0.85
    assert result.attributed_artist == "Edith Head"
    assert calls == 1
    assert cost == pytest.approx((2000 * 3.00 + 300 * 15.00) / 1_000_000)

    request = client.calls[0]
    assert request["model"] == "claude-sonnet-4-6"
    image_blocks = [b for b in request["messages"][0]["content"] if b.get("type") == "image"]
    assert len(image_blocks) == 2
    assert all(b["source"]["media_type"] == "image/jpeg" for b in image_blocks)


def test_image_count_capped_at_three():
    client = FakeAnthropic([vision_json()])
    listing = make_listing()
    listing.image_urls = [f"https://img.example/{i}.png" for i in range(6)]

    score_listing(listing, client, fetch_image=fetch_image)
    image_blocks = [b for b in client.calls[0]["messages"][0]["content"] if b.get("type") == "image"]
    assert len(image_blocks) == 3


def test_markdown_fenced_json_accepted():
    client = FakeAnthropic([f"```json\n{vision_json()}\n```"])
    result, _, _ = score_listing(make_listing(), client, fetch_image=fetch_image)
    assert result.confidence == 0.85


def test_malformed_json_retried_once_then_ok():
    client = FakeAnthropic(["not json at all", vision_json(confidence=0.7)])
    result, cost, calls = score_listing(make_listing(), client, fetch_image=fetch_image)

    assert calls == 2
    assert result.confidence == 0.7
    assert cost == pytest.approx(2 * (1000 * 3.00 + 200 * 15.00) / 1_000_000)
    # The retry turn includes the bad response and a correction.
    retry_messages = client.calls[1]["messages"]
    assert len(retry_messages) == 3
    assert "ONLY the JSON object" in retry_messages[2]["content"]


def test_malformed_twice_raises_with_cost():
    client = FakeAnthropic(["garbage", "still garbage"])
    with pytest.raises(ScoringError) as exc_info:
        score_listing(make_listing(), client, fetch_image=fetch_image)
    assert exc_info.value.calls == 2
    assert exc_info.value.cost > 0


def test_no_usable_images_raises_without_api_call():
    client = FakeAnthropic([])
    with pytest.raises(ScoringError):
        score_listing(make_listing(image_url=None), client, fetch_image=fetch_image)
    assert client.calls == []


def test_confidence_clamped():
    client = FakeAnthropic([vision_json(confidence=1.7, attribution_confidence=-0.2)])
    result, _, _ = score_listing(make_listing(), client, fetch_image=fetch_image)
    assert result.confidence == 1.0
    assert result.attribution_confidence == 0.0


def test_score_batch_respects_cap_and_isolates_failures():
    listings = [make_listing() for _ in range(5)]
    listings[1].image_urls = []  # will fail: no usable images
    client = FakeAnthropic([vision_json(), vision_json(confidence=0.4)])

    scored, cost, calls, errors = score_batch(listings, client, cap=3, fetch_image=fetch_image)

    assert len(scored) == 2          # cap 3 attempted: index 0 ok, 1 failed, 2 ok
    assert calls == 2
    assert len(errors) == 1
    assert listings[0].stage_reached == Stage.VISION_SCORED
    assert listings[0].confidence == 0.85
    assert listings[1].stage_reached == Stage.GATE_SURVIVOR or listings[1].stage_reached == Stage.FETCHED
    assert listings[3].stage_reached != Stage.VISION_SCORED  # beyond cap, untouched


def test_backfill_cap_drains_over_runs(conn):
    """Acceptance: first-run flood → exactly BACKFILL_VISION_CAP vision calls,
    remainder stays queued for later runs."""
    backlog = BACKFILL_VISION_CAP + 10
    for _ in range(backlog):
        persisted(conn, make_listing(), stage=Stage.GATE_SURVIVOR)

    client = FakeAnthropic([vision_json()] * BACKFILL_VISION_CAP)
    stats = RunStats(started_at=NOW)
    banner = gate_and_score(
        conn, {"artist": [], "generic": []}, Watchlist(), client, stats, fetch_image=fetch_image
    )

    assert banner is None
    assert stats.vision_call_count == BACKFILL_VISION_CAP
    assert len(persist.pending_vision(conn, limit=backlog)) == 10  # drains next run


def test_abort_guard_skips_vision_in_steady_state(conn):
    """Acceptance: >ABORT_NEW_SURVIVORS new survivors → no vision calls, banner.
    Steady state = at least one vision call has happened in some prior run."""
    persist.record_run(conn, RunStats(started_at=NOW, finished_at=NOW, vision_call_count=5))
    flood = [
        persisted(conn, make_listing(title="untitled gouache"), stage=Stage.FETCHED)
        for _ in range(ABORT_NEW_SURVIVORS + 1)
    ]
    client = FakeAnthropic([])  # any vision call would raise

    stats = RunStats(started_at=NOW)
    banner = gate_and_score(
        conn, {"artist": [], "generic": flood}, Watchlist(), client, stats, fetch_image=fetch_image
    )

    assert banner is not None and "filter regression" in banner
    assert client.calls == []
    assert stats.vision_call_count == 0
    # Survivors are persisted and will drain through the cap once resolved.
    assert len(persist.pending_vision(conn, limit=200)) == ABORT_NEW_SURVIVORS + 1


def test_first_run_flood_is_backfill_not_abort(conn):
    """The initial backfill IS a flood — the guard must stand down until the
    first vision batch has run, and the 150 cap does the cost control."""
    flood = [
        persisted(conn, make_listing(title="untitled gouache"), stage=Stage.FETCHED)
        for _ in range(ABORT_NEW_SURVIVORS + 50)
    ]
    client = FakeAnthropic([vision_json()] * BACKFILL_VISION_CAP)

    stats = RunStats(started_at=NOW)
    banner = gate_and_score(
        conn, {"artist": [], "generic": flood}, Watchlist(), client, stats, fetch_image=fetch_image
    )

    assert banner is None
    assert stats.vision_call_count == BACKFILL_VISION_CAP
    assert len(persist.pending_vision(conn, limit=500)) == len(flood) - BACKFILL_VISION_CAP
