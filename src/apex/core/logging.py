"""Structured logging with secret redaction.

Every handler installed by ``configure_logging`` runs ``RedactingFilter`` so values that look
like credentials never reach log output, even if a developer logs them by mistake.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

_SECRET_PATTERNS = [
    # key=value / key: value pairs whose key names a secret
    re.compile(
        r"(?i)\b(api[_-]?key|secret|password|passwd|token|authorization|private[_-]?key)"
        r"(\s*[=:]\s*)((?:(?:Bearer|Token|Basic)\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+))"
    ),
    # credentials embedded in URLs: scheme://user:pass@host
    re.compile(r"(://[^:/\s]+:)([^@/\s]+)(@)"),
]


def redact(text: str) -> str:
    text = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}***{m.group(3)}", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
