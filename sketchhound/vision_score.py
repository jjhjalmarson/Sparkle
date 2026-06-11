"""Sonnet vision scoring — the only expensive stage, paid for by the friend's
ANTHROPIC_API_KEY. Invoked ONLY on listings that are new AND survived gating.

Per listing: primary image + up to 2 more, JSON-only response matching the
exact contract in BUILD_BRIEF.md section 3 (VisionResult).

Prompt rubric: signatures; studio stamps (Paramount, MGM, Fox, Western
Costume Co.); production annotations (actress names, scene/production
numbers, attached swatches); period media (gouache/tempera on illustration
board); known artist hand characteristics. The Mackie market is flooded with
repros and "after Mackie" pieces — red-flag aggressively, never inflate
attribution confidence. `attributed_artist: "unknown"` with well-described
signals is a GOOD answer.
"""

from __future__ import annotations

from .models import Listing, VisionResult

# Updated from token usage each call; powers the run cost estimate and the
# monthly spend figure in the feed footer.
SONNET_INPUT_USD_PER_MTOK = 3.00
SONNET_OUTPUT_USD_PER_MTOK = 15.00


def score_listing(listing: Listing, anthropic_client) -> tuple[VisionResult, float]:
    """Score one listing. Returns (result, estimated cost in USD).

    Downloads up to VISION_MAX_IMAGES listing images, sends with the rubric
    prompt, parses/validates the JSON-only response. Malformed JSON → one
    retry with an explicit correction message, then treated as scoring error
    (recorded in run errors, listing stays GATE_SURVIVOR for a later run).
    """
    raise NotImplementedError  # step 3


def score_batch(listings: list[Listing], anthropic_client, cap: int) -> tuple[list[Listing], float, int]:
    """Score up to `cap` listings (backfill cap / abort guard applied by caller).

    Returns (scored listings, total estimated cost, call count).
    """
    raise NotImplementedError  # step 3
