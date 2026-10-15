# AGENTS.md

## Project

BolHire is a multilingual, voice-first AI recruitment platform for Indian candidates and recruiters, inspired by Jack & Jill AI.

Development follows incremental, independently approved milestones.

## Technology Stack

- Frontend: React, Vite, TypeScript, CSS
- Backend: FastAPI, Python
- Speech: Gnani Prisma v2.5 (STT), Timbre v3.0 (TTS)
- LLM: Provider-agnostic external API
- Database (later): Neon PostgreSQL
- Deployment (later): Vercel frontend, Railway backend

## Session Initialization

At the start of every new Codex session:

1. Read `docs/session-start.md`.
2. Read `docs/progress.md`.
3. Read `docs/codebase-map.md`.
4. Consult `docs/architecture.md` when relevant.
5. Read the relevant MVP specification if available.
6. Check `git status` and recent commits.
7. Inspect source files relevant to the assigned task.

Do not scan the entire codebase without reason. Documentation is a navigation aid, not a replacement for reading actual implementation code.

If documentation and source code disagree, inspect the discrepancy and report it.

## Engineering Rules

- Implement only explicitly approved tasks and milestones.
- Prefer simple, maintainable solutions.
- Follow existing conventions and reuse existing modules.
- Keep external integrations inside dedicated provider modules.
- Keep API keys and credentials server-side.
- Use clear types, Pydantic models, and explicit error handling.
- Write appropriate unit and integration tests.
- Mock billable external APIs in automated tests.
- Avoid unnecessary dependencies and abstractions.
- Do not introduce Redis, queues, microservices, or new frameworks without approval.
- Do not make billable API requests without explicit authorization.
- Do not deploy or modify production infrastructure without approval.
- Ask before significant architectural changes.

## Git Safety

- Inspect existing changes before editing.
- Preserve unrelated work.
- Do not execute destructive Git operations without approval.
- Do not automatically commit, push, or rewrite Git history.
- Summarize repository changes before requesting milestone approval.

## Testing and Verification

After implementing a task:

1. Run relevant automated tests.
2. Run build, lint, and type checks where configured.
3. Report exact commands and results.
4. Identify untested functionality and remaining limitations.
5. Never claim browser or real API testing succeeded without actual verification.

## Documentation and Handoff

After each implementation milestone:

1. Update `docs/progress.md` with implemented functionality, verification results, blockers, and next steps.
2. Update `docs/codebase-map.md` when module responsibilities change.
3. Update architectural documentation only when approved decisions change or need clarification.
4. Keep documentation concise and accurate.
5. Never store secrets, private candidate data, or raw audio in documentation.
6. Stop at the approved milestone boundary and wait for further instructions.

Source code remains the authority for actual implementation behavior.
