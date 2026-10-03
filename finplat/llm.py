"""One call to a chat model, over plain HTTP.

DeepSeek, OpenAI, OpenRouter and most others accept the OpenAI chat format, so one request shape
covers them all and the provider is a setting. No SDK: the request is one JSON POST, and an SDK
for each provider would add a dependency to save about 20 lines.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

TIMEOUT_SECONDS = 60


class LLMError(RuntimeError):
    """The model did not answer, or answered with something unusable."""


@dataclass
class LLM:
    base_url: str
    model: str
    api_key: str | None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        json_output: bool = False,
        max_tokens: int = 800,
        temperature: float = 0.2,
    ) -> dict:
        """The model's reply message: `content`, and `tool_calls` when it wants a tool."""
        if not self.enabled:
            raise LLMError("no LLM_API_KEY is set")

        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if tools:
            body["tools"] = tools
        if json_output:
            body["response_format"] = {"type": "json_object"}

        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                answer = json.loads(response.read())
        except urllib.error.HTTPError as error:
            # The provider's own message says what is wrong: a bad key, no balance, an unknown model.
            detail = error.read().decode(errors="replace")[:300]
            raise LLMError(f"{self.model} answered HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise LLMError(f"{self.model} did not answer: {error}") from error

        try:
            return answer["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise LLMError(f"{self.model} answered without a message: {str(answer)[:300]}") from error
