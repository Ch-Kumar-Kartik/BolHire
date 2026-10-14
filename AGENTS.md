# AGENTS.md

## Project
A multilingual, voice-first recruitment platform for Indian candidates and recruiters, inspired by Jack & Jill AI.

## Stack
- Frontend: React, Vite, TypeScript
- Backend: FastAPI, Python
- Speech APIs: Gnani Prisma v2.5 (STT), Timbre v3.0 (TTS)
- LLM: Provider-agnostic external API
- Database (later): Neon PostgreSQL
- Deployment (later): Vercel frontend, Railway backend

## Required Context
Before implementing a task:
1. Read `docs/progress.md`.
2. Read the relevant MVP specification.
3. Consult `docs/architecture.md` for architectural decisions.
4. Inspect relevant source files before editing.

Do not read the entire repository unless necessary.

## Engineering Rules
- Implement only the current approved milestone.
- Prefer the simplest maintainable solution.
- Keep external API integrations in dedicated provider modules.
- Keep secrets server-side and out of source control.
- Use clear types, Pydantic models, and explicit error handling.
- Write tests for important backend logic.
- Mock external API calls in automated tests.
- Do not introduce Redis, queues, microservices, or new frameworks without justification.
- Do not make real billable API requests unless explicitly authorized.
- Do not deploy or modify production infrastructure without approval.
- Ask before changing architecture or adding major dependencies.

## After Implementation
1. Run relevant tests, linting, and type checks.
2. Report what passed and what failed.
3. Update `docs/progress.md`.
4. Update `docs/codebase-map.md` if module responsibilities changed.
5. Summarize files modified, decisions made, and known issues.
6. Stop at the milestone boundary and wait for approval.

## Documentation Policy
- Keep documentation concise and accurate.
- Do not claim untested functionality works.
- Source code is the ultimate authority for implementation behavior.
- Never store API keys or personal candidate data in documentation.