"""Sonnet vision scoring — the only expensive stage, paid for by the friend's
ANTHROPIC_API_KEY. Invoked ONLY on listings that are new AND survived gating.

Per listing: primary image + up to 2 more, JSON-only response matching the
exact contract in BUILD_BRIEF.md section 3 (VisionResult).
"""

from __future__ import annotations

import base64
import io
import json

from PIL import Image

from .config import VISION_MAX_IMAGES, VISION_MODEL
from .http_util import download_bytes
from .models import Listing, Stage, VisionResult

# Sonnet 4.6 pricing; powers the run cost estimate and the monthly spend
# figure in the feed footer.
SONNET_INPUT_USD_PER_MTOK = 3.00
SONNET_OUTPUT_USD_PER_MTOK = 15.00

# Downscale before sending: Anthropic resizes anything over 1568px anyway,
# so shipping bigger images only costs the friend tokens and bandwidth.
MAX_IMAGE_EDGE = 1568
JPEG_QUALITY = 85

VISION_SYSTEM = """\
You are an expert appraiser of original film/TV costume design artwork
(Edith Head, Bob Mackie, Travilla, Helen Rose, Adrian, Walter Plunkett,
Irene Sharaff, Theadora Van Runkle, and their studio peers).

You will see up to 3 photos from one eBay listing. Decide whether this is an
ORIGINAL costume design sketch — hand-drawn/painted production artwork — and
attribute it if the evidence supports it.

Evidence to look for:
- signatures and monograms, and where they sit on the sheet
- studio stamps and markings: Paramount, MGM, 20th Century Fox, Warner Bros.,
  Western Costume Co., wardrobe department stamps
- production annotations: actress/character names, production or scene
  numbers, costume change numbers, attached fabric swatches
- period media: gouache/tempera on illustration board, pencil underdrawing,
  board type and aging consistent with the claimed era
- known hand characteristics of the named designers

Red-flag aggressively. The Bob Mackie market especially is flooded with
modern reproductions, prints, and "after Mackie" copies: uniform surface
sheen, halftone dots, perfectly white paper, printed signatures, giclee
texture, or a too-clean board are all red flags. NEVER inflate attribution:
"attributed_artist": "unknown" with well-described signals is a GOOD answer
and far more useful than a guessed name. attribution_confidence reflects the
evidence in THESE images, not market hope.

"confidence" is the probability that this IS an original costume design
sketch — a confident "no" means confidence near 0, not near 1.

Respond with ONLY a JSON object, no prose, no markdown fences:
{
  "is_costume_design_sketch": true/false,
  "confidence": 0.0-1.0,
  "attributed_artist": "<name>" or "unknown",
  "attribution_confidence": 0.0-1.0,
  "signals": ["..."],
  "era_estimate": "<decade or range>",
  "red_flags": ["..."],
  "summary": "One-sentence collector-facing description"
}"""

REQUIRED_KEYS = {
    "is_costume_design_sketch",
    "confidence",
    "attributed_artist",
    "attribution_confidence",
    "signals",
    "era_estimate",
    "red_flags",
    "summary",
}


class ScoringError(Exception):
    """Vision call failed for one listing; it stays GATE_SURVIVOR and retries
    on a later run. Carries the cost/calls already spent so the run stats
    stay honest even on failure."""

    def __init__(self, message: str, cost: float = 0.0, calls: int = 0):
        super().__init__(message)
        self.cost = cost
        self.calls = calls


def _prepare_image(raw: bytes) -> bytes:
    """Normalize to RGB JPEG, long edge capped — predictable media type and size."""
    with Image.open(io.BytesIO(raw)) as img:
        img = img.convert("RGB")
        img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=JPEG_QUALITY)
        return out.getvalue()


def _image_block(jpeg_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpeg_bytes).decode("ascii"),
        },
    }


def _parse_response(text: str) -> VisionResult:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    data = json.loads(cleaned)
    missing = REQUIRED_KEYS - data.keys()
    if missing:
        raise ValueError(f"missing keys: {sorted(missing)}")
    return VisionResult(
        is_costume_design_sketch=bool(data["is_costume_design_sketch"]),
        confidence=min(1.0, max(0.0, float(data["confidence"]))),
        attributed_artist=str(data["attributed_artist"]),
        attribution_confidence=min(1.0, max(0.0, float(data["attribution_confidence"]))),
        signals=[str(s) for s in data["signals"]],
        era_estimate=str(data["era_estimate"]),
        red_flags=[str(r) for r in data["red_flags"]],
        summary=str(data["summary"]),
    )


def _call_cost(usage) -> float:
    return (
        usage.input_tokens * SONNET_INPUT_USD_PER_MTOK
        + usage.output_tokens * SONNET_OUTPUT_USD_PER_MTOK
    ) / 1_000_000


def score_listing(
    listing: Listing, anthropic_client, fetch_image=None
) -> tuple[VisionResult, float, int]:
    """Score one listing. Returns (result, estimated cost in USD, API calls made).

    Malformed JSON → one retry with an explicit correction turn, then
    ScoringError. Image download/API failures → ScoringError.
    """
    fetch = fetch_image or download_bytes
    blocks: list[dict] = []
    for url in listing.image_urls[:VISION_MAX_IMAGES]:
        try:
            blocks.append(_image_block(_prepare_image(fetch(url))))
        except Exception:
            continue  # a missing extra image shouldn't sink the listing
    if not blocks:
        raise ScoringError(f"no usable images for {listing.source_listing_id}")

    context = (
        f"Listing title: {listing.title}\n"
        f"Seller description: {listing.description_snippet or '(none)'}\n"
        f"Price: {listing.price_value} {listing.price_currency or ''}\n"
        "Assess the photos above."
    )
    messages = [{"role": "user", "content": [*blocks, {"type": "text", "text": context}]}]

    total_cost = 0.0
    calls = 0
    for attempt in range(2):
        try:
            response = anthropic_client.messages.create(
                model=VISION_MODEL,
                max_tokens=1024,
                system=VISION_SYSTEM,
                messages=messages,
            )
        except Exception as exc:
            raise ScoringError(
                f"API error for {listing.source_listing_id}: {exc}", cost=total_cost, calls=calls
            ) from exc
        calls += 1
        total_cost += _call_cost(response.usage)
        text = response.content[0].text
        try:
            return _parse_response(text), total_cost, calls
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            if attempt == 0:
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": f"Invalid response ({exc}). Respond again with ONLY the JSON object.",
                    },
                ]
            else:
                raise ScoringError(
                    f"unparseable vision response for {listing.source_listing_id}: {exc}",
                    cost=total_cost,
                    calls=calls,
                ) from exc
    raise AssertionError("unreachable")


def score_batch(
    listings: list[Listing],
    anthropic_client,
    cap: int,
    fetch_image=None,
    on_scored=None,
) -> tuple[list[Listing], float, int, list[str]]:
    """Score up to `cap` listings (callers pass the backfill-capped queue and
    apply the abort guard before calling).

    `on_scored(listing)` fires immediately after each successful score so the
    caller can persist incrementally — a crash or job timeout mid-batch then
    loses at most one paid call, not the whole batch.

    Returns (scored listings, total estimated cost, call count, errors).
    Failed listings stay GATE_SURVIVOR and drain on a later run.
    """
    scored: list[Listing] = []
    errors: list[str] = []
    total_cost = 0.0
    calls = 0
    for listing in listings[:cap]:
        try:
            result, cost, listing_calls = score_listing(
                listing, anthropic_client, fetch_image=fetch_image
            )
        except ScoringError as exc:
            errors.append(str(exc))
            total_cost += exc.cost
            calls += exc.calls
            continue
        total_cost += cost
        calls += listing_calls
        listing.vision = result
        listing.confidence = result.confidence
        listing.attributed_artist = result.attributed_artist
        listing.stage_reached = Stage.VISION_SCORED
        if on_scored is not None:
            on_scored(listing)
        scored.append(listing)
    return scored, total_cost, calls, errors
