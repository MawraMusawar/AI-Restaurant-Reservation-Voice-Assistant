# AI Restaurant Reservation Voice Assistant

A real-time, browser-based AI voice receptionist for restaurants. Callers speak naturally through the browser microphone. The assistant listens and responds in real time, checks real table availability, and books reservations in the database.

**Stack:** Pipecat (Python) · SmallWebRTC · Deepgram STT · Groq LLM · ElevenLabs TTS · FastAPI · Next.js + TypeScript · Supabase PostgreSQL. Everything runs locally, and the external APIs are used on their free tiers.

---

## Project Status

| Milestone | Status |
|---|---|
| 0. Project planning and foundation | ✅ Done |
| 1. Real-time voice infrastructure (Pipecat + SmallWebRTC) | ✅ Done (verified in Chrome with a simulated microphone) |
| 2. Deepgram STT | ✅ Done (real Deepgram tests + Chrome E2E) |
| 3. Groq LLM + tool calling | ⬜ Not started |
| 4. ElevenLabs TTS | ⬜ Not started |
| 5. Complete conversation engine | ⬜ Not started |
| 6. Supabase database + reservation engine | ⬜ Not started |
| 7. Frontend dashboard | ⬜ Not started |
| 8. Testing and debugging | ⬜ Not started |
| 9. Demo preparation | ⬜ Not started |

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | **3.12** | Pipecat 1.15 requires Python ≥ 3.11. `uv` downloads and pins 3.12 automatically. |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.12 | Python package and environment manager |
| [Node.js](https://nodejs.org/) | LTS | Needed for the Next.js frontend |
| Browser | Recent Chrome or Edge | Needs microphone permission |

API accounts (all on free tiers; needed in later milestones):
- Deepgram (Milestone 2): https://console.deepgram.com
- Groq (Milestone 3): https://console.groq.com/keys
- ElevenLabs (Milestone 4): https://elevenlabs.io
- Supabase (Milestone 6): https://supabase.com

> Free quotas and rate limits are set by each provider and may change. The app never switches silently to a paid service.

---

## Repository Layout

```
.
├── CLAUDE.md              # Project requirements and engineering rules
├── README.md
├── backend/               # FastAPI + Pipecat voice agent (Python 3.12, uv)
│   ├── app/
│   │   ├── main.py        # FastAPI app factory and lifespan
│   │   ├── config.py      # Settings from environment variables (secrets as SecretStr)
│   │   ├── logging_setup.py  # Structured logging (loguru, optional JSON)
│   │   └── api/           # HTTP routes
│   ├── tests/
│   ├── pyproject.toml
│   └── .env.example
└── frontend/              # Next.js 16 + React 19 + TypeScript + Tailwind
    ├── src/app/           # App Router pages
    ├── src/components/
    ├── next.config.ts     # proxies /api/* to the FastAPI backend
    └── .env.example
```

The browser only talks to the Next.js origin (`localhost:3000`). Requests to `/api/*` are proxied server-side to FastAPI (`BACKEND_URL`, default `http://127.0.0.1:8000`), so the browser never needs the backend address or any API key.

---

## Backend Setup

```bash
cd backend
uv sync                       # creates .venv with Python 3.12 and installs dependencies
cp .env.example .env          # then fill in API keys as milestones require them
```

Run the API server:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Check that it's running:
- Health: http://127.0.0.1:8000/api/health
- Interactive API docs: http://127.0.0.1:8000/docs

The health endpoint reports which integrations have credentials configured. It shows true/false only, never the key values.

### Tests and Linting

```bash
cd backend
uv run pytest                        # all tests (integration tests use a few seconds of Deepgram quota)
uv run pytest -m "not integration"   # fast unit/API tests only, no external APIs
uv run ruff check .      # lint
uv run ruff format .     # format
```

---

## Frontend Setup

```bash
cd frontend
npm install
cp .env.example .env.local    # optional; only needed if the backend isn't on 127.0.0.1:8000
npm run dev                   # http://localhost:3000
```

With the backend also running, the home page shows **Backend online** and lists which integrations are configured.

```bash
npm run lint        # ESLint
npx tsc --noEmit    # type-check
npm run build       # production build
```

## Live Call (Milestone 2: live transcription)

1. Set `DEEPGRAM_API_KEY` in `backend/.env`. From South Asia, also set `DEEPGRAM_BASE_URL=api.eu.deepgram.com` (see the measurements below).
2. Start the backend and frontend (see below).
3. Open http://localhost:3000/call, wait for **On call**, and speak. Interim words appear in italics and are replaced by final sentences when you pause.

Pipeline: `mic → SmallWebRTC → live-edge gate → Silero VAD → Deepgram (nova-3, streaming) → session recorder`. On end of speech, the VAD triggers a Deepgram *finalize*, so the final transcript doesn't wait for Deepgram's own endpointing.

| Endpoint | Purpose |
|---|---|
| `POST /api/offer` | WebRTC offer → SDP answer (starts a voice session) |
| `PATCH /api/offer` | Trickle ICE candidates |
| `GET /api/voice/status` | Can a call start now? (credentials configured, free line) |
| `GET /api/voice/sessions` | Active and recent sessions with transcript, startup time, STT latency and errors (in memory until Milestone 6) |

Limits: at most `VOICE_MAX_CONCURRENT_SESSIONS` calls at once (default 2). A browser that never completes the connection is dropped after 30 s, and an idle call ends after 5 minutes. If Deepgram rejects the key or quota, the call ends and the error is recorded on the session.

### Measured STT performance (2026-11-06, from Pakistan, real Deepgram)

| Endpoint | Startup (Deepgram WebSocket) | VAD finalize → final transcript | Caller's last word → final transcript |
|---|---|---|---|
| US (default) | 1.5–5.9 s, highly variable | 390–1,440 ms | 0.1–4.8 s (fails the 2 s budget about 40% of the time) |
| **EU (`api.eu.deepgram.com`)** | **0.8–1.4 s** | **110–610 ms** | **≤ 0.53 s** |

Accuracy, test fixtures generated with Windows TTS: normal, fast and slow speech, and speech with white noise at SNR 5 dB, were all transcribed correctly, with only minor errors at fast speed ("Actually" → "Ashley"). Silence produces no transcript.

### Known limitations (STT)
- **Accents:** only the US-English Windows voices (David, Zira) are available for automated tests. Other accents still need live testing. `DEEPGRAM_LANGUAGE` accepts `en-IN`, `en-GB`, or `multi`.
- **Speech before "On call" is dropped.** Audio that arrives while Deepgram is still connecting is discarded as stale, so it can't become a lasting delay. Milestone 5's greeting makes the caller wait naturally.
- **Utterances split at short pauses** (e.g. "...tomorrow evening at" / "seven."). That's fine for display. Milestone 5 adds proper turn detection so the LLM receives whole turns.

## Running Everything Locally

Use two terminals:

| Terminal | Command | URL |
|---|---|---|
| 1 (backend) | `cd backend && uv run uvicorn app.main:app --reload --port 8000` | http://127.0.0.1:8000/docs |
| 2 (frontend) | `cd frontend && npm run dev` | http://localhost:3000 |

---

## Security

- API keys live only in `backend/.env`, which is git-ignored. They are never sent to the browser.
- `.env.example` documents every variable without real values.
- Secrets are typed as `SecretStr`, so they don't appear in logs or reprs.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `requires-python` / Pipecat install error | Use Python 3.12 (`uv python pin 3.12`). Python 3.10 is too old for Pipecat 1.15. |
| uv warns "Failed to hardlink files" | Harmless on Windows across drives. Set `UV_LINK_MODE=copy` to silence it. |
| Port 8000 already in use | Run with `--port 8001` and set `BACKEND_URL=http://127.0.0.1:8001` in `frontend/.env.local`. |
| Home page says "Backend offline" | Start the backend (terminal 1), then refresh the page. |
| `node` not found after installing Node.js | Restart VS Code or the terminal so the new PATH is loaded. |
| `EPERM: operation not permitted, rename ...` (Next.js), ruff/uv "Access is denied" | The drive's permissions let normal users create files but not rename or delete them. Grant yourself Modify on the project folder (no admin needed, since you own it): `icacls "D:\Agentic Voice-AI" /grant "${env:USERNAME}:(OI)(CI)M"`, then restart `npm run dev`. |
| `WinError 10013` or wrong/old API responses on port 8000 | Another process holds port 8000. Find it with `Get-NetTCPConnection -LocalPort 8000` and stop it with `taskkill /PID <pid> /T /F`. |
