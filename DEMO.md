# Client Demo Guide

How to run a smooth 10-15 minute demo of the AI voice receptionist for **Olive & Ember**, a fictional Mediterranean grill in Lahore. Its assistant is called **Sara**.

---

## 1. The day before

- [ ] **Check the free quotas:** `cd backend` then `uv run python -m app.quota`.
  - **ElevenLabs** (the voice) is the scarce one. Each demo call costs about **300-400 credits**, so keep at least ~1,500 free for a demo with a warm-up and a couple of retries. Usage: https://elevenlabs.io/app/usage (resets on the 7th of each month).
  - **Groq** allows 1,000 requests a day (one call uses ~10), and **Deepgram** has a free credit balance. Neither is usually a problem.
- [ ] **Optional: clear the development test calls** so the dashboard starts clean. This permanently deletes every call log of the demo restaurant; the reservations those calls made stay:
  `uv run python -m app.db.demo_data --clear-call-logs`
- [ ] **Use headphones** (or a headset). With speakers, the assistant can hear itself through the microphone and interrupt its own reply.

## 2. 30 minutes before

1. **Close heavy programs.** Real-time audio needs a quiet CPU: no data jobs, builds or games. A busy CPU makes the assistant mishear words and reply slowly.
2. **Start everything** from the project folder in PowerShell:

   ```powershell
   .\start-demo.ps1 -RefreshSamples
   ```

   The script:
   - checks the CPU and the quotas;
   - re-dates the 12 sample reservations from today;
   - starts the backend and frontend in two windows;
   - opens http://localhost:3000.

   Building the frontend takes 1-2 minutes. Leave both windows open during the demo. If Windows blocks the script, run `powershell -ExecutionPolicy Bypass -File .\start-demo.ps1 -RefreshSamples`.
3. **Write down the "Full-slot demo" line** the script prints, for example *"ask for 10 people on Sunday 15 November at 20:00"*. You need it for scenario B.
4. **Sign in** with the staff password (`ADMIN_PASSWORD` in `backend/.env`). Check that the dashboard shows today's sample bookings.
5. **Make one short warm-up call** on **Live call**: say "Hi, what time do you open on Friday?", then "Thanks, bye." This also tests your microphone and headphones.

Starting without the script (two terminals):

| Terminal | Command |
|---|---|
| backend | `cd backend` then `uv run uvicorn app.main:app --port 8001` (the port in `frontend/.env.local`) |
| frontend | `cd frontend` then `npm run build`, then `npm run start` |

---

## 3. The demo flow

| Step | Show | Say (talking points) |
|---|---|---|
| 1 | **Dashboard** (2 min) | Today's bookings and guests, upcoming reservations, voice calls, bookings made by voice, charts for the next 7 days. "Staff see everything the assistant does, live." |
| 2 | **Live call** (5-8 min) | Run 2-4 of the scenarios below. Point out the live transcript, the speaking indicators and *Checking availability…* while it uses the database. |
| 3 | **Reservations** (1 min) | The booking just made by voice is there with its reference, table and "via voice". Staff can also create and cancel bookings, with the same rules. |
| 4 | **Call logs** (1-2 min) | Open the call you just made: the full transcript, interruptions marked, the booking it made, and the per-stage timing (speech-to-text, AI, voice). |
| 5 | **Settings** (1 min) | Hours, tables, booking rules and the assistant's name, personality and greeting. Changes apply from the next call. |

### Example scenarios for the live call

Speak naturally: the assistant isn't scripted, so wording and order can vary. Wait for **On call** and the greeting before speaking. Words said before that are not heard.

**A. Book a table (the main one).**
> You: "Hi, I'd like to book a table for four tomorrow evening."
> Sara asks for a time. You: "Around half past seven."
> Sara checks availability and asks for your name. You: "Ali Raza."
> Sara reads everything back. You: "Yes, that's right."
> Sara confirms and reads out a booking reference such as *O-E, dash, K-7-P...*

Then open **Reservations**: the booking is there, made by voice. It's only confirmed after the database saves it; the assistant never claims a booking that didn't happen.

**B. The slot is full, so Sara offers alternatives.** Use the line the start script printed:
> You: "Can I get a table for 10 people on Sunday at 8 PM?"
> Sara: that time is fully booked, and offers the nearest free times (e.g. 6:00, 6:15 or 6:30 PM).
> You: "6:30 works." Then carry on as in A, or say you'll think about it.

**C. Questions about the restaurant.**
> "What are your opening hours on Sunday?" · "Where are you located?" · "Do you have vegetarian options?" · "Is there parking?"

**D. Closed day and party limits.**
> "Can I book for Monday?" Sara says it's closed on Mondays and suggests another day.
> "A table for 15, please." Sara explains that parties over 10 are arranged by email.

**E. Interrupt it (barge-in).** While Sara is speaking a long answer (e.g. after asking about the hours), start talking: *"Sorry, actually, can I book for Saturday instead?"* Sara stops mid-sentence and listens. The transcript marks the reply as *interrupted* and keeps only the words you actually heard.

**F. Change your mind.** In the middle of a booking: *"Actually, make that six people, not four."* Sara updates the details (checking the new party size fits) before reading them back.

**G. Cancel a booking.** In a new call: *"I'd like to cancel my booking, reference O-E-…"* (use the reference from A). Sara reads the booking back and asks you to confirm before cancelling. The Reservations page then shows it as *cancelled*.

**H. Ending the call.** Say *"That's all, thank you, bye!"* Sara says goodbye and hangs up by herself. If you go silent, she asks "Are you still there?" after about 8 seconds and ends the call politely if the silence continues.

**Credit budget:** each scenario call costs ~150-400 ElevenLabs credits. Plan 2-3 calls plus the warm-up.

### Talking points

- **Real-time and streaming:** speech is transcribed as you talk (Deepgram), the AI answers as soon as you finish (Groq), and the voice starts playing before the full reply is written (ElevenLabs). You can interrupt at any time.
- **Real bookings, checked twice:** availability comes from the real database, and double-booking is impossible. It's prevented in the code and again by a database constraint (tested with 5 callers racing for the last table: exactly one wins).
- **Safe by design:** the assistant must read the details back and hear a "yes" before booking. This is enforced in code, not just requested of the AI. If the database fails, the caller is told nothing was booked.
- **Runs locally on free tiers:** browser-to-server audio over WebRTC, with no paid telephony or voice-agent platform. All keys stay on the server.
- **Honest numbers:** a reply typically arrives 1.7-2.9 s after the caller stops speaking; the free-tier AI is the largest part. Paid tiers, or a phone line, would be the next step (see "Known limitations" in the README).

---

## 4. If something goes wrong

| What you see | Do this |
|---|---|
| The call page says **Connecting…** for a long time | Click End, wait 3 seconds, and start again. Start one call at a time (two calls in the same second can stall). |
| **"Bookings are offline"** banner | The database isn't reachable. Check the internet connection, then restart the backend window. |
| The assistant mishears or replies slowly | Check Task Manager for a busy CPU, close heavy programs, and speak a bit more slowly and clearly. |
| *"Sorry, I'm having a little trouble on my end"* | The AI's free-tier rate limit was hit. Wait 20-30 seconds and repeat yourself. |
| The assistant speaks but you hear nothing | Check the browser tab isn't muted and the right output device is selected. |
| It interrupts itself | Use headphones; speakers feed its own voice back into the microphone. |
| "The ElevenLabs free voice credits are used up" | The month's voice credits are gone. Show the dashboard and the recorded call logs instead. Credits reset on the 7th. |

## 5. After the demo

- Close the two server windows.
- Optional: remove the sample bookings with `cd backend` then `uv run python -m app.db.demo_data --remove`. Real bookings are never touched. Samples always have references `OE-DEMO01` to `OE-DEMO12`, which real bookings can't have.
