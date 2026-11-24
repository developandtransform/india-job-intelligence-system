"""Logging with automatic secret redaction, plus an in-memory buffer that is later written to the System Logs tab."""
from __future__ import annotations

import logging
from datetime import datetime, timezone


class RedactingFilter(logging.Filter):
    """Replaces any configured secret value found in a log message with ***."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self.secrets = sorted({s for s in (secrets or []) if s}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            msg = record.getMessage()
            for s in self.secrets:
                if s in msg:
                    msg = msg.replace(s, "***")
            record.msg, record.args = msg, ()
        return True


class BufferHandler(logging.Handler):
    """Keeps WARNING+ records (and the run's key INFO lines) so they can be stored in Google Sheets."""

    def __init__(self, level: int = logging.WARNING) -> None:
        super().__init__(level)
        self.rows: list[list[str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.rows.append([ts, record.levelname, record.name, record.getMessage()[:1500]])


def setup_logging(level: str = "INFO", secrets: list[str] | None = None) -> BufferHandler:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    redact = RedactingFilter(secrets)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    console.addFilter(redact)
    buffer = BufferHandler(logging.INFO)
    buffer.addFilter(redact)
    root.addHandler(console)
    root.addHandler(buffer)
    # chatty libraries
    for noisy in ("urllib3", "gspread", "google", "jobspy", "httpx", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return buffer
