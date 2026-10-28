# Task 9.1 database foundation

PostgreSQL storage is an internal foundation only. No save, authentication, or resume routes exist yet. Conversation history, candidate-intake behavior, provider selection, and speech remain unchanged. The authentication table contains only future credential storage structure; no authentication logic is implemented.

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

- `candidates`: application-generated UUID, unique 32-byte recovery verifier supplied by a future caller, creation timestamp.
- `candidate_profiles`: one row per candidate, cascading foreign key, all existing profile fields, positive revision, consent notice version, consent/creation/update/expiry timestamps.
- `candidate_auth_sessions`: UUID, cascading owner foreign key, unique 32-byte token verifier, creation/expiry timestamps. Schema only.

Experience uses double precision; timestamps use TIMESTAMPTZ. Skills and declined fields use PostgreSQL text arrays. `NULL` skills map exactly to `None`; an empty array maps to `()`; ordered skills retain their order. Declined fields map to a frozenset. Writes and reads use the existing strict `CandidateProfile` validation without changing it. Database constraints additionally reject malformed arrays, null array elements, overlength values, unknown modes/declines, invalid numeric ranges, conflicting declines, invalid hashes, and invalid lifecycle timestamps. Exact Unicode case folding remains a Python rule; SQL checks reject exact duplicate skills, and reads reject data that violates full Pydantic validation.

`CandidateRepository` provides atomic candidate-plus-profile creation, active-profile retrieval, conditional whole-profile replacement, and cascading candidate deletion. Callers supply consent metadata and timezone-aware expiry timestamps; the repository does not choose a retention policy or authenticate callers. UUID possession is not authorization. No conversation, transcript, evidence, or audio is stored.

Replace requires the expected revision and increments it in the same conditional SQL update. Concurrent saves of one revision yield one winner and one conflict. Missing and expired profiles share a safe not-found error and cannot be revived through replacement. Every read/update filters expiry using PostgreSQL `statement_timestamp()`; eligibility is evaluated at statement start. Reads do not refresh activity/retention or physically purge data. Database transaction atomicity applies only to database writes, independently of the existing in-memory session atomicity.

Constraint/data failures roll back with a fixed repository error. Connectivity failures return a separate fixed error; raw SQL exceptions are not propagated. Retry after an uncertain commit requires later save/idempotency design; no HTTP save flow is included here.
