# Codebase Map

## Frontend

- `frontend/src/main.tsx`: React application bootstrap.
- `frontend/src/App.tsx`: minimal BolHire application shell and Task 1 placeholder.
- `frontend/src/styles.css`: plain CSS for the shell.
- `frontend/src/vite-env.d.ts`: Vite TypeScript environment declarations.
- `frontend/vite.config.ts`: Vite development configuration.

## Backend

- `backend/app/main.py`: FastAPI application factory, health endpoint, and stable error handlers.
- `backend/app/api/routes.py`: `POST /api/v1/chat/text` route.
- `backend/app/models.py`: text chat request, response, and conversation-message models.
- `backend/app/providers/llm.py`: replaceable LLM protocol and deterministic local mock provider.
- `backend/app/services/pipeline.py`: text conversation orchestration and stage timing.
- `backend/app/services/sessions.py`: bounded, lock-protected in-memory session store with lazy expiry.
- `backend/tests/test_health.py`: health endpoint regression test.
- `backend/tests/test_text_chat.py`: text API, session, provider, failure, and concurrency tests.
- `backend/pyproject.toml`: Python dependencies and pytest configuration managed by uv.

## Local configuration

- `.env.example`: frontend API base URL and backend CORS-origin placeholders.
- `.gitignore`: local environments, dependency directories, and generated output.
