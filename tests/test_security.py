"""Security fixes from the 24 Nov 2026 audit: host check, browser safety headers, /docs off by
default, Alpaca keys never follow a redirect, and the feed entity check reads the whole feed."""

import httpx
import pytest
from fastapi.testclient import TestClient

from apex.api.app import SECURITY_HEADERS, create_app
from apex.core.config import Settings
from apex.core.errors import ProviderError
from apex.news.parse import FeedError, parse_feed
from apex.news.sources import FEEDS, Fetcher


def _client(db_url, **kw):
    return TestClient(create_app(Settings(_env_file=None, database_url=db_url, **kw)))


def test_default_hosts_are_this_computer_only(monkeypatch):
    monkeypatch.delenv("APEX_ALLOWED_HOSTS", raising=False)  # conftest adds "testserver"
    s = Settings(_env_file=None)
    assert s.allowed_hosts == ["127.0.0.1", "localhost"] and s.api_docs is False


def test_unknown_host_name_is_refused(db_url):
    """A page that points its own name at 127.0.0.1 (DNS rebinding) gets nothing back."""
    c = _client(db_url, allowed_hosts=["127.0.0.1", "localhost"])
    for path in ("/health", "/paper/accounts", "/ui/", "/system/checks"):
        assert c.get(path, headers={"Host": "attacker.example:8000"}).status_code == 400, path
    assert c.get("/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200
    assert c.get("/datasets", headers={"Host": "localhost"}).status_code == 200


def test_host_check_runs_before_the_token(db_url):
    from pydantic import SecretStr

    c = _client(db_url, allowed_hosts=["127.0.0.1"], api_token=SecretStr("tok"))
    r = c.get("/datasets", headers={"Host": "evil.example",
                                    "Authorization": "Bearer tok"})
    assert r.status_code == 400


def test_browser_safety_headers_on_every_response(db_url):
    c = _client(db_url)
    for path in ("/ui/", "/health", "/datasets", "/nope"):
        r = c.get(path)
        for k, v in SECURITY_HEADERS.items():
            assert r.headers.get(k) == v, (path, k)
    csp = SECURITY_HEADERS["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp and "http" not in csp  # nothing from other sites


def test_docs_pages_off_by_default(db_url):
    c = _client(db_url)
    assert c.get("/docs").status_code == 404 and c.get("/redoc").status_code == 404
    assert c.get("/openapi.json").status_code == 200
    on = _client(db_url, api_docs=True)
    assert on.get("/docs").status_code == 200
    assert "Content-Security-Policy" not in on.get("/docs").headers  # CDN scripts allowed there
    assert on.get("/docs").headers["X-Frame-Options"] == "DENY"


def test_alpaca_keys_do_not_follow_a_redirect():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.host, req.headers.get("APCA-API-SECRET-KEY")))
        if req.url.host == "data.alpaca.markets":
            return httpx.Response(302, headers={"Location": "https://elsewhere.example/x"})
        return httpx.Response(200, json={"news": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    fetcher = Fetcher(client, alpaca_keys=("KEY", "SECRET"), sleep=lambda s: None)
    feed = next(f for f in FEEDS.values() if f.fmt == "alpaca")
    with pytest.raises(ProviderError, match="HTTP 302"):
        fetcher.entries(feed)
    assert [h for h, _ in seen] == ["data.alpaca.markets"]  # nothing sent to elsewhere.example


def test_other_feeds_still_follow_redirects():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/old":
            return httpx.Response(301, headers={"Location": "https://feeds.example/new"})
        return httpx.Response(200, content=b"<rss><channel></channel></rss>")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    r = Fetcher(client, sleep=lambda s: None)._get("https://feeds.example/old")
    assert r.status_code == 200


def test_entity_declaration_refused_anywhere_in_the_feed():
    pad = b"<!--" + b"x" * 5000 + b"-->"
    bomb = (b'<?xml version="1.0"?>' + pad + b'<!DOCTYPE r [<!ENTITY a "AAAA">]>'
            b"<rss><channel><item><title>&a;</title></item></channel></rss>")
    with pytest.raises(FeedError, match="entities"):
        parse_feed(bomb)
