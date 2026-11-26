"""Finds and VERIFIES public ATS job boards for employers you name. It never edits config/targets.yaml.

How it works (polite by design):
* input: a CSV with columns  company,category[,ats,identifier]   (see config/board_candidates.csv)
* if ats/identifier are given they are tested as they are; otherwise a few *guesses* are derived from the company name
  (e.g. "Urban Company" -> urbancompany, urban-company) and tried on the four public JSON APIs that need no login
* every guess is ONE ordinary GET to the same public endpoint the company's own careers page uses, spaced by the normal
  request delay; a 404 simply means "that guess is wrong". No login, no scraping of HTML pages, no circumvention
* output: a CSV for you to review. A row is only marked ``Suggest = yes`` when the board answered with real postings.
  Nothing is added to targets.yaml automatically, and no identifier is ever invented: unverified guesses stay "not found".
"""
from __future__ import annotations

import csv
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from connectors.ats_connector import AtsConnector
from connectors.base import ConnectorResult
from processing.geo_india import parse_location

PROBE_ATS = ("greenhouse", "lever", "ashby", "smartrecruiters")      # public JSON endpoints, no login
MAX_PROBES_PER_COMPANY = 8
OUT_COLUMNS = ["Company", "Category", "ATS", "Identifier", "Result", "Total Postings", "India Postings", "Suggest",
               "targets.yaml line (review first)", "Note"]


@dataclass
class Candidate:
    company: str
    category: str = ""
    ats: str = ""
    identifier: str = ""


@dataclass
class ProbeResult:
    company: str
    category: str
    ats: str
    identifier: str
    result: str                 # found | not found | error | skipped
    total: int = 0
    india: int = 0
    note: str = ""

    @property
    def suggest(self) -> bool:
        return self.result == "found" and self.total > 0

    def row(self) -> list[str]:
        line = f"  - {self.ats}:{self.identifier}|{self.company}" if self.suggest else ""
        return [self.company, self.category, self.ats, self.identifier, self.result, str(self.total), str(self.india),
                "yes" if self.suggest else "no", line, self.note]


def slug_guesses(company: str) -> list[str]:
    """Mechanical guesses only. They are NOT facts; the probe decides."""
    name = re.sub(r"\b(pvt|private|ltd|limited|inc|llp|india|technologies|technology|solutions)\b\.?", " ", company.lower())
    words = [w for w in re.split(r"[^a-z0-9]+", name) if w]
    if not words:
        return []
    guesses = ["".join(words), "-".join(words)]
    if len(words) > 1:
        guesses.append(words[0])
    seen, out = set(), []
    for g in guesses:
        if g not in seen:
            seen.add(g)
            out.append(g)
    return out


def load_candidates(path: Path) -> list[Candidate]:
    out: list[Candidate] = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        lines = (ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#"))
        for row in csv.DictReader(lines):
            name = (row.get("company") or "").strip()
            if name:
                out.append(Candidate(name, (row.get("category") or "").strip(), (row.get("ats") or "").strip().lower(),
                                     (row.get("identifier") or "").strip()))
    return out


def _count_india(jobs: list[Any]) -> int:
    n = 0
    for j in jobs:
        try:
            if parse_location(j.location or "", j.title or "").is_india:
                n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def probe(conn: AtsConnector, company: str, category: str, ats: str, ident: str) -> ProbeResult:
    res = ConnectorResult("ats")
    try:
        conn.fetch_board(ats, ident, company, res, time.monotonic() + 60)
    except Exception as exc:  # noqa: BLE001 - 404 etc. just mean "wrong guess"
        text = f"{type(exc).__name__}: {str(exc)[:120]}"
        kind = "not found" if "404" in text or "Not Found" in text else "error"
        return ProbeResult(company, category, ats, ident, kind, note=text if kind == "error" else "")
    if res.errors and not res.jobs:
        return ProbeResult(company, category, ats, ident, "error", note=res.errors[0][:120])
    return ProbeResult(company, category, ats, ident, "found", len(res.jobs), _count_india(res.jobs),
                       "board answered with postings" if res.jobs else "board exists but has no postings now")


def discover(candidates: list[Candidate], conn: AtsConnector, max_probes: int = MAX_PROBES_PER_COMPANY,
             progress=None) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    for c in candidates:
        if c.ats and c.identifier:
            tries = [(c.ats, c.identifier)]
        else:
            tries = [(a, g) for g in slug_guesses(c.company) for a in PROBE_ATS][:max_probes]
        found_any = False
        for ats, ident in tries:
            r = probe(conn, c.company, c.category, ats, ident)
            if progress:
                progress(r)
            if r.result == "found":
                results.append(r)
                found_any = True
                break                                  # one verified board per company is enough to review
            if r.result == "error":
                results.append(r)
        if not found_any and not any(x.company == c.company for x in results):
            results.append(ProbeResult(c.company, c.category, "", "", "not found",
                                       note=f"{len(tries)} guess(es) tried on {', '.join(PROBE_ATS)}; none answered. "
                                            "Look for the company's careers page and add ats/identifier yourself"))
    return results


def write_report(results: list[ProbeResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(OUT_COLUMNS)
        for r in sorted(results, key=lambda r: (not r.suggest, -r.india, r.company.lower())):
            w.writerow(r.row())
    return path
