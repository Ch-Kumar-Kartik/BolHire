# BolHire

A voice-first, multilingual (English and Hindi) job-matching app for candidates in India. Speak about yourself, save a profile, and see explainable matches against a job catalogue.

**Stack:** React + TypeScript + Vite · FastAPI + Python (uv) · PostgreSQL 17 + Alembic · Gemini (LLM) · Gnani (speech-to-text and text-to-speech) · deterministic, rule-based matching

## Run locally

Quick start, with mock providers and no database:

```bash
cd backend && uv sync && uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend && npm install && npm run dev        # http://localhost:5173
```

Full demo with saved profiles and job matches (set `VITE_API_BASE_URL=` empty in `frontend/.env.local`):

```bash
docker compose --env-file .env.demo -f compose.demo.yaml up -d --wait
cd backend
uv run --env-file ../.env.demo python -m alembic upgrade head
uv run --env-file ../.env.demo python -m scripts.seed_job_catalogue --run --confirm-seed
uv run --env-file ../.env.demo python -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # no --reload
```

## Optional: real listings (local demo only)

- Import an export file: `python -m scripts.ingest_jobs --check <file>`, then `--run --confirm-ingest <file>`. Keep the file in `local_data/` and never commit it.
- "Find live jobs" needs `APIFY_TOKEN` in the backend environment. It is capped at 25 listings per fetch and 5 fetches per backend run.

## Tests

```bash
cd backend && uv run python -m pytest -q                                   # offline
docker compose --env-file .env.test -f compose.test.yaml up -d --wait
cd backend && uv run --env-file ../.env.test python -m pytest -q           # with a disposable PostgreSQL
cd frontend && npm test && npm run build
```

## More

[Progress](docs/progress.md) · [Database development](docs/database-development.md) · [Architecture](docs/architecture.md)

## Deployment configuration

Keep `BOLHIRE_DEV_PROFILE_INSPECTION` unset or explicitly set to `0` in every deployment. Never deploy with the local profile inspector enabled. It is only for direct local development verification; see [local verification instructions](docs/local-profile-verification.md).
