# Progress

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

Outstanding MVP 0 work begins at Task 2: the text conversation path. No recording, voice APIs, sessions, LLM, Gnani, database, authentication, or deployment infrastructure has been implemented.
