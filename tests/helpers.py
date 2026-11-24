from __future__ import annotations

import dataclasses
from pathlib import Path

from config.settings import load_settings
from connectors.base import Connector, ConnectorResult
from core.http import HttpClient
from core.models import Job
from integrations.store import MemoryStore
from processing.pipeline import run_pipeline

TODAY = "2026-11-24"


def settings(**over):
    return dataclasses.replace(load_settings(environ={}, env_path=Path("/nonexistent/.env")), **over)


class StubConnector(Connector):
    name = "stub"

    def __init__(self, jobs, scopes=(), fail=False, name="stub"):
        super().__init__(settings(), HttpClient())
        self._jobs, self._scopes, self._fail = list(jobs), set(scopes), fail
        self.name = name

    def enabled(self):
        return True

    def fetch(self, result: ConnectorResult, deadline):
        if self._fail:
            raise RuntimeError("boom")
        result.jobs = [dataclasses.replace(j, source_keys=list(j.source_keys), source_urls=list(j.source_urls),
                                           raw=dict(j.raw)) for j in self._jobs]
        result.completed_scopes = set(self._scopes)


def mk(i="1", title="Business Development Executive", company="Infosys", location="Pune, India", desc="", **kw) -> Job:
    return Job(source=kw.pop("source", "stub"), original_id=i, title=title, company=company, location=location,
               full_description=desc, job_url=f"https://example.com/j/{i}", hint_country="India", **kw)


def run(jobs, store=None, today=TODAY, scopes=(), cfg=None, **kw):
    store = store or MemoryStore()
    out = run_pipeline(cfg or settings(), store, [StubConnector(jobs, scopes)], today=today, send_mail=False, **kw)
    return out, store


def category_of(desc: str, title="Business Development Executive", company="Infosys", registry=None, location="Pune, India", **kw) -> str:
    out, _ = run([mk(title=title, company=company, location=location, desc=desc, **kw)], MemoryStore(registry))
    if not out.jobs:
        return "DROPPED"
    return out.jobs[0].category
