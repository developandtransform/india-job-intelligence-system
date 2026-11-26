"""Storage interface used by the pipeline. Google Sheets is the real backend; ``MemoryStore`` is used by tests/demo."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Protocol

from core.models import MANUAL_COLUMNS, MASTER_COLUMNS, Job
from core.textutil import norm_company
from integrations import sheet_schema as S


class Store(Protocol):
    def prepare(self) -> None: ...
    def load_master(self) -> list[dict[str, str]]: ...
    def save_master(self, jobs: list[Job]) -> dict: ...
    def write_views(self, views: dict[str, list[list[str]]]) -> None: ...
    def load_registry_rows(self) -> list[dict[str, str]]: ...
    def add_registry_names(self, names: list[str], today: str) -> int: ...
    def validate_structure(self, check_ai_cache: bool = False) -> list[str]: ...
    def load_recent_stats(self, limit: int = 600) -> list[list[str]]: ...
    def load_recent_alerts(self, limit: int = 400) -> list[list[str]]: ...
    def load_ai_cache(self) -> list[dict[str, str]]: ...
    def save_ai_cache(self, rows: list[list[str]]) -> None: ...
    def append_stats(self, rows: list[list[str]]) -> None: ...
    def append_logs(self, rows: list[list[str]]) -> None: ...
    def mark_reported(self, marks: dict[str, tuple[str, str]]) -> None: ...
    def url(self) -> str: ...


class MemoryStore:
    """In-memory stand-in with the same semantics as the Google Sheets store (incl. 'user' columns survive)."""

    def __init__(self, registry_rows: list[dict[str, str]] | None = None) -> None:
        self.master: dict[str, dict[str, str]] = {}
        self.views: dict[str, list[list[str]]] = {}
        self.stats: list[list[str]] = []
        self.logs: list[list[str]] = []
        self.registry: list[dict[str, str]] = registry_rows or []
        self.ai_cache: list[list[str]] = []
        self.writes = 0

    def prepare(self) -> None:
        return None

    def load_master(self) -> list[dict[str, str]]:
        return [dict(r) for r in self.master.values()]

    def save_master(self, jobs: list[Job]) -> dict:
        changed = 0
        for j in jobs:
            rec = j.to_record()
            old = self.master.get(j.internal_id)
            if old:                       # user-owned columns are never overwritten by the program
                for c in MANUAL_COLUMNS:
                    rec[c] = old.get(c, rec[c])
            if old != rec:
                changed += 1
            self.master[j.internal_id] = rec
        self.writes += 1
        return {"rows": len(jobs), "changed": changed}

    def write_views(self, views: dict[str, list[list[str]]]) -> None:
        self.views = views

    def load_registry_rows(self) -> list[dict[str, str]]:
        return [dict(r) for r in self.registry]

    def add_registry_names(self, names: list[str], today: str) -> int:
        have = {norm_company(r.get("Company", "")) for r in self.registry}
        n = 0
        for name in names:
            if norm_company(name) not in have:
                self.registry.append({"Company": name, "Classification": "", "Last Updated": today})
                have.add(norm_company(name))
                n += 1
        return n

    def validate_structure(self, check_ai_cache: bool = False) -> list[str]:
        return list(getattr(self, "structure_problems", []))

    def load_recent_stats(self, limit: int = 600) -> list[list[str]]:
        return [list(r) for r in self.stats[-limit:]]

    def load_recent_alerts(self, limit: int = 400) -> list[list[str]]:
        return [list(r) for r in self.logs[-limit:] if len(r) > 2 and r[2] == "alert"]

    def load_ai_cache(self) -> list[dict[str, str]]:
        from processing.ai_cache import CACHE_HEADERS
        return [dict(zip(CACHE_HEADERS, r)) for r in self.ai_cache]

    def save_ai_cache(self, rows: list[list[str]]) -> None:
        self.ai_cache = [list(r) for r in rows]

    def append_stats(self, rows: list[list[str]]) -> None:
        self.stats.extend(rows)

    def append_logs(self, rows: list[list[str]]) -> None:
        self.logs.extend(rows)

    def mark_reported(self, marks: dict[str, tuple[str, str]]) -> None:
        for jid, (h, d) in marks.items():
            if jid in self.master:
                self.master[jid]["Last Reported Hash"] = h
                self.master[jid]["Last Reported Date"] = d

    def url(self) -> str:
        return "(in-memory demo store)"

    # ---- convenience for the demo command
    def export_csv(self, folder: Path) -> list[Path]:
        folder.mkdir(parents=True, exist_ok=True)
        written = []
        p = folder / "All Jobs.csv"
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(MASTER_COLUMNS)
            for rec in self.master.values():
                w.writerow([rec.get(c, "") for c in MASTER_COLUMNS])
        written.append(p)
        for tab, rows in self.views.items():
            p = folder / f"{tab}.csv"
            with p.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(rows)
            written.append(p)
        for name, headers, rows in (("Source Statistics", S.STATS_HEADERS, self.stats), ("System Logs", S.LOG_HEADERS, self.logs)):
            p = folder / f"{name}.csv"
            with p.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(headers)
                w.writerows(rows)
            written.append(p)
        return written
