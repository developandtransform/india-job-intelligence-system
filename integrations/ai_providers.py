"""AI provider clients (one small class per vendor) with careful error handling.

Design rules
* A provider turns a prompt into a JSON *object* (dict) or raises ``AIError`` with a clear ``kind``.
* Transient problems (HTTP 429/500/502/503/504, timeouts, connection errors) are retried with capped, jittered
  exponential back-off; ``Retry-After`` is honoured when it is reasonably short.
* Permanent problems (bad key, wrong model, malformed request) are NEVER retried.
* Every log line names the provider, model and HTTP status plus the vendor's own error message. API keys are
  redacted from everything that is logged or shown.
* No provider can change an eligibility decision: callers must still validate the answer with deterministic code.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

log = logging.getLogger("ai")

# error kinds
TRANSIENT, RATE_LIMIT, QUOTA, AUTH, MODEL, BAD_REQUEST, MALFORMED, TIMEOUT, CONFIG = (
    "transient", "rate_limit", "quota", "auth", "model", "bad_request", "malformed", "timeout", "config")
RETRYABLE = {TRANSIENT, RATE_LIMIT, TIMEOUT}
PERMANENT = {AUTH, MODEL, CONFIG}          # switch the provider off for the rest of the run

_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                 # Google API keys
    re.compile(r"gsk_[0-9A-Za-z]{20,}"),                    # Groq keys
    re.compile(r"sk-or-[0-9A-Za-z_\-]{10,}"),               # OpenRouter keys
    re.compile(r"(?i)bearer\s+[0-9A-Za-z._\-]{10,}"),
    re.compile(r"(?i)\b(api[_-]?key|key|token|authorization)=[^&\s\"']+"),
]


def redact(text: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    """Remove known secret values and anything that looks like an API key from ``text``."""
    out = str(text)
    for s in sorted({x for x in secrets if x and len(x) >= 6}, key=len, reverse=True):
        out = out.replace(s, "***")
    for pat in _SECRET_PATTERNS:
        out = pat.sub("***", out)
    return out


class AIError(Exception):
    def __init__(self, provider: str, kind: str, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.provider, self.kind, self.message, self.status, self.retry_after = provider, kind, message, status, retry_after

    def __str__(self) -> str:
        st = f"HTTP {self.status} " if self.status else ""
        return f"[{self.provider}] {self.kind}: {st}{self.message}"


@dataclass
class ProviderStats:
    requests: int = 0        # HTTP requests actually sent (including retries)
    retries: int = 0
    successes: int = 0       # accepted answers
    failures: int = 0        # calls that ended in an error
    last_error: str = ""
    kinds: dict[str, int] = field(default_factory=dict)


def error_message(text: str, data: Any = None, limit: int = 300) -> str:
    """Pull the vendor's own error message out of a response body (JSON shapes of Google, OpenAI-style and Cloudflare)."""
    msg = ""
    try:
        d = data if data is not None else json.loads(text)
    except Exception:  # noqa: BLE001
        d = None
    if isinstance(d, dict):
        err = d.get("error")
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("status") or "")
        elif isinstance(err, str):
            msg = err
        elif isinstance(d.get("errors"), list) and d["errors"]:
            first = d["errors"][0]
            msg = str(first.get("message") if isinstance(first, dict) else first)
        elif d.get("message"):
            msg = str(d["message"])
    if not msg:
        msg = (text or "").strip().replace("\n", " ")
    return redact(msg)[:limit]


def parse_retry_after(headers: Any, data: Any = None) -> float | None:
    """Seconds from a Retry-After header, or Google's ``retryDelay`` ("34s") inside the error details."""
    try:
        h = headers.get("retry-after") or headers.get("Retry-After")
        if h is not None and str(h).strip():
            return max(0.0, float(str(h).strip()))
    except Exception:  # noqa: BLE001
        pass
    try:
        for det in (data or {}).get("error", {}).get("details", []):
            m = re.fullmatch(r"\s*([0-9.]+)s\s*", str(det.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def extract_json_object(text: str) -> dict:
    """Parse a model reply into a dict. Accepts a bare JSON object or one wrapped in a ```json fence."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("reply is JSON but not an object")
    return data


class Provider:
    """Base class: retry/back-off/validation logic. Subclasses only build the request and read the answer."""

    name = "base"

    def __init__(self, api_key: str, model: str, http: Any, timeout: float = 30.0, max_retries: int = 3,
                 backoff_base: float = 2.0, backoff_max: float = 30.0, max_retry_after: float = 30.0,
                 sleep: Callable[[float], None] = time.sleep, rand: Callable[[], float] = random.random, **extra: Any) -> None:
        self.api_key, self.model, self.http = api_key, model, http
        self.timeout, self.max_retries = timeout, max(0, int(max_retries))
        self.backoff_base, self.backoff_max, self.max_retry_after = backoff_base, backoff_max, max_retry_after
        self._sleep, self._rand = sleep, rand
        self.extra = extra
        self.stats = ProviderStats()

    # ---- to be provided by subclasses
    def build_request(self, prompt: str) -> tuple[str, dict[str, str], dict]:
        raise NotImplementedError

    def read_text(self, data: dict) -> str:
        raise NotImplementedError

    def classify(self, status: int, data: Any, text: str) -> str:
        if status == 429:
            msg = error_message(text, data).lower()
            return QUOTA if re.search(r"per day|daily|\brpd\b|\btpd\b", msg) else RATE_LIMIT
        if status in (500, 502, 503, 504, 408, 529):
            return TRANSIENT
        if status in (401, 403):
            return AUTH
        if status == 404:
            return MODEL
        return BAD_REQUEST

    # ---- shared logic
    def label(self) -> str:
        return f"[ai:{self.name}] model={self.model}"

    def _fail(self, err: AIError) -> AIError:
        self.stats.failures += 1
        self.stats.last_error = str(err)
        self.stats.kinds[err.kind] = self.stats.kinds.get(err.kind, 0) + 1
        return err

    def _backoff(self, attempt: int) -> float:
        return min(self.backoff_max, self.backoff_base * (2 ** attempt)) * (0.5 + self._rand())      # jitter 0.5x-1.5x, then capped
    
    def complete_json(self, prompt: str) -> dict:
        """Return the model's answer as a dict or raise AIError. Retries only temporary problems."""
        try:
            url, headers, body = self.build_request(prompt)
        except AIError as exc:
            raise self._fail(exc)
        for attempt in range(self.max_retries + 1):
            self.stats.requests += 1
            err: AIError | None = None
            try:
                resp = self.http.post(url, headers=headers, json=body, timeout=self.timeout)
            except requests.Timeout:
                err = AIError(self.name, TIMEOUT, f"request timed out after {self.timeout:g}s")
            except requests.RequestException as exc:
                err = AIError(self.name, TRANSIENT, f"network error ({type(exc).__name__})")
            else:
                status = resp.status_code
                try:
                    data = resp.json()
                except Exception:  # noqa: BLE001
                    data = None
                text = ""
                if data is None:
                    text = (getattr(resp, "text", "") or "")[:500]
                if status == 200:
                    try:
                        return self._accept(data)
                    except AIError as exc:
                        raise self._fail(exc)
                kind = self.classify(status, data, text)
                err = AIError(self.name, kind, error_message(text, data), status, parse_retry_after(getattr(resp, "headers", {}) or {}, data))
            # ---- error handling
            if err.kind in RETRYABLE and attempt < self.max_retries:
                if err.retry_after is not None and err.retry_after > self.max_retry_after:
                    log.warning("%s HTTP %s %s: %s - Retry-After %.0fs is too long, giving up on this provider for now",
                                self.label(), err.status or "-", err.kind, err.message, err.retry_after)
                    raise self._fail(err)
                wait = err.retry_after if err.retry_after is not None else self._backoff(attempt)
                log.warning("%s HTTP %s %s: %s - retrying in %.1fs (retry %d of %d)", self.label(), err.status or "-",
                            err.kind, err.message, wait, attempt + 1, self.max_retries)
                self.stats.retries += 1
                self._sleep(wait)
                continue
            log.warning("%s HTTP %s %s: %s%s", self.label(), err.status or "-", err.kind, err.message,
                        " - not retrying" if err.kind not in RETRYABLE else " - out of retries")
            raise self._fail(err)
        raise self._fail(AIError(self.name, TRANSIENT, "out of retries"))      # pragma: no cover

    def _accept(self, data: Any) -> dict:
        if not isinstance(data, dict):
            raise AIError(self.name, MALFORMED, "HTTP 200 but the body is not JSON")
        try:
            text = self.read_text(data)
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AIError(self.name, MALFORMED, f"unexpected response structure ({type(exc).__name__})") from None
        if isinstance(text, dict):                                    # some providers already return an object
            self.stats.successes += 1
            return text
        try:
            out = extract_json_object(text)
        except Exception:  # noqa: BLE001
            raise AIError(self.name, MALFORMED, "the model's reply was not valid JSON") from None
        self.stats.successes += 1
        return out


# --------------------------------------------------------------------------------------------- Gemini
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_GEMINI_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")


def normalize_gemini_model(raw: str) -> str:
    """'models/gemini-3.8-flash' (as printed by the model-list call) -> 'gemini-3.8-flash'."""
    m = (raw or "").strip().strip("\"'").strip()
    if m.lower().startswith("models/"):
        m = m[7:]
    return m


class GeminiProvider(Provider):
    name = "gemini"

    def build_request(self, prompt: str) -> tuple[str, dict[str, str], dict]:
        model = normalize_gemini_model(self.model)
        if not _GEMINI_MODEL_RE.match(model):
            raise AIError(self.name, CONFIG, f"GEMINI_MODEL {self.model!r} is not a valid model name (example: gemini-3.8-flash)")
        if not self.api_key:
            raise AIError(self.name, CONFIG, "GEMINI_API_KEY is empty")
        url = GEMINI_URL.format(model=model)
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
        return url, headers, body

    def classify(self, status: int, data: Any, text: str) -> str:
        msg = error_message(text, data)
        if status == 400 and re.search(r"api key not valid|API_KEY_INVALID|api key expired", msg, re.I):
            return AUTH
        if status == 400 and re.search(r"not found|is not supported|unknown model", msg, re.I):
            return MODEL
        return super().classify(status, data, text)

    def read_text(self, data: dict) -> str:
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates returned")
            raise AIError(self.name, MALFORMED, f"no answer ({reason})")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if not text.strip():
            raise AIError(self.name, MALFORMED, f"empty answer (finishReason={cands[0].get('finishReason', '?')})")
        return text


# --------------------------------------------------------------------------------------------- self-test
CHECK_PROMPT = 'Reply with exactly this JSON object and nothing else: {"ok": true, "word": "pong"}'


@dataclass
class CheckResult:
    provider: str
    model: str
    connected: bool = False       # an HTTP answer with status 200 was received
    validated: bool = False       # ... and it was the JSON we asked for
    status: str = ""
    reason: str = ""
    seconds: float = 0.0
    retries: int = 0


def run_check(provider: Provider) -> CheckResult:
    """One tiny real generation request. Used by `python main.py check-ai`."""
    res = CheckResult(provider.name, provider.model)
    t0 = time.monotonic()
    try:
        out = provider.complete_json(CHECK_PROMPT)
    except AIError as exc:
        res.status = f"HTTP {exc.status}" if exc.status else exc.kind
        res.connected = exc.kind == MALFORMED          # we did get a 200, just not the JSON we wanted
        res.reason = f"{exc.kind}: {exc.message}"
    else:
        res.connected = True
        res.status = "HTTP 200"
        res.validated = out.get("ok") is True and str(out.get("word", "")).lower() == "pong"
        res.reason = "" if res.validated else f"answered, but not with the expected JSON ({str(out)[:80]})"
    res.seconds = round(time.monotonic() - t0, 2)
    res.retries = provider.stats.retries
    return res


# --------------------------------------------------------------------------------------------- OpenAI-compatible (Groq, OpenRouter, ...)
class OpenAICompatProvider(Provider):
    """Any service that speaks the OpenAI ``/chat/completions`` format. To add another such service, subclass this
    and set ``name``, ``url`` and (optionally) ``extra_headers`` - that is all (see docs/CONNECTORS.md)."""

    name = "openai-compatible"
    url = ""
    key_name = "API_KEY"
    extra_headers: dict[str, str] = {}
    json_mode = True

    def build_request(self, prompt: str) -> tuple[str, dict[str, str], dict]:
        if not self.api_key:
            raise AIError(self.name, CONFIG, f"{self.key_name} is empty")
        if not self.model or not re.match(r"^[A-Za-z0-9@][A-Za-z0-9@._:/\-]*$", self.model):
            raise AIError(self.name, CONFIG, f"model name {self.model!r} is not valid")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", **self.extra_headers}
        body: dict[str, Any] = {"model": self.model, "temperature": 0,
                                "messages": [{"role": "system", "content": "You answer only with one valid JSON object."},
                                             {"role": "user", "content": prompt}]}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        return self.url, headers, body

    def read_text(self, data: dict) -> str:
        if isinstance(data.get("error"), dict):                       # some routers return HTTP 200 with an error body
            raise AIError(self.name, TRANSIENT, error_message("", data), data["error"].get("code") if isinstance(data["error"].get("code"), int) else None)
        choices = data.get("choices") or []
        if not choices:
            raise AIError(self.name, MALFORMED, "no choices returned")
        content = (choices[0].get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise AIError(self.name, MALFORMED, f"empty answer (finish_reason={choices[0].get('finish_reason', '?')})")
        return content


class GroqProvider(OpenAICompatProvider):
    name = "groq"
    url = "https://api.groq.com/openai/v1/chat/completions"
    key_name = "GROQ_API_KEY"

    def classify(self, status: int, data: Any, text: str) -> str:
        msg = error_message(text, data)
        if status == 400 and re.search(r"model.*(not found|does not exist|decommissioned)", msg, re.I):
            return MODEL
        if status == 429 and re.search(r"tokens per day|requests per day|\bTPD\b|\bRPD\b", msg, re.I):
            return QUOTA
        return super().classify(status, data, text)


class OpenRouterProvider(OpenAICompatProvider):
    name = "openrouter"
    url = "https://openrouter.ai/api/v1/chat/completions"
    key_name = "OPENROUTER_API_KEY"
    extra_headers = {"X-Title": "India Job Intelligence System"}

    def classify(self, status: int, data: Any, text: str) -> str:
        msg = error_message(text, data)
        if status == 402:
            return QUOTA                                              # credits / free-limit exhausted
        if status in (400, 404) and re.search(r"no endpoints found|model.*not (found|available)|not a valid model", msg, re.I):
            return MODEL
        if status == 429 and re.search(r"free-models-per-day|per day|daily", msg, re.I):
            return QUOTA
        return super().classify(status, data, text)


# --------------------------------------------------------------------------------------------- Cloudflare Workers AI
class CloudflareProvider(Provider):
    name = "cloudflare"

    def __init__(self, api_key: str, model: str, http: Any, account_id: str = "", **kw: Any) -> None:
        super().__init__(api_key, model, http, **kw)
        self.account_id = (account_id or "").strip()

    def build_request(self, prompt: str) -> tuple[str, dict[str, str], dict]:
        if not self.account_id or not re.fullmatch(r"[0-9a-fA-F]{32}", self.account_id):
            raise AIError(self.name, CONFIG, "CLOUDFLARE_ACCOUNT_ID must be the 32-character account ID")
        if not self.api_key:
            raise AIError(self.name, CONFIG, "CLOUDFLARE_API_TOKEN is empty")
        if not re.match(r"^@?[A-Za-z0-9][A-Za-z0-9._/\-]*$", self.model or ""):
            raise AIError(self.name, CONFIG, f"CLOUDFLARE_MODEL {self.model!r} is not valid (example: @cf/meta/llama-3.1-8b-instruct)")
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{self.model}"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {"messages": [{"role": "system", "content": "You answer only with one valid JSON object."},
                             {"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}, "temperature": 0, "max_tokens": 600}
        return url, headers, body

    def classify(self, status: int, data: Any, text: str) -> str:
        msg = error_message(text, data)
        if re.search(r"neurons|daily free allocation|quota", msg, re.I):
            return QUOTA
        if status in (400, 404) and re.search(r"no such model|model.*not found|unknown model", msg, re.I):
            return MODEL
        if status == 400 and re.search(r"json mode", msg, re.I):
            return MALFORMED                                          # "JSON Mode couldn't be met": not retried, next provider
        return super().classify(status, data, text)

    def read_text(self, data: dict) -> Any:
        if data.get("success") is False:
            raise AIError(self.name, MALFORMED, error_message("", data))
        res = data.get("result")
        if not isinstance(res, dict):
            raise AIError(self.name, MALFORMED, "no 'result' in the answer")
        out = res.get("response")
        if isinstance(out, dict):                                     # JSON mode may already return an object
            return out
        if not isinstance(out, str) or not out.strip():
            raise AIError(self.name, MALFORMED, "empty answer")
        return out


class CustomProvider(OpenAICompatProvider):
    """Any other OpenAI-compatible service (Cerebras, Mistral, Together, a local server ...) configured only through
    CUSTOM_BASE_URL / CUSTOM_API_KEY / CUSTOM_MODEL - no code needed."""

    name = "custom"
    key_name = "CUSTOM_API_KEY"

    def __init__(self, api_key: str, model: str, http: Any, base_url: str = "", **kw: Any) -> None:
        super().__init__(api_key, model, http, **kw)
        self.base_url = (base_url or "").strip().rstrip("/")

    def build_request(self, prompt: str):
        if not self.base_url.startswith("https://"):
            raise AIError(self.name, CONFIG, "CUSTOM_BASE_URL must start with https:// (example: https://api.cerebras.ai/v1)")
        self.url = self.base_url if self.base_url.endswith("/chat/completions") else self.base_url + "/chat/completions"
        return super().build_request(prompt)


PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "gemini": GeminiProvider, "groq": GroqProvider, "cloudflare": CloudflareProvider, "openrouter": OpenRouterProvider,
    "custom": CustomProvider}
