# Codebase Map

## Frontend

- `frontend/src/main.tsx`: React application bootstrap.
- `frontend/src/App.tsx`: minimal BolHire application shell and Task 1 placeholder.
- `frontend/src/styles.css`: plain CSS for the shell.
- `frontend/src/vite-env.d.ts`: Vite TypeScript environment declarations.
- `frontend/vite.config.ts`: Vite development configuration.

## Backend

- `backend/app/main.py`: FastAPI application and health endpoint.
- `backend/tests/test_health.py`: endpoint health check.
- `backend/pyproject.toml`: Python dependencies and pytest configuration managed by uv.

## Local configuration

- `.env.example`: frontend API base URL and backend CORS-origin placeholders.
- `.gitignore`: local environments, dependency directories, and generated output.
