"""Pipeline orchestrator. `python -m sketchhound.run` executes one full run:

    fetch (eBay) -> normalize -> dedup -> gate -> vision score
        -> persist -> publish page -> push hot alerts

Guardrails enforced here (brief section 6):
- Vision pool = new gate survivors + previously persisted GATE_SURVIVOR rows
  (the backfill drain), capped at BACKFILL_VISION_CAP per run.
- Abort: >ABORT_NEW_SURVIVORS new survivors this run -> skip vision entirely,
  publish the feed with a warning banner.
- Stage failures append to run errors but never abandon the run: the page is
  republished and the run row recorded even on a partial run.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from . import config, dedup, fetch_ebay, fetch_estatesales, gate, normalize, persist, publish_page, push_alerts, vision_score
from .config import ABORT_NEW_SURVIVORS, BACKFILL_VISION_CAP, Secrets, Watchlist, load_watchlist
from .models import Listing, RunStats

ABORT_BANNER = (
    "Run aborted before vision scoring: {count} new listings survived the gate "
    "in one run (limit {limit}). This usually means a filter regression or a "
    "query flood — review watchlist.yaml. No model spend occurred; the backlog "
    "will drain once resolved."
)


def fetch_and_dedup(
    conn,
    watchlist: Watchlist,
    secrets: Secrets,
    stats: RunStats,
    now: datetime,
) -> dict[str, list[Listing]]:
    """fetch -> normalize -> dedup. Returns genuinely-new listings keyed by
    query kind ('artist' | 'generic'); everything else is already persisted."""
    token = fetch_ebay.get_app_token(secrets)
    raw_by_kind: dict[str, list[dict]] = {"artist": [], "generic": []}
    for kind, queries in (("artist", watchlist.artist_queries), ("generic", watchlist.generic_queries)):
        for query in queries:
            try:
                raw_by_kind[kind].extend(fetch_ebay.search(token, query))
            except Exception as exc:
                stats.errors.append(f"fetch {kind} query '{query}': {exc}")
    stats.fetched_count = sum(len(v) for v in raw_by_kind.values())

    # Normalize, tagging provenance. An item surfaced by both an artist and a
    # generic query goes through the artist path: cheaper gate, and the title
    # evidently carries artist signal.
    kind_by_id: dict[str, str] = {}
    listings: list[Listing] = []
    for kind in ("artist", "generic"):
        for raw in raw_by_kind[kind]:
            try:
                listing = normalize.normalize_ebay_item(raw)
            except Exception as exc:
                stats.errors.append(f"normalize {raw.get('itemId', '?')}: {exc}")
                continue
            if listing.source_listing_id not in kind_by_id:
                kind_by_id[listing.source_listing_id] = kind
                listings.append(listing)

    for kind, queries in (("artist", watchlist.artist_queries), ("generic", watchlist.generic_queries)):
        for query in queries:
            try:
                lots = fetch_estatesales.search(query)
                stats.fetched_count += len(lots)
                for raw in lots:
                    try:
                        listing = normalize.normalize_estatesales_lot(raw)
                    except Exception as exc:
                        stats.errors.append(f"normalize estatesales {raw.get('lot_id', '?')}: {exc}")
                        continue
                    if listing.source_listing_id not in kind_by_id:
                        kind_by_id[listing.source_listing_id] = kind
                        listings.append(listing)
            except Exception as exc:
                stats.errors.append(f"fetch estatesales {kind} query '{query}': {exc}")

    new_listings = dedup.dedup(conn, listings, now)
    stats.new_count = len(new_listings)
    return {
        "artist": [l for l in new_listings if kind_by_id[l.source_listing_id] == "artist"],
        "generic": [l for l in new_listings if kind_by_id[l.source_listing_id] == "generic"],
    }


def gate_and_score(
    conn,
    new_by_kind: dict[str, list[Listing]],
    watchlist: Watchlist,
    anthropic_client,
    stats: RunStats,
    fetch_image=None,
) -> str | None:
    """Gate new listings, then vision-score the queue. Returns a feed banner
    when the abort guard trips, else None.

    Guardrails (brief section 6):
    - >ABORT_NEW_SURVIVORS new survivors this run → skip vision entirely.
      The guard is STEADY-STATE only: until the first vision batch has ever
      run (total vision calls == 0) the system is in initial backfill, where
      a flood is expected and the per-run cap is the cost control.
    - Vision queue = persisted GATE_SURVIVOR rows (this run's survivors plus
      any backfill backlog), capped at BACKFILL_VISION_CAP per run, drained
      highest-confidence-gate first.
    """
    survivors, rejects = gate.gate(new_by_kind, watchlist, anthropic_client)
    for listing in (*rejects, *survivors):
        persist.update_listing(conn, listing)

    steady_state = persist.total_vision_calls(conn) > 0
    if steady_state and len(survivors) > ABORT_NEW_SURVIVORS:
        stats.errors.append(f"abort guard: {len(survivors)} new gate survivors")
        return ABORT_BANNER.format(count=len(survivors), limit=ABORT_NEW_SURVIVORS)

    queue = persist.pending_vision(conn, limit=BACKFILL_VISION_CAP)
    scored, cost, calls, errors = vision_score.score_batch(
        queue,
        anthropic_client,
        cap=BACKFILL_VISION_CAP,
        fetch_image=fetch_image,
        # Persist as we go: a mid-batch crash loses at most one paid call.
        on_scored=lambda listing: persist.update_listing(conn, listing),
    )
    stats.vision_call_count += calls
    stats.est_cost_usd += cost
    stats.errors.extend(errors)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sketchhound.run")
    parser.add_argument("--watchlist", type=Path, default=config.DEFAULT_WATCHLIST)
    parser.add_argument("--db", type=Path, default=config.DEFAULT_DB)
    parser.add_argument("--site-dir", type=Path, default=config.DEFAULT_SITE_DIR)
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="Run fetch/dedup/persist/publish without model calls (no ANTHROPIC_API_KEY needed)",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    stats = RunStats(started_at=now)
    watchlist = load_watchlist(args.watchlist)
    try:
        secrets = Secrets.from_env(require_anthropic=not args.skip_vision)
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 2
    conn = persist.connect(args.db)

    new_by_kind = fetch_and_dedup(conn, watchlist, secrets, stats, now)

    banner: str | None = None
    if args.skip_vision:
        print("[skip-vision] gate and vision stages skipped")
    else:
        import anthropic

        client = anthropic.Anthropic(api_key=secrets.anthropic_api_key)
        banner = gate_and_score(conn, new_by_kind, watchlist, client, stats)

    stats.finished_at = datetime.now(timezone.utc)
    newly_hot: list[Listing] = []
    try:
        sections = publish_page.select_sections(conn, watchlist, now)
        newly_hot = publish_page.mark_newly_hot(conn, sections["hot"], now)
        # This run isn't recorded yet, so add its spend to the footer figure.
        month_spend = persist.month_spend_usd(conn, now) + stats.est_cost_usd
        html = publish_page.render(sections, stats, month_spend, banner)
        out = publish_page.publish(html, args.site_dir)
        print(f"published {out} ({sum(len(v) for v in sections.values())} listings, {len(newly_hot)} newly hot)")
    except Exception as exc:
        stats.errors.append(f"publish: {exc}")

    sent, alert_errors = push_alerts.push_hot_alerts(
        conn, watchlist.ntfy_topic, now, feed_url=watchlist.feed_url
    )
    stats.errors.extend(alert_errors)
    if sent:
        print(f"sent {sent} hot alert(s) to ntfy")

    persist.record_run(conn, stats)

    print(
        f"run complete: fetched={stats.fetched_count} new={stats.new_count} "
        f"(artist={len(new_by_kind['artist'])}, generic={len(new_by_kind['generic'])}) "
        f"vision_calls={stats.vision_call_count} est_cost=${stats.est_cost_usd:.4f} "
        f"errors={len(stats.errors)}"
    )
    for error in stats.errors:
        print(f"  error: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
