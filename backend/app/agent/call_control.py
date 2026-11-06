"""Call control: ending the call, silence prompts, and spoken fallbacks.

The fixed phrases here are fallbacks spoken without an LLM request, so they
work even when Groq is rate-limited and cost no LLM quota. The conversation
itself stays dynamic (see `prompt.py`). They are added to the context like any
other speech, so the call log shows them and the LLM knows they were said.
"""

from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import (
    EndWorkerFrame,
    Frame,
    FunctionCallResultProperties,
    TTSSpeakFrame,
)
from pipecat.services.llm_service import FunctionCallParams

# One phrase per unanswered silence; after the last one the call ends.
SILENCE_PROMPTS = (
    "Are you still there?",
    "I haven't heard anything for a while, so I'll end the call now. "
    "Please call back any time. Goodbye!",
)

# Spoken when the LLM could not answer a turn (e.g. every free model rate-limited).
LLM_TROUBLE_REPLY = "Sorry, I'm having a little trouble on my end. Could you say that again?"

# Spoken if the assistant hangs up without having said anything in that reply
# (seen in testing with the fallback model), so the line never just goes dead.
FALLBACK_GOODBYE = "Thank you for calling. Goodbye!"


def closing_frames(said_goodbye: bool) -> list[Frame]:
    """What to push when the assistant hangs up: a goodbye if needed, then the end."""
    frames: list[Frame] = []
    if not said_goodbye:
        frames.append(TTSSpeakFrame(FALLBACK_GOODBYE))
    frames.append(EndWorkerFrame())  # after everything queued ahead of it has played
    return frames


EndCall = Callable[[], Awaitable[None]]


def end_call_tool(on_end_call: EndCall) -> FunctionSchema:
    """`end_call`: lets the assistant hang up once the conversation is over."""

    async def handler(params: FunctionCallParams) -> None:
        logger.info("Assistant ended the call")
        # No further LLM run: the goodbye has already been said.
        await params.result_callback(
            {"ok": True, "status": "ending"},
            properties=FunctionCallResultProperties(run_llm=False),
        )
        await on_end_call()

    return FunctionSchema(
        name="end_call",
        description=(
            "Hang up. Only after the caller says goodbye or that they need nothing else, "
            "and after you have said goodbye."
        ),
        properties={},
        required=[],
        handler=handler,
    )


class SilenceTracker:
    """Counts unanswered silences; any caller speech resets it."""

    def __init__(self, prompts: tuple[str, ...] = SILENCE_PROMPTS) -> None:
        self._prompts = prompts
        self._count = 0

    def reset(self) -> None:
        self._count = 0

    def next_prompt(self) -> tuple[str, bool] | None:
        """The next thing to say, and whether to hang up after it. None once exhausted."""
        if self._count >= len(self._prompts):
            return None
        prompt = self._prompts[self._count]
        self._count += 1
        return prompt, self._count >= len(self._prompts)
