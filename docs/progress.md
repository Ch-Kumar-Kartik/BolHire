# Progress

## MVP 0, Task 5: Gnani Speech API Integration

Status: implemented with mocked HTTP tests. The user reports that the first live English STT smoke test passed: authentication succeeded, a valid 16 kHz mono PCM WAV was accepted, transcription was accurate, input duration was 7.21 seconds, and STT latency was 1104.97 ms. The user also reports a successful independent live Timbre v3.0 TTS smoke test: HTTP 200 `audio/wav`, 199724 bytes, 48 kHz mono 16-bit PCM, 2.08 seconds, 1175.71 ms latency, and successful ffprobe parsing. Its RIFF and data lengths were both `0xffffffff`. The full browser voice route has not been live-tested.

Full-browser preparation: an import-only run with `uv run --env-file ../.env` confirmed Gnani STT/TTS are selected while `MockLLMProvider` remains selected. The configured CORS origin matches local Vite at `http://localhost:5173`. Gnani's validated placeholder-length WAV is now normalized to concrete RIFF and data lengths before the API returns it, preserving the PCM payload. Synthetic provider and route tests cover the normalized `audio/wav` response. Browser playback remains a manual verification item; no live request was made during this preparation.

Preparation validation: `uv run pytest -q` passed (69 backend tests), `npm test` passed (2 frontend test files), `npm run build` passed, and `git diff --check` passed. No frontend media element was manually exercised in this preparation.

Task 5B preparation: `backend/scripts/tts_smoke.py` provides a no-request `--check` mode and a single-request `--run --confirm-billable` mode. It uses the configured Timbre v3.0 voice, verifies non-empty 48 kHz mono PCM WAV output, reports synthesis latency and duration, and saves to the Git-ignored `backend/smoke-output.wav` by default without overwriting an existing result. The runner never invokes STT or the LLM.

Task 5B checks: the root `.env` passed `uv run --env-file ../.env python -m scripts.tts_smoke --check` without an API request or audio output. Backend pytest, frontend tests/build, and `git diff --check` passed at that stage. The subsequent live TTS result was reported by the user; in-browser playback and credit usage remain unverified.

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

Further live verification remains pending. Before each billable manual request, obtain explicit permission. Hindi STT and the full browser voice route have not yet been verified. Confirm transcription, returned text, session continuity, and browser WAV playback. The browser voice route invokes both STT and TTS on every submission, so count both billable stages when planning the test.

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
