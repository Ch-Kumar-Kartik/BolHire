# Codebase Map

## Frontend

- `frontend/src/main.tsx`: React application bootstrap.
- `frontend/src/App.tsx`: BolHire application shell, dark/light theme toggle, and recorder entry point.
- `frontend/src/components/Recorder.tsx`: workflow and language selection, recording controls, local WAV playback, explicit voice submission, provider-neutral assistant reply display, compact latency, usage, and cost diagnostics, returned audio playback and URL cleanup, conversation reset, and errors.
- `frontend/src/lib/conversationSelection.ts`: pure mode transition that drops the previous session ID.
- `frontend/src/lib/conversationSelection.test.ts`: mode-switch session reset tests.
- `frontend/src/lib/voiceApi.ts`: multipart voice request with workflow selection, backwards-compatible response validation, and returned WAV decoding for mock or Gnani audio.
- `frontend/src/lib/voiceApi.test.ts`: voice API and decoding tests.
- `frontend/src/hooks/useAudioRecorder.ts`: browser microphone lifecycle, startup timing diagnostics, first-sample readiness, cancellation, and resource cleanup.
- `frontend/src/lib/audio.ts`: pure resampling, PCM WAV encoding, and recording-limit helpers.
- `frontend/src/lib/audio.test.ts`: Node tests for audio processing and WAV output.
- `frontend/src/styles.css`: plain CSS for the shell and dark/light theme colors.
- `frontend/src/vite-env.d.ts`: Vite TypeScript environment declarations.
- `frontend/vite.config.ts`: Vite development configuration.

## Backend

### Optional database foundation (Task 9.1)

- `backend/app/db/config.py`: explicit opt-in environment validation with safe URL handling.
- `backend/app/db/database.py`: async engine/session factory, bounded connectivity check, disposal.
- `backend/app/db/models.py`: candidate, profile, and future auth-session table structure and constraints.
- `backend/app/db/mapping.py`: strict profile conversion and immutable saved-profile results.
- `backend/app/db/candidate_repository.py`: transaction-scoped create/read/replace/delete, expiry filtering, revision conflicts, safe storage errors.
- `backend/alembic.ini`, `backend/alembic/`: explicit migrations; independent initial schema snapshot and array validation functions. Never invoked at app startup.
- `backend/tests/test_database_config.py`, `backend/tests/test_candidate_mapping.py`: offline configuration/lifecycle and mapping tests.
- `backend/tests/integration/`: isolated local PostgreSQL 17 migration, constraint, repository, concurrency, and lifecycle tests.
- `compose.test.yaml`: loopback-only disposable PostgreSQL 17 using tmpfs.
- `docs/database-development.md`: configuration, migration, repository semantics, and local test instructions.

### Application and existing workflows

- `backend/app/api/dev_profile.py`: explicitly enabled loopback-only profile inspection without provider calls or activity refresh; see `docs/local-profile-verification.md`.
- `backend/tests/test_dev_profile.py`: offline access restrictions, profile serialization, expiry, and mutation/provider-call checks.

- `backend/app/main.py`: FastAPI application factory, shared general/intake session wiring, independent speech and LLM provider selection, Gemini client shutdown, health endpoint, pricing selection, and stable error handlers.
- `backend/app/api/routes.py`: workflow selection in existing text and multipart voice routes, voice usage extraction, and estimated speech cost calculation.
- `backend/app/models.py`: text and voice response models, optional text workflow selection, typed voice usage and cost estimates, and conversation-message models.
- `backend/app/providers/llm.py`: replaceable LLM protocol, safe provider error contract, deterministic general mock, and no-extraction intake fixture.
- `backend/app/providers/gemini.py`: asynchronous Google Gen AI text and structured candidate-intake adapter, simplified Gemini-facing intake schema, history mapping, one-attempt request configuration, local response validation, and safe provider errors.
- `backend/app/providers/llm_config.py`: backend-only LLM mode, Gemini key, model, timeout, output, thinking, and system prompt validation.
- `backend/app/providers/speech.py`: replaceable STT/TTS protocols and deterministic mock providers.
- `backend/app/providers/gnani.py`: Gnani REST STT and TTS adapters with explicit timeouts, bounded response reads, chunk-aware TTS WAV validation, concrete header normalization for streaming-size placeholders, optional metadata-only diagnostics, and safe upstream errors.
- `backend/app/providers/speech_config.py`: server-side speech mode, model, key, Timbre v3.0 voice, and estimated rate validation.
- `backend/app/services/pipeline.py`: general text conversation orchestration, stage timing, and access to its provider and session store for shared intake wiring.
- `backend/app/services/candidate_profile.py`: strict, bounded candidate profile and partial-update validation with pure update application.
- `backend/app/services/candidate_intake.py`: structured intake proposal validation, deterministic missing-field questions, complete spoken English/Hindi summaries, and lock-protected candidate-turn orchestration.
- `backend/app/services/sessions.py`: bounded, lock-protected in-memory session store with lazy expiry, workflow identity, and atomic candidate profile plus exchange commits.
- `backend/app/services/wav.py`: strict bounded WAV validation.
- `backend/tests/test_health.py`: health endpoint regression test.
- `backend/tests/test_text_chat.py`: text API, session, provider, failure, and concurrency tests.
- `backend/tests/test_voice_chat.py`: voice validation, session, audio payload, and failure tests.
- `backend/tests/test_gnani_speech.py`: mocked HTTP contract, failure, configuration, and route tests for Gnani mode.
- `backend/tests/test_gemini_llm.py`: fake SDK tests for configuration, history, language instructions, provider selection, errors, sessions, and client lifecycle.
- `backend/scripts/gemini_smoke.py`: offline Gemini preflight or explicitly gated single text request.
- `backend/tests/test_gemini_smoke.py`: smoke-runner safety and single-call tests with fake providers.
- `backend/tests/test_candidate_profile.py`: offline profile, update, workflow, expiry, truncation, concurrency, and atomicity tests.
- `backend/tests/test_candidate_intake.py`: offline intake orchestration, grounding, multilingual, rollback, concurrency, and fake Gemini structured-response tests.
- `backend/tests/test_candidate_intake_api.py`: fake-provider text/voice intake routing, session sharing, mismatch, rollback, TTS failure, and diagnostics tests.
- `backend/scripts/stt_smoke.py`: explicit, single-request STT smoke runner with a no-request preflight mode.
- `backend/tests/test_stt_smoke.py`: verifies smoke-runner safety and single-call behavior with a fake provider.
- `backend/scripts/tts_smoke.py`: explicit, single-request TTS smoke runner with no-request preflight, shared WAV verification, local output, and gated metadata-only diagnostics.
- `backend/tests/test_tts_smoke.py`: verifies TTS smoke-runner safety, output handling, and single-call behavior with a fake provider.
- `backend/pyproject.toml`: Python dependencies, including `google-genai`, and pytest configuration managed by uv.

## Local configuration

- `.env.example`: frontend API base URL, backend CORS origin, and server-side speech, Gemini, and estimated rate configuration placeholders.
- `.gitignore`: local environments, dependency directories, and generated output.

## Task 8 verification

The user verified live structured extraction and stored profiles, multi-field intake, update preservation, skill correction, English/Hindi voice intake, mode switching with session reset, and continued Gnani speech and Task 6 diagnostics. See `docs/progress.md` for final regression results and limitations. `docs/local-profile-verification.md` describes the optional local inspector; deployment must disable it.
