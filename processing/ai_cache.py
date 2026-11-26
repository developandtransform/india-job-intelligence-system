"""Persistent AI answer cache. Lives in the 'AI Cache' tab of the same Google Sheet (no other database).

Why a tab and not the 'Raw Data' column: Raw Data only exists for jobs that are STORED. Most AI questions are about jobs
that the rules then drop (out of scope), and those would be asked again every morning because GitHub Actions starts with
an empty disk. The key is a hash of the exact text sent to the model, so a changed description or title is automatically a
cache miss (= content-hash invalidation); the prompt version is part of the key, so improving a prompt also refreshes.
Failures are never cached (only real, validated answers). Entries expire after ``ttl_days`` and the tab is capped.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

PROMPT_VERSION = "v1"
CACHE_HEADERS = ["Key", "Kind", "Result (JSON)", "Last Used", "Hits"]


def cache_key(kind: str, *parts: str) -> str:
    h = hashlib.sha256()
    h.update((PROMPT_VERSION + "\0" + kind).encode())
    for p in parts:
        h.update(b"\0" + p.encode("utf-8", "replace"))
    return h.hexdigest()[:32]


class AICache:
    def __init__(self, rows: list[dict[str, str]] | None = None, today: str | None = None,
                 max_entries: int = 3000, ttl_days: int = 45) -> None:
        self.today = today or date.today().isoformat()
        self.max_entries, self.ttl_days = max_entries, ttl_days
        self.entries: dict[str, dict] = {}
        self.dirty = False
        for r in rows or []:
            try:
                self.entries[r["Key"]] = {"kind": r.get("Kind", ""), "result": json.loads(r.get("Result (JSON)", "null")),
                                          "last": r.get("Last Used", "") or self.today, "hits": int(float(r.get("Hits") or 0))}
            except Exception:  # noqa: BLE001 - a hand-edited / damaged row is simply ignored
                continue

    def get(self, key: str):
        e = self.entries.get(key)
        if e is None:
            return False, None
        e["hits"] += 1
        e["last"] = self.today
        self.dirty = True
        return True, e["result"]

    def put(self, key: str, kind: str, result) -> None:
        self.entries[key] = {"kind": kind, "result": result, "last": self.today, "hits": 0}
        self.dirty = True

    def __len__(self) -> int:
        return len(self.entries)

    def to_rows(self) -> list[list[str]]:
        """Newest first, expired and surplus entries dropped."""
        try:
            cutoff = (date.fromisoformat(self.today) - timedelta(days=self.ttl_days)).isoformat()
        except ValueError:
            cutoff = ""
        live = [(k, e) for k, e in self.entries.items() if e["last"] >= cutoff]
        live.sort(key=lambda kv: kv[1]["last"], reverse=True)
        return [[k, e["kind"], json.dumps(e["result"], ensure_ascii=False), e["last"], str(e["hits"])]
                for k, e in live[: self.max_entries]]
