# Browser end-to-end tests

Real Chrome drives the real app: Next.js production build, FastAPI, Supabase, and, for
the voice scenario, real Deepgram, Groq and ElevenLabs. Everything runs against a
throwaway `e2e-*` restaurant created for the run and deleted afterwards. Your demo
restaurant is never touched, and your own dev servers (3000/8001) keep running: the
tests use ports **8010** (backend) and **3010** (frontend), plus a separate build
folder (`frontend/.next-e2e`).

| Scenario | What it checks |
|---|---|
| `admin.mjs` (19 checks) | Login redirect and wrong password; dashboard; staff booking, full slot with a one-click alternative, cancel; call-log transcript; nav highlighting; opening hours, tables (duplicate rejected), assistant settings; dashboard counts; sign-out; no horizontal scroll at phone width; no console errors |
| `voice-booking.mjs` | A complete phone booking by voice: Chrome's fake microphone plays `.audio/booking_call.wav` (request, name, "yes", goodbye). Checks that the assistant books and hangs up, then that **the booking exists in the database**, confirmed and linked to its saved call log. Prints the per-stage latency and the backend's CPU and memory. |

## Setup (once)

```powershell
cd e2e
npm install                                             # playwright-core (uses your installed Chrome)
powershell -ExecutionPolicy Bypass -File make_audio.ps1  # caller audio (Windows speech engine)
```

`backend/.env` must have `DATABASE_URL` and the API keys. The tests set their own
admin password and restaurant; nothing in your `.env` changes.

## Run

```powershell
..\backend\.venv\Scripts\python.exe run.py admin    # ~2 min, no API quota used
..\backend\.venv\Scripts\python.exe run.py voice    # ~3 min, uses free quota (see below)
..\backend\.venv\Scripts\python.exe run.py all
```

Add `--no-build` to reuse the last frontend build. Set `E2E_LOG_LEVEL=DEBUG` for a
detailed backend log in `.audio/backend.log`. Screenshots and the RTVI message log go
to `.audio/`.

**Quota per voice run:** about 1 minute of Deepgram audio, about 10 Groq requests
(about 25K tokens), and about **300-400 ElevenLabs credits** (greeting, replies, the
booking reference). The ElevenLabs free plan has 10,000 credits a month, so don't
run the voice scenario casually before a demo.

**Run voice tests on an idle computer.** At 100% CPU (another heavy program) the
real-time audio loop stalls and the scripted conversation goes wrong; see the main
README's known limitations.
