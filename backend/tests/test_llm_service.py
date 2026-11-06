"""LLM factory and rate-limit failover, with Groq's API replaced by a fake client."""

from types import SimpleNamespace

import httpx
import pytest
from openai import AuthenticationError, RateLimitError
from pipecat.processors.aggregators.llm_context import LLMContext

from app.config import Settings
from app.services import ServiceNotConfiguredError
from app.services.llm import create_llm_service


def _status_error(cls, status: int):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return cls("error", response=httpx.Response(status, request=request), body=None)


class FakeCompletions:
    """Raises the scripted error for each listed model, else returns a stream marker."""

    def __init__(self, failures: dict[str, Exception]) -> None:
        self.failures = failures
        self.models: list[str] = []

    async def create(self, **params):
        self.models.append(params["model"])
        if params["model"] in self.failures:
            raise self.failures[params["model"]]
        return f"stream-from-{params['model']}"


def _service(failures: dict[str, Exception], fallbacks: str = "qwen/qwen3.8-27b"):
    settings = Settings(_env_file=None, groq_api_key="test-key", groq_fallback_models=fallbacks)
    events: list[tuple[str, str]] = []
    llm = create_llm_service(
        settings,
        system_instruction="You are a receptionist.",
        on_failover=lambda a, b, _e: events.append((a, b)),
    )
    completions = FakeCompletions(failures)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm, completions, events


def _context() -> LLMContext:
    return LLMContext(messages=[{"role": "user", "content": "Hi"}])


async def test_uses_main_model_when_available() -> None:
    llm, completions, events = _service({})
    assert await llm.get_chat_completions(_context()) == "stream-from-openai/gpt-oss-120b"
    assert completions.models == ["openai/gpt-oss-120b"] and events == []


async def test_rate_limited_request_fails_over_to_the_next_free_model() -> None:
    llm, completions, events = _service({"openai/gpt-oss-120b": _status_error(RateLimitError, 429)})
    assert await llm.get_chat_completions(_context()) == "stream-from-qwen/qwen3.8-27b"
    assert completions.models == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    assert events == [("openai/gpt-oss-120b", "qwen/qwen3.8-27b")]


async def test_error_is_raised_when_every_model_is_rate_limited() -> None:
    limit = _status_error(RateLimitError, 429)
    llm, _, _ = _service({"openai/gpt-oss-120b": limit, "qwen/qwen3.8-27b": limit})
    with pytest.raises(RateLimitError):
        await llm.get_chat_completions(_context())


async def test_rejected_key_is_not_retried_on_other_models() -> None:
    llm, completions, _ = _service({"openai/gpt-oss-120b": _status_error(AuthenticationError, 401)})
    with pytest.raises(AuthenticationError):
        await llm.get_chat_completions(_context())
    assert completions.models == ["openai/gpt-oss-120b"]


def test_main_model_is_not_repeated_as_its_own_fallback() -> None:
    llm, _, _ = _service({}, fallbacks="openai/gpt-oss-120b, qwen/qwen3.8-27b")
    assert llm._fallback_models == ["qwen/qwen3.8-27b"]


def test_factory_requires_a_key() -> None:
    with pytest.raises(ServiceNotConfiguredError):
        create_llm_service(Settings(_env_file=None), system_instruction="x")
