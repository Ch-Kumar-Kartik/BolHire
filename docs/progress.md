# Progress

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
