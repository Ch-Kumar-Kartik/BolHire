# Codebase Map

## Frontend

- `frontend/src/main.tsx`: React application bootstrap.
- `frontend/src/App.tsx`: BolHire application shell, dark/light theme toggle, and recorder entry point.
- `frontend/src/components/Recorder.tsx`: recording controls, local WAV playback, explicit voice submission, speech response and compact latency, usage, and cost diagnostics, returned audio playback and URL cleanup, conversation reset, and errors.
- `frontend/src/lib/voiceApi.ts`: multipart voice request, backwards-compatible response validation, and returned WAV decoding for mock or Gnani audio.
- `frontend/src/lib/voiceApi.test.ts`: voice API and decoding tests.
- `frontend/src/hooks/useAudioRecorder.ts`: browser microphone lifecycle, startup timing diagnostics, first-sample readiness, cancellation, and resource cleanup.
- `frontend/src/lib/audio.ts`: pure resampling, PCM WAV encoding, and recording-limit helpers.
- `frontend/src/lib/audio.test.ts`: Node tests for audio processing and WAV output.
- `frontend/src/styles.css`: plain CSS for the shell and dark/light theme colors.
- `frontend/src/vite-env.d.ts`: Vite TypeScript environment declarations.
- `frontend/vite.config.ts`: Vite development configuration.

## Backend

- `backend/app/main.py`: FastAPI application factory, health endpoint, speech provider and pricing selection, and stable error handlers.
- `backend/app/api/routes.py`: text chat and multipart voice chat routes, voice usage extraction, and estimated speech cost calculation.
- `backend/app/models.py`: text and voice response models, typed voice usage and cost estimates, and conversation-message models.
- `backend/app/providers/llm.py`: replaceable LLM protocol and deterministic local mock provider.
- `backend/app/providers/speech.py`: replaceable STT/TTS protocols and deterministic mock providers.
- `backend/app/providers/gnani.py`: Gnani REST STT and TTS adapters with explicit timeouts, bounded response reads, chunk-aware TTS WAV validation, concrete header normalization for streaming-size placeholders, optional metadata-only diagnostics, and safe upstream errors.
- `backend/app/providers/speech_config.py`: server-side speech mode, model, key, Timbre v3.0 voice, and estimated rate validation.
- `backend/app/services/pipeline.py`: text conversation orchestration and stage timing.
- `backend/app/services/sessions.py`: bounded, lock-protected in-memory session store with lazy expiry.
- `backend/app/services/wav.py`: strict bounded WAV validation.
- `backend/tests/test_health.py`: health endpoint regression test.
- `backend/tests/test_text_chat.py`: text API, session, provider, failure, and concurrency tests.
- `backend/tests/test_voice_chat.py`: voice validation, session, audio payload, and failure tests.
- `backend/tests/test_gnani_speech.py`: mocked HTTP contract, failure, configuration, and route tests for Gnani mode.
- `backend/scripts/stt_smoke.py`: explicit, single-request STT smoke runner with a no-request preflight mode.
- `backend/tests/test_stt_smoke.py`: verifies smoke-runner safety and single-call behavior with a fake provider.
- `backend/scripts/tts_smoke.py`: explicit, single-request TTS smoke runner with no-request preflight, shared WAV verification, local output, and gated metadata-only diagnostics.
- `backend/tests/test_tts_smoke.py`: verifies TTS smoke-runner safety, output handling, and single-call behavior with a fake provider.
- `backend/pyproject.toml`: Python dependencies and pytest configuration managed by uv.

## Local configuration

- `.env.example`: frontend API base URL, backend CORS origin, and server-side speech provider and estimated rate configuration placeholders.
- `.gitignore`: local environments, dependency directories, and generated output.
