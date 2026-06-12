"""SketchHound — Identify (provenance analysis) + Dashboard (pipeline stats).

Deployed on Render (free tier, ephemeral). Reads the pipeline DB from the
repo checkout (refreshed each daily Actions commit → Render auto-deploy).
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from flask import Flask, jsonify, render_template, request
from PIL import Image

app = Flask(__name__)

MAX_IMAGES = 3
MAX_IMAGE_EDGE = 1568
JPEG_QUALITY = 85
MAX_UPLOAD_MB = 20
VISION_MODEL = "claude-sonnet-4-6"
FEED_URL = "https://jjhjalmarson.github.io/Sparkle/"

app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sketchhound.db"

# ---------------------------------------------------------------------------
# Vision (shared with the pipeline but self-contained here)
# ---------------------------------------------------------------------------

VISION_SYSTEM = """\
You are an expert appraiser of original film/TV costume design artwork
(Edith Head, Bob Mackie, Travilla, Helen Rose, Adrian, Walter Plunkett,
Irene Sharaff, Theadora Van Runkle, and their studio peers).

You will see up to 3 photos of a piece from a private collection. Decide
whether this is an ORIGINAL costume design sketch — hand-drawn/painted
production artwork — and attribute it if the evidence supports it.

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
texture, or a too-clean board are all red flags. A PHOTOGRAPH of a costume
sketch — publicity stills, lobby cards, studio photos showing a designer
holding a sketch — is a photograph, not a sketch; score it
is_costume_design_sketch: false, however interesting the subject.

NEVER inflate attribution:
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
    "is_costume_design_sketch", "confidence", "attributed_artist",
    "attribution_confidence", "signals", "era_estimate", "red_flags", "summary",
}

# ---------------------------------------------------------------------------
# Provenance research (second pass: identification drives web searches)
# ---------------------------------------------------------------------------

MAX_WEB_SEARCHES = 6
MAX_SEARCH_CONTINUATIONS = 5

PROVENANCE_SYSTEM = """\
You are a provenance researcher for film/TV costume design artwork. You are
given an appraiser's identification of a physical sketch (artist attribution,
era, visual signals) plus any collector notes. Your job is to search public
sources for evidence that corroborates or contradicts that identification.

Search targets, in rough priority order:
- auction records: Heritage Auctions, Julien's, Bonhams, Christie's,
  Sotheby's, Profiles in History — sales of the same or comparable pieces
- film archives and databases: the production and character the design may
  belong to, whether the costume appears on screen
- museum and library collections: FIDM Museum, V&A, Met Costume Institute,
  Margaret Herrick Library, university special collections
- costume history sites, designer monographs, and documented exhibitions
- photos of the designer at work with similar sketches

Rules:
- Cite ONLY sources you actually found via search. Never invent a URL,
  auction lot, or film title. An empty evidence list is a good answer when
  the search comes up dry.
- Contradicting evidence matters as much as supporting evidence — e.g. the
  design is a known piece that already sold elsewhere (suggesting a copy),
  or the claimed era doesn't match the production's dates.
- "provenance_confidence" reflects how well PUBLIC RECORD supports the
  identification — not whether the sketch is genuine (the appraiser already
  judged that from the photos).

Respond with ONLY a JSON object, no prose, no markdown fences:
{
  "production_match": "<film or TV production>" or "unknown",
  "character_or_performer": "<character/actress the design was for>" or "unknown",
  "supporting_evidence": ["..."],
  "contradicting_evidence": ["..."],
  "comparable_sales": ["<auction house, year, price if found>"],
  "sources": [{"title": "...", "url": "..."}],
  "provenance_confidence": 0.0-1.0,
  "summary": "Two or three sentences a collector can act on"
}"""

PROVENANCE_KEYS = {
    "production_match", "character_or_performer", "supporting_evidence",
    "contradicting_evidence", "comparable_sales", "sources",
    "provenance_confidence", "summary",
}

# ---------------------------------------------------------------------------
# Background job store (disk-persisted — gunicorn workers can die at any time,
# so job state must never live in an in-memory dict)
# ---------------------------------------------------------------------------

_JOB_DIR = Path(os.environ.get("DATA_DIR", "").strip() or tempfile.gettempdir()) / "provenance_jobs"
_JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")  # job ids appear in file paths
JOB_STALL_SECONDS = 15 * 60  # worker restarted mid-job → report instead of spinning forever
# (deep searches have been observed taking 9-10 minutes; don't call them stalled early)


def _job_path(job_id: str) -> Path:
    return _JOB_DIR / f"{job_id}.json"


def _write_job(job_id: str, meta: dict) -> None:
    _JOB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _job_path(job_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(meta), encoding="utf-8")
    os.replace(tmp, _job_path(job_id))


def _read_job(job_id: str) -> dict | None:
    try:
        return json.loads(_job_path(job_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _cleanup_old_jobs() -> None:
    cutoff = time.time() - 3600
    try:
        for path in _JOB_DIR.iterdir():
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass


def _run_provenance_job(job_id: str, vision: dict, notes: str) -> None:
    try:
        result = provenance_search(vision, notes)
        _write_job(job_id, {"status": "done", "result": result})
    except Exception as exc:
        _write_job(job_id, {"status": "error", "error": str(exc)})


def _prepare_image(raw: bytes) -> bytes:
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


def _parse_response(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    data = json.loads(cleaned)
    missing = REQUIRED_KEYS - data.keys()
    if missing:
        raise ValueError(f"missing keys: {sorted(missing)}")
    return data


def analyze(images: list[bytes], notes: str = "") -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    blocks: list[dict] = []
    for raw in images[:MAX_IMAGES]:
        blocks.append(_image_block(_prepare_image(raw)))

    context = "Assess the photos above."
    if notes.strip():
        context = f"Collector's notes: {notes.strip()}\n\n{context}"
    blocks.append({"type": "text", "text": context})

    response = client.messages.create(
        model=VISION_MODEL,
        max_tokens=1024,
        system=VISION_SYSTEM,
        messages=[{"role": "user", "content": blocks}],
    )
    text = response.content[0].text

    try:
        return _parse_response(text)
    except (json.JSONDecodeError, ValueError):
        retry = client.messages.create(
            model=VISION_MODEL,
            max_tokens=1024,
            system=VISION_SYSTEM,
            messages=[
                {"role": "user", "content": blocks},
                {"role": "assistant", "content": text},
                {"role": "user", "content": "Invalid JSON. Respond again with ONLY the JSON object."},
            ],
        )
        return _parse_response(retry.content[0].text)


def _parse_provenance(content: list) -> dict:
    text = "".join(b.text for b in content if b.type == "text")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    data = json.loads(cleaned)
    missing = PROVENANCE_KEYS - data.keys()
    if missing:
        raise ValueError(f"missing keys: {sorted(missing)}")
    return data


def provenance_search(vision: dict, notes: str = "") -> dict:
    """Second pass: drive web searches of public archives off the identification."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    brief = {
        "attributed_artist": vision.get("attributed_artist", "unknown"),
        "attribution_confidence": vision.get("attribution_confidence"),
        "era_estimate": vision.get("era_estimate"),
        "signals": vision.get("signals", []),
        "summary": vision.get("summary", ""),
    }
    context = (
        "Appraiser's identification of the sketch:\n"
        f"{json.dumps(brief, indent=2)}\n"
    )
    if notes.strip():
        context += f"\nCollector's notes: {notes.strip()}\n"
    context += "\nResearch the public record for this piece."

    tools = [{
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": MAX_WEB_SEARCHES,
    }]
    messages = [{"role": "user", "content": context}]

    response = None
    for _ in range(MAX_SEARCH_CONTINUATIONS):
        response = client.messages.create(
            model=VISION_MODEL,
            max_tokens=4096,
            system=PROVENANCE_SYSTEM,
            tools=tools,
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            break
        # Server-side search loop paused; re-send to let it resume.
        messages = [
            {"role": "user", "content": context},
            {"role": "assistant", "content": response.content},
        ]

    try:
        return _parse_provenance(response.content)
    except (json.JSONDecodeError, ValueError):
        retry = client.messages.create(
            model=VISION_MODEL,
            max_tokens=4096,
            system=PROVENANCE_SYSTEM,
            tools=tools,
            messages=[
                {"role": "user", "content": context},
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": "Invalid JSON. Respond again with ONLY the JSON object — no new searches."},
            ],
        )
        return _parse_provenance(retry.content)


# ---------------------------------------------------------------------------
# Dashboard helpers
# ---------------------------------------------------------------------------

STAGE_COLORS = {
    "vision_scored": "#8cc63f",
    "gate_survivor": "#29a8d4",
    "negative_keyword_reject": "#3a244066",
    "haiku_reject": "#f7941d",
    "relisted": "#d4a017",
}
STAGE_LABELS = {
    "vision_scored": "Vision scored",
    "gate_survivor": "Awaiting vision",
    "negative_keyword_reject": "Keyword reject",
    "haiku_reject": "Haiku reject",
    "relisted": "Relisted",
}


DB_RAW_URL = "https://raw.githubusercontent.com/jjhjalmarson/Sparkle/master/data/sketchhound.db"
_DB_CACHE = Path(tempfile.gettempdir()) / "sketchhound-db-cache.db"
DB_CACHE_TTL_SECONDS = 600


def _freshest_db() -> Path | None:
    """The daily pipeline commits a new DB to GitHub, but this service only
    gets a fresh checkout on deploy. Pull from GitHub raw (10-min cache) so
    the dashboard stays current without redeploying; fall back to the
    checkout copy, then to nothing."""
    try:
        if not _DB_CACHE.exists() or time.time() - _DB_CACHE.stat().st_mtime > DB_CACHE_TTL_SECONDS:
            import urllib.request

            tmp = _DB_CACHE.with_suffix(".tmp")
            with urllib.request.urlopen(DB_RAW_URL, timeout=30) as resp:
                tmp.write_bytes(resp.read())
            os.replace(tmp, _DB_CACHE)
    except Exception:
        pass  # network hiccup → serve whatever we already have
    if _DB_CACHE.exists():
        return _DB_CACHE
    if DB_PATH.exists():
        return DB_PATH
    return None


def _open_db() -> sqlite3.Connection | None:
    db = _freshest_db()
    if db is None:
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _dashboard_data() -> dict | None:
    conn = _open_db()
    if conn is None:
        return None
    try:
        total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        if total == 0:
            return None

        stages_raw = conn.execute(
            "SELECT stage_reached, COUNT(*) as c FROM listings GROUP BY stage_reached ORDER BY c DESC"
        ).fetchall()
        stages = []
        for row in stages_raw:
            s = row["stage_reached"]
            stages.append({
                "label": STAGE_LABELS.get(s, s),
                "count": row["c"],
                "pct": round(row["c"] / total * 100, 1),
                "color": STAGE_COLORS.get(s, "#e6399b"),
            })

        sketch_count = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE vision_json IS NOT NULL "
            "AND json_extract(vision_json, '$.is_costume_design_sketch') = 1"
        ).fetchone()[0]

        hot_count = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE went_hot_at IS NOT NULL"
        ).fetchone()[0]

        now = datetime.now(timezone.utc)
        prefix = now.strftime("%Y-%m")
        month_spend = conn.execute(
            "SELECT COALESCE(SUM(est_cost_usd), 0) FROM runs WHERE started_at LIKE ?",
            (f"{prefix}%",),
        ).fetchone()[0]

        runs_raw = conn.execute(
            "SELECT started_at, fetched_count, new_count, vision_call_count, "
            "est_cost_usd, errors FROM runs ORDER BY id DESC LIMIT 10"
        ).fetchall()
        runs = []
        for r in runs_raw:
            started = r["started_at"]
            if started:
                dt = datetime.fromisoformat(started)
                started = dt.strftime("%b %d %H:%M")
            errors = json.loads(r["errors"]) if r["errors"] else []
            runs.append({
                "started": started or "?",
                "fetched": r["fetched_count"],
                "new": r["new_count"],
                "vision": r["vision_call_count"],
                "cost": r["est_cost_usd"],
                "errors": len(errors),
            })

        artists_raw = conn.execute(
            "SELECT attributed_artist, COUNT(*) as c FROM listings "
            "WHERE attributed_artist IS NOT NULL AND LOWER(attributed_artist) != 'unknown' "
            "AND vision_json IS NOT NULL "
            "AND json_extract(vision_json, '$.is_costume_design_sketch') = 1 "
            "GROUP BY attributed_artist ORDER BY c DESC LIMIT 15"
        ).fetchall()
        artists = [(r["attributed_artist"], r["c"]) for r in artists_raw]

        backfill = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE stage_reached = 'gate_survivor'"
        ).fetchone()[0]

        return {
            "total_listings": total,
            "sketch_count": sketch_count,
            "hot_count": hot_count,
            "month_spend": month_spend,
            "stages": stages,
            "runs": runs,
            "artists": artists,
            "backfill_remaining": backfill,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.after_request
def no_store(response):
    """Never let browsers cache pages or polls — a stale page from before a
    deploy speaks the old API shape and renders garbage."""
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/healthz")
def healthz():
    """Deployed revision, for verifying what's actually live."""
    return jsonify({"rev": os.environ.get("RENDER_GIT_COMMIT", "unknown")[:7]})


@app.route("/")
def index():
    return render_template("identify.html",
                           tagline="Drop photos of a sketch and let Sonnet trace its provenance",
                           active_page="identify", feed_url=FEED_URL)


@app.route("/dashboard")
def dashboard():
    data = _dashboard_data()
    if data is None:
        return render_template("dashboard.html", has_data=False,
                               tagline="Pipeline health at a glance",
                               active_page="dashboard", feed_url=FEED_URL)
    return render_template("dashboard.html", has_data=True,
                           tagline="Pipeline health at a glance",
                           active_page="dashboard", feed_url=FEED_URL, **data)


@app.route("/analyze", methods=["POST"])
def analyze_endpoint():
    files = request.files.getlist("images")
    if not files or not files[0].filename:
        return jsonify({"error": "Upload at least one image."}), 400
    if len(files) > MAX_IMAGES:
        return jsonify({"error": f"Maximum {MAX_IMAGES} images."}), 400

    images = []
    for f in files:
        data = f.read()
        if not data:
            continue
        images.append(data)

    if not images:
        return jsonify({"error": "No readable images."}), 400

    notes = request.form.get("notes", "")
    try:
        result = analyze(images, notes)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@app.route("/provenance", methods=["POST"])
def provenance_start():
    """Kick off the archive search as a background job and return immediately.
    The search runs 2-6 minutes (up to 6 server-side web searches) — far past
    any sane worker/proxy timeout, so the browser polls instead of waiting."""
    payload = request.get_json(silent=True)
    if not payload or not isinstance(payload.get("vision"), dict):
        return jsonify({"error": "Send JSON with a 'vision' object from /analyze."}), 400

    vision = payload["vision"]
    if not vision.get("is_costume_design_sketch"):
        return jsonify({"error": "Provenance research only runs on identified sketches."}), 400

    _cleanup_old_jobs()
    job_id = secrets.token_hex(6)
    _write_job(job_id, {"status": "running", "started_at": time.time()})
    thread = threading.Thread(
        target=_run_provenance_job,
        args=(job_id, vision, payload.get("notes", "") or ""),
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id}), 202


@app.route("/provenance/<job_id>")
def provenance_status(job_id: str):
    if not _JOB_ID_RE.match(job_id):
        return jsonify({"error": "Invalid job id."}), 400
    meta = _read_job(job_id)
    if meta is None:
        return jsonify({"error": "Unknown or expired job."}), 404
    if meta.get("status") == "running" and time.time() - meta.get("started_at", 0) > JOB_STALL_SECONDS:
        return jsonify({"status": "error", "error": "Search stalled (server restarted mid-job). Try again."})
    return jsonify(meta)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
