import asyncio
import base64
import io
import struct
import wave

import httpx
import pytest

from app.main import create_app
from app.models import ConversationMessage
from app.providers.speech import MockSTTProvider, MockTTSProvider
from app.services.pipeline import TextConversationPipeline
from app.services.sessions import InMemorySessionStore
from app.services.wav import MAX_AUDIO_BYTES, validate_wav


def wav_bytes(*, seconds=0.1, rate=16000, channels=1, width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\0" * int(seconds * rate * channels * width))
    return output.getvalue()


class LLM:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def generate(self, messages, language):
        self.calls.append(list(messages))
        if self.fail:
            raise RuntimeError("failure")
        return f"Reply {len(messages)}"


class STT(MockSTTProvider):
    fail = False

    async def transcribe(self, audio, language):
        if self.fail:
            raise RuntimeError("failure")
        return await super().transcribe(audio, language)


class TTS(MockTTSProvider):
    fail = False

    async def synthesize(self, text, language):
        if self.fail:
            raise RuntimeError("failure")
        return await super().synthesize(text, language)


def setup():
    llm, stt, tts = LLM(), STT(), TTS()
    pipeline = TextConversationPipeline(llm=llm, sessions=InMemorySessionStore())
    return create_app(pipeline, stt=stt, tts=tts), llm, stt, tts


async def post(app, audio=None, session_id=None, language="en-IN"):
    data = {"language": language}
    if session_id is not None:
        data["session_id"] = session_id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.post("/api/v1/voice/chat", data=data, files={"audio": ("test.wav", wav_bytes() if audio is None else audio, "audio/wav")})


def test_valid_voice_turn_and_continuity():
    app, llm, _, _ = setup()
    first = asyncio.run(post(app)).json()
    second = asyncio.run(post(app, session_id=first["session_id"], language="hi-IN")).json()
    assert second["session_id"] == first["session_id"]
    assert first["transcript"] == "Mock transcript [en-IN]. No speech recognition was performed."
    assert second["reply"] == "Reply 3"
    assert len(llm.calls[1]) == 3
    assert first["audio"]["mime_type"] == "audio/wav"
    assert validate_wav(base64.b64decode(first["audio"]["base64"]))
    assert first["estimated_speech_credits"] == 0
    assert set(first["timings_ms"]) == {"stt", "llm", "tts", "total"}


@pytest.mark.parametrize("audio", [
    b"", b"bad", b"RIFF\0\0\0\0WAVE", wav_bytes(rate=8000),
    wav_bytes(channels=2), wav_bytes(width=1), wav_bytes(seconds=30.1),
    b"x" * (MAX_AUDIO_BYTES + 1),
    wav_bytes()[:-2],
])
def test_rejects_invalid_audio(audio):
    app, llm, _, _ = setup()
    response = asyncio.run(post(app, audio=audio))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_AUDIO"
    assert llm.calls == []


def test_rejects_declared_length_and_metadata_mismatch():
    app, _, _, _ = setup()
    payload = bytearray(wav_bytes())
    struct.pack_into("<I", payload, 28, 1)
    assert asyncio.run(post(app, audio=bytes(payload))).status_code == 422
    payload = bytearray(wav_bytes())
    struct.pack_into("<I", payload, 40, 999999)
    assert asyncio.run(post(app, audio=bytes(payload))).status_code == 422


def test_failures_do_not_commit_incomplete_turns():
    app, llm, stt, _ = setup()
    first = asyncio.run(post(app)).json()
    stt.fail = True
    assert asyncio.run(post(app, session_id=first["session_id"])).json()["error"]["code"] == "STT_TRANSCRIPTION_FAILED"
    stt.fail = False
    llm.fail = True
    assert asyncio.run(post(app, session_id=first["session_id"])).json()["error"]["code"] == "LLM_GENERATION_FAILED"
    llm.fail = False
    assert asyncio.run(post(app, session_id=first["session_id"])).json()["reply"] == "Reply 3"
    assert len(llm.calls[-1]) == 3


def test_tts_failure_preserves_text_turn():
    app, llm, _, tts = setup()
    tts.fail = True
    first = asyncio.run(post(app)).json()
    assert first["audio"] is None
    assert first["warning"]
    tts.fail = False
    second = asyncio.run(post(app, session_id=first["session_id"])).json()
    assert second["reply"] == "Reply 3"
    assert len(llm.calls[-1]) == 3


def test_invalid_session_and_language():
    app, _, _, _ = setup()
    assert asyncio.run(post(app, session_id="missing")).json()["error"]["code"] == "SESSION_NOT_FOUND"
    assert asyncio.run(post(app, language="fr-FR")).json()["error"]["code"] == "UNSUPPORTED_LANGUAGE"


def test_concurrent_voice_turns_preserve_history():
    app, llm, _, _ = setup()
    first = asyncio.run(post(app)).json()

    async def send():
        return await asyncio.gather(post(app, session_id=first["session_id"]), post(app, session_id=first["session_id"]))

    responses = asyncio.run(send())
    assert all(response.status_code == 200 for response in responses)
    assert sorted(len(call) for call in llm.calls[1:]) == [3, 5]
