"""Shared HTTP helper: every outbound request retries 3x with exponential
backoff (brief section 6). 4xx responses (other than 429) are real errors and
raise immediately; 429/5xx and connection/timeout failures retry.
"""

from __future__ import annotations

import time

import requests

from .config import HTTP_RETRIES, HTTP_TIMEOUT_SECONDS


def request_with_retry(
    method: str,
    url: str,
    *,
    retries: int = HTTP_RETRIES,
    backoff_seconds: float = 1.0,
    **kwargs,
) -> requests.Response:
    kwargs.setdefault("timeout", HTTP_TIMEOUT_SECONDS)
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.request(method, url, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
        else:
            if resp.status_code < 500 and resp.status_code != 429:
                resp.raise_for_status()
                return resp
            last_error = requests.HTTPError(f"HTTP {resp.status_code} from {url}")
        if attempt < retries - 1:
            time.sleep(backoff_seconds * (2**attempt))
    raise last_error  # type: ignore[misc]


def download_bytes(url: str, **kwargs) -> bytes:
    return request_with_retry("GET", url, **kwargs).content
