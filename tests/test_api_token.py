"""Optional API/dashboard access token (APEX_API_TOKEN)."""

import logging

from fastapi.testclient import TestClient
from pydantic import SecretStr

from apex.api.app import TOKEN_COOKIE, create_app
from apex.core.config import Settings
from apex.core.logging import RedactingFilter


def _client(db_url, token):
    s = Settings(_env_file=None, database_url=db_url,
                 api_token=SecretStr(token) if token is not None else None)
    return TestClient(create_app(s))


def test_no_token_means_open_as_before(db_url):
    c = _client(db_url, None)
    assert c.get("/datasets").status_code == 200 and c.get("/ui/").status_code == 200
    assert _client(db_url, "").get("/datasets").status_code == 200  # blank = not set


def test_token_required_except_health(db_url):
    c = _client(db_url, "tok-123")
    assert c.get("/health").status_code == 200
    for path in ("/datasets", "/ui/", "/strategies", "/paper/accounts", "/system/checks"):
        assert c.get(path).status_code == 401, path
    assert c.get("/datasets", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/datasets", headers={"Authorization": "Bearer tok-123"}).status_code == 200
    assert c.get("/ui/?token=nope", follow_redirects=False).status_code == 401
    r = c.get("/ui/?token=tok-123", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/ui/"  # token leaves the URL
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.cookies.get(TOKEN_COOKIE) == "tok-123"
    assert c.get("/ui/").status_code == 200 and c.get("/datasets").status_code == 200
    c.cookies.clear()
    assert c.get("/datasets").status_code == 401


def test_token_in_a_request_line_is_redacted_in_logs():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                            '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:1", "GET", "/ui/?token=tok-123", "1.1", 307), None)
    RedactingFilter().filter(rec)
    assert "tok-123" not in rec.getMessage() and "token=***" in rec.getMessage()


def test_serve_routes_uvicorn_logs_through_redaction(monkeypatch, tmp_path):
    import uvicorn

    from apex.cli import main as cli
    from apex.core.config import get_settings

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("APEX_DATABASE_URL", f"sqlite:///{tmp_path / 'a.db'}")
    get_settings.cache_clear()
    try:
        assert cli(["serve", "--port", "8123"]) == 0
    finally:
        get_settings.cache_clear()
    assert seen["log_config"] is None and seen["port"] == 8123
