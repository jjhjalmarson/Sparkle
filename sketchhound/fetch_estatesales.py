"""estatesales.net search-details API client (no auth required).

Search results are sale-level containers; individual matching lots are extracted
from each sale's `highlightedPictures` array. Each lot links back to a
LiveAuctioneers item via globalAtgLotUrl and has a stable UUID (globalAtgLotId)
used for deduplication.

1s delay between paginated pages to stay polite on an unauthenticated API.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from .http_util import request_with_retry

SEARCH_URL = "https://www.estatesales.net/api/search-details"
PAGE_SIZE = 25
MAX_RESULTS_PER_QUERY = 50

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SketchHound/1.0; collector research bot)",
    "Accept": "application/json",
}
_INCLUDE = "highlightedPictures,dates"
_MULTISELECT = "saleDetail:id,orgName,name,lastLocalEndDate"


def _parse_dt(node: dict | None) -> datetime | None:
    if not node or node.get("_type") != "DateTime":
        return None
    try:
        return datetime.fromisoformat(node["_value"].replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (KeyError, ValueError):
        return None


def _extract_lots(sale: dict) -> list[dict]:
    end_time = _parse_dt(sale.get("lastLocalEndDate"))
    org_name = sale.get("orgName", "")
    lots = []
    for pic in sale.get("highlightedPictures") or []:
        lot_id = pic.get("globalAtgLotId")
        lot_url = pic.get("globalAtgLotUrl")
        title = pic.get("description")
        if not (lot_id and lot_url and title):
            continue
        lots.append(
            {
                "lot_id": lot_id,
                "lot_url": lot_url,
                "title": title,
                "image_url": pic.get("url"),
                "thumbnail_url": pic.get("thumbnailUrl"),
                "end_time": end_time,
                "org_name": org_name,
            }
        )
    return lots


def search(query: str, max_results: int = MAX_RESULTS_PER_QUERY) -> list[dict]:
    """Search one query; return extracted lot dicts deduped by lot_id."""
    seen: set[str] = set()
    results: list[dict] = []
    skip = 0
    while len(results) < max_results:
        take = min(PAGE_SIZE, max_results - len(results))
        resp = request_with_retry(
            "GET",
            SEARCH_URL,
            headers=_HEADERS,
            params={
                "aggsMetadata": "true",
                "filter": f"byCombinedSearch:{query}|highlight|skip:{skip}|take:{take}|withCountAggs",
                "include": _INCLUDE,
                "multiSelect": _MULTISELECT,
                "explicitTypes": "DateTime",
            },
        )
        payload = resp.json()
        sales = payload.get("items") or []
        if not sales:
            break
        for sale in sales:
            for lot in _extract_lots(sale):
                if lot["lot_id"] not in seen:
                    seen.add(lot["lot_id"])
                    results.append(lot)
        skip += len(sales)
        if len(sales) < take:
            break
        time.sleep(1.0)
    return results


def fetch_all(queries: list[str]) -> dict[str, list[dict]]:
    """All queries → {query: [raw lot dicts]}."""
    return {query: search(query) for query in queries}
