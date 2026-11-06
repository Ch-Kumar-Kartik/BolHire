# Task 9.1 database foundation

PostgreSQL storage is optional. The candidate-facing routes are the Task 9.2 authentication routes and the Task 9.3 save and retrieval routes (see the sections below). Profile editing, deletion, and renewal do not exist. Conversation history, candidate-intake behavior, provider selection, and speech remain unchanged.

## Configuration and lifecycle

`DATABASE_ENABLED` defaults to `0`; only `0` and `1` are accepted. Disabled mode ignores `DATABASE_URL`, creates no engine, and makes no connection attempts. Task 8 development and tests work without PostgreSQL.

When enabled, `DATABASE_URL` must use `postgresql+psycopg://` with a host and database. Keep it server-side in an ignored environment file. Hosted PostgreSQL will require TLS, but no Neon setup or connection is part of this milestone. URLs and credentials are excluded from configuration repr and SQL parameter logging. Do not enable SQL/body logging or log raw database exceptions; PostgreSQL server logging needs equivalent privacy controls before storing real candidate information.

FastAPI creates one engine during lifespan, checks connectivity within five seconds, and disposes it on shutdown or failed startup. The pool has five connections, no overflow, a five-second checkout timeout, and connection liveness checks. Explicit enablement with an unavailable database fails startup with a fixed safe error. No schema creation or migration runs on startup. Each repository operation owns an independent async session and transaction.

## Disposable local PostgreSQL 17

Create an ignored repository-root `.env.test` containing a synthetic local password and matching URLs (URL-encode special characters in passwords):

```dotenv
TEST_POSTGRES_PASSWORD=<disposable-local-password>
DATABASE_ENABLED=0
DATABASE_URL=postgresql+psycopg://bolhire_test:<encoded-password>@127.0.0.1:55432/bolhire_test
TEST_DATABASE_URL=postgresql+psycopg://bolhire_test:<encoded-password>@127.0.0.1:55432/bolhire_test
```

From the repository root:

```sh
docker compose --env-file .env.test -f compose.test.yaml up -d --wait
```

The container exposes only loopback port 55432. Its PostgreSQL data uses tmpfs and is disposable; do not use it for real candidate data or expect it to survive container restarts. Persistence tests recreate application engines while keeping PostgreSQL running.

## Migrations and tests

From `backend`:

```sh
uv sync --locked
uv run --env-file ../.env.test python -m alembic upgrade head
uv run --env-file ../.env.test python -m alembic check
uv run python -m pytest -q tests/test_database_config.py tests/test_candidate_mapping.py
uv run --env-file ../.env.test python -m pytest -q tests/integration
uv run --env-file ../.env.test python -m pytest -q
```

The migration CLI intentionally reads `DATABASE_URL` even when application support is disabled. Credentials are never written into `alembic.ini`. The initial migration is an explicit SQL snapshot independent of live ORM models and includes array-validation functions. Future migrations must preserve that independence.

Integration fixtures require PostgreSQL 17, a numeric loopback host or `localhost`, a database name ending in `_test`, and no incoming URL options. Each test creates a unique schema, applies real Alembic migrations, then removes only that schema. Without `TEST_DATABASE_URL`, integration tests are explicitly skipped; setting an invalid or unavailable test URL fails rather than pretending a successful database test. Never point the test suite at an application database.

Downgrade/upgrade tests run in these isolated schemas. A manual `alembic downgrade base` destroys all three foundation tables in the configured schema and is appropriate only for disposable data. Do not use ORM `create_all()` to initialize the schema.

To stop the disposable service, from the root:

```sh
docker compose --env-file .env.test -f compose.test.yaml down
```

## Schema, mapping, and repository semantics

- `candidates`: application-generated UUID, unique 32-byte recovery verifier (a domain-separated SHA-256 of the recovery key; the key itself is never stored), creation timestamp. It has no expiry of its own: an identity lives exactly as long as its profile.
- `candidate_profiles`: one row per candidate, cascading foreign key, all existing profile fields, positive revision, consent notice version, consent/creation/update/expiry timestamps.
- `candidate_auth_sessions`: UUID, cascading owner foreign key, unique 32-byte token verifier (SHA-256 of the cookie token), creation/expiry timestamps. Used by `AuthRepository` since Task 9.2.

Experience uses double precision; timestamps use TIMESTAMPTZ. Skills and declined fields use PostgreSQL text arrays. `NULL` skills map exactly to `None`; an empty array maps to `()`; ordered skills retain their order. Declined fields map to a frozenset. Writes and reads use the existing strict `CandidateProfile` validation without changing it. Database constraints additionally reject malformed arrays, null array elements, overlength values, unknown modes/declines, invalid numeric ranges, conflicting declines, invalid hashes, and invalid lifecycle timestamps. Exact Unicode case folding remains a Python rule; SQL checks reject exact duplicate skills, and reads reject data that violates full Pydantic validation.

`CandidateRepository` provides atomic candidate-plus-profile creation, active-profile retrieval, conditional whole-profile replacement, and cascading candidate deletion. Callers supply consent metadata and timezone-aware expiry timestamps; the repository does not choose a retention policy or authenticate callers. UUID possession is not authorization. No conversation, transcript, evidence, or audio is stored.

Replace requires the expected revision and increments it in the same conditional SQL update. Concurrent saves of one revision yield one winner and one conflict. Missing and expired profiles share a safe not-found error and cannot be revived through replacement. Every read/update filters expiry using PostgreSQL `statement_timestamp()`; eligibility is evaluated at statement start. Reads do not refresh activity/retention or physically purge data. Database transaction atomicity applies only to database writes, independently of the existing in-memory session atomicity.

Constraint/data failures roll back with a fixed repository error. Connectivity failures return a separate fixed error; raw SQL exceptions are not propagated. `create` is not idempotent and is kept for tests and Task 9.1 only. Routes use `register` (Task 9.3, below), which is safe to retry after an uncertain commit.

## Candidate authentication (Task 9.2)

Authentication runs only when the database is enabled; otherwise every auth route returns 503 `CANDIDATE_STORAGE_DISABLED` and chat is unaffected. Configuration (read only when `DATABASE_ENABLED=1`):
- `AUTH_TRUSTED_ORIGIN`: the exact browser origin, e.g. `https://app.example.com`. Default ports (`:443` for https, `:80` for http) are dropped, because browsers never send them. Paths, credentials, port 0, and an empty port are rejected. Unset rejects every state-changing request. A non-loopback `http` origin is rejected.
- `AUTH_COOKIE_SECURE`: default `1`. `0` is accepted only together with an `http` origin on a loopback host (local development) and drops the `__Host-` cookie prefix; any other combination fails at startup.
- `AUTH_TRUSTED_PROXY_HOPS`: default `0`. See the rate-limit deployment gate below before changing it.

- **Credentials:** a recovery key is 128 random bits shown as 26 Crockford-base32 characters; a session token is 256 random bits carried only in the `HttpOnly; Secure; SameSite=Strict` cookie. Only SHA-256 verifiers with distinct domain prefixes are stored.
- **Lifecycle:** identity lifetime equals profile lifetime. Login and session resolution require an unexpired profile using the database clock, so expired identities are inaccessible even if cleanup is delayed. Sessions have a fixed 7-day maximum with no renewal. Logout deletes the row and is idempotent. Logging in again does not revoke earlier sessions (accepted for the MVP): the previous session stays valid until it expires or is logged out.
- **Origin protection:** one router-level guard, `enforce_trusted_origin`, requires an exactly matching `Origin` header on every method except GET/HEAD/OPTIONS. Install it on every router that holds state-changing candidate routes.
- **Identity creation** is `CandidateRepository.register` (Task 9.3, below). The 9.1 `create` is not retry-safe and is not used by routes.
- **Cleanup:** run `uv run python -m scripts.purge_expired_candidates --check` to validate configuration offline, then `--run --confirm-delete` from a scheduled job (Railway scheduling is unconfirmed). It is safe to run concurrently and repeatedly and skips rows locked by other transactions. Output is counts only. A failure exits 2 and prints the stage (`connecting` or `purging`) and the exception class name; the message is withheld because driver errors can contain hosts or credentials, so diagnose further with `--check` and a separate connectivity test.
- **Never log or expose** recovery keys, tokens, cookies, or verifiers. Keys travel only in JSON request bodies, never in URLs or headers.
- **Login rate limit:** one per-client bucket, process-local, best-effort load protection; key entropy is the real defence. Deployment gate (nothing is established yet):
  - The real forwarding-header chain between the browser, Vercel, and the backend must be observed before any hop is trusted.
  - With `AUTH_TRUSTED_PROXY_HOPS=0` behind a proxy, all users share one bucket and a single caller can block everyone's login.
  - Setting the hop count does not make a directly reachable backend safe: a caller who reaches the Railway URL directly can forge the entries to the left of the trusted ones. Whether direct access must be restricted is a separate deployment decision.
  - Several workers or instances multiply the limit.

## Candidate profile save and retrieval (Task 9.3)

Demo scope only; see `docs/progress.md` for the limits. Routes (all under `/api/v1/candidate`, all state-changing ones behind the Origin guard, none accepting a candidate id): `POST /intake/preview`, `POST /recovery-key`, `POST /profile` (save), `GET /profile` (retrieval through the session cookie).

- **Key:** generated by the server, bound to the in-memory intake session as a SHA-256 verifier only; a reissue replaces it. The plaintext appears only in the issue response and the later save/login request bodies.
- **Save:** the profile comes from the server-side session, never the request. The client supplies the session id, key, profile digest, consent version and an explicit `consent_accepted: true`. The save is idempotent per key: a retry with an active identity returns `created: false` and writes nothing, even after the intake session was discarded.
- **Retention:** 90 days from the successful save, computed in the same SQL statement as `created_at` (database clock). Login, save retries, and profile reads never renew it; there is no renewal route. Expired profiles are inaccessible at once through the login/session joins and `get`, whether or not cleanup has run.
- **Collisions:** a key whose identity is expired but not yet deleted by cleanup fails closed (`RECOVERY_KEY_UNAVAILABLE`, 409); nothing is reclaimed or overwritten. After cleanup deletes the row the key can register as a brand-new identity with none of the old data. Keys are server-issued 128-bit values, so this needs a retry that outlives retention or a 2^-128 coincidence.
- **Isolation:** the engine pins `READ COMMITTED`.
- **Single process:** pending intake sessions and key verifiers are in memory. Run one backend process and not `uvicorn --reload` while using the flow.

### Local demo database

Persistent, loopback-only, separate from the tmpfs test database. Create an ignored repository-root `.env.demo` (mode 600) with a local password: `DEMO_POSTGRES_PASSWORD`, `DATABASE_ENABLED=1`, `DATABASE_URL=postgresql+psycopg://bolhire_demo:<password>@127.0.0.1:55433/bolhire_demo`, `AUTH_TRUSTED_ORIGIN=http://localhost:5173`, `AUTH_COOKIE_SECURE=0`. Then, from the repository root:

```sh
docker compose --env-file .env.demo -f compose.demo.yaml up -d --wait
cd backend && uv run --env-file ../.env.demo python -m alembic upgrade head
uv run --env-file ../.env.demo python -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # no --reload; the module form because a moved virtualenv can leave `.venv/bin/uvicorn` with a stale interpreter path
```

In `frontend/.env.local` (ignored) set `VITE_API_BASE_URL=` (empty) and run `npm run dev`: the Vite server proxies `/api` to `127.0.0.1:8000`, so the browser uses one origin and the session cookie needs no CORS credentials. Candidate Intake extraction needs `LLM_PROVIDER=gemini` with a key; with the default mock provider the profile stays empty and cannot be saved. For a real voice recording (Gemini plus Gnani, both billable) pass both env files with the project `.env` last, since the later file wins: `uv run --env-file ../.env.demo --env-file ../.env python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` (checked offline: it selects the Gemini provider and Gnani speech with the database and auth settings from `.env.demo`; no request was made). `AUTH_COOKIE_SECURE=0` is accepted only with an `http` loopback origin.
