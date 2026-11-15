# Glow Atelier AI Customer Service Agent

**English** | [简体中文](README.zh-CN.md)

An AI customer service agent for **Glow Atelier 光研美学**, a beauty salon in SS2, Kuala Lumpur.
Customers can ask questions, book, reschedule or cancel through **web chat** or **email**. The AI answers from the company knowledge base and hands anything it cannot handle to a **staff dashboard**.

> A portfolio project. The focus is not just "it can chat", but: **important rules are enforced in code, every feature has tests and evaluations, and failures alert a human.**

---

## Features

### For customers

| Feature | Description |
|---|---|
| Q&A | Prices, services, opening hours, aftercare… The agent searches the knowledge base first and cites its sources; if the answer is not there, it says so |
| Booking | Check availability → collect details → write only after the customer confirms; assigns a free beautician, adds the booking to Google Calendar and sends a confirmation email |
| Reschedule / cancel | Requires the booking code + phone number, then an **OTP code** sent to the customer's email |
| Email support | Mail sent to the company inbox gets an automatic AI reply (switch on the dashboard, off by default) |
| Human handoff | Complaints, allergic reactions, refunds or requests for a human open a ticket for salon staff |
| Language | Answers in Chinese and understands Malaysian Chinese–English mixing ("这个 package 几钱？", "可以 book 明天吗？") |

### For staff (login required)

| Feature | Description |
|---|---|
| Handoff dashboard | Ticket list with an AI-written summary and the full conversation; accept → resolve |
| Booking calendar | Bookings for the next 30 days, synced with Google Calendar |
| Email switch | Turn email auto-replies on or off |
| Telegram notifications | New tickets and system errors arrive on the phone immediately |

---

## Technical highlights

**RAG (retrieval-augmented generation)**
- Knowledge base split into 69 chunks, embedded with Gemini (768 dimensions) and stored in Supabase pgvector
- **Hybrid search**: vector + Chinese keyword search (PGroonga), merged with RRF
- Retrieval evaluation on 60 questions (50 answerable, 10 deliberately unanswerable): Hit@5 = 1.00 on the answerable ones

**Function calling**
- Gemini decides which tool to use: search knowledge base, check availability, find bookings, book, reschedule, cancel, confirm, hand off (8 tools)
- The tool loop is run manually (not by the SDK), so every step is visible and recorded

**Guardrails: important rules are enforced in code, not only in the prompt**
- **Two-step confirmation**: booking / rescheduling / cancelling only runs after the customer has seen a summary and agreed in the next message
- **OTP codes**: stored only as hashes, valid for 10 minutes, at most 5 attempts
- **Database-level double-booking protection**: an exclusion constraint, so the same beautician can never be booked twice, even with several servers
- **False-claim protection**: if an email reply says "transferred to a human" but no ticket exists, code opens one before sending
- **Prompt-injection defence**: the AI never reads spam; secrets never enter the prompt; error messages never leak internal details

**Reliability**
- **Automatic model fallback**: when a free-tier quota runs out or a model is overloaded, the next model is used; it switches back once the quota resets
- **Calendar retry**: a Google Calendar failure does not affect the booking; a background job fills it in every minute
- **Streaming (SSE)**: text appears as it is generated; after 60 seconds without progress the customer is told the system is slow

**Quality and operations**
- **195 automated tests** (pytest, offline, no real services) + **GitHub Actions** on every push
- **35 agent scenario evaluations** (real Gemini): booking, handoff, trick questions, privacy, prompt injection
- **Langfuse tracing**: every model call, tool call and token count for each question
- **Telegram alerts**: ERROR logs are sent automatically (phone numbers and emails masked); health check at `/api/health`

---

## Tech stack

| Part | Technology |
|---|---|
| Backend | Python 3.14 · FastAPI |
| AI model | Gemini (gemini-3.6-flash first, automatic fallback) |
| Embeddings | gemini-embedding-001 (768 dimensions) |
| Database | Supabase: Postgres + pgvector + PGroonga |
| Staff login | Supabase Auth |
| Booking / email | Google Calendar API · Gmail API |
| Frontend | React 19 · Vite · TypeScript |
| Observability | Langfuse · Python logging · Telegram Bot |
| CI | GitHub Actions |

---

## Architecture

```
            Customers                               Staff
        ┌──────┴──────┐                    ┌──────┴──────┐
     Web chat        Email              Dashboard      Telegram
   (streaming)  (dedicated Gmail)    (login required)  (alerts)
        │              │                     │             ▲
        ▼              ▼                     ▼             │
   ┌───────────────────────────────────────────────────────┴──┐
   │  FastAPI backend                                          │
   │   ┌──────────────────────────────┐   Background jobs:     │
   │   │ Gemini agent (tool loop)      │   · email auto-reply   │
   │   │ Guardrails: 2-step confirm,   │   · calendar retry     │
   │   │ OTP, booking rules            │                        │
   │   └──┬────────┬────────┬────────┬┘                        │
   │      ▼        ▼        ▼        ▼                         │
   │     RAG    Booking   Email   Handoff                      │
   └──────┬────────┬────────┬────────┬────────────────────────┘
          ▼        ▼        ▼        ▼
      Supabase  Google   Gmail   Supabase        Langfuse (tracing)
      (vectors) Calendar  API    (tickets)
```

---

## Running locally

Requirements: Python 3.14, Node.js, and `backend/.env` (Gemini, Supabase and Google settings; never committed).

```bash
# Backend (from the project root)
.venv\Scripts\python -m uvicorn api:app --app-dir backend --port 8010
# → http://localhost:8010/docs to try every API

# Frontend (in a second terminal)
npm --prefix frontend install
npm --prefix frontend run dev
# → http://localhost:5180

# Automated tests (inside backend/, offline, about 2 seconds)
python -m pytest -q

# Agent scenario evaluation (inside backend/, uses Gemini quota)
python -m evals.agent_eval
```

---

## More details

[RAG-blueprint.md](RAG-blueprint.md) (in Chinese): how and why each RAG step was built, evaluation results, problems found along the way, **design decisions**, and interview Q&A.
