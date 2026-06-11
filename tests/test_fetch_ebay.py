"""eBay client tests: OAuth token caching, search pagination, HTTP retry.
All HTTP mocked via `responses` — no live calls.
"""

import json
import time

import pytest
import requests
import responses

from sketchhound import fetch_ebay, http_util
from sketchhound.config import Secrets

SECRETS = Secrets(ebay_client_id="cid", ebay_client_secret="csec")


def _register_token(token="tok-fresh"):
    responses.add(
        responses.POST,
        fetch_ebay.EBAY_OAUTH_URL,
        json={"access_token": token, "expires_in": 7200},
    )


@responses.activate
def test_token_fetched_and_cached(tmp_path):
    cache = tmp_path / "token.json"
    _register_token()

    assert fetch_ebay.get_app_token(SECRETS, cache) == "tok-fresh"
    assert fetch_ebay.get_app_token(SECRETS, cache) == "tok-fresh"  # served from cache
    assert len(responses.calls) == 1

    cached = json.loads(cache.read_text())
    assert cached["expires_at"] > time.time()


@responses.activate
def test_expired_token_refetched(tmp_path):
    cache = tmp_path / "token.json"
    cache.write_text(json.dumps({"access_token": "tok-stale", "expires_at": time.time() - 10}))
    _register_token("tok-new")

    assert fetch_ebay.get_app_token(SECRETS, cache) == "tok-new"
    assert len(responses.calls) == 1


@responses.activate
def test_search_paginates():
    page1 = {
        "total": 3,
        "next": "https://api.ebay.com/...&offset=2",
        "itemSummaries": [{"itemId": "1"}, {"itemId": "2"}],
    }
    page2 = {"total": 3, "itemSummaries": [{"itemId": "3"}]}
    responses.add(responses.GET, fetch_ebay.EBAY_BROWSE_SEARCH_URL, json=page1)
    responses.add(responses.GET, fetch_ebay.EBAY_BROWSE_SEARCH_URL, json=page2)

    items = fetch_ebay.search("tok", "edith head sketch", max_results=10)
    assert [i["itemId"] for i in items] == ["1", "2", "3"]
    assert len(responses.calls) == 2
    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok"
    assert "category_ids" in responses.calls[0].request.url


@responses.activate
def test_search_empty():
    responses.add(responses.GET, fetch_ebay.EBAY_BROWSE_SEARCH_URL, json={"total": 0})
    assert fetch_ebay.search("tok", "nothing") == []


@responses.activate
def test_retry_on_5xx_then_success():
    url = "https://example.com/flaky"
    responses.add(responses.GET, url, status=500)
    responses.add(responses.GET, url, status=503)
    responses.add(responses.GET, url, json={"ok": True})

    resp = http_util.request_with_retry("GET", url, backoff_seconds=0)
    assert resp.json() == {"ok": True}
    assert len(responses.calls) == 3


@responses.activate
def test_no_retry_on_4xx():
    url = "https://example.com/missing"
    responses.add(responses.GET, url, status=404)

    with pytest.raises(requests.HTTPError):
        http_util.request_with_retry("GET", url, backoff_seconds=0)
    assert len(responses.calls) == 1


@responses.activate
def test_retries_exhausted_raises():
    url = "https://example.com/down"
    for _ in range(3):
        responses.add(responses.GET, url, status=500)

    with pytest.raises(requests.HTTPError):
        http_util.request_with_retry("GET", url, backoff_seconds=0)
    assert len(responses.calls) == 3
