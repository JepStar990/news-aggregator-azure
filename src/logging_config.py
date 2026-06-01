"""Structured JSON logging — consumed by Application Insights automatically."""
import logging
import json
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Emit JSON log lines for App Insights structured querying."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            payload["exception"] = str(record.exc_info[1])
        return json.dumps(payload, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    # Remove any pre-existing handlers to avoid duplicate log lines in Functions
    root.handlers = [handler]
