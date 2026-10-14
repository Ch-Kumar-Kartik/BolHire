# Progress

## MVP 0, Task 2: Text Conversation Pipeline

Status: complete and validated locally.

Implemented:

- `POST /api/v1/chat/text` with Pydantic request and response models.
- Deterministic local mock LLM with no network or credentials.
- Single-process, in-memory session history, lazy 30-minute expiry, 20-exchange cap, and per-session request serialization.
- Stable JSON error envelopes for malformed requests, validation errors, missing or expired sessions, and mock-provider failures.
- Automated tests for the text pipeline and the health endpoint.

Outstanding MVP 0 work begins at Task 3: browser audio recording and genuine WAV resampling. No recording, voice APIs, real LLM, Gnani, database, authentication, or deployment infrastructure has been implemented.

## MVP 0, Task 1: Scaffold

Status: complete and validated locally.

Implemented:

- React, Vite, and TypeScript frontend shell with plain CSS.
- FastAPI application with `GET /api/v1/health` returning `{"status":"ok"}`.
- Pytest and HTTPX health endpoint test.
- Root development configuration and ignore rules.

Validation commands:

```sh
cd frontend && npm run build
cd backend && uv run pytest
```

Local development:

```sh
# Terminal 1
cd backend && uv sync && uv run uvicorn app.main:app --reload

# Terminal 2
cd frontend && npm install && npm run dev
```
