"""Optional Gemini helper. Disabled unless GEMINI_API_KEY is set; strictly call-limited; rules always win.

Safety design: Gemini is used only as a *locator*.
* Experience: it must return a VERBATIM quote from the job text that states the experience requirement. We verify the
  quote exists in the text and then interpret it with our own deterministic parser. Gemini never supplies numbers.
* Function: it may suggest one label from our fixed list, used only when the rules found nothing.
If anything fails (no key, quota, bad JSON, quote not found) the caller silently falls back to the rules.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time

from config.profile import FUNCTIONS
from core.http import HttpClient

log = logging.getLogger("ai")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiAssistant:
    def __init__(self, api_key: str, model: str, max_calls: int, http: HttpClient) -> None:
        self.api_key, self.model, self.max_calls, self.http = api_key, model, max_calls, http
        self.calls = 0
        self.failures = 0
        self._cache: dict[str, dict | None] = {}
        self.retry_pause = 2.0

    @property
    def available(self) -> bool:
        return bool(self.api_key) and self.calls < self.max_calls and self.failures < 3

    def _ask(self, prompt: str) -> dict | None:
        """One Gemini call, cached per prompt. Transient errors (429/5xx/network) get ONE retry after a short jittered
        pause; permanent errors (400/401/403/404) are never retried and count as a failure."""
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if key in self._cache:
            return self._cache[key]
        if not self.available:
            return None
        result: dict | None = None
        for attempt in (1, 2):
            self.calls += 1
            try:
                resp = self.http.post(
                    URL.format(model=self.model),
                    headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}},
                    timeout=30,
                )
            except Exception as exc:  # noqa: BLE001 - network problem: transient
                log.warning("Gemini request failed (%s)", type(exc).__name__)
                if attempt == 1 and self.available:
                    time.sleep(self.retry_pause * (1 + random.random()))
                    continue
                self.failures += 1
                break
            if resp.status_code == 200:
                try:
                    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                    data = json.loads(text)
                    result = data if isinstance(data, dict) else None
                except Exception as exc:  # noqa: BLE001
                    self.failures += 1
                    log.warning("Gemini reply could not be parsed (%s); falling back to rules", type(exc).__name__)
                break
            if resp.status_code in (429, 500, 502, 503, 504) and attempt == 1 and self.available:
                time.sleep(self.retry_pause * (1 + random.random()))
                continue
            self.failures += 1
            log.warning("Gemini returned HTTP %s; falling back to rules", resp.status_code)
            break
        self._cache[key] = result
        return result

    def find_experience_quote(self, title: str, description: str) -> str | None:
        data = self._ask(
            "From the job posting below, copy VERBATIM the single sentence or phrase that states how many years of "
            "work experience the candidate needs. If none is stated, return an empty string. Reply as JSON: "
            '{"quote": "..."}\n\nTITLE: ' + title + "\n\nPOSTING:\n" + description[:6000])
        quote = (data or {}).get("quote") if isinstance(data, dict) else None
        if not quote or not isinstance(quote, str):
            return None
        norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()  # noqa: E731
        return quote if norm(quote) in norm(description) else None

    def suggest_function(self, title: str, description: str) -> str | None:
        labels = [f for f in FUNCTIONS if f != "Other Management"]
        data = self._ask(
            "Classify this job into exactly one function from this list, or return \"None\" if it is not a "
            f"business/management/finance/marketing/operations style role. List: {labels}. Reply as JSON: "
            '{"function": "..."}\n\nTITLE: ' + title + "\n\nPOSTING:\n" + description[:3000])
        fn = (data or {}).get("function") if isinstance(data, dict) else None
        return fn if fn in labels else None
