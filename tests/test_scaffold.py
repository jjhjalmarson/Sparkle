"""Scaffold sanity: package imports, watchlist parses, schema applies.

Real suites land with each build step:
  test_fetch_ebay.py   - mocked Browse API payloads, token caching (step 2)
  test_dedup.py        - same listing across two runs -> one row; pHash relist (step 2)
  test_gate.py         - negative keywords, Haiku mock, generic bypass (step 3)
  test_vision_score.py - mocked Sonnet JSON contract, backfill/abort caps (step 3)
  test_publish_page.py - section selection, banner, footer stats (step 4)
  test_push_alerts.py  - fires once and only once per hot listing (step 5)
No live eBay or Anthropic calls anywhere in CI.
"""

import sqlite3

from sketchhound import config, persist
from sketchhound.models import Listing, Stage  # noqa: F401 — import smoke test


def test_watchlist_parses():
    wl = config.load_watchlist()
    assert wl.artist_queries and wl.generic_queries
    assert wl.negative_keywords
    assert 0 < wl.hot_alert.min_confidence <= 1
    assert wl.ntfy_topic


def test_schema_applies_in_memory():
    conn = sqlite3.connect(":memory:")
    conn.executescript(persist.SCHEMA)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"listings", "runs"} <= tables
