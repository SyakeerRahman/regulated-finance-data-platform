"""Text to vectors. No test calls a real provider: the HTTP call is replaced by a scripted answer."""

import io
import json
from datetime import date

import pytest
from pydantic import ValidationError

from finplat import llm as llm_module
from finplat.embed import Embedder
from finplat.llm import Budget, BudgetExceeded, LLMError
from finplat.settings import Settings


class Provider:
    """Stands in for urlopen. Keeps each request and answers with the next scripted body."""

    def __init__(self, *answers: dict) -> None:
        self.answers = list(answers)
        self.requests: list = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps(self.answers.pop(0)).encode())


def vectors(*rows: tuple[int, list[float]]) -> dict:
    return {"data": [{"index": index, "embedding": vector} for index, vector in rows]}


@pytest.fixture
def provider(monkeypatch):
    def install(*answers: dict) -> Provider:
        fake = Provider(*answers)
        monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake)
        return fake

    return install


def embedder(dim: int = 3, key: str | None = "key", budget: Budget | None = None) -> Embedder:
    return Embedder("https://openrouter.ai/api/v1/", "openai/text-embedding-3-small", dim, key, budget)


def test_each_text_gets_its_own_vector_in_the_order_sent(provider):
    # The provider may answer out of order. Only the index says which vector is which.
    fake = provider(vectors((1, [0.0, 1.0, 0.0]), (0, [1.0, 0.0, 0.0])))

    assert embedder().embed(["first", "second"]) == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]

    request = fake.requests[0]
    assert request.full_url == "https://openrouter.ai/api/v1/embeddings"
    assert request.get_header("Authorization") == "Bearer key"
    assert json.loads(request.data) == {"model": "openai/text-embedding-3-small", "input": ["first", "second"]}


def test_a_vector_of_the_wrong_length_stops_before_it_reaches_the_table(provider):
    provider(vectors((0, [1.0, 0.0])))

    with pytest.raises(LLMError, match="returned 2 numbers, and EMBED_DIM is 3"):
        embedder(dim=3).embed(["text"])


def test_a_missing_vector_is_an_error_not_a_shorter_list(provider):
    provider(vectors((0, [1.0, 0.0, 0.0])))

    with pytest.raises(LLMError, match="returned 1 vectors for 2 texts"):
        embedder().embed(["one", "two"])


def test_an_answer_without_vectors_is_an_error(provider):
    provider({"error": {"message": "No endpoints found"}})

    with pytest.raises(LLMError, match="answered without vectors"):
        embedder().embed(["text"])


def test_with_no_key_nothing_is_sent(provider):
    fake = provider()
    off = embedder(key=None)

    assert off.enabled is False
    with pytest.raises(LLMError, match="no LLM_API_KEY"):
        off.embed(["text"])
    assert fake.requests == []


def test_each_request_spends_one_call_from_the_shared_budget(provider):
    fake = provider(vectors((0, [1.0, 0.0, 0.0]), (1, [0.0, 1.0, 0.0])))
    budget = Budget(per_day=1, today=lambda: date(2026, 10, 4))
    paid = embedder(budget=budget)

    # Two texts, one request, one call.
    paid.embed(["one", "two"])
    assert budget.snapshot() == {"used": 1, "per_day": 1}

    with pytest.raises(BudgetExceeded):
        paid.embed(["three"])
    assert len(fake.requests) == 1


def test_no_text_costs_nothing(provider):
    fake = provider()
    budget = Budget(per_day=1, today=lambda: date(2026, 10, 4))

    assert embedder(budget=budget).embed([]) == []
    assert fake.requests == []
    assert budget.snapshot()["used"] == 0


def test_a_dimension_over_the_index_limit_stops_startup():
    # pgvector cannot build an HNSW index on a vector longer than 2000.
    with pytest.raises(ValidationError, match="embed_dim"):
        Settings(lake_uri="data/lake", embed_dim=2560)
