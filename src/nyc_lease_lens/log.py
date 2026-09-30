import json
import logging
import sys
import time
from datetime import UTC, datetime

from nyc_lease_lens.context import request_id

# Attributes every LogRecord has; anything else came from `extra=` and is a structured field.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "request_id", "taskName", "color_message"}
_NOISY = {
    "LiteLLM": logging.WARNING,
    "httpx": logging.WARNING,
    "urllib3": logging.WARNING,
    "uvicorn.access": logging.WARNING,
}
_configured = False


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class TextFormatter(logging.Formatter):
    """`time LEVEL logger [request] message key=value ...`"""

    def format(self, record: logging.LogRecord) -> str:
        time = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        line = (
            f"{time} {record.levelname:<7} {record.name} [{getattr(record, 'request_id', '-')}] {record.getMessage()}"
        )
        if fields := _fields(record):
            line += " " + " ".join(f"{k}={v}" for k, v in fields.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log collectors such as Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "severity": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
            **_fields(record),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Send all logs, including uvicorn's, to stderr in one format. Safe to call more than once."""
    global _configured
    root = logging.getLogger()
    if _configured:
        root.setLevel(level)
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(_RequestIdFilter())
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    for name, noisy_level in _NOISY.items():
        logging.getLogger(name).setLevel(max(noisy_level, logging.getLevelName(level)))
    _configured = True


def ms_since(start: float) -> int:
    """Milliseconds elapsed since a time.perf_counter() reading."""
    return round((time.perf_counter() - start) * 1000)


def _fields(record: logging.LogRecord) -> dict:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD and not k.startswith("_")}
