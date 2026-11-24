"""HttpClient retry behaviour against a throw-away server on 127.0.0.1 (no external network)."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.http import HttpClient


class Handler(BaseHTTPRequestHandler):
    script: list = []          # list of (status, headers) consumed in order; last one repeats
    hits: list = []

    def _go(self):
        Handler.hits.append(self.command)
        status, headers = Handler.script[min(len(Handler.hits) - 1, len(Handler.script) - 1)]
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = do_POST = _go

    def log_message(self, *a):
        pass


@pytest.fixture()
def server(monkeypatch):
    sleeps = []
    monkeypatch.setattr("urllib3.util.retry.time.sleep", lambda s: sleeps.append(s))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Handler.hits = []
    yield f"http://127.0.0.1:{srv.server_port}", sleeps
    srv.shutdown()


def client():
    return HttpClient(timeout=5, max_retries=3, delay=0)


def test_get_retries_503_then_succeeds(server):
    url, sleeps = server
    Handler.script = [(503, {}), (503, {}), (200, {})]
    assert client().get(url).status_code == 200
    assert Handler.hits.count("GET") == 3


def test_backoff_is_bounded_and_retries_are_limited(server):
    url, sleeps = server
    Handler.script = [(503, {})]
    r = client().get(url)
    assert r.status_code == 503 and Handler.hits.count("GET") == 4          # 1 try + 3 retries
    assert all(s <= 30.0 for s in sleeps)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_errors_are_not_retried(server, status):
    url, _ = server
    Handler.script = [(status, {})]
    assert client().get(url).status_code == status
    assert Handler.hits.count("GET") == 1


def test_post_is_never_retried(server):
    url, _ = server
    Handler.script = [(503, {})]
    assert client().post(url, json={}).status_code == 503
    assert Handler.hits.count("POST") == 1


def test_retry_after_is_honoured_but_capped(server):
    url, sleeps = server
    Handler.script = [(429, {"Retry-After": "7"}), (200, {})]
    assert client().get(url).status_code == 200
    assert 7 in [round(s) for s in sleeps]
    Handler.hits.clear(); sleeps.clear()
    Handler.script = [(429, {"Retry-After": "99999"}), (200, {})]
    try:
        client().get(url)
    except Exception:
        pass
    assert all(s <= 60 for s in sleeps)


def test_retry_policy_has_jitter_and_cap():
    adapter = client().session.get_adapter("https://x.example")
    r = adapter.max_retries
    assert r.backoff_jitter > 0 and r.backoff_max <= 30 and "POST" not in r.allowed_methods
