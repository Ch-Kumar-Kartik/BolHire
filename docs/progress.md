# Progress

## MVP 1, Task 9.2: Candidate Authentication Primitives

Status: implemented and verified locally against disposable PostgreSQL 17, then remediated after an independent security audit; uncommitted. No schema change or new dependency. Candidate saving remains disabled: no route issues recovery keys, creates identities, or stores consent; those belong to Task 9.3. Profile read/update/delete is Task 9.4 and all frontend integration is Task 9.5. Anonymous General Chat and Candidate Intake are unchanged and never read the auth cookie.

Implemented:
- `app/services/candidate_auth.py`: 128-bit Crockford-base32 recovery keys (case, spacing, and I/L/O aliases tolerated; non-canonical padding rejected), 256-bit opaque session tokens, domain-separated SHA-256 verifiers fitting the existing 32-byte columns, secret wrappers with redacted `repr`/`str`, exact-match Origin check, cookie rules, and `AuthSettings` (`AUTH_TRUSTED_ORIGIN`, `AUTH_COOKIE_SECURE`, `AUTH_TRUSTED_PROXY_HOPS`). Session lifetime is a fixed 7-day maximum (no renewal).
- `app/db/auth_repository.py`: single-statement login that inserts a session only for a key matching an unexpired profile; `resolve` requiring both session and profile to be unexpired; idempotent revocation (one, or all of a candidate's); batched `purge_expired`.
- `app/api/auth.py` and `main.py`: `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/session` (returns `{authenticated}` only), the `require_candidate` principal dependency, and fail-closed handling: 503 `CANDIDATE_STORAGE_DISABLED` when the database is off, 503 `CANDIDATE_STORAGE_UNAVAILABLE` on database errors, 403 on any Origin mismatch or when `AUTH_TRUSTED_ORIGIN` is unset, a generic 401 `INVALID_RECOVERY_KEY` for unknown and expired keys alike, 422 `MALFORMED_RECOVERY_KEY` without echoing input, and 429 with `Retry-After`. Logout and stale-cookie 401s clear the cookie; auth responses are `no-store`.
- `app/services/rate_limit.py`: one per-client sliding window (20 attempts per minute), a bounded key table, and client-address selection. There is no global limiter (see Deployment gate).
- `backend/scripts/purge_expired_candidates.py`: `--check` (offline) or `--run --confirm-delete`. Output is counts only; a failure prints the stage (`connecting` or `purging`) and the exception class name, never the message, URL, or credentials. No in-process cleanup task exists.

Audit remediation (this round): the global 600/minute limiter was removed (a shared bucket let one caller lock everyone out). `CandidateRepository.register` and its tests were removed from this milestone and left to Task 9.3; `candidate_repository.py` is again identical to its Task 9.1 version and the 9.1 `create` is kept. Origin protection is now one router-level guard (`enforce_trusted_origin`) that covers every method other than GET/HEAD/OPTIONS, so a route added to the router later inherits it. Configured origins are normalized (default ports `:443`/`:80` dropped; port 0, an empty port, paths, credentials rejected). `AUTH_COOKIE_SECURE=0` is accepted only with an `http` origin on a loopback host, and a non-loopback `http` origin is rejected, so a deployed origin cannot silently get insecure cookies. The cleanup docstring and the concurrency description below were corrected. Deterministic PostgreSQL tests were added for the login/delete foreign-key race and the cleanup interleaving.

Known behaviour accepted for the MVP: logging in again does not revoke earlier sessions. A browser that already holds a session and logs in again simply receives a new cookie; the old session stays valid until it expires (at most 7 days) or is revoked by logout. This only matters if the old token had already been stolen.

Expiry and concurrency strategy: expiry is evaluated by the database clock inside every login and `resolve` statement, so an expired identity is inaccessible whether or not cleanup runs. Each cleanup batch is one transaction: a `SELECT ... FOR UPDATE OF candidate, profile SKIP LOCKED` followed by a `DELETE` that repeats the expiry predicate. `SKIP LOCKED` only keeps cleanup from waiting; the protection against deleting a renewed identity is the row locks. An update that arrives after cleanup took its locks blocks and then finds the row deleted, so it neither loses nor resurrects data. The delete's own expiry predicate is defence in depth, because the locked rows cannot change between the two statements. Cleanup never waits on a candidate or profile lock; its `DELETE` cascade can wait on a session-row lock held by a concurrent logout, and that holder waits on nothing cleanup owns, so no deadlock cycle forms. Login's foreign-key check turns a concurrent candidate delete into a generic 401, and no orphan session can commit.

Verification (from `backend`): `uv run --env-file ../.env.test python -m pytest -q` passed (434 tests); `uv run python -m pytest -q` without a database passed (354 passed, 80 integration tests skipped by design); `uv run --env-file ../.env.test python -m alembic check` reported no new upgrade operations; frontend `npm test` (13 tests) and `npm run build` passed; `git diff --check` passed. One 9.1 test double (`FakeDatabase` in `tests/test_database_config.py`) gained a `sessions` attribute because the lifespan now passes it to the auth repository. The suite emits a Starlette deprecation warning for `TestClient` with `httpx`, alongside the existing Google SDK warning. No live Gemini, Gnani, or Neon request was made.

What the concurrency tests establish (`tests/integration/test_auth_concurrency.py`; each proceeds only after `pg_stat_activity` shows a statement blocked on a lock, never after a sleep, and each was mutation-checked by breaking the code under test and confirming a failure):
- Login is blocked in its foreign-key check while another transaction holds the candidate row; when that transaction deletes the candidate and commits, login fails with `InvalidCredentialsError` (the SQLSTATE 23503 mapping) and no session row exists. A control test with a rollback shows the same blocked login succeeds, so the failure comes from the delete.
- Cleanup is paused between its lock statement and its delete statement. An unconditional profile update started in that gap blocks, then updates zero rows once cleanup deletes the identity. This stands for a renewal that began before the expiry instant; today's `replace` refuses expired rows, so the future renewal rule (Tasks 9.3/9.4) must still be checked against this protocol.
- The delete statement refuses an identity that was renewed inside cleanup's own transaction. This is a unit test of the predicate, not proof of the cross-transaction protocol.
- Existing tests also cover lock skipping (cleanup does not wait on a profile or candidate row another transaction holds), concurrent cleanups, and many simultaneous logins.

Not tested, and why: a renewal that commits in the instant between cleanup's snapshot and its lock attempt (the window cannot be reproduced without changing production SQL; it relies on PostgreSQL's documented READ COMMITTED re-check of locked rows). The random login-versus-delete stress test and the expired-login-during-cleanup test are scheduling-dependent and adjacent cases, named as such. Not verified: browser cookie behavior, the Vercel rewrite (Set-Cookie/Origin passthrough, no caching, 2 MB uploads, response-time limits), the real forwarding-header chain, Safari with `Secure` cookies on `http://localhost`, and Railway scheduled-job feasibility.

Deployment gate for the login rate limit (nothing here is established yet):
- Verify the real forwarding-header chain between the browser, Vercel, and the backend before trusting any hop.
- Until `AUTH_TRUSTED_PROXY_HOPS` matches that chain, every user behind a proxy shares one bucket, and one caller can exhaust it and block everyone's login. Do not deploy with the default `0` behind a proxy without accepting that.
- Setting the hop count does not by itself protect a directly reachable backend: a caller who reaches the Railway URL directly can forge the header entries to the left of the trusted ones. Decide separately whether direct backend access must be restricted, and how.
- The limiter is per process; several workers or instances multiply the limit. It is load protection, not the security boundary (key entropy is).

Deferred to Task 9.3 (identity creation; the Task 9.2 reference implementation was removed from the tree, and the requirements are recorded here):
- Issuance of the recovery key is stateless and happens before saving; the user must confirm they recorded it before the save is enabled. A lost key cannot be recovered and must be disclosed.
- The save must create the candidate and profile in one transaction keyed by the key's verifier, with `INSERT ... ON CONFLICT (recovery_secret_hash) DO NOTHING` rather than parsing a constraint error. A retry with the same key must return "already exists" with no write at all (never an update of fields, consent, revision, or expiry).
- The unique hash outlives an expired profile until cleanup runs, so the 9.1 `create` cannot reuse that key (pinned by `test_an_expired_identity_reserves_its_key_hash_until_cleanup_releases_it`). The save must delete an expired or profile-less identity holding the verifier and create a fresh one in the same transaction, never reviving old data. Take the candidate row lock, then the profile row lock, and read expiry from the locked profile row; retry once if the row vanished while waiting.
- Tests needed: retry after a lost response, concurrent identical first saves (one creator), expired reuse, a renewal holding the profile lock, rollback on constraint errors.
- Retention period and renewal rules must be decided before saving is enabled.

Decisions recorded from review: same-origin Vercel `/api/*` proxy is the preferred target but not committed; 7-day fixed session; a lost key is unrecoverable (must be disclosed in the 9.3/9.5 UI); an old key after expiry creates a fresh identity; cleanup should run as a separate scheduled job. Task 9.3 is not approved.

## MVP 1, Task 9.1: Database Foundation

Status: implemented and verified with disposable local PostgreSQL 17; committed as `ae40ac6`. Database support defaults to disabled. Explicit enablement requires a PostgreSQL psycopg URL, performs a bounded startup connectivity check, and disposes the engine on shutdown or startup failure. Startup never applies migrations. Existing provider/session construction, intake/speech pipelines, public routes, and frontend are unchanged.

Added SQLAlchemy async storage, an explicit Alembic migration, and candidates, candidate_profiles, and candidate_auth_sessions tables with UUIDs, TIMESTAMPTZ, text arrays, double-precision experience, foreign keys/cascades, unique hash verifiers, structural checks, and revision constraints. Internal repository operations create candidate/profile atomically, retrieve unexpired profiles, replace using an expected revision, and delete candidates with cascade. Pydantic validation remains authoritative; mapping preserves null versus empty skills, order, declined fields, Unicode, and float experience. Expiry is caller-supplied and checked at statement start; reads do not renew retention. No retention policy, authentication functionality, save/resume routes, or conversation persistence was added.

Verification on Python 3.14: `.venv/bin/python -m pytest -q tests/test_database_config.py tests/test_candidate_mapping.py` passed (14 unit tests). `uv run --env-file ../.env.test python -m pytest -q tests/integration tests/test_database_config.py tests/test_candidate_mapping.py` passed (68 tests: 54 PostgreSQL integration plus 14 unit). The final `uv run --env-file ../.env.test python -m pytest -q` passed outside the sandbox (297 tests, no skips/deselections). PostgreSQL tests covered migration repeatability/downgrade/upgrade, ORM schema and CHECK-expression agreement, round trips, direct invalid SQL, concurrent revision conflicts, rollback, expiry, engine recreation, cascade deletion, and optional app lifecycle. `uv run --env-file ../.env.test python -m alembic upgrade head` succeeded; `python -m alembic check` through the same uv invocation reported no new upgrade operations. Backend runs reported only the existing Google SDK Python 3.14 warning. Frontend `npm test` passed (3 test files), `npm run build` passed, and `git diff --check` passed.

Local infrastructure and instructions are in `compose.test.yaml` and `docs/database-development.md`. The generated root `.env.test` contains only disposable local credentials and is ignored. No Neon or live speech/LLM request was made. SQL structural checks do not emulate Python Unicode casefold; full validation is applied on both writes and reads. The disposable PostgreSQL container uses tmpfs, so engine recreation demonstrates database durability across application connections, while container recreation intentionally loses test data. No blocking issue remains; ready for local review. Task 9.2 authentication remains outside this milestone and requires separate approval.

## MVP 1, Task 8: Candidate Intake Workflow closure

Status: completed and committed as `2e9348c`; Tasks 8.1 through 8.3 and compatibility fixes are included. The user manually verified live Gemini structured extraction and the stored profiles, multiple fields from one utterance, preservation across updates, PostgreSQL to MongoDB skill correction, English and Hindi intake voice conversations, and mode switching with session reset. Gnani STT/TTS and Task 6 diagnostics remained functional in those checks. These observations establish the tested interactions, not latency benchmarks or general extraction reliability.

Final cleanup makes English and Hindi summaries spoken sentences, includes every stored skill and both experience values when present, handles explicit empty skills, and retains deterministic single-question follow-ups. General chat remains the default; public response models and diagnostics are unchanged. Reviewed tracked and untracked Task 8 changes; no unrelated changes found. The local inspector remains disabled by default, rejects nonloopback and forwarded requests, and logs no profile or session data. Deployment must leave it unset or set it to `0`.

Final validation: from `backend`, `.venv/bin/python -m pytest -q tests/test_candidate_profile.py tests/test_candidate_intake.py tests/test_candidate_intake_api.py tests/test_dev_profile.py tests/test_gemini_llm.py tests/test_text_chat.py` passed (150 tests); `.venv/bin/python -m pytest -q` passed outside the sandbox (229 tests, no deselections). Both reported the existing Google SDK Python 3.14 deprecation warning. From `frontend`, `npm test` passed (3 test files) and `npm run build` passed, including TypeScript compilation. `git diff --check` passed. Local `.env` paths are ignored and untracked; neither local credential value appeared in tracked or untracked nonignored files. No live provider calls, commits, or pushes were made during cleanup. No blocking issue remains. The revised summary wording has offline coverage but has not been separately listened to through live TTS. Sessions remain process-local and ephemeral; extraction grounding does not guarantee semantic correctness for every utterance. No next milestone is approved.

## Task 8 local profile verification

Added opt-in `POST /api/v1/dev/candidate-profile`, enabled only by `BOLHIRE_DEV_PROFILE_INSPECTION=1`. It accepts a session ID in the JSON body, permits numeric loopback clients only, rejects forwarded headers, and returns only the profile with declined fields. It uses the shared store without provider calls or activity refresh. Existing lazy expiry deletes expired sessions. Public chat contracts and extraction behavior are unchanged. Never enable in deployment. Local usage and one controlled live verification procedure are in `docs/local-profile-verification.md`. The user reports a successful live structured intake response; the user subsequently inspected and verified its stored fields (see Task 8 closure). No live requests were made during this change.

Verification: `.venv/bin/python -m pytest -q tests/test_dev_profile.py tests/test_candidate_intake_api.py tests/test_candidate_intake.py tests/test_candidate_profile.py` passed (98 tests). `.venv/bin/python -m pytest -q` passed outside the sandbox (219 tests); the sandbox run stalled at the known large-upload case. Both passing runs reported the existing Google SDK Python 3.14 deprecation warning. `git diff --check` passed. `uv run pytest` could not launch the local pytest executable, so verification used the same environment's Python module entry point. No further milestone is approved.

## MVP 1, Task 8.3: Candidate Intake API and Frontend Integration

Status: implemented without a new route. Both existing chat endpoints accept optional `workflow`, defaulting to `general`; `candidate_intake` selects the Task 8.2 service. General and intake pipelines share one session store, so a session cannot switch workflows. A mismatch returns HTTP 409 with `SESSION_WORKFLOW_MISMATCH`; an invalid workflow returns HTTP 422 with `UNSUPPORTED_WORKFLOW`. Text and voice response fields remain unchanged. The voice route still uses the selected STT and TTS providers, WAV limits, timings, usage and cost estimates, and preserves a committed text turn if TTS fails.

The recorder offers General Chat and Candidate Intake, defaulting to General Chat. Changing modes clears the recording, reply, returned audio, error, and session ID, while keeping the language choice. The selected workflow is sent with voice requests. The default mock LLM has a minimal no-extraction intake fixture for offline routing checks; actual profile extraction requires Gemini. No candidate profile editor or persistence was added.

The user reported that the first live structured Gemini intake request returned HTTP 400 with `INVALID_ARGUMENT` and the safe diagnostic category `response_schema`. The Gemini-facing intake schema was simplified without changing the model or local profile rules. It retains every partial-update field and evidence, but sends no nullable fields, array-size or string-length constraints. The four operation lists are required arrays and may be empty; unchanged optional values are omitted. Returned JSON is still checked by the strict `CandidateIntakeProposal` model before any session commit. Offline validation passed (106 focused tests and 194 full backend tests outside the sandbox, with the existing Python 3.14 SDK warning); `git diff --check` passed. Subsequent live schema acceptance and stored-profile verification are recorded in the Task 8 closure above.

The next live structured request passed Gemini JSON and proposal parsing but returned `CANDIDATE_INTAKE_INVALID_RESPONSE` during local intake validation. A temporary local warning now records only `component=candidate_intake` and a fixed failure reason; the public error, evidence rules, profile semantics, and atomic commit behavior are unchanged. Empty updates remain valid for clarification turns. Offline focused validation passed (117 tests, one existing SDK warning), and `git diff --check` passed. The earlier failure reason was not established; subsequent live intake and profile verification succeeded.

Validation: focused backend API, intake, profile, Gemini, and text-chat tests passed (103 tests). The full backend suite passed outside the sandbox (182 tests, including the large-upload case). An earlier focused command containing the full voice test file was stopped at the known in-sandbox large-upload stall. Both passing backend runs reported one pre-existing Google Gen AI SDK deprecation warning on Python 3.14. Frontend `npm test` passed (3 test files), `npm run build` passed, and `git diff --check` passed. Tests used fake or mock providers; no live Gemini or Gnani requests were made. Live structured Gemini intake and browser mode switching were subsequently verified by the user, as recorded above. No further milestone has been approved.

## MVP 1, Task 8.2: Structured Candidate Intake Generation and Orchestration

Status: implemented as a backend application path. Task 8.3 connects it to the existing HTTP routes and frontend. The Gemini adapter can make one schema-constrained JSON generation request for an intake turn using its existing model, timeout, thinking, output, one-attempt, and error settings. Its general text-generation contract remains unchanged. The proposed partial profile update and acknowledgement are validated locally against the Task 8.1 profile rules. Each changed field requires a quote from the latest utterance; names, roles, levels, locations, skills, numeric experience, and work mode receive additional literal grounding checks. Ambiguous or invalid proposals fail without committing a turn. These checks reduce unsupported extraction but cannot prove semantic correctness without live review.

The candidate intake pipeline uses the session lock across generation, validation, and the atomic profile-plus-exchange commit. The server selects the first unknown, non-declined field in a fixed order and asks one short English or Hindi follow-up. Once all fields are accounted for, it summarizes the known profile and invites corrections. Romanized Hindi is accepted with `hi-IN`; the prompt requests a Hindi acknowledgement, and the server owns follow-up wording. Offline tests use scripted structured responses and a fake Gemini SDK client. General chat, Task 6 diagnostics, and provider selection remain unchanged. Live structured Gemini verification was subsequently completed by the user, as recorded above.

Validation: focused intake, profile, Gemini, and text-chat tests passed (95 tests). The complete backend suite passed outside the sandbox (174 tests, including the large-upload test). The same full command timed out after 60 seconds inside the sandbox at the known large-upload case. Both passing runs reported one pre-existing Google Gen AI SDK deprecation warning on Python 3.14. `git diff --check` passed. No live provider request was made.

## MVP 1, Task 8.1: Session-Scoped Candidate Profile

Status: implemented for the in-memory session layer only. A strict, bounded candidate profile stores name, target role, explicit experience years and/or level, technical skills, preferred location, work mode, and declined fields. Partial updates distinguish omitted values, explicit clears, declines, and corrections. Skill additions and removals preserve unrelated skills. The profile is separate from bounded conversation history and survives its 20-exchange truncation.

Candidate sessions have a separate workflow identity and cannot be used as general-chat sessions. A session-store operation validates an intake update and a completed user/assistant exchange, then commits profile, history, and activity time together. Existing general-chat behavior, text and voice API contracts, provider selection, and Task 6 diagnostics are unchanged. Gemini extraction, HTTP intake routing, frontend changes, and durable persistence are not part of Task 8.1.

Validation: focused candidate-profile and existing text-session tests passed (39 tests). The complete backend suite passed outside the sandbox (151 tests, including the large-upload test; one pre-existing Google Gen AI SDK deprecation warning on Python 3.14). No live provider request was made. Candidate-turn extraction was implemented in Task 8.2; API integration followed in Task 8.3.

## MVP 0, Task 7: Hosted Gemini LLM Integration

Status: implemented and manually verified by the user. The first live Gemini text smoke test returned `GEMINI_MODEL_UNAVAILABLE` for `gemini-2.5-flash`; the compatibility fix defaults to `gemini-3.5-flash-lite` with `GEMINI_THINKING_LEVEL=minimal`. The user then confirmed a successful Gemini-only English text smoke test, two-turn conversation memory through FastAPI, a full English Gnani STT to Gemini to Gnani TTS browser interaction, English voice-session continuity, and a full Hindi voice interaction with audible TTS. Task 6 diagnostics appeared in the live browser response. These checks establish functionality for the tested interactions; they do not establish general latency, reliability, or provider billing accuracy. Gemini 2.5 Flash retains its default numeric thinking budget of zero when selected explicitly. `LLM_PROVIDER=mock` remains the default. `LLM_PROVIDER=gemini` selects the asynchronous Google Gen AI provider independently of mock or Gnani speech mode. The provider maps existing user and assistant history to Gemini user and model messages, supplies a separate voice-friendly system instruction for English or Hindi, and returns a plain text reply through the existing pipeline. It uses a 20-second default timeout, one HTTP attempt, no automatic continuation, and a configurable output limit. The SDK client is closed at application shutdown.

Configuration is backend-only: `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TIMEOUT_SECONDS`, `GEMINI_MAX_OUTPUT_TOKENS`, optional `GEMINI_THINKING_LEVEL` for Gemini 3.x, optional `GEMINI_THINKING_BUDGET` for older models, and optional `GEMINI_SYSTEM_PROMPT`. A Gemini 3.x model rejects `GEMINI_THINKING_BUDGET`; a non-3.x model rejects `GEMINI_THINKING_LEVEL`. Invalid Gemini settings fail startup when Gemini is selected. Provider errors have safe application codes for authentication, model access, rate limits, timeouts, blocked or empty output, and invalid responses. Failed LLM turns do not enter session history, and no mock fallback occurs. Existing text and voice response fields, Task 6 diagnostics, session limits, and Gnani adapters are unchanged. The frontend's LLM label is provider-neutral.

`backend/scripts/gemini_smoke.py` provides a no-request `--check` mode and a single-request `--run --confirm-billable` mode. The ignored local `.env` model and thinking settings were updated to 3.5 Flash-Lite with minimal thinking, without changing credentials; its offline `--check` passed and made no API request. Final Task 7 validation: the complete backend suite passed outside the sandbox (121 tests, including the large-upload case; one deprecation warning from the installed SDK on Python 3.14). The same suite timed out after 45 seconds inside the sandbox at the known large-upload case. `npm test` passed (2 frontend test files), `npm run build` passed, and `git diff --check` passed. Automated Gemini tests use fake SDK responses and no live speech calls. No live provider request was made during final cleanup. Task 7 was committed as `16d6d08`.

## MVP 0, Task 6: Latency, Usage, and Estimated Speech Cost

Status: implemented with automated tests. The voice response retains `timings_ms` and `estimated_speech_credits` and adds typed `usage` and `estimated_speech_cost_inr` objects. Usage comes from validated input PCM duration, the exact reply sent to TTS, the language, and the selected speech provider. The existing monotonic STT, LLM, TTS, and total route timers remain in place. Total route time starts before upload reading and ends before response serialization; it is not browser round-trip latency.

Gnani cost estimates use configurable server-side rates, defaulting to the observed assumptions of ₹27 per audio hour and ₹27 per 10,000 TTS characters. They are estimates, not provider billing. Mock speech reports zero external speech cost. When Gnani TTS fails, the response retains the completed text turn and reports the STT estimate, while TTS and total cost are unavailable. Older voice responses without the new fields remain usable in the frontend. A compact diagnostics block shows timings, usage, and estimated cost.

Validation at Task 6: `./.venv/bin/python -m pytest -q` passed (84 backend tests) outside the sandbox, `npm test` passed (2 frontend test files), and `npm run build` passed. All Task 6 provider requests in tests used fakes or mocked HTTP; no live Gnani calls were made. The in-sandbox backend run stalled on the pre-existing large-upload test because AnyIO worker threads did not complete there. `git diff --check` passed. The user later confirmed that Task 6 diagnostics appeared during Task 7 live browser testing.

## MVP 0, Task 5: Gnani Speech API Integration

Status: implemented with mocked HTTP tests. The user reports that the first live English STT smoke test passed: authentication succeeded, a valid 16 kHz mono PCM WAV was accepted, transcription was accurate, input duration was 7.21 seconds, and STT latency was 1104.97 ms. The user also reports a successful independent live Timbre v3.0 TTS smoke test: HTTP 200 `audio/wav`, 199724 bytes, 48 kHz mono 16-bit PCM, 2.08 seconds, 1175.71 ms latency, and successful ffprobe parsing. Its RIFF and data lengths were both `0xffffffff`. The user subsequently manually verified browser voice interactions in English and Hindi, including session continuity and returned audio playback.

Full-browser preparation: an import-only run with `uv run --env-file ../.env` confirmed Gnani STT/TTS are selected while `MockLLMProvider` remains selected. The configured CORS origin matches local Vite at `http://localhost:5173`. Gnani's validated placeholder-length WAV is now normalized to concrete RIFF and data lengths before the API returns it, preserving the PCM payload. Synthetic provider and route tests cover the normalized `audio/wav` response. No live request was made during this preparation; browser playback was verified later as reported above.

Preparation validation: `uv run pytest -q` passed (69 backend tests), `npm test` passed (2 frontend test files), `npm run build` passed, and `git diff --check` passed. No frontend media element was manually exercised in this preparation.

Task 5B preparation: `backend/scripts/tts_smoke.py` provides a no-request `--check` mode and a single-request `--run --confirm-billable` mode. It uses the configured Timbre v3.0 voice, verifies non-empty 48 kHz mono PCM WAV output, reports synthesis latency and duration, and saves to the Git-ignored `backend/smoke-output.wav` by default without overwriting an existing result. The runner never invokes STT or the LLM.

Task 5B checks: the root `.env` passed `uv run --env-file ../.env python -m scripts.tts_smoke --check` without an API request or audio output. Backend pytest, frontend tests/build, and `git diff --check` passed at that stage. The subsequent live TTS result was reported by the user; provider credit usage remained unverified. Browser playback was verified later as reported above.

Task 5B first live attempt, reported by the user: configuration passed, but TTS returned `GNANI_TTS_INVALID_RESPONSE` with "Gnani returned incomplete WAV audio" after 1371.73 ms; no file was saved. That message came from the adapter's former exact RIFF-size equality check. The later successful diagnostic test established that Gnani uses `0xffffffff` placeholder sizes for this WAV response. The adapter accepts these placeholders, validates concrete chunks and non-empty 48 kHz mono 16-bit PCM, and still rejects contradictory lengths or detectable truncation. An explicitly gated `--diagnostic` mode prints only safe response metadata. A response using placeholder sizes can still be physically truncated by a whole PCM frame without a declared length; that is not provable from WAV bytes alone.

Preflight on 2026-10-17: the configured file is the ignored repository-root `.env`; `backend/.env` does not exist. The normal backend startup command does not load either file. `uv run --env-file ../.env` from `backend` loads the root file, passes provider, key, model, and catalog-voice validation, and selects the Gnani adapters in an import-only check. `backend/scripts/stt_smoke.py` provides a no-request `--check` mode and requires `--run --confirm-billable` for one STT request. Its tests passed with fake providers (51 backend tests total at that preflight).

- `SPEECH_PROVIDER=mock` remains the default. `SPEECH_PROVIDER=gnani` selects Gnani STT and Timbre v3.0 TTS at backend startup; the LLM remains mocked.
- Gnani STT receives the validated WAV with an explicit en-IN or hi-IN language code and parses the documented `success` and `transcript` response fields.
- Gnani TTS uses a configured Timbre v3.0 catalog voice and requests mono 48 kHz, 16-bit PCM WAV. Binary output is bounded and validated before the existing response path returns it.
- Provider errors distinguish authentication, rate limits, timeouts, rejected requests, and invalid responses. No automatic retries are configured. TTS failures still save the completed text turn and return `audio: null` with a warning.
- Mock speech requests report zero estimated credits. Gnani requests report `null` because a reliable credit estimate is unavailable from the documented response.
- The frontend labels speech results generically and continues to explain that the LLM is mocked.

Validation: `npm test` passed (2 frontend test files), `npm run build` passed, `uv run pytest -q` passed (48 backend tests), and `git diff --check` passed. All Gnani HTTP responses in tests were mocked.

Configuration: export `SPEECH_PROVIDER=gnani`, `GNANI_API_KEY`, and `GNANI_TTS_VOICE` for the backend process. `GNANI_TTS_MODEL` defaults to `timbre-v3.0`; Task 5 accepts only that model and validates the voice against its catalog. Keep the API key server-side. The browser's language selection is passed explicitly to both Gnani adapters. See `.env.example`.

Provider contracts: [Gnani STT REST](https://docs.gnani.ai/api/STT/speech-to-text), [Gnani TTS REST](https://docs.gnani.ai/api/TTS/tts-inference), and [Timbre v3.0 voice catalog](https://docs.gnani.ai/api/TTS/available-voices).

The user reports that Task 5 browser verification is complete in English and Hindi. Each browser voice submission invokes both billable STT and TTS stages.

## MVP 0, Task 4: Mock Voice Pipeline

Status: implemented. The user reports manual verification of the multi-turn mock voice pipeline. The New Conversation control has not yet been manually verified.

Implemented:

- `POST /api/v1/voice/chat` accepts multipart WAV and optional session ID with en-IN or hi-IN.
- Bounded uploads and RIFF/WAVE chunk validation enforce mono, 16 kHz, signed 16-bit PCM, 30 seconds, and 2 MB.
- Deterministic mock STT returns a placeholder that explicitly says no speech recognition occurred. The existing text pipeline handles the turn and session history. Mock TTS returns a playable WAV tone, not spoken words.
- Response includes transcript, reply, session ID, stage timing, zero estimated speech credits, and optional audio/warning. TTS failure keeps the completed text turn.
- Recorder UI explicitly submits the WAV, shows transcript and reply, plays returned mock audio separately from the local recording, and keeps the session ID for further turns.
- New Conversation clears the local recording, displayed response, returned audio, request error, and frontend session ID. Clear Recording remains limited to the local recording. Returned audio object URLs are revoked when replaced, reset, or unmounted.
- The frontend offers a dark/light toggle, with dark mode selected on each initial page load.
- Local frontend API base URL uses `VITE_API_BASE_URL`; backend CORS origin uses `BACKEND_CORS_ORIGIN`.

Validation: `npm test` and `npm run build` in `frontend`, `uv run pytest -q` in `backend`, and `git diff --check` at the repository root passed. No live speech or LLM provider calls were made.

The user manually verified the multi-turn mock voice pipeline. The New Conversation control, returned-audio cleanup, and theme toggle still need browser verification after these changes. Network failures and browser audio playback have automated coverage only where practical.

## MVP 0, Task 3: Browser Audio Recording

Status: implemented with automated checks passed. Built-in microphone recording was manually verified.

Implemented:

- Browser microphone recording through `getUserMedia` and Web Audio.
- Genuine linear resampling to mono 16 kHz samples before WAV encoding.
- Local mono 16-bit PCM WAV playback, a 30-second capture cap, and a 2 MB output guard.
- Recorder state, duration, permission and browser-support errors, plus cleanup of tracks, audio contexts, and object URLs.
- Dependency-free Node tests for resampling, WAV headers, PCM encoding, duration, and size limits.
- A Brave and Chromium initialization repair: the recorder resets its mounted flag during React Strict Mode effect setup, supports cancelling initialization, bounds `AudioContext.resume()`, and writes development-only stage diagnostics without audio data.
- Start latency repair: the UI enters Recording after the first captured callback, skips `AudioContext.resume()` when already running, times out if no samples arrive, and uses a 2048-frame capture buffer. Development logs include a `performance.now()` breakdown for microphone acquisition, audio setup, first sample, and React commit. Fake Chromium timing improved from 111.4/72.9 ms to 69.0/17.6 ms for first/repeated starts; real device timing still needs verification.
- A local fake-microphone Chromium check verified repeated Start/Stop and rapid Start/Cancel returned the UI to Ready without retaining cancelled audio.
- WAV encoding was verified as 16 kHz, mono, 16-bit PCM. Recording and repeated recording work with the laptop's built-in microphone.
- The Rockerz 551ANC Bluetooth headset can produce delayed or silent recordings on repeated attempts in Brave on Linux. The root cause is unproven; Bluetooth profile switching or device reinitialization is only a possible explanation. This limitation is accepted for MVP 0. Use the built-in microphone for development and demonstrations.

Validation commands:

```sh
cd frontend && npm test
cd frontend && npm run build
```

Live STT, TTS, LLM, Gnani, database, authentication, and deployment infrastructure remain outside this milestone.

## MVP 0, Task 2: Text Conversation Pipeline

Status: complete and validated locally.

Implemented:

- `POST /api/v1/chat/text` with Pydantic request and response models.
- Deterministic local mock LLM with no network or credentials.
- Single-process, in-memory session history, lazy 30-minute expiry, 20-exchange cap, and per-session request serialization.
- Stable JSON error envelopes for malformed requests, validation errors, missing or expired sessions, and mock-provider failures.
- Automated tests for the text pipeline and the health endpoint.

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
cd frontend && npm test
cd backend && uv run pytest
```

Local development:

```sh
# Terminal 1
cd backend && uv sync && uv run uvicorn app.main:app --reload

# Terminal 2
cd frontend && npm install && npm run dev
```
