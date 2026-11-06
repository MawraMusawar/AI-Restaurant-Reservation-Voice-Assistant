# AI Restaurant Reservation Voice Assistant

A real-time, browser-based AI voice receptionist for restaurants. Callers speak naturally through the browser microphone. The assistant listens and responds in real time, checks real table availability, and books reservations in the database.

**Stack:** Pipecat (Python) · SmallWebRTC · Deepgram STT · Groq LLM · ElevenLabs TTS · FastAPI · Next.js + TypeScript · Supabase PostgreSQL. Everything runs locally, and the external APIs are used on their free tiers.

---

## Project Status

| Milestone | Status |
|---|---|
| 0. Project planning and foundation | 🟡 In progress: backend done, frontend waiting on Node.js |
| 1. Real-time voice infrastructure (Pipecat + SmallWebRTC) | ⬜ Not started |
| 2. Deepgram STT | ⬜ Not started |
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
└── frontend/              # Next.js + TypeScript (coming in Milestone 0)
```

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
uv run pytest            # tests
uv run ruff check .      # lint
uv run ruff format .     # format
```

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
| Port 8000 already in use | Run with `--port 8001`, or stop the other process. |
