"""GoogleSheetsStore logic against an in-memory fake of the gspread worksheet (no network).
Proves our diffing / manual-column protection / row-shift detection; does NOT prove real Google API behaviour."""
import pytest

from core.models import MASTER_COLUMNS, Job
from integrations import google_sheets as G
from integrations import sheet_schema as S


class FakeWS:
    def __init__(self, values):
        self.values, self.row_count, self.col_count, self.batches = values, 1000, len(MASTER_COLUMNS), []

    def get_all_values(self):
        return [list(r) for r in self.values]

    def col_values(self, c):
        return [r[c - 1] if c - 1 < len(r) else "" for r in self.values]

    def add_rows(self, n):
        self.row_count += n

    def batch_update(self, data, value_input_option=None):
        assert value_input_option == "RAW"
        self.batches.append(data)
        for item in data:
            a, b = item["range"].split(":") if ":" in item["range"] else (item["range"], item["range"])
            r1, c1 = _a1(a)
            for ri, row in enumerate(item["values"]):
                while len(self.values) < r1 + ri:
                    self.values.append([""] * len(MASTER_COLUMNS))
                for ci, v in enumerate(row):
                    tgt = self.values[r1 + ri - 1]
                    while len(tgt) < c1 + ci:
                        tgt.append("")
                    tgt[c1 + ci - 1] = v


def _a1(ref):
    import re
    m = re.match(r"([A-Z]+)(\d+)", ref)
    col = 0
    for ch in m.group(1):
        col = col * 26 + ord(ch) - 64
    return int(m.group(2)), col


def make_store(rows):
    store = G.GoogleSheetsStore.__new__(G.GoogleSheetsStore)
    store.ws = {S.TAB_ALL: FakeWS([MASTER_COLUMNS] + rows)}
    store._hdr, store._rows, store._ids, store._pos = {}, [], [], {}
    store._retry_call = lambda fn, *a, **k: fn(*a, **k)
    return store


def row_for(job):
    rec = job.to_record()
    return [rec[c] for c in MASTER_COLUMNS]


def job(i="a1", title="T"):
    return Job(internal_id=i, title=title, company="C", category="Strong Matches", application_status="New", job_status="Active")


def test_updates_only_changed_cells_and_keeps_manual_columns():
    j = job()
    store = make_store([row_for(j)])
    store.ws[S.TAB_ALL].values[1][0] = "Applied"       # user edited manually in the sheet
    store.ws[S.TAB_ALL].values[1][1] = "my note"
    store.load_master()
    j.title = "New Title"
    j.application_status = "New"                         # program's copy is stale on purpose
    info = store.save_master([j])
    vals = store.ws[S.TAB_ALL].values
    assert info["changed"] == 1 and info["new"] == 0
    assert vals[1][0] == "Applied" and vals[1][1] == "my note"
    assert vals[1][MASTER_COLUMNS.index("Job Title")] == "New Title"


def test_unchanged_rows_write_nothing():
    j = job()
    store = make_store([row_for(j)])
    store.load_master()
    assert store.save_master([j])["ranges"] == 0


def test_new_rows_appended():
    j, k = job("a1"), job("b2", "Other")
    store = make_store([row_for(j)])
    store.load_master()
    info = store.save_master([j, k])
    assert info["new"] == 1 and store.ws[S.TAB_ALL].values[2][MASTER_COLUMNS.index("Internal Job ID")] == "b2"


def test_row_shift_detected_and_nothing_written():
    a, b = job("a1"), job("b2", "B")
    store = make_store([row_for(a), row_for(b)])
    store.load_master()
    ws = store.ws[S.TAB_ALL]
    ws.values[1], ws.values[2] = ws.values[2], ws.values[1]      # user sorted the sheet mid-run
    a.title = "changed"
    with pytest.raises(G.SheetChangedError):
        store.save_master([a, b])
    assert ws.batches == []
