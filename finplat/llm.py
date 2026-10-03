"""One call to a chat model, over plain HTTP.

DeepSeek, OpenAI, OpenRouter and most others accept the OpenAI chat format, so one request shape
covers them all and the provider is a setting. No SDK: the request is one JSON POST, and an SDK
for each provider would add a dependency to save about 20 lines.
"""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

TIMEOUT_SECONDS = 60


class LLMError(RuntimeError):
    """The model did not answer, or answered with something unusable."""


class BudgetExceeded(LLMError):
    """Today's model calls are used up."""


def _utc_today() -> date:
    return datetime.now(UTC).date()


@dataclass
class Budget:
    """A cap on model calls for each UTC day.

    The dashboard has no login until Cloudflare Access is in front of it, and every visitor can
    press Ask. Each press spends the owner's credit, so the day has a limit. It is counted in
    memory: a restart resets it, and only the owner can restart the service.
    """

    per_day: int
    today: Callable[[], date] = _utc_today
    used: int = 0
    day: date | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def take(self) -> None:
        # Endpoints run in worker threads, so two questions can arrive at once.
        with self._lock:
            if self.today() != self.day:
                self.day, self.used = self.today(), 0
            if self.used >= self.per_day:
                raise BudgetExceeded(f"the limit of {self.per_day} AI calls today is used up. It resets at 00:00 UTC")
            self.used += 1

    def snapshot(self) -> dict:
        with self._lock:
            used = self.used if self.day == self.today() else 0
        return {"used": used, "per_day": self.per_day}


@dataclass
class LLM:
    base_url: str
    model: str
    api_key: str | None
    budget: Budget | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        json_output: bool = False,
        max_tokens: int = 800,
        temperature: float = 0.2,
    ) -> dict:
        """The model's reply message: `content`, and `tool_calls` when it wants a tool."""
        body = self._body(messages, tools, tool_choice, max_tokens, temperature)
        if json_output:
            body["response_format"] = {"type": "json_object"}
        try:
            with self._open(body) as response:
                answer = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise LLMError(f"{self.model} did not answer: {error}") from error

        try:
            return answer["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise LLMError(f"{self.model} answered without a message: {str(answer)[:300]}") from error

    def stream(
        self,
        messages: list[dict],
        *,
        on_text: Callable[[str], None],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        max_tokens: int = 800,
        temperature: float = 0.2,
    ) -> dict:
        """The same reply as `chat`, with each piece of text passed to `on_text` as it arrives.

        Text and tool calls arrive in pieces. The text goes out at once. The tool calls are
        assembled by their index, because the arguments of one call are split across many pieces.
        """
        body = self._body(messages, tools, tool_choice, max_tokens, temperature) | {"stream": True}
        text, calls = [], {}
        try:
            with self._open(body) as response:
                for raw in response:
                    line = raw.decode(errors="replace").strip()
                    # A blank line ends an event. A line that starts with a colon is a provider
                    # comment, such as the OpenRouter keep-alive.
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    choices = json.loads(data).get("choices") or [{}]
                    delta = choices[0].get("delta") or {}
                    if delta.get("content"):
                        text.append(delta["content"])
                        on_text(delta["content"])
                    for piece in delta.get("tool_calls") or []:
                        call = calls.setdefault(
                            piece.get("index", 0),
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        call["id"] = piece.get("id") or call["id"]
                        function = piece.get("function") or {}
                        call["function"]["name"] += function.get("name") or ""
                        call["function"]["arguments"] += function.get("arguments") or ""
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise LLMError(f"{self.model} stopped answering: {error}") from error

        message = {"role": "assistant", "content": "".join(text) or None}
        if calls:
            message["tool_calls"] = [calls[index] for index in sorted(calls)]
        return message

    def _body(self, messages, tools, tool_choice, max_tokens, temperature) -> dict:
        if not self.enabled:
            raise LLMError("no LLM_API_KEY is set")
        if self.budget:
            self.budget.take()
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if tools:
            body["tools"] = tools
        if tools and tool_choice:
            body["tool_choice"] = tool_choice
        return body

    def _open(self, body: dict):
        return post(self.base_url, "/chat/completions", self.api_key, body)


def post(base_url: str, path: str, api_key: str | None, body: dict):
    """One JSON POST in the OpenAI format. The response is open, for the caller to read or stream."""
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)
    except urllib.error.HTTPError as error:
        # The provider's own message says what is wrong: a bad key, no balance, an unknown model.
        detail = error.read().decode(errors="replace")[:300]
        raise LLMError(f"{body.get('model')} answered HTTP {error.code}: {detail}") from error
