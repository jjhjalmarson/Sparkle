"""Gating: decides which new listings earn a (paid) vision call.

Order is load-bearing (brief section 3):
1. Negative-keyword kill list — free, runs on everything first. Substring
   match, deliberately aggressive: "print" also kills "prints", "printed",
   "reprint". The list is configurable in watchlist.yaml if it over-kills.
2. Artist-query results → Haiku text triage. RELEVANT and UNSURE proceed
   (recall over precision); IRRELEVANT is rejected. A failed or unparseable
   Haiku call degrades to UNSURE — never to a rejection.
3. Generic-query results → NO text gate, straight to vision. Their titles
   carry no signal by construction; the judgment lives in the image.

Vision must never see a negative-keyword or Haiku-rejected listing
(acceptance criterion 4).
"""

from __future__ import annotations

from .config import FILTER_MODEL, Watchlist
from .models import HaikuVerdict, Listing, Stage

HAIKU_SYSTEM = """\
You triage eBay listings for a collector of ORIGINAL film/TV costume design
sketches (Edith Head, Bob Mackie, Travilla, Helen Rose, Adrian, Walter
Plunkett, Irene Sharaff, Theadora Van Runkle, and peers). Original means
hand-drawn/painted production artwork — not prints, posters, photographs,
finished garments, books, or fan art.

Given a listing title and description snippet, answer with EXACTLY one word:
RELEVANT   — plausibly an original costume design sketch or production artwork
IRRELEVANT — clearly something else
UNSURE     — cannot tell from text alone

When in doubt, say UNSURE. A false IRRELEVANT loses a treasure forever; a
false RELEVANT only costs one image review."""


def kill_by_negative_keywords(
    listings: list[Listing], negative_keywords: list[str]
) -> tuple[list[Listing], list[Listing]]:
    """Case-insensitive substring match against title + description snippet.

    Returns (survivors, killed). Killed get stage_reached=NEGATIVE_KEYWORD_REJECT.
    """
    keywords = [k.lower() for k in negative_keywords]
    survivors: list[Listing] = []
    killed: list[Listing] = []
    for listing in listings:
        haystack = f"{listing.title} {listing.description_snippet or ''}".lower()
        if any(keyword in haystack for keyword in keywords):
            listing.stage_reached = Stage.NEGATIVE_KEYWORD_REJECT
            killed.append(listing)
        else:
            survivors.append(listing)
    return survivors, killed


def haiku_triage(listing: Listing, anthropic_client) -> HaikuVerdict:
    """claude-haiku-4-5, title + snippet only. One-word verdict; anything
    unparseable or any API failure is treated as UNSURE (which proceeds)."""
    text = f"Title: {listing.title}\nDescription: {listing.description_snippet or '(none)'}"
    try:
        response = anthropic_client.messages.create(
            model=FILTER_MODEL,
            max_tokens=10,
            system=HAIKU_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        word = response.content[0].text.strip().upper()
    except Exception:
        return HaikuVerdict.UNSURE
    for verdict in (HaikuVerdict.RELEVANT, HaikuVerdict.IRRELEVANT, HaikuVerdict.UNSURE):
        if word.startswith(verdict.value):
            return verdict
    return HaikuVerdict.UNSURE


def gate(
    new_by_kind: dict[str, list[Listing]],  # {"artist": [...], "generic": [...]}
    watchlist: Watchlist,
    anthropic_client,
) -> tuple[list[Listing], list[Listing]]:
    """Returns (survivors bound for vision, rejects). Both have stage_reached
    set; survivors are GATE_SURVIVOR with haiku_verdict recorded where gated."""
    survivors: list[Listing] = []
    rejects: list[Listing] = []

    artist_pool, killed = kill_by_negative_keywords(
        new_by_kind.get("artist", []), watchlist.negative_keywords
    )
    rejects.extend(killed)
    for listing in artist_pool:
        verdict = haiku_triage(listing, anthropic_client)
        listing.haiku_verdict = verdict
        if verdict is HaikuVerdict.IRRELEVANT:
            listing.stage_reached = Stage.HAIKU_REJECT
            rejects.append(listing)
        else:
            listing.stage_reached = Stage.GATE_SURVIVOR
            survivors.append(listing)

    generic_pool, killed = kill_by_negative_keywords(
        new_by_kind.get("generic", []), watchlist.negative_keywords
    )
    rejects.extend(killed)
    for listing in generic_pool:
        listing.stage_reached = Stage.GATE_SURVIVOR  # no text gate: straight to vision
        survivors.append(listing)

    return survivors, rejects
