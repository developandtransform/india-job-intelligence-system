import pytest
from gspread.exceptions import APIError
from tenacity import wait_none

from integrations import google_sheets as G


class FakeResp:
    def __init__(self, code):
        self.status_code = code
        self.text = "x"

    def json(self):
        return {"error": {"code": self.status_code, "message": "m", "status": "S"}}


def api_error(code):
    return APIError(FakeResp(code))


def flaky(codes):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] <= len(codes):
            raise api_error(codes[calls["n"] - 1])
        return "ok"
    return fn, calls


def test_quota_and_server_errors_are_retried():
    fn, calls = flaky([429, 503, 500])
    assert G._retry(fn).retry_with(wait=wait_none())() == "ok"
    assert calls["n"] == 4


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_permanent_errors_are_not_retried(code):
    fn, calls = flaky([code] * 10)
    with pytest.raises(APIError):
        G._retry(fn).retry_with(wait=wait_none())()
    assert calls["n"] == 1


def test_gives_up_after_bounded_attempts():
    fn, calls = flaky([429] * 50)
    with pytest.raises(APIError):
        G._retry(fn).retry_with(wait=wait_none())()
    assert calls["n"] == 6


def test_friendly_messages_do_not_leak_details():
    assert "Share the spreadsheet" in G.explain_error(api_error(403), "svc@proj.iam.gserviceaccount.com")
    assert "GOOGLE_SHEET_ID" in G.explain_error(api_error(404))
    assert "quota" in G.explain_error(api_error(429)).lower()
