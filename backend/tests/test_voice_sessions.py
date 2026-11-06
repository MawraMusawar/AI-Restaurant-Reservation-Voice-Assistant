import asyncio
from dataclasses import dataclass

import pytest

from app.voice.sessions import SessionLimitError, VoiceSession, VoiceSessionManager


@dataclass
class FakeConnection:
    pc_id: str


class ControllableBot:
    """A bot runner that marks the session active and waits until released."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.release = asyncio.Event()
        self.fail_with = fail_with

    async def __call__(self, connection: FakeConnection, session: VoiceSession) -> None:
        session.mark_active()
        await self.release.wait()
        if self.fail_with:
            raise self.fail_with


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_session_lifecycle_connecting_active_ended() -> None:
    bot = ControllableBot()
    manager = VoiceSessionManager(bot_runner=bot, max_concurrent=2)

    session = manager.start(FakeConnection("pc-1"))
    assert session.status == "connecting"
    await _settle()
    assert session.status == "active"
    assert manager.is_active("pc-1")

    bot.release.set()
    await _settle()
    assert session.status == "ended"
    assert session.ended_at is not None
    assert not manager.is_active("pc-1")
    assert manager.list_sessions() == [session]


async def test_concurrency_limit_is_enforced() -> None:
    bot = ControllableBot()
    manager = VoiceSessionManager(bot_runner=bot, max_concurrent=1)
    manager.start(FakeConnection("pc-1"))
    assert not manager.has_capacity()
    with pytest.raises(SessionLimitError):
        manager.start(FakeConnection("pc-2"))
    bot.release.set()
    await _settle()
    assert manager.has_capacity()


async def test_bot_failure_marks_session_as_error() -> None:
    bot = ControllableBot(fail_with=RuntimeError("audio device exploded"))
    manager = VoiceSessionManager(bot_runner=bot, max_concurrent=2)
    session = manager.start(FakeConnection("pc-1"))
    bot.release.set()
    await _settle()
    assert session.status == "error"
    assert session.error == "RuntimeError: audio device exploded"
    assert manager.active_count == 0


async def test_fatal_service_error_marks_session_as_error() -> None:
    async def deaf_bot(connection: FakeConnection, session: VoiceSession) -> None:
        session.mark_active()
        session.record_error("DeepgramSTTService#0: connection error: timeout", fatal=False)
        session.record_error("DeepgramSTTService#0: Deepgram rejected (status 401)", fatal=True)
        # The pipeline then ends on its own (ProcessorUnusablePolicy.END).

    manager = VoiceSessionManager(bot_runner=deaf_bot, max_concurrent=1)
    session = manager.start(FakeConnection("pc-1"))
    await _settle()
    assert session.status == "error"
    assert session.error == "DeepgramSTTService#0: Deepgram rejected (status 401)"
    assert len(session.errors) == 2


async def test_recovered_error_does_not_fail_the_session() -> None:
    async def flaky_bot(connection: FakeConnection, session: VoiceSession) -> None:
        session.mark_active()
        session.record_error("DeepgramSTTService#0: connection error, retrying", fatal=False)

    manager = VoiceSessionManager(bot_runner=flaky_bot, max_concurrent=1)
    session = manager.start(FakeConnection("pc-1"))
    await _settle()
    assert session.status == "ended"
    assert session.error is None
    assert session.errors == ["DeepgramSTTService#0: connection error, retrying"]


async def test_session_that_never_connects_times_out() -> None:
    async def never_connects(connection: FakeConnection, session: VoiceSession) -> None:
        await asyncio.Event().wait()  # client never shows up

    manager = VoiceSessionManager(
        bot_runner=never_connects, max_concurrent=1, connect_timeout_secs=0.05
    )
    session = manager.start(FakeConnection("pc-1"))
    await asyncio.sleep(0.2)
    assert session.status == "error"
    assert session.error is not None and "did not connect" in session.error
    assert manager.has_capacity()  # the slot is released


async def test_connected_session_is_not_timed_out() -> None:
    bot = ControllableBot()
    manager = VoiceSessionManager(bot_runner=bot, max_concurrent=1, connect_timeout_secs=0.05)
    session = manager.start(FakeConnection("pc-1"))
    await asyncio.sleep(0.2)
    assert session.status == "active"
    bot.release.set()
    await _settle()
    assert session.status == "ended"


async def test_shutdown_cancels_running_sessions() -> None:
    manager = VoiceSessionManager(bot_runner=ControllableBot(), max_concurrent=2)
    s1 = manager.start(FakeConnection("pc-1"))
    s2 = manager.start(FakeConnection("pc-2"))
    await _settle()
    await manager.shutdown()
    assert manager.active_count == 0
    assert {s1.status, s2.status} == {"ended"}
