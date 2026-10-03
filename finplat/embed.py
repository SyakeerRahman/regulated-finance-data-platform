"""Text to vectors, for the case search.

The same plain POST as the chat model, to `/embeddings` in the OpenAI format. OpenRouter, OpenAI
and Alibaba's Qwen all accept it, so the provider is a setting here too. DeepSeek has no
embeddings, which is why the base URL can differ from the chat model's.
"""

import json
import urllib.error
from dataclasses import dataclass

from finplat.llm import Budget, LLMError, post


@dataclass
class Embedder:
    base_url: str
    model: str
    dim: int
    api_key: str | None
    budget: Budget | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector for each text, in the same order. One request, so one call from the budget."""
        if not self.enabled:
            raise LLMError("no LLM_API_KEY is set")
        if not texts:
            return []
        if self.budget:
            self.budget.take()
        try:
            with post(self.base_url, "/embeddings", self.api_key, {"model": self.model, "input": texts}) as response:
                answer = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise LLMError(f"{self.model} did not answer: {error}") from error

        try:
            # The order is the request's only when sorted by index. The spec does not promise it.
            vectors = [row["embedding"] for row in sorted(answer["data"], key=lambda row: row["index"])]
        except (KeyError, TypeError) as error:
            raise LLMError(f"{self.model} answered without vectors: {str(answer)[:300]}") from error
        if len(vectors) != len(texts):
            raise LLMError(f"{self.model} returned {len(vectors)} vectors for {len(texts)} texts")
        for vector in vectors:
            # A vector column has one length. A model that changed, or a wrong EMBED_DIM, must stop
            # here and not at the insert, where the cause is harder to see.
            if len(vector) != self.dim:
                raise LLMError(f"{self.model} returned {len(vector)} numbers, and EMBED_DIM is {self.dim}")
        return vectors
