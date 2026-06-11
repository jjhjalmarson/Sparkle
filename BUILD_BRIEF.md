# SketchHound — Phase 1 Build Brief v2 (Claude Code Handoff)

**Project:** Autonomous acquisition scout for film costume design sketches (Bob Mackie, Edith Head, et al.)
**Owner/builder:** JJ
**End user:** One collector (non-technical). This is a gift. His entire experience must be: bookmark one URL, optionally subscribe to one push notification topic, pay for his own Anthropic API key. Zero accounts to create, zero infra to manage, zero email anywhere.
**Status:** Architecture locked. Execute as specified. Flag conflicts, don't silently redesign.

---

## 1. What this is

A scheduled agent that monitors eBay for film/TV costume design sketches — including badly-listed sleepers the seller doesn't know how to describe — and publishes a continuously updated feed page, with instant push alerts for hot Buy-It-Now finds.

Core alpha: eBay saved searches only match seller text. This runs a vision model on listing images, so "vintage fashion drawing gouache" gets recognized as a probable Edith Head Paramount sketch even when the seller has no idea.

---

## 2. Locked architecture decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Builder's stack |
| Scheduler | GitHub Actions, hourly | Sleepers get sniped in hours; daily is too slow. Free tier covers hourly runs of this size |
| State | SQLite, committed back to repo each run | Free, versioned, single-user scale |
| Source | eBay Browse API only (Phase 1) | Builder's eBay app credentials — app-level, free tier 5k calls/day, friend never registers anything |
| Filter model | claude-haiku-4-5, artist-name queries only | Generic queries skip Haiku — titles carry no signal there; the judgment lives in the image |
| Vision model | claude-sonnet-4-6, new survivors only | Friend's Anthropic API key, stored as a repo secret. He owns the only real cost |
| Output | Static HTML feed published to GitHub Pages | The gift is a URL. Regenerated every run |
| Hot alerts | ntfy.sh topic (no account required) | Friend installs the ntfy app, subscribes to one private topic name. Agent POSTs on hot finds |
| Email | None. No sending, no receiving, no vendor | — |

Hard requirements: vision calls only on listings that are new (never seen) AND survive gating. Hourly cadence is cheap *because* dedup means steady-state runs see a handful of new listings; cost concentrates in the first backfill run.

---

## 3. Pipeline (every run)

```
fetch (eBay) → normalize → dedup → gate → vision score → persist → publish page → push hot alerts
```

**Fetch.** eBay Browse API, every query in `watchlist.yaml`, categories restricted to Art / Entertainment Memorabilia where sensible. Pull title, description snippet, price, format (auction/BIN), end time, image URLs, item ID, URL. OAuth app token via client-credentials flow, cached.

**Normalize → Dedup.** Single `Listing` schema. Primary dedup key `(source, source_listing_id)`; secondary perceptual hash (pHash) on primary image to catch relistings under new item IDs — Hamming distance ≤5 marks `relisted_from`, suppressed from feed unless price dropped >20%.

**Gate.**
1. Negative-keyword kill list first (configurable): print, reproduction, repro, poster, giclee, digital, pattern, cosplay, etc.
2. Artist-name query results → Haiku text triage (`RELEVANT`/`IRRELEVANT`/`UNSURE`; UNSURE proceeds — recall over precision).
3. Generic-query results → straight to vision. No text gate.

**Vision score (Sonnet, friend's key).** Primary image + up to 2 more. JSON-only response:

```json
{
  "is_costume_design_sketch": true,
  "confidence": 0.85,
  "attributed_artist": "Edith Head | Bob Mackie | ... | unknown",
  "attribution_confidence": 0.6,
  "signals": ["signature lower right", "Paramount wardrobe stamp", "gouache on board", "annotation: actress name"],
  "era_estimate": "1950s",
  "red_flags": ["uniform surface suggests print"],
  "summary": "One-sentence collector-facing description"
}
```

Rubric in prompt: signatures, studio stamps (Paramount, MGM, Fox, Western Costume Co.), production annotations (names, scene/production numbers, attached swatches), period media (gouache/tempera on illustration board), known artist hand characteristics. Be explicit that `unknown` + well-described signals is a good answer; the Mackie market is flooded with repros and "after Mackie" pieces — red-flag aggressively, never inflate attribution confidence.

**Persist.** Everything, including rejects with stage rejected at (tuning data).

**Publish.** Regenerate `index.html`, commit to `gh-pages` (or `/docs`). Static, no JS dependencies required to read it. Sections:
1. **Hot** — BIN, confidence ≥0.7, listed <24h ago
2. **High-confidence attributions** — any format
3. **Probable sketches, unattributed**
4. **Ending soon** — previously surfaced auctions ending <48h
Each card: thumbnail, title, price + format, end time, confidence, attributed artist, signals one-liner, direct eBay link, first-seen timestamp. Footer: last run time, run stats, estimated model spend this month. Mobile-first layout — he'll read it on his phone.

**Push.** Any listing entering the **Hot** section that wasn't previously hot → POST to `https://ntfy.sh/<private-topic>` with title, price, confidence, and the eBay link as click action. Topic name is a long random string in config (security-by-obscurity is acceptable here; nothing sensitive transits).

---

## 4. watchlist.yaml shape

```yaml
artist_queries:        # Haiku-gated
  - "Edith Head sketch"
  - "Bob Mackie sketch"
  - "Travilla costume"
  - "Helen Rose costume sketch"
  - "Adrian gown sketch"
  - "Walter Plunkett"
  - "Irene Sharaff"
  - "Theadora Van Runkle"
generic_queries:       # straight to vision
  - "vintage costume sketch"
  - "Hollywood costume design"
  - "movie wardrobe sketch"
  - "studio fashion illustration gouache"
  - "costume design original art"
negative_keywords: ["print", "reproduction", "repro", "poster", "giclee", "digital", "pattern", "cosplay"]
hot_alert: { max_price: 2000, min_confidence: 0.7 }   # tune with friend
ntfy_topic: "<long-random-string>"
```

This file is the only tuning surface. Friend edits it via GitHub web UI or texts JJ.

## 5. Data model (SQLite)

`listings`: id, source, source_listing_id, url, title, description_snippet, price_value, price_currency, listing_format, end_time, image_urls (JSON), image_phash, first_seen_at, last_seen_at, stage_reached, haiku_verdict, vision_json (JSON), confidence, attributed_artist, relisted_from (nullable FK), went_hot_at (nullable), alerted_at (nullable).

`runs`: id, started_at, finished_at, fetched_count, new_count, vision_call_count, est_cost_usd, errors (JSON).

## 6. Ops & guardrails

- Repo secrets: `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` (JJ's), `ANTHROPIC_API_KEY` (friend's). Never committed.
- First-run backfill cap: vision on max 150 listings, highest-confidence-gate first; remainder drains over subsequent hourly runs.
- Steady-state abort: >100 new survivors in one run → skip vision, publish a warning banner on the feed page instead (filter regression / query flood signal).
- Idempotent runs; re-runs never duplicate alerts (`alerted_at` guard).
- All HTTP retried 3x exponential backoff; per-stage timeout budget.
- Monthly spend estimate computed from token usage, displayed in feed footer — the friend's cost visibility.

## 7. Acceptance criteria

- [ ] `python -m sketchhound.run` executes full pipeline locally against live eBay with test watchlist
- [ ] Unit tests pass with mocked eBay payloads and mocked Anthropic responses (no live calls in CI)
- [ ] Dedup proven: same listing across two runs → one feed appearance; pHash relist test passes
- [ ] Vision never invoked on negative-keyword or Haiku-rejected listings; generic-query bypass verified
- [ ] Feed page renders correctly on mobile from gh-pages URL
- [ ] ntfy alert fires once and only once per hot listing
- [ ] Hourly Actions workflow runs, commits SQLite + regenerated page
- [ ] Backfill cap and abort cap both covered by tests

## 8. Out of scope (do not build)

Email of any kind, LiveAuctioneers/Invaluable (Phase 1.5 — mailbox-ingest decision deferred), provenance/upload tool (Phase 2), auto-bidding or purchasing actions, accounts/auth/multi-user, web app frameworks (the feed is static HTML), proxies, WorthPoint.
