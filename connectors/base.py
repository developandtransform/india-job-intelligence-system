"""Connector contract. Every source returns the same thing: a ``ConnectorResult`` full of common-schema ``Job`` objects."""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

from config.settings import Settings
from core.http import HttpClient
from core.models import Job

log = logging.getLogger("connector")


@dataclass
class ConnectorResult:
    name: str
    jobs: list[Job] = field(default_factory=list)
    completed_scopes: set[str] = field(default_factory=set)   # boards fully fetched (only these may close jobs)
    errors: list[str] = field(default_factory=list)
    raw_count: int = 0
    requests: int = 0
    cost_usd: float = 0.0
    status: str = "ok"            # ok | partial | failed | skipped
    note: str = ""
    seconds: float = 0.0
    dropped_prefilter: int = 0    # non-India jobs dropped inside the connector (counted for the statistics)


class Connector(ABC):
    name = "base"
    free = True

    def __init__(self, settings: Settings, http: HttpClient) -> None:
        self.settings, self.http = settings, http
        self.known_keys: set[str] = set()   # 'source:id' keys already in the sheet (skip expensive detail calls)

    @abstractmethod
    def enabled(self) -> bool: ...

    @abstractmethod
    def fetch(self, result: ConnectorResult, deadline: float) -> None:
        """Fill ``result``. May raise; ``run`` isolates failures."""

    def run(self) -> ConnectorResult:
        res = ConnectorResult(self.name)
        if not self.enabled():
            res.status, res.note = "skipped", "disabled"
            return res
        start = time.monotonic()
        deadline = start + self.settings.source_time_budget_seconds
        try:
            self.fetch(res, deadline)
            if res.errors:
                res.status = "partial" if res.jobs or res.completed_scopes else "failed"
        except Exception as exc:  # noqa: BLE001 - a failing source must never stop the run
            log.exception("Connector %s crashed", self.name)
            res.errors.append(f"{type(exc).__name__}: {str(exc)[:300]}")
            res.status = "partial" if res.jobs else "failed"
        res.seconds = round(time.monotonic() - start, 1)
        log.info("Connector %s: status=%s jobs=%d errors=%d (%.1fs)", self.name, res.status, len(res.jobs), len(res.errors), res.seconds)
        return res


def run_with_deadline(items: list, fn: Callable, deadline: float, errors: list[str], label: str) -> None:
    """Call ``fn(item)`` for each item; stop at the deadline; record (not raise) per-item errors."""
    for item in items:
        if time.monotonic() > deadline:
            errors.append(f"{label}: time budget reached, skipped remaining items")
            return
        try:
            fn(item)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label} {item}: {type(exc).__name__}: {str(exc)[:200]}")
