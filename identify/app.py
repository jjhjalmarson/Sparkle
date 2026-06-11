"""SketchHound Identify — drag-and-drop provenance analysis for costume design
sketches. Upload up to 3 photos; Sonnet vision returns attribution, confidence,
signals, and red flags.

Deployed on Render (free tier, ephemeral). The ANTHROPIC_API_KEY env var is the
only secret.
"""

from __future__ import annotations

import base64
import io
import json
import os

import anthropic
from flask import Flask, jsonify, request
from PIL import Image

app = Flask(__name__)

MAX_IMAGES = 3
MAX_IMAGE_EDGE = 1568
JPEG_QUALITY = 85
MAX_UPLOAD_MB = 20
VISION_MODEL = "claude-sonnet-4-6"

app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

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
    "is_costume_design_sketch",
    "confidence",
    "attributed_artist",
    "attribution_confidence",
    "signals",
    "era_estimate",
    "red_flags",
    "summary",
}


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


@app.route("/")
def index():
    return PAGE_HTML


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


PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>SketchHound Identify</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Chewy&display=swap" rel="stylesheet">
<style>
  :root {
    color-scheme: light dark;
    --pink: #e6399b; --blue: #29a8d4; --lime: #8cc63f; --orange: #f7941d;
    --gold: #d4a017;
    --bg: #fff7ef; --card: #ffffff; --ink: #3a2440; --muted: #3a244099;
    --edge: #f3cfe3;
    --display: "Chewy", "Comic Sans MS", cursive;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #271a33; --card: #322342; --ink: #fdeef7; --muted: #fdeef799;
      --edge: #59386b;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0 auto; max-width: 640px; padding: 0 12px 48px;
         font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         background: var(--bg); color: var(--ink); }
  .awning { height: 14px; margin: 0 -12px;
            background: repeating-linear-gradient(90deg,
              var(--pink) 0 28px, var(--blue) 28px 56px,
              var(--lime) 56px 84px, var(--orange) 84px 112px);
            border-bottom: 3px solid var(--gold); }
  header { padding: 18px 0 4px; text-align: center; }
  h1 { font-family: var(--display); font-weight: 400; font-size: 2.1rem;
       margin: 0; letter-spacing: .02em; color: var(--pink);
       text-shadow: 2px 2px 0 color-mix(in srgb, var(--gold) 55%, transparent); }
  .tagline { margin: 2px 0 0; font-size: .85rem; color: var(--muted); }

  /* Upload zone */
  .drop-zone { border: 3px dashed var(--edge); border-radius: 18px;
    padding: 36px 20px; text-align: center; margin: 20px 0 12px;
    cursor: pointer; transition: border-color .2s, background .2s; }
  .drop-zone.hover { border-color: var(--pink);
    background: color-mix(in srgb, var(--pink) 8%, transparent); }
  .drop-zone p { margin: 0; font-size: 1.05rem; }
  .drop-zone .hint { font-size: .82rem; color: var(--muted); margin-top: 6px; }
  .drop-zone input { display: none; }

  .previews { display: flex; gap: 10px; flex-wrap: wrap; margin: 0 0 14px; }
  .previews img { width: 92px; height: 92px; object-fit: cover; border-radius: 10px;
    border: 2px solid var(--edge); }

  .notes { width: 100%; padding: 10px 12px; border: 2px solid var(--edge);
    border-radius: 12px; font-size: .9rem; background: var(--card);
    color: var(--ink); resize: vertical; min-height: 48px;
    font-family: inherit; }
  .notes::placeholder { color: var(--muted); }

  .btn { display: block; width: 100%; margin: 14px 0; padding: 14px;
    font-family: var(--display); font-size: 1.2rem; letter-spacing: .02em;
    background: linear-gradient(135deg, var(--pink), var(--orange));
    color: #fff; border: none; border-radius: 14px; cursor: pointer;
    box-shadow: 0 3px 0 color-mix(in srgb, var(--pink) 60%, #000);
    transition: opacity .2s; }
  .btn:disabled { opacity: .5; cursor: not-allowed; }
  .btn:hover:not(:disabled) { opacity: .9; }

  /* Loading */
  .loading { display: none; text-align: center; padding: 24px 0; }
  .loading.active { display: block; }
  .spinner { display: inline-block; width: 36px; height: 36px;
    border: 4px solid var(--edge); border-top-color: var(--pink);
    border-radius: 50%; animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loading p { margin: 10px 0 0; font-size: .9rem; color: var(--muted); }

  /* Result card */
  .result { display: none; margin: 20px 0; }
  .result.active { display: block; }
  .result-card { background: var(--card); border: 2px solid var(--edge);
    border-radius: 16px; padding: 20px; overflow: hidden;
    box-shadow: 0 3px 0 color-mix(in srgb, var(--edge) 50%, transparent); }
  .result-card .verdict { font-family: var(--display); font-size: 1.4rem;
    margin: 0 0 4px; }
  .result-card .verdict.yes { color: var(--lime); }
  .result-card .verdict.no { color: var(--pink); }
  .result-card .summary { font-size: .95rem; margin: 0 0 14px; color: var(--muted); }

  .detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 16px; }
  .detail-grid .label { font-size: .75rem; text-transform: uppercase;
    letter-spacing: .05em; color: var(--muted); margin: 0 0 2px; }
  .detail-grid .value { font-size: .95rem; margin: 0; font-weight: 600; }

  .signals-box, .flags-box { margin-top: 14px; }
  .signals-box h3, .flags-box h3 { font-size: .8rem; text-transform: uppercase;
    letter-spacing: .05em; color: var(--muted); margin: 0 0 6px; }
  .tag { display: inline-block; font-size: .82rem; padding: 2px 10px;
    border-radius: 999px; margin: 0 4px 6px 0;
    background: color-mix(in srgb, var(--blue) 15%, transparent);
    color: var(--ink); }
  .tag.flag { background: color-mix(in srgb, var(--pink) 18%, transparent); }

  .confidence-bar { height: 8px; border-radius: 4px; margin: 4px 0 0;
    background: color-mix(in srgb, var(--ink) 10%, transparent); overflow: hidden; }
  .confidence-bar .fill { height: 100%; border-radius: 4px;
    transition: width .6s ease; }
  .fill.high { background: var(--lime); }
  .fill.mid { background: var(--orange); }
  .fill.low { background: var(--pink); }

  .error-msg { background: #b3261e; color: #fff; border-radius: 12px;
    padding: 12px 14px; margin: 16px 0; font-size: .9rem; display: none; }
  .error-msg.active { display: block; }

  .again { text-align: center; margin-top: 16px; }
  .again a { color: var(--pink); cursor: pointer; font-size: .9rem; }

  footer { margin-top: 36px; font-size: .78rem; color: var(--muted);
    border-top: 3px dotted var(--edge); padding-top: 12px; text-align: center; }
</style>
</head>
<body>
<div class="awning"></div>
<header>
  <h1>🔍 SketchHound Identify</h1>
  <p class="tagline">Drop photos of a sketch and let Sonnet trace its provenance</p>
</header>

<form id="form">
  <div class="drop-zone" id="dropzone">
    <p>📸 Drop photos here or tap to browse</p>
    <p class="hint">Up to 3 images — front, back, detail</p>
    <input type="file" id="fileinput" accept="image/*" multiple>
  </div>
  <div class="previews" id="previews"></div>
  <textarea class="notes" name="notes" placeholder="Any context? e.g. 'Bought at Sotheby's 2019, seller said Helen Rose'" rows="2"></textarea>
  <button type="submit" class="btn" id="submit-btn" disabled>Identify this sketch</button>
</form>

<div class="loading" id="loading">
  <div class="spinner"></div>
  <p>Sonnet is examining your images&hellip;</p>
</div>

<div class="error-msg" id="error"></div>

<div class="result" id="result">
  <div class="result-card">
    <p class="verdict" id="r-verdict"></p>
    <p class="summary" id="r-summary"></p>
    <div class="detail-grid">
      <div>
        <p class="label">Artist</p>
        <p class="value" id="r-artist"></p>
      </div>
      <div>
        <p class="label">Attribution confidence</p>
        <p class="value" id="r-attr-conf"></p>
        <div class="confidence-bar"><div class="fill" id="r-attr-bar"></div></div>
      </div>
      <div>
        <p class="label">Sketch confidence</p>
        <p class="value" id="r-conf"></p>
        <div class="confidence-bar"><div class="fill" id="r-conf-bar"></div></div>
      </div>
      <div>
        <p class="label">Era estimate</p>
        <p class="value" id="r-era"></p>
      </div>
    </div>
    <div class="signals-box" id="r-signals-box">
      <h3>Signals</h3>
      <div id="r-signals"></div>
    </div>
    <div class="flags-box" id="r-flags-box">
      <h3>Red flags</h3>
      <div id="r-flags"></div>
    </div>
  </div>
  <div class="again"><a id="reset-link">Analyze another sketch</a></div>
</div>

<footer>
  Powered by Claude Sonnet &middot; Made with ✨ for Glitterville Studios
</footer>

<script>
const dropzone = document.getElementById('dropzone');
const fileinput = document.getElementById('fileinput');
const previews = document.getElementById('previews');
const form = document.getElementById('form');
const submitBtn = document.getElementById('submit-btn');
const loading = document.getElementById('loading');
const errorEl = document.getElementById('error');
const resultEl = document.getElementById('result');
let selectedFiles = [];

dropzone.addEventListener('click', () => fileinput.click());
dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('hover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('hover'));
dropzone.addEventListener('drop', e => {
  e.preventDefault();
  dropzone.classList.remove('hover');
  addFiles(e.dataTransfer.files);
});
fileinput.addEventListener('change', () => addFiles(fileinput.files));

function addFiles(fileList) {
  for (const f of fileList) {
    if (selectedFiles.length >= 3) break;
    if (!f.type.startsWith('image/')) continue;
    selectedFiles.push(f);
  }
  renderPreviews();
}

function renderPreviews() {
  previews.innerHTML = '';
  selectedFiles.forEach((f, i) => {
    const img = document.createElement('img');
    img.src = URL.createObjectURL(f);
    img.addEventListener('click', () => {
      selectedFiles.splice(i, 1);
      renderPreviews();
    });
    img.title = 'Click to remove';
    img.style.cursor = 'pointer';
    previews.appendChild(img);
  });
  submitBtn.disabled = selectedFiles.length === 0;
}

form.addEventListener('submit', async e => {
  e.preventDefault();
  if (!selectedFiles.length) return;

  errorEl.classList.remove('active');
  resultEl.classList.remove('active');
  form.style.display = 'none';
  loading.classList.add('active');

  const fd = new FormData();
  selectedFiles.forEach(f => fd.append('images', f));
  fd.append('notes', form.notes.value);

  try {
    const res = await fetch('/analyze', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Analysis failed');
    showResult(data);
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('active');
    form.style.display = 'block';
  } finally {
    loading.classList.remove('active');
  }
});

function confClass(v) { return v >= 0.7 ? 'high' : v >= 0.4 ? 'mid' : 'low'; }

function showResult(d) {
  const isSketch = d.is_costume_design_sketch;
  const verdictEl = document.getElementById('r-verdict');
  verdictEl.textContent = isSketch ? '✨ Original costume design sketch' : '✗ Not an original sketch';
  verdictEl.className = 'verdict ' + (isSketch ? 'yes' : 'no');

  document.getElementById('r-summary').textContent = d.summary;
  document.getElementById('r-artist').textContent = d.attributed_artist || 'unknown';

  const attrConf = d.attribution_confidence;
  document.getElementById('r-attr-conf').textContent = Math.round(attrConf * 100) + '%';
  const attrBar = document.getElementById('r-attr-bar');
  attrBar.style.width = (attrConf * 100) + '%';
  attrBar.className = 'fill ' + confClass(attrConf);

  const conf = d.confidence;
  document.getElementById('r-conf').textContent = Math.round(conf * 100) + '%';
  const confBar = document.getElementById('r-conf-bar');
  confBar.style.width = (conf * 100) + '%';
  confBar.className = 'fill ' + confClass(conf);

  document.getElementById('r-era').textContent = d.era_estimate || '—';

  const sigBox = document.getElementById('r-signals-box');
  const sigEl = document.getElementById('r-signals');
  sigEl.innerHTML = '';
  if (d.signals && d.signals.length) {
    d.signals.forEach(s => {
      const span = document.createElement('span');
      span.className = 'tag';
      span.textContent = s;
      sigEl.appendChild(span);
    });
    sigBox.style.display = '';
  } else { sigBox.style.display = 'none'; }

  const flagBox = document.getElementById('r-flags-box');
  const flagEl = document.getElementById('r-flags');
  flagEl.innerHTML = '';
  if (d.red_flags && d.red_flags.length) {
    d.red_flags.forEach(f => {
      const span = document.createElement('span');
      span.className = 'tag flag';
      span.textContent = f;
      flagEl.appendChild(span);
    });
    flagBox.style.display = '';
  } else { flagBox.style.display = 'none'; }

  resultEl.classList.add('active');
}

document.getElementById('reset-link').addEventListener('click', () => {
  selectedFiles = [];
  renderPreviews();
  fileinput.value = '';
  form.notes.value = '';
  form.style.display = 'block';
  resultEl.classList.remove('active');
  errorEl.classList.remove('active');
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
