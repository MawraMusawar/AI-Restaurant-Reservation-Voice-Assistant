"""Call control: ending the call, silence check-ins, end reasons."""

import asyncio
from types import SimpleNamespace

from app.agent.call_control import SILENCE_PROMPTS, SilenceTracker, end_call_tool
from app.agent.reservations import OfflineReservationBackend
from app.agent.restaurant import DEMO_RESTAURANT
from app.agent.tools import ReservationTools
from app.voice.sessions import VoiceSession, VoiceSessionManager


def test_silence_tracker_checks_in_then_hangs_up() -> None:
    tracker = SilenceTracker()
    assert tracker.next_prompt() == (SILENCE_PROMPTS[0], False)  # "Are you still there?"
    text, hang_up = tracker.next_prompt()  # type: ignore[misc]
    assert hang_up is True and "Goodbye" in text
    assert tracker.next_prompt() is None  # nothing more once the goodbye is queued


def test_caller_speaking_resets_the_silence_count() -> None:
    tracker = SilenceTracker()
    tracker.next_prompt()
    tracker.reset()  # the caller answered "yes, I'm here"
    assert tracker.next_prompt() == (SILENCE_PROMPTS[0], False)


async def test_end_call_tool_hangs_up_without_another_llm_run() -> None:
    ended: list[bool] = []

    async def on_end_call() -> None:
        ended.append(True)

    results: list[tuple[dict, object]] = []

    async def result_callback(result: dict, *, properties=None) -> None:
        results.append((result, properties))

    tool = end_call_tool(on_end_call)
    await tool.handler(SimpleNamespace(result_callback=result_callback))
    [(result, properties)] = results
    assert result["ok"] is True
    assert properties.run_llm is False  # the goodbye was already said
    assert ended == [True]


def test_end_call_is_offered_alongside_the_reservation_tools() -> None:
    async def noop() -> None: ...

    tools = ReservationTools(DEMO_RESTAURANT, OfflineReservationBackend())
    names = [t.name for t in tools.schema(extra=[end_call_tool(noop)]).standard_tools]
    assert names[-1] == "end_call" and "create_reservation" in names


class _Conn:
    def __init__(self, pc_id: str) -> None:
        self.pc_id = pc_id


async def _finished(manager: VoiceSessionManager) -> VoiceSession:
    for _ in range(100):
        if not manager.active_count:
            break
        await asyncio.sleep(0.01)
    return manager.list_sessions()[0]


async def test_end_reason_defaults_to_caller_hung_up() -> None:
    async def bot(_conn, session: VoiceSession) -> None:
        session.mark_active()

    manager = VoiceSessionManager(bot_runner=bot, max_concurrent=1)
    manager.start(_Conn("a"))
    session = await _finished(manager)
    assert (session.status, session.end_reason) == ("ended", "caller_hung_up")


async def test_end_reason_set_by_the_pipeline_is_kept() -> None:
    async def bot(_conn, session: VoiceSession) -> None:
        session.mark_active()
        session.end_reason = "assistant_ended"

    manager = VoiceSessionManager(bot_runner=bot, max_concurrent=1)
    manager.start(_Conn("b"))
    assert (await _finished(manager)).end_reason == "assistant_ended"


async def test_fatal_error_is_the_end_reason() -> None:
    async def bot(_conn, session: VoiceSession) -> None:
        session.mark_active()
        session.record_error("DeepgramSTTService: 401", fatal=True)
        session.end_reason = "caller_hung_up"  # the disconnect that followed

    manager = VoiceSessionManager(bot_runner=bot, max_concurrent=1)
    manager.start(_Conn("c"))
    session = await _finished(manager)
    assert (session.status, session.end_reason) == ("error", "error")


def test_hanging_up_without_a_goodbye_adds_one() -> None:
    from pipecat.frames.frames import EndWorkerFrame, TTSSpeakFrame

    from app.agent.call_control import FALLBACK_GOODBYE, closing_frames

    silent = closing_frames(said_goodbye=False)
    assert isinstance(silent[0], TTSSpeakFrame) and silent[0].text == FALLBACK_GOODBYE
    assert isinstance(silent[-1], EndWorkerFrame)
    assert [type(f) for f in closing_frames(said_goodbye=True)] == [EndWorkerFrame]
