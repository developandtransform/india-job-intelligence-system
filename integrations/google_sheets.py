"""Google Sheets as the one and only persistent database.

* Auth: service account, key supplied as Base64 in the root .env (never a file in the project).
* Reads are batched (one ``get_all_values`` per tab), writes are batched (``batch_update`` with many ranges).
* Only CHANGED cells of "All Jobs" are written; the user-owned columns (Application Status, Notes) are never written
  for existing rows, so your manual edits cannot be overwritten.
* Quota / transient errors (HTTP 429, 5xx) are retried with exponential back-off.
* Values are written with ``RAW`` so nothing scraped can ever be interpreted as a formula.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError, SpreadsheetNotFound
from gspread.utils import rowcol_to_a1
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import Settings
from core.models import MANUAL_COLUMNS, MASTER_COLUMNS, Job
from core.textutil import norm_company
from integrations import sheet_schema as S

log = logging.getLogger("sheets")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HIDDEN_COLUMNS = ["Source Keys", "Source Scopes", "Content Hash", "Last Reported Hash", "Last Reported Date", "Raw Data",
                  "Missed Runs", "Duplicate Group ID", "CTC LPA Min", "CTC LPA Max", "Stipend INR Min", "Stipend INR Max"]
WIDTHS = {"Job Title": 280, "Company": 180, "Notes": 240, "Match Explanation": 360, "Direct Apply URL": 260, "Job URL": 220,
          "Location": 160, "Verification Flags": 300, "Exclusion Reason": 260, "Job Description": 400, "Category": 190,
          "Application Status": 130, "All Source URLs": 260, "Company Evidence": 220}


class SheetChangedError(RuntimeError):
    """Someone re-ordered/inserted rows while the program was running."""


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, APIError):
        code = getattr(exc.response, "status_code", 0)
        return code in (429, 500, 502, 503, 504)
    return isinstance(exc, (ConnectionError, TimeoutError)) or type(exc).__name__ in ("ConnectionError", "ReadTimeout", "ConnectTimeout")


_retry = retry(retry=retry_if_exception(_retryable), wait=wait_exponential(multiplier=2, min=2, max=60),
               stop=stop_after_attempt(6), reraise=True,
               before_sleep=lambda rs: log.warning("Google API busy/quota (%s); retrying in %.0fs", type(rs.outcome.exception()).__name__, rs.next_action.sleep))


def explain_error(exc: Exception, service_email: str = "") -> str:
    msg = str(exc)
    if isinstance(exc, SpreadsheetNotFound):
        return (f"Spreadsheet not found. Check GOOGLE_SHEET_ID and share the sheet with {service_email or 'the service-account e-mail'} as Editor.")
    if isinstance(exc, APIError):
        code = getattr(exc.response, "status_code", "?")
        if code == 403 and "has not been used" in msg or "disabled" in msg.lower():
            return "The Google Sheets API is not enabled for your Google Cloud project. Enable it (README step 'Enable Google Sheets API')."
        if code == 403:
            return f"Permission denied. Share the spreadsheet with {service_email or 'the service-account e-mail'} as Editor."
        if code == 404:
            return "Spreadsheet not found (HTTP 404). Check GOOGLE_SHEET_ID."
        if code == 429:
            return "Google API quota exceeded (HTTP 429). The run retried and still failed; try again in a few minutes."
    return f"{type(exc).__name__}: {msg[:300]}"


class GoogleSheetsStore:
    def __init__(self, settings: Settings, read_only: bool = False) -> None:
        self.s = settings
        self.read_only = read_only        # dry run: every write method refuses (belt and braces; the pipeline also avoids them)
        info = settings.service_account_info()
        self.service_email = info.get("client_email", "")
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        self.client = gspread.authorize(creds)
        self.sh: gspread.Spreadsheet | None = None
        self.ws: dict[str, gspread.Worksheet] = {}
        self._hdr: dict[str, int] = {}
        self._rows: list[list[str]] = []
        self._ids: list[str] = []
        self._pos: dict[str, int] = {}

    def _guard(self, what: str) -> None:
        if getattr(self, "read_only", False):
            raise RuntimeError(f"read-only (dry run): refused to {what}")

    # ------------------------------------------------------------- basics
    def url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.s.google_sheet_id}/edit"

    @_retry
    def _open(self) -> gspread.Spreadsheet:
        return self.client.open_by_key(self.s.google_sheet_id)

    def connect(self) -> None:
        try:
            self.sh = self._open()
            self._refresh_worksheets()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(explain_error(exc, self.service_email)) from None

    @_retry
    def _refresh_worksheets(self) -> None:
        assert self.sh
        self.ws = {w.title: w for w in self.sh.worksheets()}

    # ----------------------------------------------------------- structure
    def validate_structure(self, check_ai_cache: bool = False) -> list[str]:
        """READ-ONLY check used by dry runs: lists missing tabs / empty header rows / missing columns instead of repairing
        them. Writes nothing. Extra user-created columns are fine. Required: All Jobs, Company Registry, Source Statistics,
        System Logs and every generated view. The AI Cache tab is optional: it is only checked (never required) when it exists
        and AI is going to use it (check_ai_cache=True)."""
        if self.sh is None:
            self.connect()
        problems: list[str] = []
        headers_for = {S.TAB_ALL: MASTER_COLUMNS, S.TAB_STATS: S.STATS_HEADERS, S.TAB_LOGS: S.LOG_HEADERS,
                       S.TAB_REGISTRY: S.REGISTRY_HEADERS, **{t: S.VIEW_COLUMNS for t in S.VIEW_RULES}}
        missing = [t for t in headers_for if t not in self.ws]
        if missing:
            problems.append("Missing tab(s): " + ", ".join(missing))
        to_check = {t: h for t, h in headers_for.items() if t not in missing}
        if check_ai_cache and S.TAB_AI_CACHE in self.ws:
            to_check[S.TAB_AI_CACHE] = S.CACHE_HEADERS
        for tab, headers in to_check.items():
            first = [str(c).strip() for c in (self._retry_call(self.ws[tab].row_values, 1) or [])]
            if not any(first):
                problems.append(f"Tab '{tab}' has no header row")
                continue
            absent = [h for h in headers if h not in first]
            if absent:
                problems.append(f"Tab '{tab}' is missing column(s): {', '.join(absent)}")
        return problems

    def prepare(self) -> None:
        """Create any missing tab, header row, filter, dropdown. Never touches existing data."""
        self._guard("create/repair tabs, headers or formatting")
        if self.sh is None:
            self.connect()
        assert self.sh
        # re-use the default empty "Sheet1" as the All Jobs tab
        if S.TAB_ALL not in self.ws and "Sheet1" in self.ws:
            try:
                self._retry_call(self.ws["Sheet1"].update_title, S.TAB_ALL)
                self._refresh_worksheets()
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not rename Sheet1: %s", exc)
        missing = [t for t in S.TAB_ORDER if t not in self.ws]
        if missing:
            reqs = [{"addSheet": {"properties": {"title": t, "index": S.TAB_ORDER.index(t),
                                                 "gridProperties": {"rowCount": 1000 if t == S.TAB_ALL else 200,
                                                                    "columnCount": max(len(MASTER_COLUMNS), 26)}}}} for t in missing]
            self._retry_call(self.sh.batch_update, {"requests": reqs})
            self._refresh_worksheets()
            log.info("Created tabs: %s", ", ".join(missing))
        headers_for = {S.TAB_ALL: MASTER_COLUMNS, S.TAB_STATS: S.STATS_HEADERS, S.TAB_LOGS: S.LOG_HEADERS,
                       S.TAB_REGISTRY: S.REGISTRY_HEADERS, S.TAB_AI_CACHE: S.CACHE_HEADERS, **{t: S.VIEW_COLUMNS for t in S.VIEW_RULES}}
        for tab, headers in headers_for.items():
            w = self.ws[tab]
            first = self._retry_call(w.row_values, 1)
            if not first:
                self._retry_call(w.update, range_name="A1", values=[headers], value_input_option="RAW")
                self._format_new_tab(tab, w, headers)
            elif tab in (S.TAB_ALL, S.TAB_REGISTRY):
                absent = [h for h in headers if h not in first]
                if absent:                                  # schema upgrade: append new columns, never reorder
                    need = len(first) + len(absent)
                    if w.col_count < need:
                        self._retry_call(w.add_cols, need - w.col_count)
                    self._retry_call(w.update, range_name=rowcol_to_a1(1, len(first) + 1), values=[absent], value_input_option="RAW")
                    log.info("%s: added columns %s", tab, absent)

    def _retry_call(self, fn, *a, **k):
        return _retry(fn)(*a, **k)

    def _format_new_tab(self, tab: str, w: gspread.Worksheet, headers: list[str]) -> None:
        """Best-effort cosmetics: failure here must never break the run."""
        try:
            sid = w.id
            n = len(headers)
            reqs: list[dict[str, Any]] = [
                {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {
                    "frozenRowCount": 1, "frozenColumnCount": 4 if tab == S.TAB_ALL else (3 if tab in S.VIEW_RULES else 1)}},
                    "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
                {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                                "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                                                               "backgroundColor": {"red": 0.12, "green": 0.25, "blue": 0.45}}},
                                "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
                {"setBasicFilter": {"filter": {"range": {"sheetId": sid, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": n}}}},
            ]
            for i, h in enumerate(headers):
                if h in WIDTHS:
                    reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                               "properties": {"pixelSize": WIDTHS[h]}, "fields": "pixelSize"}})
                if tab == S.TAB_ALL and h in HIDDEN_COLUMNS:
                    reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                               "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}})
            if tab == S.TAB_ALL:
                col = headers.index("Application Status")
                reqs.append({"setDataValidation": {"range": {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": col, "endColumnIndex": col + 1},
                                                   "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in S.APPLICATION_STATUS_CHOICES]},
                                                            "showCustomUi": True, "strict": False}}})
            if tab == S.TAB_REGISTRY:
                col = headers.index("Classification")
                reqs.append({"setDataValidation": {"range": {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": col, "endColumnIndex": col + 1},
                                                   "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in S.CLASSIFICATION_CHOICES]},
                                                            "showCustomUi": True, "strict": False}}})
            if tab in S.VIEW_RULES:
                reqs.append({"updateCells": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1},
                                             "rows": [{"values": [{"note": "GENERATED VIEW - rewritten every run. Change Application Status and Notes in the 'All Jobs' tab only."}]}],
                                             "fields": "note"}})
            self._retry_call(self.sh.batch_update, {"requests": reqs})
        except Exception as exc:  # noqa: BLE001
            log.warning("Formatting of tab '%s' skipped (%s)", tab, type(exc).__name__)

    # ---------------------------------------------------------------- master
    def load_master(self) -> list[dict[str, str]]:
        w = self.ws[S.TAB_ALL]
        values = self._retry_call(w.get_all_values)
        if not values:
            self._hdr, self._rows, self._ids, self._pos = {}, [], [], {}
            return []
        header = values[0]
        self._hdr = {h: i for i, h in enumerate(header) if h}
        width = len(header)
        self._rows = [(r + [""] * (width - len(r)))[:width] for r in values[1:]]
        id_idx = self._hdr["Internal Job ID"]
        self._ids = [r[id_idx] for r in self._rows]
        self._pos = {}
        for i, jid in enumerate(self._ids):
            if jid and jid not in self._pos:
                self._pos[jid] = i
        return [{h: r[i] for h, i in self._hdr.items()} for r in self._rows if r[id_idx]]

    def _verify_unchanged(self) -> None:
        w = self.ws[S.TAB_ALL]
        id_col = self._hdr["Internal Job ID"] + 1
        current = self._retry_call(w.col_values, id_col)[1:]
        mine = list(self._ids)
        while mine and not mine[-1]:
            mine.pop()
        while current and not current[-1]:
            current.pop()
        if current != mine:
            raise SheetChangedError("Rows in 'All Jobs' were re-ordered, inserted or deleted while the run was in progress; "
                                    "nothing was written. Run again.")

    def _flush(self, w: gspread.Worksheet, data: list[dict[str, Any]]) -> None:
        chunk: list[dict[str, Any]] = []
        size = 0
        for item in data:
            item_size = sum(len(str(c)) for row in item["values"] for c in row) + 50
            if chunk and (len(chunk) >= 300 or size + item_size > 1_500_000):
                self._retry_call(w.batch_update, chunk, value_input_option="RAW")
                chunk, size = [], 0
                time.sleep(1.1)
            chunk.append(item)
            size += item_size
        if chunk:
            self._retry_call(w.batch_update, chunk, value_input_option="RAW")

    def save_master(self, jobs: list[Job]) -> dict:
        self._guard("write job records")
        w = self.ws[S.TAB_ALL]
        if not self._hdr:
            self.load_master()
        if self._rows:
            self._verify_unchanged()
        width = max(self._hdr.values()) + 1 if self._hdr else len(MASTER_COLUMNS)
        order = sorted(self._hdr.items(), key=lambda kv: kv[1])
        data: list[dict[str, Any]] = []
        new_rows: list[list[str]] = []
        changed_rows = 0
        for j in jobs:
            rec = j.to_record()
            pos = self._pos.get(j.internal_id)
            if pos is None:
                new_rows.append([rec.get(h, "") for h, _ in order] + [""] * (width - len(order)))
                continue
            old = self._rows[pos]
            run: list[tuple[int, str]] = []
            row_changed = False
            for h, idx in order:
                if h in MANUAL_COLUMNS or h not in rec:
                    if run:
                        self._emit(data, w, pos + 2, run)
                        run = []
                    continue
                if rec[h] != old[idx]:
                    if run and run[-1][0] != idx - 1:
                        self._emit(data, w, pos + 2, run)
                        run = []
                    run.append((idx, rec[h]))
                    old[idx] = rec[h]
                    row_changed = True
                elif run:
                    self._emit(data, w, pos + 2, run)
                    run = []
            if run:
                self._emit(data, w, pos + 2, run)
            changed_rows += row_changed
        if new_rows:
            start = len(self._rows) + 2
            need = start + len(new_rows)
            if need > w.row_count:
                self._retry_call(w.add_rows, need - w.row_count + 500)
            for off in range(0, len(new_rows), 200):
                block = new_rows[off: off + 200]
                a1 = f"{rowcol_to_a1(start + off, 1)}:{rowcol_to_a1(start + off + len(block) - 1, width)}"
                data.append({"range": a1, "values": block})
            id_idx = self._hdr["Internal Job ID"]
            for r in new_rows:
                self._pos[r[id_idx]] = len(self._rows)
                self._rows.append(r)
                self._ids.append(r[id_idx])
        self._flush(w, data)
        return {"rows": len(jobs), "changed": changed_rows, "new": len(new_rows), "ranges": len(data)}

    @staticmethod
    def _emit(data: list, w: gspread.Worksheet, row: int, run: list[tuple[int, str]]) -> None:
        c1, c2 = run[0][0] + 1, run[-1][0] + 1
        data.append({"range": f"{rowcol_to_a1(row, c1)}:{rowcol_to_a1(row, c2)}", "values": [[v for _, v in run]]})

    def mark_reported(self, marks: dict[str, tuple[str, str]]) -> None:
        self._guard("mark jobs as reported")
        w = self.ws[S.TAB_ALL]
        hi, di = self._hdr.get("Last Reported Hash"), self._hdr.get("Last Reported Date")
        if hi is None or di is None:
            return
        data = []
        for jid, (h, d) in marks.items():
            pos = self._pos.get(jid)
            if pos is None:
                continue
            self._rows[pos][hi], self._rows[pos][di] = h, d
            if di == hi + 1:
                data.append({"range": f"{rowcol_to_a1(pos + 2, hi + 1)}:{rowcol_to_a1(pos + 2, di + 1)}", "values": [[h, d]]})
            else:
                data.append({"range": rowcol_to_a1(pos + 2, hi + 1), "values": [[h]]})
                data.append({"range": rowcol_to_a1(pos + 2, di + 1), "values": [[d]]})
        self._flush(w, data)

    # ----------------------------------------------------------------- views
    def write_views(self, views: dict[str, list[list[str]]]) -> None:
        self._guard("rewrite the view tabs")
        for tab, rows in views.items():
            w = self.ws[tab]
            self._retry_call(w.clear)
            need = len(rows) + 10
            if need > w.row_count:
                self._retry_call(w.add_rows, need - w.row_count)
            for off in range(0, len(rows), 500):
                self._retry_call(w.update, range_name=rowcol_to_a1(off + 1, 1), values=rows[off: off + 500], value_input_option="RAW")
            time.sleep(1.0)

    # ------------------------------------------------------- registry / logs
    def load_registry_rows(self) -> list[dict[str, str]]:
        values = self._retry_call(self.ws[S.TAB_REGISTRY].get_all_values)
        if len(values) < 2:
            return []
        header = values[0]
        return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(header)} for r in values[1:] if any(r)]

    def add_registry_names(self, names: list[str], today: str) -> int:
        self._guard("add Company Registry rows")
        have = {norm_company(r.get("Company", "")) for r in self.load_registry_rows()}
        rows = []
        for n in names:
            k = norm_company(n)
            if k and k not in have:
                have.add(k)
                rows.append([n, "", "", "", "", "", "Auto-added: fill Classification (and Founded Year / Employee Count if it is a Startup)", today, "Unverified"])
        if rows:
            self._retry_call(self.ws[S.TAB_REGISTRY].append_rows, rows, value_input_option="RAW")
        return len(rows)

    def load_recent_stats(self, limit: int = 600) -> list[list[str]]:
        values = self._retry_call(self.ws[S.TAB_STATS].get_all_values)
        return [r for r in values[1:] if any(r)][-limit:]

    def load_recent_alerts(self, limit: int = 400) -> list[list[str]]:
        values = self._retry_call(self.ws[S.TAB_LOGS].get_all_values)
        return [r for r in values[1:] if len(r) > 2 and r[2] == "alert"][-limit:]

    def load_ai_cache(self) -> list[dict[str, str]]:
        values = self._retry_call(self.ws[S.TAB_AI_CACHE].get_all_values)
        if len(values) < 2:
            return []
        header = values[0]
        return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(header)} for r in values[1:] if any(r)]

    def save_ai_cache(self, rows: list[list[str]]) -> None:
        """Rewrites only the small cache tab (never touches job data)."""
        self._guard("write the AI cache")
        w = self.ws[S.TAB_AI_CACHE]
        self._retry_call(w.batch_clear, ["A2:E"])
        if rows:
            need = len(rows) + 5
            if need > w.row_count:
                self._retry_call(w.add_rows, need - w.row_count)
            for off in range(0, len(rows), 500):
                self._retry_call(w.update, range_name=f"A{off + 2}", values=rows[off: off + 500], value_input_option="RAW")

    def append_stats(self, rows: list[list[str]]) -> None:
        self._guard("append statistics")
        if rows:
            self._retry_call(self.ws[S.TAB_STATS].append_rows, rows, value_input_option="RAW")

    def append_logs(self, rows: list[list[str]]) -> None:
        self._guard("append logs")
        if rows:
            self._retry_call(self.ws[S.TAB_LOGS].append_rows, rows[:400], value_input_option="RAW")
