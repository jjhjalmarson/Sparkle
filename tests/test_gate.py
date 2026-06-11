"""Gating acceptance proof (brief section 7): vision never invoked on
negative-keyword or Haiku-rejected listings; generic-query bypass verified.
"""

from sketchhound import persist
from sketchhound.config import Watchlist
from sketchhound.gate import gate, haiku_triage, kill_by_negative_keywords
from sketchhound.models import HaikuVerdict, Stage

from .conftest import FakeAnthropic, make_listing, persisted

WATCHLIST = Watchlist(negative_keywords=["print", "reproduction", "giclee", "cosplay"])


def test_negative_keywords_kill_title_and_snippet():
    clean = make_listing(title="Original Edith Head costume sketch")
    title_hit = make_listing(title="Bob Mackie REPRODUCTION sketch")
    snippet_hit = make_listing(description_snippet="High quality giclee on canvas")
    aggressive_hit = make_listing(title="Vintage reprinted fashion plate")  # substring: "print"

    survivors, killed = kill_by_negative_keywords(
        [clean, title_hit, snippet_hit, aggressive_hit], WATCHLIST.negative_keywords
    )
    assert survivors == [clean]
    assert {l.source_listing_id for l in killed} == {
        title_hit.source_listing_id,
        snippet_hit.source_listing_id,
        aggressive_hit.source_listing_id,
    }
    assert all(l.stage_reached == Stage.NEGATIVE_KEYWORD_REJECT for l in killed)


def test_haiku_triage_verdicts():
    for text, expected in [
        ("RELEVANT", HaikuVerdict.RELEVANT),
        ("IRRELEVANT", HaikuVerdict.IRRELEVANT),
        ("UNSURE", HaikuVerdict.UNSURE),
        ("relevant — looks like a sketch", HaikuVerdict.RELEVANT),
        ("I cannot determine that", HaikuVerdict.UNSURE),  # unparseable → UNSURE
    ]:
        assert haiku_triage(make_listing(), FakeAnthropic([text])) == expected


def test_haiku_api_failure_degrades_to_unsure():
    client = FakeAnthropic([RuntimeError("api down")])
    assert haiku_triage(make_listing(), client) == HaikuVerdict.UNSURE


def test_gate_artist_path():
    relevant = make_listing(title="Edith Head original sketch")
    irrelevant = make_listing(title="Edith Head biography hardcover")
    unsure = make_listing(title="Vintage drawing signed E.H.")
    client = FakeAnthropic(["RELEVANT", "IRRELEVANT", "UNSURE"])

    survivors, rejects = gate({"artist": [relevant, irrelevant, unsure]}, WATCHLIST, client)

    assert [l.source_listing_id for l in survivors] == [
        relevant.source_listing_id,
        unsure.source_listing_id,
    ]
    assert all(l.stage_reached == Stage.GATE_SURVIVOR for l in survivors)
    assert irrelevant.stage_reached == Stage.HAIKU_REJECT
    assert relevant.haiku_verdict == HaikuVerdict.RELEVANT
    assert unsure.haiku_verdict == HaikuVerdict.UNSURE


def test_generic_bypass_no_haiku_calls():
    listings = [make_listing(title="weird untitled gouache drawing") for _ in range(3)]
    client = FakeAnthropic([])  # any API call would raise

    survivors, rejects = gate({"generic": listings}, WATCHLIST, client)

    assert len(survivors) == 3
    assert client.calls == []
    assert all(l.haiku_verdict is None for l in survivors)
    assert all(l.stage_reached == Stage.GATE_SURVIVOR for l in survivors)


def test_negative_keywords_kill_before_haiku():
    killed_listing = make_listing(title="Edith Head sketch PRINT")
    client = FakeAnthropic([])  # Haiku must not be consulted for killed listings

    survivors, rejects = gate({"artist": [killed_listing]}, WATCHLIST, client)

    assert survivors == []
    assert client.calls == []
    assert killed_listing.stage_reached == Stage.NEGATIVE_KEYWORD_REJECT


def test_rejects_never_enter_vision_queue(conn):
    """Acceptance: the vision queue is drawn from GATE_SURVIVOR rows only."""
    survivor = persisted(conn, make_listing(title="clean"), stage=Stage.FETCHED)
    keyword_reject = persisted(conn, make_listing(title="a print"), stage=Stage.FETCHED)
    haiku_reject = persisted(conn, make_listing(title="biography"), stage=Stage.FETCHED)

    client = FakeAnthropic(["RELEVANT", "IRRELEVANT"])
    survivors, rejects = gate(
        {"artist": [survivor, keyword_reject, haiku_reject]}, WATCHLIST, client
    )
    for listing in (*survivors, *rejects):
        persist.update_listing(conn, listing)

    queue_ids = {l.id for l in persist.pending_vision(conn, limit=100)}
    assert queue_ids == {survivor.id}
