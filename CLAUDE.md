# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

**AI Restaurant Reservation Voice Assistant**: a real-time, browser-based voice agent that acts like a human restaurant receptionist. It greets callers, understands speech as it streams in, keeps context, collects reservation details, checks real availability in the database, suggests alternatives, confirms details, and books tables.

- **Purpose:** a client demonstration. It must feel like a real phone call, not a chatbot or a voice bot that speaks in disconnected turns.
- **Runs locally.** No paid hosting, telephony, or voice-agent platforms.
- **Real integrations** with Deepgram, Groq, and ElevenLabs, all on free tiers.

### Hard rule
Never tell the caller, the UI, or the logs that a reservation was created unless the database confirms it was. Never simulate a successful booking when a database operation fails.

---

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Voice framework | **Pipecat (Python)** | Orchestrates the pipeline, turn-taking, interruptions, and streaming. Prefer official Pipecat integrations over custom code. |
| Audio transport | **SmallWebRTC** | Two-way audio between the browser and the agent. **No LiveKit Cloud or other paid transport.** |
| STT | **Deepgram** (streaming) | Low latency, endpointing/VAD, error handling and reconnection. |
| LLM | **Groq** (free-tier model) | Structured tool/function calling only, with no text parsing. Keep the provider modular so it can be swapped. |
| TTS | **ElevenLabs** (streaming) | Start playback before the full response is generated. Handle API failures gracefully. |
| Backend | **Python + FastAPI** | APIs, agent lifecycle, sessions, business logic, logging, security, WebSockets where needed. |
| Frontend | **Next.js + React + TypeScript** | Live-call UI, transcript, visualization, dashboard, reservation management. Modern, responsive, SaaS style. |
| Database | **Supabase PostgreSQL** | Constraints and transactions prevent double-booking. |
| Agent framework | **None at first** | Use Pipecat's native LLM and function calling. Add LangGraph/LangChain later **only** if the workflow genuinely needs complex state or multi-step orchestration. |

**Before using any library, check its current API against the official docs.** Do not invent deprecated methods, undocumented interfaces, or unsupported integrations.

### Verified library notes (checked 2026-11-06 against installed `pipecat-ai==1.15.0`)
- Pipecat 1.15 requires **Python ≥ 3.11**. The backend is pinned to **3.12** through `uv` (`backend/.python-version`).
- Use **`PipelineWorker`** (`pipecat.pipeline.worker`) and **`WorkerRunner`** (`pipecat.workers.runner`). `PipelineTask` and `PipelineRunner` have been **deprecated since 1.3.0** and will be removed in 2.0. Don't use them, even though older docs and quickstarts still show them.
- Custom FastAPI signaling: `SmallWebRTCRequestHandler`, `SmallWebRTCRequest`, `SmallWebRTCPatchRequest` from `pipecat.transports.smallwebrtc.request_handler`. Use `POST /api/offer` for the offer and `PATCH /api/offer` for ICE candidates. Call `handler.close()` in the FastAPI lifespan.
- Transport: `SmallWebRTCTransport` (`pipecat.transports.smallwebrtc.transport`) + `TransportParams` (`pipecat.transports.base_transport`).
- Services take a `settings=<Service>.Settings(...)` object. Constructor args like `model=`, `voice_id=` and `live_options=` are deprecated.
  - `DeepgramSTTService` (`pipecat.services.deepgram.stt`). `DeepgramFluxSTTService` (`pipecat.services.deepgram.flux.stt`) has built-in turn detection.
  - `GroqLLMService` (`pipecat.services.groq.llm`)
  - `ElevenLabsTTSService` (`pipecat.services.elevenlabs.tts`) streams over WebSocket and is the one to use for live calls.
- Context: `LLMContext` (`pipecat.processors.aggregators.llm_context`) + `LLMContextAggregatorPair` / `LLMUserAggregatorParams` (`pipecat.processors.aggregators.llm_response_universal`). VAD: `SileroVADAnalyzer` (`pipecat.audio.vad.silero`).
- RTVI: `PipelineWorker` attaches `RTVIProcessor` and `RTVIObserver` automatically (`worker.rtvi`). Don't add them to the pipeline list yourself.
- Tools: `FunctionSchema` (`pipecat.adapters.llm.schemas.function_schema`) with `handler=`, or direct functions taking `FunctionCallParams` (`pipecat.services.llm_service`). Return results via `await params.result_callback(...)`.
- Browser client: `@pipecat-ai/client-js` (`PipecatClient`) + `@pipecat-ai/small-webrtc-transport`. Connect with `connect({ webrtcRequestParams: { endpoint: "/api/offer" } })`. Check npm versions with `npm view` when installing. Installed: client-js 1.16.0, client-react 1.11.0, small-webrtc-transport 1.11.2.
- Gotchas found in Milestone 1 (verified in source):
  - `SmallWebRTCRequestHandler.handle_web_request` **awaits** the connection callback before answering, and **swallows** callback exceptions. The callback must only schedule the bot (`VoiceSessionManager.start`), never run it.
  - aiortc **accepts malformed SDP** and returns an empty answer. `OfferRequest` validates `v=0` + `m=audio` first.
  - Without an LLM user aggregator, nothing emits `UserStarted/StoppedSpeakingFrame`, and the JS client ignores raw `vad-user-*` messages. `VADSpeakingBridge` covers this until Milestone 5.
  - `makeRequest` in client-js rejects with the raw `Response` on non-2xx. Map it with `describeConnectError`.
  - The output transport resamples automatically (input is 16 kHz, output 24 kHz).
  - Bot Started/Stopped Speaking is only emitted for `TTSAudioRawFrame` or `SpeechOutputAudioRawFrame`, **not** for a plain `OutputAudioRawFrame`.
  - With SmallWebRTC the JS client **never emits `RemoteAudioLevel`** (only the WebSocket playback path computes it). Measure the bot's track with `useTrackAudioLevel(usePipecatClientMediaTrack("audio", "bot"))`.
- Gotchas found in Milestone 2 (verified by measurement):
  - **A backlog becomes a lasting delay.** Audio queued before the pipeline starts (the STT WebSocket handshake blocks `StartFrame`) gets burst into Deepgram, which answers at about real-time pace. The lag never clears. `LiveEdgeAudioGate` (a token bucket that allows 200 ms bursts) must stay directly after `transport.input()`.
  - The Deepgram **US** handshake from here takes 1.5–7 s and varies a lot; **EU** (`DEEPGRAM_BASE_URL=api.eu.deepgram.com`) takes 0.7–1.7 s. The cause is the server side, not DNS/IPv6/TLS or the SDK (all ruled out).
  - Transcripts reach the client as RTVI `user-input` (`RTVIEvent.UserInput`, `input_type === "transcription"`). `user-transcription` / `UserTranscript` are deprecated since 1.13.
  - `ProcessorUnusablePolicy.END` + the `on_pipeline_error` handler: a rejected key ends the call, with the error recorded on the session.
  - In `run_test`, VAD/system frames jump ahead of data frames. Use `SleepFrame` to keep real-time ordering.
  - Integration tests must **speak only after `session.ready_at`**, as a real caller does after "On call". Earlier audio is dropped by the gate.
- **Test the browser side yourself; don't rely on the user.** Drive the real UI with `playwright-core` + installed Chrome (`C:\Program Files (x86)\Google\Chrome\Application\chrome.exe`) and the flags `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<speech.wav>`. Generate the WAV with Windows System.Speech. Keep Playwright in the scratchpad until it's added to the repo in Milestone 8.
- Voice code lives in `backend/app/voice/`: `router.py` (signaling, validation, `/voice/status` readiness), `sessions.py` (lifecycle, concurrency cap, 30 s connect watchdog, transcript/latency/errors), `bot.py` (pipeline), `processors.py` (`LiveEdgeAudioGate`, `VADSpeakingBridge`, `SessionRecorder`). Service factories live in `backend/app/services/` (`stt.py`). Add each new service's credential check to `missing_voice_credentials()`. `run_voice_bot(connection, session)` is the single place where the pipeline is built.

## Repository Layout & Commands

```
backend/    FastAPI + Pipecat agent (Python 3.12, uv). app/ = code, tests/ = pytest
frontend/   Next.js + TypeScript
supabase/   SQL migrations + seed (Milestone 6)
```

| Task | Command (run in `backend/`) |
|---|---|
| Install / sync deps | `uv sync` |
| Run API | `uv run uvicorn app.main:app --reload --port 8000` |
| Tests | `uv run pytest` |
| Lint / format | `uv run ruff check .` / `uv run ruff format .` |

| Task | Command (run in `frontend/`) |
|---|---|
| Dev server | `npm run dev` (http://localhost:3000) |
| Lint / types / build | `npm run lint` / `npx tsc --noEmit` / `npm run build` |
| Build while the user's dev server runs | `NEXT_DIST_DIR=.next-verify npm run build` (separate output folder, git-ignored) |

- **The user usually has `npm run dev` and the backend running.** Never build into `.next/` and never `npm install` without telling them first, because it disrupts their dev server. Test servers go on ports 8010/3010, and you must confirm they're stopped afterwards.
- Browser-only code (Pipecat client and transport) is loaded with `next/dynamic` + `ssr: false` (see `components/call/call-screen.tsx`).

- Frontend is **Next.js 16.4** with Cache Components enabled. Read `frontend/AGENTS.md` and the bundled docs in `frontend/node_modules/next/dist/docs/` before writing Next.js code, because APIs differ from older versions.
- The browser calls same-origin `/api/*`, which `next.config.ts` rewrites to FastAPI via the server-only `BACKEND_URL`. Keep backend routes under `/api`.

### Coding standards
- **Python:** type hints everywhere. Async I/O. Pydantic models for every request and response. Configuration only through `app/config.py` (`Settings`, with secrets as `SecretStr`). Log with `loguru` (`from loguru import logger`), never `print`. Inject FastAPI dependencies with `Annotated[..., Depends(...)]`. Ruff must pass (line length 100).
- **TypeScript:** strict mode. No secrets in the frontend and no `NEXT_PUBLIC_` API keys.
- New backend integrations go in their own module (for example `app/services/stt.py`), not inside route handlers.

---

## Architecture

```
Customer
  → Next.js frontend
  → SmallWebRTC transport
  → Pipecat voice pipeline
       ├─ Deepgram STT        (voice → text)
       ├─ Groq LLM            (understanding + decisions)
       ├─ Reservation tools   → FastAPI business logic → Supabase
       └─ ElevenLabs TTS      (text → voice)
  → Customer hears the response
```

- The pipeline runs continuously for the whole session and is event-driven and streaming.
- The frontend receives session status, transcript updates, and audio playback events.
- Keep audio processing **separate** from dashboard and reservation-management logic.
- Keep STT, LLM, TTS, and database integrations in **separate service modules**.

### Real-time voice requirements (top priority)
Implement streaming STT and TTS, VAD, turn detection, **barge-in** (cancel in-progress speech when the caller interrupts), silence handling, audio buffering and playback management, conversation state, and session cleanup.

**Anti-pattern to avoid:** record the whole message → upload → process → generate the full reply → play it.

Do not promise zero latency. **Measure** end-to-end latency (STT, LLM, TTS, total) and optimize it.

---

## Reservation Tools (LLM-callable)

Every tool validates its input and returns structured output.

| Tool | Inputs | Returns / behavior |
|---|---|---|
| `get_restaurant_info()` | none | Name, address, hours, contact details, services |
| `check_availability()` | date, preferred time, guest count | Availability status, available slots, suitable tables |
| `get_available_tables()` | guest count (+ date/time) | Tables that can seat the party |
| `create_reservation()` | name, phone (if the restaurant requires it), date, time, guests, special requests | Call **only after the caller explicitly confirms** the details. The backend **re-validates availability** before committing. |
| `get_reservation()` | booking reference or customer info | The existing booking |
| `cancel_reservation()` | booking identifier | Cancel only after confirming the caller's intent and verifying the booking |

## Conversation Design

The persona is a professional, friendly receptionist. A typical flow: greet → party size → time → check availability → get the name → read back the details → caller confirms → book → confirm by voice.

- Handle incomplete sentences, corrections, missing information (ask follow-up questions), ambiguity, silence, background noise, misunderstandings, and API failures.
- Offer alternative slots when the requested time is unavailable.
- Answer basic questions about the restaurant.
- **Do not hardcode the sample script.** The conversation must stay dynamic.

---

## Frontend Screens

1. **Dashboard:** total reservations, today's reservations, total voice sessions, successful bookings, recent conversations, recent reservation activity.
2. **Live Voice Assistant** (looks like a phone call): start/end buttons, mic status, AI-speaking and customer-speaking indicators, audio visualization, live transcript, session timer, connection status, conversation status.
3. **Reservations:** customer, date, time, guests, status, booking reference. Authorized users can view, create, and cancel.
4. **Restaurant Settings:** name, hours, tables and capacities, reservation rules, restaurant info, AI personality.
5. **Call Logs:** session ID, start/end time, transcript, status, the reservation created (if any), errors.

## Database (Supabase PostgreSQL)

Tables: `restaurants`, `restaurant_hours`, `restaurant_tables`, `customers`, `reservations`, `voice_sessions`, `conversation_messages`, `assistant_settings`.

- Define PKs, FKs, indexes, unique booking references, CHECK-constrained reservation statuses, and timestamps.
- Prevent overlapping bookings at **both** levels: backend validation **and** database constraints (for example, exclusion constraints and transactions).
- Store timestamps consistently and handle the restaurant's local timezone correctly.
- Ship the schema as migrations, plus seed data for the demo restaurant.

---

## Engineering Rules

1. Python for the backend and agent; TypeScript for the frontend.
2. Modular architecture with one service module per integration.
3. Use async where appropriate. Stream audio instead of request/response.
4. **Never hardcode API keys.** Use environment variables and maintain `.env.example`.
5. **Never expose secrets to the browser.**
6. Use structured logging and meaningful exception handling.
7. Validate every API input.
8. Write tests for important business logic (availability, conflicts, booking, cancellation).
9. Avoid unnecessary dependencies, frameworks, and abstractions.
10. **No paid services without explicit approval.** No Twilio, Vapi, Retell AI, or similar platforms.
11. No cloud-hosted deployment dependency. The app must stay locally runnable.

### Free-tier awareness
Deepgram, Groq, and ElevenLabs quotas are limited and can change, so do not assume unlimited usage. If a quota runs out, **report it clearly** and never switch silently to a paid service. If a requirement cannot be met with free resources, explain why and propose the closest free alternative **before** changing the architecture.

---

## Milestones (work strictly in order)

Do not build everything at once. Complete, test, and verify each milestone before starting the next.

| # | Milestone | Deliverable |
|---|---|---|
| 0 | Planning and foundation: verify docs and versions, repo structure, env config, coding standards, README | Frontend and backend run locally |
| 1 | Real-time voice infrastructure: Pipecat + SmallWebRTC, mic access, session lifecycle, connection status | Two-way browser ↔ agent audio works |
| 2 | Deepgram integration: streaming STT, speech boundaries, noise testing, error handling | Live transcription |
| 3 | Groq integration: system prompt, context, tool calling, missing information, corrections | An agent that responds intelligently |
| 4 | ElevenLabs integration: voice config, streaming, transport hookup, interruption and cancellation | An agent that speaks naturally |
| 5 | Full conversation engine: STT + LLM + TTS, turn-taking, barge-in, silence and ambiguity handling, error recovery | Natural real-time two-way conversation |
| 6 | Database and reservation engine: schema, seed, availability, table selection, create, lookup, cancel, conflict prevention, LLM tool wiring | Real availability checks and real bookings |
| 7 | Frontend and dashboard: every screen, visualization, transcript, backend integration | A complete professional web app |
| 8 | Testing and debugging: voice (speeds, noise, silence, accents, interruptions, long calls), AI (missing or invalid info, corrections, unavailable slots, tool failures), booking (duplicates, conflicts, invalid counts, cancellation, DB failures), performance (latency per stage, playback, CPU/memory) | A stable app with known limitations documented |
| 9 | Demo prep: demo restaurant, sample reservations, example conversations, quota check, startup instructions, no exposed keys, demo workflow | Locally runnable and ready for the client demo |

### Per-milestone workflow
At the start of each milestone:
1. State the objective.
2. List the files to create or modify.
3. Explain the implementation plan.
4. Implement it.
5. Run the relevant tests.
6. Debug critical issues.
7. Verify the deliverables.
8. Update the README and docs.
9. Give a concise completion report.

- **Do not move to the next milestone** until the current one is implemented and verified.
- If an API key or account setup is needed, say exactly what is required and **wait** for it.
- **Never fabricate test results** or claim something works without verifying it.

---

## Final Deliverables

Frontend app · FastAPI backend · Pipecat agent · Deepgram STT · Groq LLM · ElevenLabs TTS · SmallWebRTC transport · Supabase schema and migrations · reservation tools · dashboard · live voice UI · call logs and reservation management · unit and integration tests · `.env.example` · README · local setup guide · troubleshooting guide · client demo instructions.

**End goal:** a production-minded, modular, real-time voice receptionist. Callers speak naturally, interrupt, ask questions, and book a table through the browser mic, backed by real database operations and solid error handling.
