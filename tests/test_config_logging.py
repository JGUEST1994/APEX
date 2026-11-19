import logging

import pytest
from pydantic import ValidationError

from apex.core.config import Settings, TradingMode
from apex.core.logging import RedactingFilter, redact


def test_default_mode_is_research():
    assert Settings(_env_file=None).trading_mode is TradingMode.RESEARCH


@pytest.mark.parametrize("mode", ["live", "LIVE", "real", "production"])
def test_live_trading_cannot_be_enabled(mode, monkeypatch):
    monkeypatch.setenv("APEX_TRADING_MODE", mode)
    with pytest.raises(ValidationError, match="not available"):
        Settings(_env_file=None)


def test_secret_settings_not_in_repr(monkeypatch):
    monkeypatch.setenv("APEX_AI_API_KEY", "sk-very-secret")
    s = Settings(_env_file=None)
    assert "sk-very-secret" not in repr(s)


@pytest.mark.parametrize("raw,leak", [
    ("api_key=abc123 other", "abc123"),
    ("password: 'hunter2'", "hunter2"),
    ("Authorization=Bearer-xyz", "Bearer-xyz"),
    ("postgresql://apex:s3cret@db:5432/apex", "s3cret"),
])
def test_redact(raw, leak):
    assert leak not in redact(raw)


def test_filter_redacts_formatted_args():
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "connecting with token=%s", ("t0k",), None)
    RedactingFilter().filter(rec)
    assert "t0k" not in rec.getMessage()
