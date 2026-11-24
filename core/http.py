"""One HTTP client used by every connector: timeouts, retries with back-off, polite rate limiting."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("http")

USER_AGENT = ("Mozilla/5.0 (compatible; IndiaJobIntelligence/1.0; personal job-search tracker; "
              "public endpoints only)")


class HttpClient:
    """Thin wrapper over ``requests.Session``.

    * every request has a timeout
    * GET/HEAD: 429/5xx retried with capped, jittered exponential back-off (honours ``Retry-After``, max 60 s);
      POST is never retried automatically; 4xx other than 429 (auth, permission, bad request) are never retried
    * a minimum delay between two requests to the same host (rate limiting)
    """

    def __init__(self, timeout: float = 25.0, max_retries: int = 3, delay: float = 1.0) -> None:
        self.timeout = timeout
        self.delay = max(0.0, delay)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.session = requests.Session()
        # Only idempotent methods are retried automatically. POST is NEVER retried here: an Apify "start run" POST
        # that was retried after a 5xx could start a second PAID run. Callers that need POST retries do it explicitly.
        # Back-off is exponential, capped (backoff_max), jittered, and Retry-After is honoured up to retry_after_max seconds.
        retry = Retry(total=max_retries, backoff_factor=1.5, backoff_max=30, backoff_jitter=0.5,
                      status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET", "HEAD"}),
                      respect_retry_after_header=True, retry_after_max=60, raise_on_status=False)
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9, */*;q=0.5"})

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            wait = self._last.get(host, 0.0) + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self._throttle(url)
        kwargs.setdefault("timeout", self.timeout)
        return self.session.request(method, url, **kwargs)

    def get(self, url: str, **kw: Any) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> requests.Response:
        return self.request("POST", url, **kw)

    def get_json(self, url: str, **kw: Any) -> Any:
        resp = self.get(url, **kw)
        resp.raise_for_status()
        return resp.json()

    def post_json(self, url: str, **kw: Any) -> Any:
        resp = self.post(url, **kw)
        resp.raise_for_status()
        return resp.json()
