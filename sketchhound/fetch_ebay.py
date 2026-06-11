"""eBay Browse API client. Application-level OAuth (client-credentials flow),
token cached to .ebay_token.json (gitignored). All HTTP retried 3x with
exponential backoff. Free tier: 5k calls/day — hourly runs over ~13 queries
use a small fraction of that.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import Secrets
from .http_util import request_with_retry

EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
BROWSE_SCOPE = "https://api.ebay.com/oauth/api_scope"

# Browse API category IDs to constrain searches where sensible. The API only
# accepts ONE category_ids value per request, so each query is searched once
# per category and merged.
#   550   = Art
#   45100 = Entertainment Memorabilia
SEARCH_CATEGORY_IDS = ("550", "45100")

TOKEN_CACHE = Path(".ebay_token.json")
TOKEN_EXPIRY_BUFFER_SECONDS = 120
PAGE_SIZE = 50
MAX_RESULTS_PER_QUERY = 200


def get_app_token(secrets: Secrets, cache_path: Path = TOKEN_CACHE) -> str:
    """Client-credentials OAuth token, cached on disk until expiry."""
    now = time.time()
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("expires_at", 0) > now + TOKEN_EXPIRY_BUFFER_SECONDS:
                return cached["access_token"]
        except (json.JSONDecodeError, KeyError):
            pass  # corrupt cache → refetch

    resp = request_with_retry(
        "POST",
        EBAY_OAUTH_URL,
        auth=(secrets.ebay_client_id, secrets.ebay_client_secret),
        data={"grant_type": "client_credentials", "scope": BROWSE_SCOPE},
    )
    payload = resp.json()
    cache_path.write_text(
        json.dumps(
            {
                "access_token": payload["access_token"],
                "expires_at": now + int(payload["expires_in"]),
            }
        ),
        encoding="utf-8",
    )
    return payload["access_token"]


def search_category(
    token: str, query: str, category_id: str, max_results: int = MAX_RESULTS_PER_QUERY
) -> list[dict]:
    """One Browse API search within one category, paginating as needed.

    Returns raw item_summary dicts: title, shortDescription, price, buyingOptions,
    itemEndDate, image/additionalImages, itemId, itemWebUrl.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    items: list[dict] = []
    offset = 0
    while len(items) < max_results:
        resp = request_with_retry(
            "GET",
            EBAY_BROWSE_SEARCH_URL,
            headers=headers,
            params={
                "q": query,
                "category_ids": category_id,
                "limit": min(PAGE_SIZE, max_results - len(items)),
                "offset": offset,
            },
        )
        payload = resp.json()
        page = payload.get("itemSummaries", [])
        if not page:
            break
        items.extend(page)
        offset += len(page)
        if "next" not in payload or offset >= payload.get("total", 0):
            break
    return items


def search(token: str, query: str, max_results: int = MAX_RESULTS_PER_QUERY) -> list[dict]:
    """Search one query across all watch categories, merged and deduped by itemId."""
    seen: set[str] = set()
    merged: list[dict] = []
    for category_id in SEARCH_CATEGORY_IDS:
        for item in search_category(token, query, category_id, max_results):
            item_id = item.get("itemId")
            if item_id and item_id not in seen:
                seen.add(item_id)
                merged.append(item)
    return merged


def fetch_all(secrets: Secrets, queries: list[str]) -> dict[str, list[dict]]:
    """All queries → {query: [raw items]}. Query provenance matters downstream:
    artist queries are Haiku-gated, generic queries bypass straight to vision.
    Per-query failures raise; the orchestrator catches and records them so one
    bad query never kills the run.
    """
    token = get_app_token(secrets)
    return {query: search(token, query) for query in queries}
