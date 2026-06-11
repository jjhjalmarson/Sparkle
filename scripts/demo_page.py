"""Generate a demo feed page with fixture data for visual/template work.

Usage: python scripts/demo_page.py [--banner] [--out _demo/index.html]
No network, no secrets — everything is synthetic.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sketchhound import persist, publish_page
from sketchhound.config import HotAlertConfig, Watchlist
from sketchhound.models import Listing, ListingFormat, RunStats, Stage, VisionResult

NOW = datetime.now(timezone.utc)
WATCHLIST = Watchlist(hot_alert=HotAlertConfig(min_price=500, min_confidence=0.7))


def fixture(
    n: int,
    title: str,
    *,
    conf: float,
    artist: str = "unknown",
    attr_conf: float = 0.0,
    fmt: ListingFormat = ListingFormat.BUY_IT_NOW,
    price: float = 450.0,
    seen_hours_ago: float = 2,
    ends_in_hours: float = 96,
    signals: list[str] | None = None,
    red_flags: list[str] | None = None,
) -> Listing:
    return Listing(
        source="ebay",
        source_listing_id=f"v1|demo{n}|0",
        url=f"https://www.ebay.com/itm/demo{n}",
        title=title,
        description_snippet=None,
        price_value=price,
        price_currency="USD",
        listing_format=fmt,
        end_time=NOW + timedelta(hours=ends_in_hours),
        image_urls=[f"https://picsum.photos/seed/sketch{n}/400/400"],
        first_seen_at=NOW - timedelta(hours=seen_hours_ago),
        last_seen_at=NOW,
        stage_reached=Stage.VISION_SCORED,
        vision=VisionResult(
            is_costume_design_sketch=True,
            confidence=conf,
            attributed_artist=artist,
            attribution_confidence=attr_conf,
            signals=signals or ["gouache on illustration board", "pencil underdrawing"],
            era_estimate="1950s",
            red_flags=red_flags or [],
            summary="Demo listing.",
        ),
        confidence=conf,
        attributed_artist=artist,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--banner", action="store_true", help="render the abort banner variant")
    parser.add_argument("--out", type=Path, default=Path("_demo/index.html"))
    args = parser.parse_args()

    conn = persist.connect(":memory:")
    fixtures = [
        fixture(1, "Edith Head original costume sketch Paramount 1954 gouache", conf=0.92,
                artist="Edith Head", attr_conf=0.85, price=1450,
                signals=["signature lower right", "Paramount wardrobe stamp", "actress name annotation"]),
        fixture(2, "Vintage Hollywood wardrobe drawing — unmarked studio piece", conf=0.78,
                price=825, seen_hours_ago=5),
        fixture(3, "Bob Mackie signed gown design, Carol Burnett Show era", conf=0.88,
                artist="Bob Mackie", attr_conf=0.74, fmt=ListingFormat.AUCTION,
                price=620, seen_hours_ago=40,
                signals=["signature", "production number 47-C", "attached fabric swatch"]),
        fixture(4, "Travilla attributed costume design, fox stamp verso", conf=0.81,
                artist="Travilla", attr_conf=0.66, fmt=ListingFormat.AUCTION,
                price=240, seen_hours_ago=70, ends_in_hours=14),
        fixture(5, "Mid-century fashion illustration, possibly studio wardrobe", conf=0.58,
                price=85, seen_hours_ago=30,
                red_flags=["paper whiter than era suggests"]),
        fixture(6, "Unsigned costume rendering, MGM-style board", conf=0.64,
                fmt=ListingFormat.AUCTION, price=150, seen_hours_ago=60, ends_in_hours=40),
    ]
    for listing in fixtures:
        persist.insert_listing(conn, listing)

    sections = publish_page.select_sections(conn, WATCHLIST, NOW)
    stats = RunStats(
        started_at=NOW, finished_at=NOW, fetched_count=412, new_count=6,
        vision_call_count=6, est_cost_usd=0.19,
    )
    banner = (
        "Run aborted before vision scoring: 137 new listings survived the gate in one run "
        "(limit 100). This usually means a filter regression — review watchlist.yaml."
        if args.banner
        else None
    )
    html = publish_page.render(sections, stats, month_spend=4.37, banner=banner)
    out = publish_page.publish(html, args.out.parent)
    counts = {k: len(v) for k, v in sections.items()}
    print(f"wrote {out} — sections: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
