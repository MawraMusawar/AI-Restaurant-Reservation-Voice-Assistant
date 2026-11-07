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
| 3. Groq LLM + tool calling | ✅ Done (real Groq conversation tests + Chrome E2E) |
| 4. ElevenLabs TTS | ✅ Done (spoken replies, barge-in) |
| 5. Complete conversation engine | ✅ Done (turn-taking, silence, goodbye, error recovery; Chrome E2E) |
| 6. Supabase database + reservation engine | ✅ Done (database tests + real-Groq booking against Supabase; spoken Chrome booking pending a quiet CPU) |
| 7. Frontend dashboard | ✅ Done (all screens; API tests + 19-check Chrome E2E) |
| 8. Testing and debugging | ✅ Done (spoken booking verified end to end; limitations documented below) |
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
│   │   ├── api/           # HTTP routes (health)
│   │   ├── voice/         # Signaling, session lifecycle, the Pipecat pipeline, processors
│   │   ├── services/      # One module per provider: stt.py (Deepgram), llm.py (Groq), tts.py (ElevenLabs)
│   │   ├── agent/         # Conversation logic: restaurant profile, booking rules, availability, prompt, tools
│   │   └── db/            # PostgreSQL: pool, migration runner, restaurant/reservation/call-log repositories
│   ├── tests/
│   ├── pyproject.toml
│   └── .env.example
├── supabase/
│   ├── migrations/        # SQL schema (0001_init.sql)
│   └── seed.sql           # demo restaurant Olive & Ember
└── frontend/              # Next.js 16 + React 19 + TypeScript + Tailwind
    ├── src/app/           # App Router: (app)/ = sidebar screens, login/
    ├── src/lib/           # API client, data hook, formatting, types
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
cp .env.example .env          # then fill in API keys and DATABASE_URL
uv run python -m app.db.migrate --seed   # create the tables + demo restaurant (safe to re-run)
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
uv run pytest                        # all tests (integration tests use Deepgram and Groq quota)
uv run pytest -m "not integration"   # fast unit/API tests only, no external APIs
uv run pytest tests/test_agent_llm.py   # real Groq conversations (~30K of the 200K daily tokens)
uv run pytest tests/test_db_reservations.py   # reservation engine against the real database
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

## Live Call (Milestone 5: a natural conversation)

1. Set `DEEPGRAM_API_KEY`, `GROQ_API_KEY` and `ELEVENLABS_API_KEY` in `backend/.env`. From South Asia, also set `DEEPGRAM_BASE_URL=api.eu.deepgram.com` (see the measurements below).
2. Start the backend and frontend (see below).
3. Open http://localhost:3000/call and wait for **On call**. The assistant greets you out loud. Ask for a table or about the restaurant, interrupt it whenever you like, and say goodbye when you're done: it says goodbye and hangs up. The transcript follows the voice: spoken words are solid and words about to be spoken are faded. A reply you cut off is marked *interrupted* and keeps only what you actually heard. While the assistant works, the status shows *Thinking…* or *Checking availability…*.

Pipeline: `mic → SmallWebRTC → live-edge gate → Deepgram (nova-3, streaming) → user aggregator (Silero VAD + smart-turn end-of-turn model) → Groq LLM (tool calling) → ElevenLabs TTS (streaming WebSocket) → speaker → assistant aggregator`. The VAD runs in the user aggregator and sends its events upstream too, so Deepgram is still asked to *finalize* the moment you stop speaking.

### Speech output (Milestone 4)

- **Voice:** ElevenLabs premade voice *Sarah* (`EXAVITQu4vr4xnSDxMaL`), model `eleven_flash_v2_5` (lowest latency, 0.5 credits per character). Set `ELEVENLABS_VOICE_ID` to any premade voice in your account.
- **Streaming:** one WebSocket per call. LLM text streams in as it is generated, and playback starts after the first phrase, not the whole reply.
- **Greeting:** spoken through the TTS the moment the browser is ready. It uses no LLM request and enters the context only once spoken.
- **Barge-in:** when the caller starts speaking (see *Turn-taking* below), the pipeline cancels the LLM response and the TTS context and drops queued audio. The assistant aggregator keeps only the words actually spoken (from ElevenLabs word timestamps), so the AI knows exactly what the caller heard.
- **Failures:** a rejected ElevenLabs key ends the call with the error recorded. If a reply can't be spoken (e.g. credits used up), the call continues and the page explains why.
- **Free plan:** 10,000 credits a month ≈ 20,000 characters on Flash. A full demo call (greeting plus ~10 replies) uses about 600–700 credits, so **about 15 full calls a month**. Remaining credits: https://elevenlabs.io/app/usage

Measured (2026-11-06, quiet CPU, Deepgram EU):

| What | Result |
|---|---|
| Barge-in: caller's first word → assistant audio stops | 1.15–1.6 s with the 0.4 s VAD start (0.8–0.9 s at 0.2 s); in the pipeline, interruption → voice stopped in 30–50 ms |
| ElevenLabs: first text → first audio | 0.38–0.55 s |
| WebSocket handshakes, run in parallel (Deepgram US / EU / ElevenLabs) | 1.8–2.8 s / 1.2–1.6 s / 0.9–1.5 s |
| Caller's turn end → reply heard (Chrome E2E) | 0.9–1.7 s (Groq ~0.4–1.2 s + ElevenLabs ~0.4 s) |
| Call start → "On call" | 1.6–2.3 s (EU); 3.3–5.5 s with Deepgram US |
| Groq time to first output, warm (tool call request) | `gpt-oss-120b` median 0.6 s; `qwen3.8-27b` median 0.2 s |

Call health, per session in `GET /api/voice/sessions`:
- `voice_latencies_ms`: turn end → first audio.
- `dropped_audio_secs`: caller audio dropped mid-call because the server fell behind.
- `max_loop_lag_ms` / `loop_stalls`: event-loop stalls over 100 ms.

On a quiet CPU, mid-call dropped audio is 0–0.1 s. Under 100% CPU load (another program running), stalls reached 1.5 s and about 1 s of caller audio was dropped per utterance, which garbled transcripts and failed 3 speech tests. **Run the demo on a machine that isn't busy with other heavy work.**

### Conversation engine (Milestone 5)

**Turn-taking** (settings in `backend/.env`, measured with server and client in separate processes):

| Setting | Default | Why |
|---|---|---|
| `VOICE_VAD_START_SECS` | 0.4 | Continuous voice that starts the caller's turn and interrupts the assistant. At 0.2–0.3 s a one-word "okay" (0.37 s) interrupted it; at 0.4 s it doesn't. Also limits the assistant interrupting itself when its voice leaks from speakers into the mic. Cost: barge-in 1.15–1.6 s when the caller opens with a short word and a pause ("Hi, I..."). Use 0.2 with a headset for faster barge-in. |
| `VOICE_BARGE_IN_MIN_WORDS` | 2 | While the assistant speaks, 2 transcribed words also interrupt it. Words alone were too slow (2.3 s), so this backs up the VAD. |
| `VOICE_TURN_END_SILENCE_SECS` | 0.4 | The turn ends only when smart-turn judges the sentence complete **and** the caller has paused this long. Before this, "Great. That's all. Thanks. Goodbye." became 4 turns and 7 LLM runs; now 2 turns and 3 runs, for about +0.1 s of reply time. |
| `VOICE_SILENCE_PROMPT_SECS` | 8 | Silence after the assistant spoke before it checks in. |

**Behaviour:**
- **Silence:** after 8 s it asks *"Are you still there?"*. After a second silence it says goodbye and ends the call (`end_reason: caller_silent`).
- **Goodbye:** the `end_call` tool. The assistant says goodbye and hangs up once the queued speech has played (`assistant_ended`). If the model hangs up without saying anything (seen with the fallback model), the pipeline speaks *"Thank you for calling. Goodbye!"* first, so the line never just goes dead.
- **Error recovery:** if Groq can't answer a turn (every free model rate-limited), the caller *hears* *"Sorry, I'm having a little trouble on my end. Could you say that again?"*, with no LLM needed.
- **Ambiguity and corrections:** vague requests ("7 or 8") get a clarifying question, corrections are re-checked, and the assistant never announces a check without doing it in the same reply.
- **Faster answers:** opening hours are answered from the prompt without a tool call, which saves a full LLM request (about 0.6 s and 2K tokens).
- **Call logs:** every session records `end_reason` (`caller_hung_up`, `assistant_ended`, `caller_silent`, `connect_timeout`, `error`, `server_shutdown`). The page tells the caller when the assistant ended the call.
- **Startup:** the first call after a server (re)start used to block 6–9 s while it built models, schemas and TLS clients. The voice stack now warms up in a worker thread at server start (0.8 s), and the VAD and smart-turn models load off the event loop. Each call still has one ~0.3–0.5 s stall during its TLS handshakes (Python's SSL context creation on Windows, ~0.2 s each), before the greeting.

> **Without a database** (no `DATABASE_URL`, or it's unreachable), calls still work, but the reservation tools report that the booking system is offline and the call page shows *"Bookings are offline"*. The assistant never pretends a table is free or booked.

| Endpoint | Purpose |
|---|---|
| `POST /api/offer` | WebRTC offer → SDP answer (starts a voice session) |
| `PATCH /api/offer` | Trickle ICE candidates |
| `GET /api/voice/status` | Can a call start now? (credentials configured, free line) |
| `GET /api/voice/sessions` | Active and recent sessions: transcript (both sides), startup time, STT and response latency, tool calls (name, result, duration; never arguments), errors. In memory until Milestone 6 |

Limits: at most `VOICE_MAX_CONCURRENT_SESSIONS` calls at once (default 2). A browser that never completes the connection is dropped after 30 s, and an idle call ends after 5 minutes. If Deepgram or Groq rejects the key, the call ends and the error is recorded on the session.

## Conversation Agent (Milestone 3)

Code: `backend/app/agent/` (conversation logic, testable with text alone) and `backend/app/services/llm.py`.

- **Restaurant profile** (`restaurant.py`): the fictional demo restaurant *Olive & Ember* (Lahore, `Asia/Karachi`, closed Mondays). It moves to the database in Milestone 6.
- **System prompt** (`prompt.py`): goals and rules, not a script. It's built per call with the restaurant's local date and the next 7 days, so "tomorrow" or "Friday" resolve correctly. The greeting is fixed, so the call opens instantly without an LLM request.
- **Booking rules** (`booking_rules.py`): parse and validate date, time and party size against opening hours, last seating (60 min before close), minimum notice (30 min), booking window (30 days) and max party size (10). Each failure returns a code and a message the assistant explains.
- **Tools** (`tools.py`), called through structured function calling:

| Tool | Behaviour |
|---|---|
| `get_restaurant_info` | Address, hours, contact, services, policies |
| `check_availability` / `get_available_tables` | Validates the request, then asks the reservation backend |
| `create_reservation` | **Two-step, enforced in code:** the first call only returns the details to read back. The backend is called only by a later call with the *same* details, after the caller has spoken again, with `caller_confirmed: true` |
| `get_reservation` | By booking reference, or by name and phone |
| `cancel_reservation` | Two-step like booking: reads the booking back first |

The two-step rule exists because testing showed the model booking straight after hearing the name, with no read-back, despite the prompt. Every tool returns `{ok, ...}`; on failure it adds `error_code` and `message`. Exceptions become `internal_error` results, so the caller hears an apology instead of silence.

### Groq free tier

Measured on this account (response headers) and documented by Groq: **every model has its own budget of ~8K tokens/minute, 200K tokens/day, 30 requests/minute and 1,000 requests/day.** One request (prompt, tools and history) is about 2–2.5K tokens, so one model serves only 3–4 requests a minute.

- **Failover:** when the main model (`openai/gpt-oss-120b`) is rate-limited, the same request is retried immediately on `qwen/qwen3.8-27b`, which has its own budget. It is free, never a paid tier, and every failover is logged and recorded on the session. `openai/gpt-oss-20b` was rejected as a fallback: in testing it invented caller details and answered for the caller.
- If every model is rate-limited, the call stays up, the turn goes unanswered, and the page shows *"The assistant reached the Groq free-tier rate limit… say that again."*
- **Daily budget:** a full booking call is roughly 15–20 requests (~40K tokens), so expect about 5 full calls per model per day, about 10 with failover. Keep this in mind when rehearsing the demo.

### Measured (2026-11-06, real Groq)

| What | Result |
|---|---|
| Groq time to first token (`gpt-oss-120b`, reasoning low) | 0.5–0.8 s per request |
| Caller's turn end → assistant's first word, no tool | 0.4–1.0 s |
| Caller's turn end → first word, with one tool call (two requests) | 1.0–1.9 s (browser E2E: 1.94 s) |
| Caller's last word → turn end (smart-turn) | 0.3–1.0 s when judged complete; 3 s fallback when judged unfinished |

Conversation tests (`tests/test_agent_llm.py`, real Groq, run with the scripted backend that stands in for the Milestone 6 database):
- **Missing information:** "I'd like to book a table" → asks for party size, with no tool call.
- **Relative dates:** "tomorrow at 7" → `check_availability(2026-11-07, 19:00, 4)`; 7 PM was taken, so it offered 6:30 and 8:15.
- **Confirmation:** booking only after the read-back and "Yes": `Ali Raza, 4, 2026-11-07 20:15`, reference given.
- **Corrections:** "actually make that six" → availability re-checked with 6 guests.
- **Restaurant info:** Sunday closing time correct, Monday reported closed.
- **Honesty:** with the offline backend, never claims a table is available or booked.

### Known limitations (LLM)
- **Turn splitting:** much reduced (see `VOICE_TURN_END_SILENCE_SECS`), but pauses longer than 0.4 s between sentences still start a new turn, and smart-turn waits its 3 s fallback when a sentence sounds unfinished.
- **Free-tier throughput** limits how fast and how many calls can run (see above).
- **Fallback model quality:** `qwen3.8-27b` (used when `gpt-oss-120b` is rate-limited) sometimes hangs up without words or announces a check without doing it. The prompt and code guards cover the cases seen so far.
- **Barge-in flag:** in about 1 of 8 measured barge-ins the audio stopped but the transcript didn't mark the greeting as interrupted.

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

## Database and Reservations (Milestone 6)

**Setup:** create a free Supabase project, then **Connect → Direct → Method: Session pooler**, and copy the URI into `backend/.env` as `DATABASE_URL` with your database password filled in. Passwords with `@ # / :` work as typed; the backend encodes them. Then run:

```bash
cd backend
uv run python -m app.db.migrate --seed     # tables + demo restaurant; --status lists migrations
```

The backend talks to PostgreSQL directly with `asyncpg`. No Supabase REST keys are needed, and row-level security keeps the tables closed to Supabase's public REST API.

**Schema** (`supabase/migrations/0001_init.sql`):

| Table | Holds |
|---|---|
| `restaurants` | Details, IANA time zone, booking rules (party size, window, notice, last seating, dining duration 90 min, slot step 15 min, phone required?) |
| `restaurant_hours` | Local opening hours per weekday (no row = closed) |
| `restaurant_tables` | Label, seats, minimum party, area, active |
| `customers` | Name, phone (unique per restaurant when given) |
| `reservations` | Table, customer, unique booking reference (`OE-7K3P9Q`), party size, `starts_at`/`ends_at` (timestamptz), status (`confirmed`/`cancelled`/`completed`/`no_show`), voice session |
| `voice_sessions`, `conversation_messages` | Call logs: status, end reason, latency and audio-health metrics, the transcript |
| `assistant_settings` | Assistant name, greeting, personality, voice |

**No double-booking, guaranteed twice:**
1. **In code** (`app/agent/availability.py`, `app/db/reservations.py`): availability is computed from the tables and the day's confirmed bookings inside the booking transaction. The best-fitting free table (fewest seats) is chosen.
2. **In the database:** the exclusion constraint `reservations_no_overlap` (`table_id =`, `tstzrange(starts_at, ends_at) &&`, only for `confirmed`) makes overlapping bookings impossible. If two callers race for one table, the loser's insert fails and the next free table is tried. Tested: 5 simultaneous bookings for the last table, exactly 1 succeeds.

**Behaviour:**
- **Full slot:** the 3 nearest bookable times that day are offered (same rules: hours, last seating, notice).
- **Time zones:** "Saturday 19:00" is converted with the restaurant's zone (`Asia/Karachi`) and stored as an instant (14:00 UTC).
- **Booking references:** no 0/O or 1/I, so they're easy to read aloud. The assistant reads them character by character.
- **Lookup:** by reference, or by the phone number given at booking. **Cancel** is two-step (read back, then confirm). Already-cancelled or past bookings are refused with an explanation.
- **Hard rule:** `create_reservation` returns `ok: true` with a reference only after the transaction commits. Any database error becomes `database_error` ("nothing was booked"), which the assistant explains. Tested with a dead connection pool.
- **Call logs:** every finished call is saved (`voice_sessions` + `conversation_messages`). A failed save never affects calls.
- **Per call:** the restaurant profile (hours, rules, assistant settings) is loaded from the database. If that fails, the call continues with bookings offline.

**Tests against the real database:**
- `tests/test_db_reservations.py`: 9 tests, each in a throwaway restaurant, so your demo data isn't touched.
- `tests/test_agent_llm_db.py`: real Groq books a table, reads back the reference, then cancels with confirmation. The database is checked at each step.

## Web App (Milestone 7)

Open http://localhost:3000. The **Live call** screen is public (it's the caller's side). Everything else needs the staff login: set `ADMIN_PASSWORD` in `backend/.env`, restart the backend, and sign in at `/login`.

| Screen | What it does |
|---|---|
| **Dashboard** (`/`) | Today's bookings and guests, total and upcoming reservations, voice calls, bookings made by voice and the booking rate; charts of bookings for the next 7 days and calls for the last 7 days (hover for values, "Show as table" for exact numbers); recent conversations and reservation activity. Refreshes every 15 s. |
| **Live call** (`/call`) | The phone-call screen (Milestones 1-6) |
| **Reservations** (`/reservations`) | Filter by day range, status, or name/phone/reference; **create** bookings (same rules and double-booking guards as the voice agent; a full slot offers nearby free times as one-click buttons); **cancel** with confirmation |
| **Call logs** (`/calls`) | Every call with its first request, duration, outcome and booking; click one for the transcript (interruptions marked), latency, errors and the booking it made. Calls in progress show as *Live*. |
| **Settings** (`/settings`) | Restaurant details and booking rules; opening hours per weekday; tables (add, take out of use); AI personality (assistant name, personality, greeting, voice); system status. Changes apply from the next call. |

**API** (all under `/api`, staff login required except `auth`):

| Endpoint | Purpose |
|---|---|
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | Staff session: an HttpOnly, SameSite=Lax cookie with an HMAC-signed token, valid 12 h. Changing `ADMIN_PASSWORD` signs everyone out. |
| `GET /dashboard` | Stats, charts, recent calls and activity |
| `GET /reservations?from=&to=&status=&q=`, `POST /reservations`, `POST /reservations/{ref}/cancel` | List / create (201; 409 with `alternatives` when full; 422 with a rule `code`) / cancel (404, or 409 if already cancelled or in the past) |
| `GET /settings`, `PUT /settings/restaurant`, `PUT /settings/hours`, `PUT /settings/assistant`, `POST /settings/tables`, `PATCH /settings/tables/{id}` | Settings (validated: IANA time zone, closing after opening, party-size limits, unique table labels) |
| `GET /calls`, `GET /calls/{id}` | Call logs (stored + live) |

Database errors in these requests become **503** (unavailable), **409** (duplicate) or **422** (rule violated), never a crash. `GET /api/voice/sessions` (live debug data, including transcripts) requires the staff login when `APP_ENV=production`.

**Tests:**
- `tests/test_management_api.py`: auth (expiry, forgery, logout, locked without a password) and every endpoint, against the real database in a throwaway restaurant.
- Browser E2E (Chrome, test servers on 8010/3010, throwaway restaurant, 19 checks): the login redirect and wrong password; the dashboard; a staff booking, a full slot with a one-click alternative, and a cancellation; a call transcript; hours, tables (including duplicate rejection) and assistant settings; the dashboard counts; sign-out; no horizontal scroll at phone width; no console errors.

## Testing and Known Limitations (Milestone 8)

### Test suite

| Suite | How to run | What it covers |
|---|---|---|
| Unit (fast, no external services) | `cd backend && uv run pytest -m "not integration"` | Booking rules, availability and table fit, tools (two-step confirmation, validation), turn/latency probes, failover, call control, sessions, auth tokens (~100 tests) |
| Speech (real Deepgram over WebRTC) | `uv run pytest tests/test_voice_api.py -m integration` | Normal, fast and slow speech, noise at SNR 5 dB, **British male, British female and Australian accents**, silence, a rejected key, barge-in, a one-word backchannel, silence check-in and hang-up, a spoken answer |
| AI (real Groq) | `uv run pytest tests/test_agent_llm.py tests/test_agent_llm_db.py` | Missing info, relative dates, alternatives, confirmation, corrections, vague times, goodbye, **a database failure mid-booking**, invalid requests (14 people, a past date), **an 11-turn call**, booking and cancelling in the real database |
| Database (real Supabase) | `uv run pytest tests/test_db_reservations.py tests/test_management_api.py` | Booking, best fit, a full slot with alternatives, **5 concurrent bookings for the last table: exactly 1 wins**, the exclusion constraint on its own, cancellation, **duplicate requests** (repeat confirmation, double-submitted form), database outage, call logs, every management endpoint |
| Browser E2E (real Chrome) | see [e2e/README.md](e2e/README.md) | All staff screens (19 checks), and **a complete booking made by voice**, verified in the database |

Every database test uses a throwaway restaurant, so the demo data is never touched.

### Measured performance (2026-11-07, quiet CPU, Deepgram EU, real services)

From the spoken-booking E2E (per-stage timings recorded in each call log):

| Stage | Typical | Range seen |
|---|---|---|
| Call start → live ("On call") | 2.2-2.4 s | 2.2-2.9 s |
| Speech-to-text: caller stops → final transcript | **110-160 ms** | 78-344 ms |
| Turn detection: caller stops → turn ends (smart-turn + 0.4 s pause window) | **~0.6 s** | 0.59-0.69 s |
| AI: turn end → first word of text | **1.1-2.4 s** | 0.5-3.1 s (Groq free tier; varies with load and failovers) |
| Voice: first text → first audio (ElevenLabs Flash) | **~0.4-0.55 s** | 0.38-0.6 s |
| **Total: caller's turn ends → caller hears the reply** | **1.7-2.9 s** | 1.0-3.6 s |
| Barge-in: caller's first word → assistant stops | 1.15-1.6 s (VAD 0.4 s) | 0.8-0.9 s with `VOICE_VAD_START_SECS=0.2` |

**Groq is the largest and most variable stage.** In the same booking script, the AI's reply took 0.5-1.4 s on some turns and 1.7-3.1 s on others, depending on free-tier load and rate-limit failovers.

**Capacity** (backend process, separate client processes like real browsers):

| Load | CPU (% of one core) | Memory | Audio health |
|---|---|---|---|
| Idle | ~0 | 169 MB | - |
| 1 call | avg 55, peak 100 | 212 MB | 0 s dropped, loop lag ≤ 106 ms |
| 2 calls (started 4 s apart) | avg ~60, peak 106 | 240 MB | 0 s dropped, loop lag ≤ 90 ms |

**Free quota used by testing:** one spoken-booking E2E costs about 1 minute of Deepgram audio, ~10 Groq requests and **~300-400 ElevenLabs credits**. Milestone 8 testing used about half of the month's 10,000 ElevenLabs credits (reset 7 Dec 2026), so about 4,500 were left, roughly **7 full demo calls**. Check before a demo: https://elevenlabs.io/app/usage

### Bugs found and fixed in Milestone 8

- **The call log could be lost when the assistant hung up.** If the browser disconnected during the ~4.5 s graceful shutdown (goodbye playing, then Deepgram, ElevenLabs and WebRTC closing), a cancel raced the shutdown and the session never finished. Now a disconnect during a graceful end is ignored, and a 20 s watchdog forces the end if it ever stalls.
- **The AI asked for an optional phone number, then claimed it was required**, and the call stalled. The prompt now says not to ask unless the restaurant requires one.
- **Duplicate bookings:** a repeated confirmation or a double-submitted staff form created a second reservation. The same phone with an overlapping time, or the same name, time and party size, now returns the existing booking (`already_booked`).
- **The model sometimes named a tool that doesn't exist** (e.g. `check_arrival`), and Groq rejected the turn mid-stream. The turn is now retried once automatically, before the spoken apology.
- **Turn detection wasn't measured.** Each call now records it, alongside STT, AI and voice timings.

### Known limitations

**Voice and audio**
- **CPU starvation breaks real-time audio.** If another program keeps the CPU at 100%, the backend's audio loop stalls (up to 1.5-2 s measured), caller audio is dropped and transcripts are garbled. Run the demo on an otherwise idle computer; `dropped_audio_secs` and `loop_stalls` in the call log show when this happened.
- **Two calls starting in the same second:** one of them may fail to connect (its WebRTC/ICE negotiation stays "checking"), and that caller sees a connection error after 30 s; retrying works. Calls started a few seconds apart work fine, and two concurrent calls ran with healthy audio. The cause is in the WebRTC library layer, not the app.
- **Barge-in takes 1.15-1.6 s** when the caller opens with a short word and a pause, the price of ignoring "okay"/"mm-hmm" and speaker echo. In about 1 of 8 barge-ins the audio stopped but the transcript didn't mark the reply as interrupted.
- **Turns can still split** when the caller pauses longer than 0.4 s between sentences. Each fragment is answered, and the assistant is interrupted by the next one.
- **Accents:** British and Australian English were transcribed perfectly; South Asian accents haven't been tested with recordings (no such voices are available on the free tier). `DEEPGRAM_LANGUAGE=en-IN` is available.
- **Words spoken before "On call" are dropped** (by design: stale audio would otherwise delay the whole call).

**AI**
- **Reply time is dominated by Groq's free tier:** 0.5-3 s for the first word, about 8K tokens per minute per model. A fast-paced call can trigger failover to `qwen3.8-27b`, which is good but slightly less disciplined; code guards (two-step booking, fallback goodbye, tool-call retry) cover the issues seen.
- Names are stored as speech recognition heard them (e.g. "Sara" became "Sarah"); the read-back lets the caller correct it.

**Data and operations**
- **Database round trip is ~250 ms** (Pakistan → Supabase Singapore pooler), so each booking takes several round trips (~1-2 s). Database tests occasionally hit the 10 s command timeout on the free pooler; rerunning passes.
- Opening hours that run past midnight aren't supported.
- **Free quotas** (Deepgram, Groq, ElevenLabs) limit how many calls and test runs are possible. They are documented above, and quota exhaustion is reported clearly, never silently switched to a paid tier.

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
- Staff screens need `ADMIN_PASSWORD`. The session cookie is HttpOnly (JavaScript can't read it) and HMAC-signed with an expiry; the password is compared in constant time.
- Supabase tables have row-level security on with no policies, so the public REST API can't read them; only the backend (as table owner) can.

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
| Transcripts garbled, slow replies, `dropped_audio_secs` > 0 or `loop_stalls` > 0 in `/api/voice/sessions` | The computer's CPU is overloaded (another heavy program), so the real-time audio loop falls behind. Close heavy programs (check Task Manager) and retry. |
| "The ElevenLabs free voice credits are used up" | The monthly 10,000 credits are spent; they reset monthly. Check https://elevenlabs.io/app/usage |
| `uv run ...`: "Failed to spawn: uvicorn" | Run it from the `backend` folder; the environment lives there. |
| "Bookings are offline" on the call page | `DATABASE_URL` is missing or the database is unreachable (see the backend log). Use the **Session pooler** URI (the direct host is IPv6-only), then run `uv run python -m app.db.migrate --seed` and restart the backend. |
| `getaddrinfo failed` connecting to the database | Usually a password with `@` read as part of the host; the backend now encodes it. Otherwise check the host in `DATABASE_URL`. |
| "The assistant reached the Groq free-tier rate limit" | Every free model's per-minute budget is used up. Wait ~30 s. If it keeps happening, the daily budget (200K tokens per model) is spent; it resets within 24 h. Usage: https://console.groq.com/settings/limits |
| `WinError 10013` or wrong/old API responses on port 8000 | Another process holds port 8000. Find it with `Get-NetTCPConnection -LocalPort 8000` and stop it with `taskkill /PID <pid> /T /F`. |
