# 🐕 SketchHound

Daily agent that hunts eBay for original film/TV costume design sketches
(Edith Head, Bob Mackie, Travilla, …) — including badly-listed sleepers — and
publishes a static feed page to GitHub Pages, with ntfy.sh push alerts for hot
Buy-It-Now finds. Full spec: [BUILD_BRIEF.md](BUILD_BRIEF.md).

## For the collector

1. Bookmark the feed URL (GitHub Pages link for this repo). That's the gift.
2. Optional: install the [ntfy app](https://ntfy.sh/) and subscribe to the
   topic named in `watchlist.yaml` to get pinged the moment a hot Buy-It-Now
   find appears.
3. Tuning (queries, blocked words, price cap): edit `watchlist.yaml` in the
   GitHub web UI, or text JJ.

## One-time setup (JJ)

1. Push this repo to GitHub (public — Pages on the free tier requires it).
2. **Settings → Secrets and variables → Actions**: add
   `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` (your eBay app keys) and
   `ANTHROPIC_API_KEY` (his key).
3. **Settings → Pages**: deploy from branch, `master` + `/docs`.
4. **Actions tab**: run "SketchHound daily run" once manually
   (`workflow_dispatch`). The first run backfills — vision is capped at 150
   listings per run, so the backlog drains over the next few runs (trigger
   extra manual runs to drain it faster).
5. Replace the `ntfy_topic` in `watchlist.yaml` if you want a fresh random
   string, and have him subscribe before the first run.

## Local development

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest -q                      # no live calls anywhere
$env:EBAY_CLIENT_ID = "..."; $env:EBAY_CLIENT_SECRET = "..."
.venv\Scripts\python -m sketchhound.run --skip-vision   # pipeline minus model spend
```

`python -m sketchhound.run` (with `ANTHROPIC_API_KEY` also set) runs the full
pipeline: fetch → normalize → dedup → gate → vision → persist → publish → push.
State is `data/sketchhound.db` (SQLite, committed by the daily workflow), the
page is `docs/index.html`.

## Cost model

The only real spend is Sonnet vision calls on **new listings that survive
gating** — steady-state that's a handful per day. Haiku triage costs are
negligible. The feed footer shows estimated model spend this month.
