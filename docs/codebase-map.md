# Codebase Map

## Frontend

- `frontend/src/main.tsx`: React application bootstrap.
- `frontend/src/App.tsx`: BolHire application shell, dark/light theme toggle, and recorder entry point.
- `frontend/src/components/Recorder.tsx`: recording controls, local WAV playback, explicit voice submission, response display, mock tone playback and URL cleanup, conversation reset, and errors.
- `frontend/src/lib/voiceApi.ts`: multipart voice request, response validation, and returned WAV decoding.
- `frontend/src/lib/voiceApi.test.ts`: voice API and decoding tests.
- `frontend/src/hooks/useAudioRecorder.ts`: browser microphone lifecycle, startup timing diagnostics, first-sample readiness, cancellation, and resource cleanup.
- `frontend/src/lib/audio.ts`: pure resampling, PCM WAV encoding, and recording-limit helpers.
- `frontend/src/lib/audio.test.ts`: Node tests for audio processing and WAV output.
- `frontend/src/styles.css`: plain CSS for the shell and dark/light theme colors.
- `frontend/src/vite-env.d.ts`: Vite TypeScript environment declarations.
- `frontend/vite.config.ts`: Vite development configuration.

## Backend

- `backend/app/main.py`: FastAPI application factory, health endpoint, and stable error handlers.
- `backend/app/api/routes.py`: text chat and multipart voice chat routes.
- `backend/app/models.py`: text and voice response models, and conversation-message models.
- `backend/app/providers/llm.py`: replaceable LLM protocol and deterministic local mock provider.
- `backend/app/providers/speech.py`: replaceable STT/TTS protocols and deterministic mock providers.
- `backend/app/services/pipeline.py`: text conversation orchestration and stage timing.
- `backend/app/services/sessions.py`: bounded, lock-protected in-memory session store with lazy expiry.
- `backend/app/services/wav.py`: strict bounded WAV validation.
- `backend/tests/test_health.py`: health endpoint regression test.
- `backend/tests/test_text_chat.py`: text API, session, provider, failure, and concurrency tests.
- `backend/tests/test_voice_chat.py`: voice validation, session, audio payload, and failure tests.
- `backend/pyproject.toml`: Python dependencies and pytest configuration managed by uv.

## Local configuration

- `.env.example`: frontend API base URL and backend CORS-origin placeholders.
- `.gitignore`: local environments, dependency directories, and generated output.
