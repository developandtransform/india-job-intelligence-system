"""Fake HTTP layer shared by the AI tests. Nothing here touches the network."""


class R:
    def __init__(self, status, body=None, headers=None, text=None):
        self.status_code, self._b, self.headers = status, body, headers or {}
        self.text = text if text is not None else ("" if body is None else str(body))

    def json(self):
        if self._b is None:
            raise ValueError("no json")
        return self._b


class FakeHttp:
    """Plays back responses in order (the last one repeats); an Exception instance is raised instead."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        r = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def openai_body(text):
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


def cf_body(resp):
    return {"success": True, "errors": [], "result": {"response": resp}}


def gemini_body(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def jerr(status, msg, headers=None):
    return R(status, {"error": {"code": status, "message": msg}}, headers)


class FakeProvider:
    """Stands in for a real provider inside an AIRouter: scripted answers, counts calls. Mocked - not a live test."""

    def __init__(self, name="groq", answer=None, fail_kind=None, model="fake-model"):
        from integrations.ai_providers import ProviderStats
        self.name, self.model, self.stats = name, model, ProviderStats()
        self.calls, self.prompts = 0, []
        self.answer, self.fail_kind = answer, fail_kind

    def complete_json(self, prompt):
        from integrations.ai_providers import AIError
        self.calls += 1
        self.stats.requests += 1
        self.prompts.append(prompt)
        if self.fail_kind:
            self.stats.failures += 1
            raise AIError(self.name, self.fail_kind, "scripted failure", 503 if self.fail_kind == "transient" else None)
        out = self.answer(prompt) if callable(self.answer) else self.answer
        self.stats.successes += 1
        return out


def role_aware_answer(prompt):
    """Experience question -> quote found in the posting text; function question -> 'Operations'."""
    if "VERBATIM" in prompt:
        return {"quote": "Exp: 1-2 yrs" if "Exp: 1-2 yrs" in prompt else ""}
    return {"function": "Operations"}
