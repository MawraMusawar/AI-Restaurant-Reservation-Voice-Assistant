"""Text-only conversations with the real agent (real Groq, real prompt and tools).

Runs the same Pipecat pieces as the voice pipeline (user aggregator, Groq LLM
with tool calling, assistant aggregator), with the caller's turns injected as
text. This tests the conversation logic without spending speech quota.

A reply is complete when an LLM response ends without requesting a tool call.
A response that calls tools is always followed by another one with the results.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMRunFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.errors import ErrorCategory
from pipecat.workers.runner import WorkerRunner

from app.agent.call_control import end_call_tool
from app.agent.prompt import build_greeting, build_system_prompt
from app.agent.reservations import ReservationBackend
from app.agent.restaurant import DEMO_RESTAURANT, RestaurantProfile
from app.agent.tools import ReservationTools
from app.config import Settings
from app.services.llm import create_llm_service

RATE_LIMIT_WAIT_SECS = 20.0
MAX_RATE_LIMIT_RETRIES = 4


@dataclass
class ToolCall:
    name: str
    result: dict[str, Any]


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    latency_ms: float = 0.0
    llm_requests: int = 0


class _ResponseWatcher(FrameProcessor):
    """Signals when an LLM response ends without asking for tools."""

    def __init__(self) -> None:
        super().__init__()
        self.done = asyncio.Event()
        self.requests = 0
        self._called_tools = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self.requests += 1
            self._called_tools = False
        elif isinstance(frame, FunctionCallsStartedFrame):
            self._called_tools = True
        elif isinstance(frame, LLMFullResponseEndFrame) and not self._called_tools:
            self.done.set()
        await self.push_frame(frame, direction)


class TextConversation:
    def __init__(
        self,
        settings: Settings,
        backend: ReservationBackend,
        now: datetime,
        profile: RestaurantProfile = DEMO_RESTAURANT,
    ) -> None:
        self.tool_calls: list[ToolCall] = []
        self.failovers: list[str] = []
        self.ended = False  # the assistant called end_call
        self._texts: list[str] = []
        self._rate_limited = False
        tools = ReservationTools(profile, backend, observer=self._on_tool, clock=lambda: now)
        self.context = LLMContext(
            messages=[{"role": "assistant", "content": build_greeting(profile)}],
            tools=tools.schema(extra=[end_call_tool(self._on_end_call)]),
        )
        llm = create_llm_service(
            settings,
            system_instruction=build_system_prompt(profile, now),
            on_failover=lambda a, b, _e: self.failovers.append(f"{a}->{b}"),
        )
        user_agg, assistant_agg = LLMContextAggregatorPair(self.context)
        self._watcher = _ResponseWatcher()
        self._worker = PipelineWorker(
            Pipeline([user_agg, llm, self._watcher, assistant_agg]),
            params=PipelineParams(enable_metrics=True),
            enable_rtvi=False,
            idle_timeout_secs=None,
        )
        self._runner = WorkerRunner(handle_sigint=False)
        self._task: asyncio.Task[None] | None = None

        @assistant_agg.event_handler("on_assistant_turn_stopped")
        async def on_turn(_agg, message: AssistantTurnStoppedMessage) -> None:
            if message.content.strip():
                self._texts.append(message.content.strip())

        @self._worker.event_handler("on_pipeline_error")
        async def on_error(_worker, frame: ErrorFrame) -> None:
            logger.warning("LLM harness error: {}", frame.error)
            if frame.category == ErrorCategory.RATE_LIMIT:
                self._rate_limited = True
                self._watcher.done.set()

    async def _on_end_call(self) -> None:
        # end_call returns without another LLM run, so this reply is complete.
        self.ended = True
        self.tool_calls.append(ToolCall("end_call", {"ok": True}))
        self._watcher.done.set()

    def _on_tool(self, name: str, result: dict[str, Any], _ms: float) -> None:
        self.tool_calls.append(ToolCall(name, result))

    async def __aenter__(self) -> "TextConversation":
        await self._runner.add_workers(self._worker)
        self._task = asyncio.create_task(self._runner.run())
        await asyncio.sleep(0.2)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._runner.cancel()
        if self._task:
            await asyncio.wait_for(self._task, timeout=10)

    async def say(self, text: str, timeout_secs: float = 45.0) -> Reply:
        """Send one caller turn; return everything the assistant said in reply."""
        first_tool = len(self.tool_calls)
        self._texts.clear()
        started = time.perf_counter()
        frame: Frame = LLMMessagesAppendFrame(
            messages=[{"role": "user", "content": text}], run_llm=True
        )
        for _attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            self._rate_limited = False
            self._watcher.done.clear()
            self._watcher.requests = 0
            await self._worker.queue_frame(frame)
            await asyncio.wait_for(self._watcher.done.wait(), timeout=timeout_secs)
            if not self._rate_limited:
                break
            # Every free model is rate-limited: wait for the per-minute budget, retry.
            logger.warning("All Groq models rate-limited; waiting {}s", RATE_LIMIT_WAIT_SECS)
            await asyncio.sleep(RATE_LIMIT_WAIT_SECS)
            frame = LLMRunFrame()
            started = time.perf_counter()
        else:
            raise AssertionError("Groq stayed rate-limited")
        # Let the assistant aggregator record the final turn.
        await asyncio.sleep(0.1)
        return Reply(
            text=" | ".join(self._texts),
            tool_calls=self.tool_calls[first_tool:],
            latency_ms=round((time.perf_counter() - started) * 1000),
            llm_requests=self._watcher.requests,
        )
