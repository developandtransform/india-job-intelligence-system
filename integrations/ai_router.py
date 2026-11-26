"""Central AI router: tries providers in priority order, fails over, remembers which ones are unavailable this run.

Rules (see docs/CONNECTORS.md):
* providers are tried in ``AI_PROVIDER_ORDER``; a provider with no key/credentials or ``*_ENABLED=false`` is never built
* a permanent error (bad key, bad model, bad config) or an exhausted quota switches that provider off for the rest of the run
* a rate limit whose Retry-After is longer than we are willing to wait puts the provider on cool-down for that long
* two failures in a row switch a provider off for the run, so a down provider is not hammered
* every reply must pass the caller's validator, otherwise the next provider is tried
* the router never raises into the pipeline: callers get ``None`` and fall back to the Python rules
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from integrations.ai_providers import (AIError, MALFORMED, PERMANENT, QUOTA, RATE_LIMIT, Provider, PROVIDER_CLASSES,
                                       run_check, CheckResult)

log = logging.getLogger("ai")
DEFAULT_ORDER = ["groq", "gemini", "cloudflare", "openrouter"]
MAX_CONSECUTIVE_FAILURES = 2
DEFAULT_COOLDOWN = 60.0


@dataclass
class ProviderState:
    provider: Provider
    max_calls: int = 0                      # 0 = no per-run limit
    calls: int = 0                          # questions routed to this provider
    successes: int = 0
    failures: int = 0
    fallbacks_from: int = 0                 # times we had to move on to the next provider after this one failed
    consecutive_failures: int = 0
    disabled_reason: str = ""
    cooldown_until: float = 0.0


class AIRouter:
    def __init__(self, providers: list[Provider], max_calls: dict[str, int] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        max_calls = max_calls or {}
        self.states = [ProviderState(p, max_calls.get(p.name, 0)) for p in providers]
        self._clock = clock
        self.fallback_attempts = 0

    # ---- availability
    def _usable(self, st: ProviderState) -> bool:
        if st.disabled_reason or (st.max_calls and st.calls >= st.max_calls):
            return False
        return self._clock() >= st.cooldown_until

    @property
    def available(self) -> bool:
        return any(self._usable(s) for s in self.states)

    @property
    def names(self) -> list[str]:
        return [s.provider.name for s in self.states]

    # ---- the one entry point
    def complete_json(self, prompt: str, validate: Callable[[dict], bool] | None = None) -> tuple[dict, str]:
        """Return (answer, provider_name). Raises AIError only when every provider failed or none is usable."""
        last: AIError | None = None
        tried = 0
        for st in self.states:
            if not self._usable(st):
                continue
            if tried:
                self.fallback_attempts += 1
                log.info("[ai] falling back to %s", st.provider.name)
            tried += 1
            st.calls += 1
            try:
                out = st.provider.complete_json(prompt)
                if validate is not None and not validate(out):
                    raise AIError(st.provider.name, MALFORMED, "the answer did not have the expected fields")
            except AIError as err:
                self._record_failure(st, err)
                last = err
                st.fallbacks_from += 1
                continue
            st.successes += 1
            st.consecutive_failures = 0
            return out, st.provider.name
        raise last or AIError("router", "config", "no AI provider is available (all disabled, exhausted or cooling down)")

    def _record_failure(self, st: ProviderState, err: AIError) -> None:
        st.failures += 1
        st.consecutive_failures += 1
        name = st.provider.name
        if err.kind in PERMANENT or err.kind == QUOTA:
            st.disabled_reason = str(err)
            log.error("[ai] %s switched off for the rest of this run: %s", name, err)
        elif err.kind == RATE_LIMIT or err.retry_after:
            wait = min(max(err.retry_after or DEFAULT_COOLDOWN, 1.0), 3600.0)
            st.cooldown_until = self._clock() + wait
            log.warning("[ai] %s is rate limited; skipping it for %.0fs", name, wait)
        elif st.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            st.disabled_reason = f"{st.consecutive_failures} failures in a row (last: {err})"
            log.error("[ai] %s switched off for the rest of this run: %s", name, st.disabled_reason)

    # ---- reporting (measured values only)
    def usage(self) -> dict[str, dict[str, Any]]:
        return {s.provider.name: {
            "calls": s.calls, "http_requests": s.provider.stats.requests, "retries": s.provider.stats.retries,
            "successes": s.successes, "failures": s.failures, "fallbacks": s.fallbacks_from,
            "model": s.provider.model, "disabled": s.disabled_reason[:160],
        } for s in self.states}

    def check_all(self) -> list[CheckResult]:
        """`check-ai`: every provider tested on its own (ignores cool-downs and run limits)."""
        return [run_check(s.provider) for s in self.states]


def build_providers(settings: Any, http: Any) -> list[Provider]:
    """Providers that are enabled AND have credentials, in the configured order."""
    if not settings.ai_enabled:
        return []
    common = dict(timeout=settings.ai_timeout_seconds, max_retries=settings.ai_max_retries,
                  backoff_base=settings.ai_backoff_base_seconds, backoff_max=settings.ai_backoff_max_seconds,
                  max_retry_after=settings.ai_max_retry_after_seconds)
    out: list[Provider] = []
    order = list(settings.ai_provider_order)
    if settings.custom_enabled and "custom" not in order:
        order.append("custom")                       # enabling it is enough; it goes last
    for name in order:
        cls = PROVIDER_CLASSES.get(name)
        if cls is None:
            continue
        cfg = settings.provider_config(name)
        if not (cfg["enabled"] and cfg["api_key"]):
            continue
        if name == "cloudflare":
            if not cfg.get("account_id"):
                continue
            out.append(cls(cfg["api_key"], cfg["model"], http, account_id=cfg["account_id"], **common))
        elif name == "custom":
            if not cfg.get("base_url"):
                continue
            out.append(cls(cfg["api_key"], cfg["model"], http, base_url=cfg["base_url"], **common))
        else:
            out.append(cls(cfg["api_key"], cfg["model"], http, **common))
    return out


def build_router(settings: Any, http: Any) -> AIRouter | None:
    providers = build_providers(settings, http)
    if not providers:
        return None
    return AIRouter(providers, {p.name: settings.provider_config(p.name)["max_calls"] for p in providers})
