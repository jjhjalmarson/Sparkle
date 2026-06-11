"""Static feed page generator → docs/index.html, served by GitHub Pages.

Mobile-first, zero client-side JS. Sections in priority order (brief
section 3 "Publish") — a listing appears in its highest section only:
1. Hot — BIN, confidence ≥ threshold, within budget, first seen <24h ago
2. High-confidence attributions — any format, named artist
3. Probable sketches, unattributed
4. Ending soon — previously surfaced auctions ending <48h

Relistings are suppressed unless price dropped >20% vs the original.
If the abort guard tripped, a warning banner renders at the top.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Template

from . import persist
from .config import (
    ENDING_SOON_HOURS,
    HOT_LISTED_WITHIN_HOURS,
    RELIST_PRICE_DROP,
    Watchlist,
)
from .models import Listing, ListingFormat, RunStats, Stage

# Section-2/3 floors. The hot threshold comes from watchlist.yaml; these are
# layout decisions, not collector tuning, so they live in code.
ATTRIBUTION_MIN_CONFIDENCE = 0.6
PROBABLE_MIN_CONFIDENCE = 0.5

SECTION_TITLES = {
    "hot": "Hot — Buy It Now",
    "attributed": "High-confidence attributions",
    "probable": "Probable sketches, unattributed",
    "ending_soon": "Ending soon",
}


def _visible(conn: sqlite3.Connection, listing: Listing, now: datetime) -> bool:
    """Sketch verdict + not-ended + relist suppression."""
    if not listing.vision or not listing.vision.is_costume_design_sketch:
        return False
    if listing.end_time is not None and listing.end_time < now:
        return False  # ended listings leave the feed
    if listing.stage_reached is Stage.RELISTED:
        if listing.relisted_from is None or listing.price_value is None:
            return False
        original = persist.original_price(conn, listing.relisted_from)
        if original is None or original <= 0:
            return False
        return listing.price_value < original * (1 - RELIST_PRICE_DROP)
    return True


def is_hot(listing: Listing, watchlist: Watchlist, now: datetime) -> bool:
    return (
        listing.listing_format is ListingFormat.BUY_IT_NOW
        and (listing.confidence or 0) >= watchlist.hot_alert.min_confidence
        and listing.price_value is not None
        and listing.price_value <= watchlist.hot_alert.max_price
        and listing.first_seen_at is not None
        and now - listing.first_seen_at < timedelta(hours=HOT_LISTED_WITHIN_HOURS)
    )


def _is_attributed(listing: Listing) -> bool:
    return bool(
        listing.attributed_artist
        and listing.attributed_artist.lower() != "unknown"
        and listing.vision.attribution_confidence >= ATTRIBUTION_MIN_CONFIDENCE
    )


def _meets_feed_floor(listing: Listing) -> bool:
    return _is_attributed(listing) or (listing.confidence or 0) >= PROBABLE_MIN_CONFIDENCE


def select_sections(
    conn: sqlite3.Connection, watchlist: Watchlist, now: datetime
) -> dict[str, list[Listing]]:
    """Query the DB into the four feed sections; each listing appears once.

    "Ending soon" claims feed-worthy auctions closing <48h ahead of sections
    2/3 — qualification is the stateless proxy for "previously surfaced",
    and the point of the section is that those listings need urgency, not
    their usual slot.
    """
    candidates = [l for l in persist.feed_candidates(conn) if _visible(conn, l, now)]
    placed: set[int] = set()
    sections: dict[str, list[Listing]] = {}

    hot = [l for l in candidates if is_hot(l, watchlist, now)]
    hot.sort(key=lambda l: l.first_seen_at, reverse=True)
    placed.update(l.id for l in hot)
    sections["hot"] = hot

    ending_soon = [
        l
        for l in candidates
        if l.id not in placed
        and _meets_feed_floor(l)
        and l.listing_format is ListingFormat.AUCTION
        and l.end_time is not None
        and now <= l.end_time <= now + timedelta(hours=ENDING_SOON_HOURS)
    ]
    ending_soon.sort(key=lambda l: l.end_time)
    placed.update(l.id for l in ending_soon)
    sections["ending_soon"] = ending_soon

    attributed = [l for l in candidates if l.id not in placed and _is_attributed(l)]
    attributed.sort(key=lambda l: (l.vision.attribution_confidence, l.confidence), reverse=True)
    placed.update(l.id for l in attributed)
    sections["attributed"] = attributed

    probable = [
        l
        for l in candidates
        if l.id not in placed and (l.confidence or 0) >= PROBABLE_MIN_CONFIDENCE
    ]
    probable.sort(key=lambda l: l.confidence, reverse=True)
    sections["probable"] = probable

    return sections


def mark_newly_hot(conn: sqlite3.Connection, hot: list[Listing], now: datetime) -> list[Listing]:
    """Stamp went_hot_at on first entry into Hot; returns the newly-hot subset
    (the only listings push_alerts may notify about)."""
    newly = [l for l in hot if l.went_hot_at is None]
    for listing in newly:
        listing.went_hot_at = now
        persist.update_listing(conn, listing)
    return newly


PAGE_TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>SketchHound</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0 auto; max-width: 640px; padding: 0 12px 48px;
         font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         background: Canvas; color: CanvasText; }
  header { padding: 20px 0 4px; }
  h1 { font-size: 1.5rem; margin: 0; }
  .tagline { margin: 2px 0 0; font-size: .85rem; opacity: .65; }
  .banner { background: #b3261e; color: #fff; border-radius: 10px;
            padding: 12px 14px; margin: 14px 0; font-size: .95rem; }
  h2 { font-size: 1.05rem; margin: 26px 0 10px; padding-bottom: 4px;
       border-bottom: 1px solid color-mix(in srgb, CanvasText 18%, transparent); }
  .empty { font-size: .85rem; opacity: .55; margin: 8px 0 0; }
  .card { display: flex; gap: 12px; padding: 12px;
          border: 1px solid color-mix(in srgb, CanvasText 14%, transparent);
          border-radius: 12px; margin-bottom: 10px; }
  .card a.thumb { flex: none; }
  .card img { width: 92px; height: 92px; object-fit: cover; border-radius: 8px;
              background: color-mix(in srgb, CanvasText 8%, transparent); }
  .card .body { min-width: 0; }
  .title { font-size: .95rem; font-weight: 600; margin: 0 0 4px; }
  .title a { color: inherit; text-decoration: none; }
  .meta { font-size: .85rem; margin: 0 0 4px; }
  .meta .price { font-weight: 700; }
  .badge { display: inline-block; font-size: .75rem; font-weight: 600;
           border-radius: 6px; padding: 1px 7px; margin-left: 6px;
           background: color-mix(in srgb, CanvasText 10%, transparent); }
  .badge.hot { background: #b3261e; color: #fff; }
  .signals { font-size: .8rem; opacity: .75; margin: 0 0 4px; }
  .stamps { font-size: .75rem; opacity: .55; margin: 0; }
  footer { margin-top: 36px; font-size: .78rem; opacity: .65;
           border-top: 1px solid color-mix(in srgb, CanvasText 18%, transparent);
           padding-top: 12px; }
</style>
</head>
<body>
<header>
  <h1>🐕 SketchHound</h1>
  <p class="tagline">Film costume design sketches, sniffed out hourly on eBay</p>
</header>

{% if banner %}<div class="banner">⚠ {{ banner }}</div>{% endif %}

{% for key, title in section_titles.items() %}
<section>
  <h2>{{ title }}</h2>
  {% if not sections[key] %}<p class="empty">Nothing right now.</p>{% endif %}
  {% for l in sections[key] %}
  <article class="card">
    {% if l.image_urls %}<a class="thumb" href="{{ l.url }}"><img src="{{ l.image_urls[0] }}" alt="" loading="lazy"></a>{% endif %}
    <div class="body">
      <p class="title"><a href="{{ l.url }}">{{ l.title }}</a></p>
      <p class="meta">
        <span class="price">{{ "%.0f"|format(l.price_value) if l.price_value is not none else "?" }} {{ l.price_currency or "" }}</span>
        · {{ "Buy It Now" if l.listing_format and l.listing_format.value == "buy_it_now" else "Auction" }}
        {% if l.end_time %} · ends {{ l.end_time.strftime("%b %d, %H:%M") }} UTC{% endif %}
        {% if key == "hot" %}<span class="badge hot">HOT</span>{% endif %}
      </p>
      <p class="meta">
        Sketch confidence {{ "%.0f%%"|format(l.confidence * 100) }}
        {% if l.attributed_artist and l.attributed_artist|lower != "unknown" %}
          · <strong>{{ l.attributed_artist }}</strong>
          ({{ "%.0f%%"|format(l.vision.attribution_confidence * 100) }})
        {% endif %}
      </p>
      {% if l.vision and l.vision.signals %}<p class="signals">{{ l.vision.signals[:4]|join(" · ") }}</p>{% endif %}
      <p class="stamps">first seen {{ l.first_seen_at.strftime("%b %d, %H:%M") }} UTC</p>
    </div>
  </article>
  {% endfor %}
</section>
{% endfor %}

<footer>
  Last run {{ stats.finished_at.strftime("%b %d %Y, %H:%M") }} UTC ·
  {{ stats.fetched_count }} fetched · {{ stats.new_count }} new ·
  {{ stats.vision_call_count }} vision calls
  {% if stats.errors %} · {{ stats.errors|length }} errors{% endif %}<br>
  Estimated model spend this month: ${{ "%.2f"|format(month_spend) }}
</footer>
</body>
</html>
"""
)


def render(
    sections: dict[str, list[Listing]],
    stats: RunStats,
    month_spend: float,
    banner: str | None,
) -> str:
    return PAGE_TEMPLATE.render(
        sections=sections,
        section_titles=SECTION_TITLES,
        stats=stats,
        month_spend=month_spend,
        banner=banner,
    )


def publish(html: str, site_dir: Path) -> Path:
    """Write docs/index.html (+ .nojekyll). The Actions workflow commits it."""
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / ".nojekyll").touch()
    out = site_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    return out
