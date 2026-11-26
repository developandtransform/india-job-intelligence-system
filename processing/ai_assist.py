"""Optional Gemini helper. Disabled unless GEMINI_API_KEY is set; strictly call-limited; rules always win.

Safety design: Gemini is used only as a *locator*.
* Experience: it must return a VERBATIM quote from the job text that states the experience requirement. We verify the
  quote exists in the text and then interpret it with our own deterministic parser. Gemini never supplies numbers.
* Function: it may suggest one label from our fixed list, used only when the rules found nothing.
If anything fails (no key, quota, bad JSON, quote not found) the caller silently falls back to the rules.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
import re
import time
from typing import Any, Callable

from config.profile import FUNCTIONS
from integrations.ai_providers import AIError, GeminiProvider
from integrations.ai_router import AIRouter, build_router
from processing.ai_cache import AICache, cache_key

log = logging.getLogger("ai")


@dataclass
class AIUsage:
    """Measured (not estimated) per-run counters shown in the Source Statistics tab."""
    seen: set = field(default_factory=set)             # distinct jobs evaluated this run
    needed: set = field(default_factory=set)           # jobs where the rules were inconclusive and AI could help
    asked: set = field(default_factory=set)            # ... of which a question was really sent to a provider
    cache_hits: int = 0
    fn_accepted: set = field(default_factory=set)      # AI function suggestions that the posting text supports
    fn_uncertain: set = field(default_factory=set)     # only weak support: kept at the lowest score and flagged
    fn_rejected: set = field(default_factory=set)      # no support in the text: ignored, rules decide
    ineligible: set = field(default_factory=set)       # rules already excluded the job: no AI needed
    thin_text: set = field(default_factory=set)        # description too short to be worth a question
    unavailable: set = field(default_factory=set)      # AI wanted but off / limit reached / all providers down
    prompt_chars: int = 0                              # characters sent (tokens ~ chars/4, an estimate)

    @property
    def skipped_ineligible(self) -> int:
        return len(self.ineligible)

    @property
    def skipped_thin_text(self) -> int:
        return len(self.thin_text)

    @property
    def skipped_unavailable(self) -> int:
        return len(self.unavailable)

    @property
    def rules_only(self) -> int:
        return len(self.seen) - len(self.needed)


class AIAssistant:
    """Call-limited helper on top of an :class:`AIRouter` (provider choice, retries and fail-over live there)."""

    def __init__(self, router: AIRouter, max_calls: int = 0, cache: AICache | None = None) -> None:
        self.router = router
        self.cache = cache if cache is not None else AICache()
        self.usage = AIUsage()
        self.max_calls = max_calls            # 0 = unlimited (per-provider limits still apply)
        self.calls = 0                        # questions that reached the router
        self.failures = 0                     # questions that no provider could answer
        self._cache: dict[str, dict | None] = {}
        self.last_provider: str | None = None

    @property
    def available(self) -> bool:
        return self.router.available and (not self.max_calls or self.calls < self.max_calls)

    @property
    def disabled_reason(self) -> str | None:
        reasons = [s.disabled_reason for s in self.router.states]
        return "; ".join(reasons) if reasons and all(reasons) else None

    def _ask(self, prompt: str, validate: Callable[[dict], bool] | None = None, kind: str = "q",
             job_id: str = "") -> dict | None:
        """One question. Order: this run's memory -> persistent cache -> providers. Only real answers are cached
        persistently; a failure is remembered for this run only. Returns None when nothing could answer."""
        key = cache_key(kind, prompt)
        if key in self._cache:
            return self._cache[key]
        hit, val = self.cache.get(key)
        if hit:
            self.usage.cache_hits += 1
            self._cache[key] = val
            return val
        if not self.available:
            self.usage.unavailable.add(job_id or key)
            return None
        self.calls += 1
        self.usage.prompt_chars += len(prompt)
        if job_id:
            self.usage.asked.add(job_id)
        result: dict | None = None
        try:
            result, self.last_provider = self.router.complete_json(prompt, validate)
            self.cache.put(key, kind, result)
        except AIError as err:
            self.failures += 1
            log.warning("No AI provider could answer (%s); using the rules for this job", err)
        self._cache[key] = result
        return result

    def find_experience_quote(self, title: str, description: str, job_id: str = "") -> str | None:
        data = self._ask(
            "From the job posting below, copy VERBATIM the single sentence or phrase that states how many years of "
            "work experience the candidate needs. If none is stated, return an empty string. Reply as JSON: "
            '{"quote": "..."}\n\nTITLE: ' + title + "\n\nPOSTING:\n" + description[:6000],
            lambda d: isinstance(d.get("quote"), str), "exp", job_id)
        quote = (data or {}).get("quote") if isinstance(data, dict) else None
        if not quote or not isinstance(quote, str):
            return None
        norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()  # noqa: E731
        return quote if norm(quote) in norm(description) else None

    def suggest_function(self, title: str, description: str, job_id: str = "") -> str | None:
        labels = [f for f in FUNCTIONS if f != "Other Management"]
        data = self._ask(
            "Classify this job into exactly one function from this list, or return \"None\" if it is not a "
            f"business/management/finance/marketing/operations style role. List: {labels}. Reply as JSON: "
            '{"function": "..."}\n\nTITLE: ' + title + "\n\nPOSTING:\n" + description[:3000],
            lambda d: isinstance(d.get("function"), str), "fn", job_id)
        fn = (data or {}).get("function") if isinstance(data, dict) else None
        return fn if fn in labels else None




class GeminiAssistant(AIAssistant):
    """Backward-compatible single-provider assistant (Gemini only)."""

    def __init__(self, api_key: str, model: str, max_calls: int, http: Any, *, timeout: float = 30.0,
                 max_retries: int = 3, backoff_base: float = 2.0, backoff_max: float = 30.0,
                 max_retry_after: float = 30.0, sleep: Callable[[float], None] = time.sleep,
                 rand: Callable[[], float] | None = None) -> None:
        import random
        self.api_key, self.model, self.http = api_key, model, http
        self.provider = GeminiProvider(api_key, model, http, timeout=timeout, max_retries=max_retries,
                                       backoff_base=backoff_base, backoff_max=backoff_max,
                                       max_retry_after=max_retry_after, sleep=sleep, rand=rand or random.random)
        super().__init__(AIRouter([self.provider]), max_calls)


def build_assistant(settings: Any, http: Any, cache: AICache | None = None) -> "AIAssistant | None":
    """The configured assistant (all enabled providers in order), or None for a rules-only run."""
    router = build_router(settings, http)
    return AIAssistant(router, settings.ai_max_calls_per_run, cache) if router else None


build_gemini = build_assistant        # old name kept for compatibility
