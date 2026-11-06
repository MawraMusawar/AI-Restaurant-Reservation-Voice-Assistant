"""LLM service factory (Groq, OpenAI-compatible streaming with tool calling).

Free-tier limits (response headers and Groq docs, 2026-11-06): each chat model
has its own budget of about 8,000 tokens/minute (qwen3.8-27b: 7,000 input
tokens/minute), 200K tokens/day and 1,000 requests/day. A request sends the
system prompt, tools and history (about 2-2.5K tokens), so one model serves
only 3-4 requests a minute, and a brisk call can exceed that.
`FailoverGroqLLMService` then retries the same request on the next free model
instead of leaving the caller in silence. It never falls back
to a paid service, and every failover is logged.
"""

from collections.abc import Callable

from loguru import logger
from openai import APIConnectionError, AsyncStream, InternalServerError, RateLimitError
from openai.types.chat import ChatCompletionChunk
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.groq.llm import GroqLLMService
from pipecat.utils.types import assert_given

from app.config import Settings
from app.services import ServiceNotConfiguredError, has_secret

# Errors where another model may still answer: rate/quota limits on one model,
# or a transient provider failure. Everything else (bad key, bad request) is raised.
FAILOVER_ERRORS = (RateLimitError, InternalServerError, APIConnectionError)

# Includes gpt-oss reasoning tokens; replies are one or two spoken sentences.
MAX_COMPLETION_TOKENS = 256

FailoverCallback = Callable[[str, str, Exception], None]


class FailoverGroqLLMService(GroqLLMService):
    """`GroqLLMService` that retries a rate-limited request on fallback models.

    The OpenAI client's own retries are disabled: they wait out `retry-after`,
    which can be many seconds, while the caller hears nothing. Failing over to
    another model's separate free budget answers in under a second instead.
    """

    def __init__(
        self,
        *,
        fallback_models: list[str] | None = None,
        on_failover: FailoverCallback | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._fallback_models = fallback_models or []
        self._on_failover = on_failover

    def create_client(self, api_key=None, base_url=None, **kwargs):
        return super().create_client(api_key, base_url, **kwargs).with_options(max_retries=0)

    async def get_chat_completions(self, context: LLMContext) -> AsyncStream[ChatCompletionChunk]:
        adapter = self.get_llm_adapter()
        self._log_llm_response(context)
        params_from_context = await adapter.get_llm_invocation_params(
            context,
            system_instruction=assert_given(self._settings.system_instruction),
            convert_developer_to_user=not self.supports_developer_role,
        )
        params = self.build_chat_completion_params(params_from_context)

        models = [params["model"], *self._fallback_models]
        for index, model in enumerate(models):
            try:
                return await self._client.chat.completions.create(**{**params, "model": model})
            except FAILOVER_ERRORS as exc:
                if index == len(models) - 1:
                    raise
                next_model = models[index + 1]
                logger.warning(
                    "LLM {} unavailable ({}); retrying on {}",
                    model,
                    type(exc).__name__,
                    next_model,
                )
                if self._on_failover:
                    self._on_failover(model, next_model, exc)
        raise AssertionError("unreachable")  # pragma: no cover


def create_llm_service(
    settings: Settings,
    system_instruction: str,
    on_failover: FailoverCallback | None = None,
) -> FailoverGroqLLMService:
    """Build the conversation LLM. Swap this factory to change provider."""
    if not has_secret(settings.groq_api_key):
        raise ServiceNotConfiguredError("GROQ_API_KEY is not set")
    assert settings.groq_api_key is not None
    return FailoverGroqLLMService(
        api_key=settings.groq_api_key.get_secret_value(),
        settings=GroqLLMService.Settings(
            model=settings.groq_model,
            system_instruction=system_instruction,
            reasoning_effort=settings.groq_reasoning_effort,
            temperature=0.3,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
        ),
        fallback_models=[m for m in settings.groq_fallback_model_list if m != settings.groq_model],
        on_failover=on_failover,
        # A tool that hangs must not freeze the call.
        function_call_timeout_secs=10.0,
    )
