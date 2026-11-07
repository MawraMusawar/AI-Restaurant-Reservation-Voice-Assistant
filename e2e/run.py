"""Run the browser end-to-end scenarios against throwaway data and test servers.

    cd e2e
    npm install                                   # once: playwright-core
    powershell -ExecutionPolicy Bypass -File make_audio.ps1   # once: caller audio
    ..\\backend\\.venv\\Scripts\\python.exe run.py all       # or: admin | voice

For each run this:
1. creates an `e2e-*` restaurant in the database (DATABASE_URL from backend/.env),
   so the demo restaurant is never touched;
2. builds the frontend into `.next-e2e` (pointing at the test backend) unless
   --no-build, and starts the backend on :8010 and the frontend on :3010;
3. runs the Chrome scenarios, then checks the database;
4. always stops both servers and deletes the `e2e-*` data.

The voice scenario uses the real Deepgram, Groq and ElevenLabs free quotas
(about 1 minute of audio, ~10 LLM requests, ~600 TTS characters).
"""

import argparse
import asyncio
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
E2E = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # backend/.env

import httpx  # noqa: E402
import psutil  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import create_database  # noqa: E402
from app.db.sessions import save_session  # noqa: E402
from app.voice.sessions import VoiceSession  # noqa: E402

PASSWORD = "e2e-admin-pass"
SLUG = "e2e-" + secrets.token_hex(3)
BACKEND_PORT, FRONTEND_PORT = 8010, 3010
CHROME = os.environ.get(
    "CHROME_PATH", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)
NODE = shutil.which("node") or r"D:\node.exe"


async def setup() -> None:
    db = await create_database(get_settings())
    async with db.pool.acquire() as conn, conn.transaction():
        rid = await conn.fetchval(
            """insert into restaurants (slug, name, timezone, booking_reference_prefix,
                   min_notice_minutes) values ($1, 'E2E Bistro', 'Asia/Karachi', 'EE', 0)
               returning id""",
            SLUG,
        )
        for weekday in range(7):
            await conn.execute(
                "insert into restaurant_hours (restaurant_id, weekday, opens_at, closes_at) "
                "values ($1, $2, '12:00', '23:00')",
                rid,
                weekday,
            )
        await conn.execute(
            "insert into restaurant_tables (restaurant_id, label, seats, area) values "
            "($1, 'T2', 2, 'window'), ($1, 'T4', 4, 'main')",
            rid,
        )
    # A stored call for the call-log screen.
    session = VoiceSession(session_id=f"e2e-call-{SLUG}")
    session.mark_active()
    session.add_transcript("assistant", "Thank you for calling E2E Bistro, this is Sara.")
    session.add_transcript("user", "Do you have a table for two tomorrow evening?")
    session.add_transcript("assistant", "Yes, we have 7 PM available. May I have your name?")
    session.status, session.end_reason = "ended", "caller_hung_up"
    session.ended_at = datetime.now(UTC)
    await save_session(db.pool, session, str(rid))
    await db.close()


async def voice_booking_in_database() -> list[tuple[str, bool, str]]:
    """The spoken booking must exist, confirmed, and be linked to its call log."""
    db = await create_database(get_settings())
    # The call log is written once the call has fully ended: after the goodbye has
    # played and the pipeline has shut down, a few seconds after the browser saw it.
    deadline = time.time() + 30
    async with db.pool.acquire() as conn:
        while time.time() < deadline and not await conn.fetchval(
            """select count(*) from voice_sessions s join restaurants x
               on x.id = s.restaurant_id where x.slug = $1 and s.id not like 'e2e-call-%'""",
            SLUG,
        ):
            await asyncio.sleep(1)
        rows = await conn.fetch(
            """select r.booking_reference, r.status, r.party_size, c.name, s.end_reason
               from reservations r
               join restaurants x on x.id = r.restaurant_id
               join customers c on c.id = r.customer_id
               left join voice_sessions s on s.id = r.voice_session_id
               where x.slug = $1 and r.source = 'voice'""",
            SLUG,
        )
        metrics = await conn.fetchval(
            """select s.metrics from voice_sessions s join restaurants x
               on x.id = s.restaurant_id where x.slug = $1 and s.id not like 'e2e-call-%'""",
            SLUG,
        )
    await db.close()
    if metrics:
        print_latency_report(metrics)
    checks = [("exactly one booking made by voice", len(rows) == 1, f"{len(rows)} found")]
    if rows:
        r = rows[0]
        checks += [
            ("booking confirmed in the database", r["status"] == "confirmed", r["booking_reference"]),
            ("party size and name stored", r["party_size"] == 2 and bool(r["name"]), f"{r['party_size']} / {r['name']}"),
            ("call log saved and linked", r["end_reason"] == "assistant_ended", str(r["end_reason"])),
        ]
    return checks


def print_latency_report(m: dict) -> None:
    """Per-stage timings of the spoken call, from its call log (milliseconds)."""

    def fmt(key: str) -> str:
        values = m.get(key) or []
        if not values:
            return "-"
        return f"median {sorted(values)[len(values) // 2]:.0f} (range {min(values):.0f}-{max(values):.0f}, n={len(values)})"

    print("\nLATENCY (ms) per stage, this call:")
    print("  startup (offer -> live)        ", m.get("startup_ms"))
    print("  STT: VAD stop -> final text    ", fmt("stt_latencies_ms"))
    print("  turn detection: VAD stop -> end", fmt("turn_latencies_ms"))
    print("  LLM: turn end -> first text    ", fmt("response_latencies_ms"))
    print("  heard: turn end -> first audio ", fmt("voice_latencies_ms"))
    print("  audio health: dropped", m.get("dropped_audio_secs"), "s, max loop lag",
          m.get("max_loop_lag_ms"), "ms, stalls", m.get("loop_stalls"))


async def teardown() -> None:
    db = await create_database(get_settings())
    async with db.pool.acquire() as conn:
        # Also removes leftovers of earlier interrupted runs.
        await conn.execute(
            """delete from voice_sessions where id like 'e2e-call-%'
               or restaurant_id in (select id from restaurants where slug like 'e2e-%')"""
        )
        await conn.execute("delete from restaurants where slug like 'e2e-%'")
    await db.close()
    print(f"cleanup: {SLUG} and its data deleted")


def wait_http(url: str, secs: float = 120) -> None:
    deadline = time.time() + secs
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=3).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"{url} did not come up")


class ResourceSampler:
    """Samples the backend process (and its children) every 0.5 s: CPU and memory."""

    def __init__(self, pid: int) -> None:
        import threading

        self.proc = psutil.Process(pid)
        self.samples: list[tuple[float, float]] = []  # (cpu % of one core, RSS MB)
        self._known: dict[int, psutil.Process] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _procs(self) -> list[psutil.Process]:
        """The same Process objects each time: cpu_percent() measures since the last call
        on that object (a fresh object always reports 0)."""
        try:
            current = [self.proc, *self.proc.children(recursive=True)]
        except psutil.Error:
            return list(self._known.values())
        for p in current:
            if p.pid not in self._known:
                self._known[p.pid] = p
                p.cpu_percent(None)  # prime
        return [self._known[p.pid] for p in current]

    def _run(self) -> None:
        self._procs()
        while not self._stop.wait(0.5):
            cpu = rss = 0.0
            for p in self._procs():
                try:
                    cpu += p.cpu_percent(None)
                    rss += p.memory_info().rss / 2**20
                except psutil.Error:
                    pass
            self.samples.append((cpu, rss))

    def start(self) -> "ResourceSampler":
        self._thread.start()
        return self

    def report(self, label: str) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        if not self.samples:
            return
        cpus = [c for c, _ in self.samples]
        mems = [m for _, m in self.samples]
        cores = psutil.cpu_count() or 1
        print(f"\nRESOURCES backend during {label} ({len(self.samples)} samples, {cores} cores):")
        print(f"  CPU % of one core: avg {sum(cpus) / len(cpus):.0f}, peak {max(cpus):.0f}")
        print(f"  memory RSS MB: start {mems[0]:.0f}, peak {max(mems):.0f}, end {mems[-1]:.0f}")


def build_frontend() -> None:
    env = {**os.environ, "BACKEND_URL": f"http://127.0.0.1:{BACKEND_PORT}", "NEXT_DIST_DIR": ".next-e2e"}
    subprocess.run([NODE, "node_modules/next/dist/bin/next", "build"], cwd=FRONTEND, env=env, check=True)
    # `next build` adds the dist dir to tsconfig.json; keep the working tree clean.
    subprocess.run(["git", "checkout", "--", "frontend/tsconfig.json"], cwd=ROOT, check=False)


def run_scenario(script: str) -> int:
    env = {**os.environ, "BASE_URL": f"http://localhost:{FRONTEND_PORT}",
           "PASSWORD": PASSWORD, "CHROME_PATH": CHROME}  # fmt: skip
    print(f"\n=== {script} ===", flush=True)
    return subprocess.run([NODE, script], cwd=E2E, env=env, timeout=400).returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["admin", "voice", "all"])
    parser.add_argument("--no-build", action="store_true", help="reuse frontend/.next-e2e")
    args = parser.parse_args()
    if args.scenario in ("voice", "all") and not (E2E / ".audio" / "booking_call.wav").exists():
        print("Run make_audio.ps1 first.")
        return 2
    if not args.no_build:
        build_frontend()

    procs: list[subprocess.Popen] = []
    logs = [open(E2E / ".audio" / f"{n}.log", "w") for n in ("backend", "frontend")]
    failures = 0
    try:
        asyncio.run(setup())
        env = {**os.environ, "RESTAURANT_SLUG": SLUG, "ADMIN_PASSWORD": PASSWORD,
               "LOG_LEVEL": os.environ.get("E2E_LOG_LEVEL", "INFO"),
               # The scripted caller pauses up to ~20 s while the assistant answers; with
               # the default 8 s a second "Are you still there?" could hang up mid-test.
               "VOICE_SILENCE_PROMPT_SECS": "15"}  # fmt: skip
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(BACKEND_PORT)],
            cwd=BACKEND, env=env, stdout=logs[0], stderr=logs[0],
        ))  # fmt: skip
        procs.append(subprocess.Popen(
            [NODE, "node_modules/next/dist/bin/next", "start", "-p", str(FRONTEND_PORT)],
            cwd=FRONTEND, env={**os.environ, "NEXT_DIST_DIR": ".next-e2e"},
            stdout=logs[1], stderr=logs[1],
        ))  # fmt: skip
        wait_http(f"http://127.0.0.1:{BACKEND_PORT}/api/health")
        wait_http(f"http://127.0.0.1:{FRONTEND_PORT}/login")

        if args.scenario in ("admin", "all"):
            failures += run_scenario("admin.mjs") != 0
        if args.scenario in ("voice", "all"):
            sampler = ResourceSampler(procs[0].pid).start()
            failures += run_scenario("voice-booking.mjs") != 0
            sampler.report("the spoken booking call")
            for name, ok, extra in asyncio.run(voice_booking_in_database()):
                print(f"{'PASS' if ok else 'FAIL'} {name} - {extra}")
                failures += not ok
    finally:
        for proc in reversed(procs):
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        for log in logs:
            log.close()
        asyncio.run(teardown())
    print("\nE2E RESULT:", "PASS" if failures == 0 else f"{failures} scenario/check(s) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
