"""eBay Browse API client. Application-level OAuth (client-credentials flow),
token cached to .ebay_token.json (gitignored). All HTTP retried 3x with
exponential backoff. Free tier: 5k calls/day — hourly runs over ~13 queries
use a small fraction of that.
"""

from __future__ import annotations

from .config import Secrets

EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
BROWSE_SCOPE = "https://api.ebay.com/oauth/api_scope"

# Browse API category IDs to constrain searches where sensible:
#   550   = Art
#   45100 = Entertainment Memorabilia
SEARCH_CATEGORY_IDS = "550,45100"


def get_app_token(secrets: Secrets) -> str:
    """Client-credentials OAuth token, cached on disk until expiry."""
    raise NotImplementedError  # step 2


def search(token: str, query: str, limit: int = 50) -> list[dict]:
    """Run one Browse API search, paginating as needed.

    Returns raw item_summary dicts: title, shortDescription, price, buyingOptions,
    itemEndDate, image/additionalImages, itemId, itemWebUrl.
    """
    raise NotImplementedError  # step 2


def fetch_all(secrets: Secrets, queries: list[str]) -> dict[str, list[dict]]:
    """All queries → {query: [raw items]}. Query provenance matters downstream:
    artist queries are Haiku-gated, generic queries bypass straight to vision.
    """
    raise NotImplementedError  # step 2
