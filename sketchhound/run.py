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

from . import config, dedup, fetch_ebay, normalize, persist
from .config import Secrets, Watchlist, load_watchlist
from .models import Listing, RunStats


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

    new_listings = dedup.dedup(conn, listings, now)
    stats.new_count = len(new_listings)
    return {
        "artist": [l for l in new_listings if kind_by_id[l.source_listing_id] == "artist"],
        "generic": [l for l in new_listings if kind_by_id[l.source_listing_id] == "generic"],
    }


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

    if args.skip_vision:
        print("[skip-vision] gate and vision stages skipped")
    else:
        # Build step 3: gate (negative keywords -> Haiku for artist queries),
        # then vision on new survivors + drained backlog, capped, with the
        # >ABORT_NEW_SURVIVORS guard.
        print("gate/vision not yet implemented (build step 3)")

    # Build step 4: publish docs/index.html.  Build step 5: ntfy push.

    stats.finished_at = datetime.now(timezone.utc)
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
