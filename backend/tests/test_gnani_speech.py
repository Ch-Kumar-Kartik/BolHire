import asyncio
import base64
import io
import json
import struct
import wave

import httpx
import pytest

from app.main import create_app
from app.providers.gnani import GnaniSTTProvider, GnaniTTSProvider, SpeechProviderError, normalize_tts_wav, validate_tts_wav
from app.providers.speech_config import SpeechSettings
from test_voice_chat import post, wav_bytes


def tts_wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\0\0" * 480)
    return output.getvalue()


def wav_chunk(kind: bytes, payload: bytes, *, declared: int | None = None) -> bytes:
    size = len(payload) if declared is None else declared
    return kind + struct.pack("<I", size) + payload + (b"\0" if len(payload) & 1 else b"")


def synthetic_wav(*, riff_size: int | None = None, data_size: int | None = None, extensible: bool = False) -> bytes:
    fmt = struct.pack("<HHIIHH", 0xfffe if extensible else 1, 1, 48000, 96000, 2, 16)
    if extensible:
        fmt += struct.pack("<HHI", 22, 16, 0) + bytes.fromhex("0100000000001000800000aa00389b71")
    chunks = wav_chunk(b"fmt ", fmt) + wav_chunk(b"LIST", b"abc") + wav_chunk(b"data", b"\0\0" * 480, declared=data_size)
    return b"RIFF" + struct.pack("<I", len(chunks) + 4 if riff_size is None else riff_size) + b"WAVE" + chunks


def stt_provider(handler):
    return GnaniSTTProvider("test-secret", transport=httpx.MockTransport(handler))


def tts_provider(handler):
    return GnaniTTSProvider("test-secret", "timbre-v3.0", "Shivani", transport=httpx.MockTransport(handler))


def test_stt_sends_documented_multipart_and_parses_transcript():
    recording = wav_bytes()

    def handler(request):
        assert request.url.path == "/stt/v3"
        assert request.headers["X-API-Key-ID"] == "test-secret"
        assert "multipart/form-data" in request.headers["content-type"]
        assert b'name="audio_file"' in request.content
        assert b'name="language_code"' in request.content
        assert b"hi-IN" in request.content
        assert recording in request.content
        return httpx.Response(200, json={"success": True, "transcript": "नमस्ते"})

    assert asyncio.run(stt_provider(handler).transcribe(recording, "hi-IN")) == "नमस्ते"


@pytest.mark.parametrize(("response", "code"), [
    (httpx.Response(200, json={"success": True, "transcript": "  "}), "GNANI_STT_EMPTY_TRANSCRIPT"),
    (httpx.Response(200, content=b"{"), "GNANI_STT_INVALID_RESPONSE"),
    (httpx.Response(200, json={"success": False, "transcript": "text"}), "GNANI_STT_INVALID_RESPONSE"),
    (httpx.Response(403), "GNANI_STT_AUTH_FAILED"),
    (httpx.Response(429), "GNANI_STT_RATE_LIMITED"),
])
def test_stt_rejects_failed_responses(response, code):
    provider = stt_provider(lambda _request: response)
    with pytest.raises(SpeechProviderError) as raised:
        asyncio.run(provider.transcribe(wav_bytes(), "en-IN"))
    assert raised.value.code == code


def test_stt_timeout_has_no_retry():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(SpeechProviderError) as raised:
        asyncio.run(stt_provider(handler).transcribe(wav_bytes(), "en-IN"))
    assert raised.value.code == "GNANI_STT_TIMEOUT"
    assert calls == 1


def test_tts_sends_documented_json_and_returns_wav():
    output = tts_wav()

    def handler(request):
        assert request.url.path == "/api/v1/tts/inference"
        assert request.headers["X-API-Key-ID"] == "test-secret"
        body = json.loads(request.content)
        assert body == {
            "text": "Hello", "voice": "Shivani", "model": "timbre-v3.0",
            "language": "en-IN",
            "audio_config": {
                "sample_rate": 48000, "num_channels": 1, "sample_width": 2,
                "encoding": "linear_pcm", "container": "wav",
            },
        }
        return httpx.Response(200, headers={"content-type": "audio/wav"}, content=output)

    assert asyncio.run(tts_provider(handler).synthesize("Hello", "en-IN")) == ("audio/wav", output)


@pytest.mark.parametrize("audio", [
    synthetic_wav(),
    synthetic_wav(riff_size=0xffffffff),
    synthetic_wav(riff_size=0xffffffff, data_size=0xffffffff),
    synthetic_wav(extensible=True),
])
def test_tts_accepts_complete_wav_variants(audio):
    assert validate_tts_wav(audio) == 0.01
    normalized = normalize_tts_wav(audio)
    assert struct.unpack_from("<I", normalized, 4)[0] == len(normalized) - 8
    assert b"\0\0" * 480 == normalized[-960:]
    with wave.open(io.BytesIO(normalized), "rb") as wav:
        assert wav.getnframes() == 480
        assert wav.readframes(480) == b"\0\0" * 480
    assert asyncio.run(tts_provider(lambda _request: httpx.Response(200, headers={"content-type": "audio/wav"}, content=audio)).synthesize("Hello", "en-IN")) == ("audio/wav", normalized)


@pytest.mark.parametrize(("audio", "reason"), [
    (synthetic_wav(riff_size=123), "riff_size_mismatch"),
    (synthetic_wav()[:-2], "riff_size_mismatch"),
    (synthetic_wav(riff_size=0xffffffff)[:-2], "truncated_chunk"),
    (synthetic_wav(riff_size=0xffffffff, data_size=0xffffffff)[:-1], "missing_or_invalid_pcm_data"),
    (synthetic_wav(data_size=999999), "truncated_chunk"),
])
def test_tts_rejects_inconsistent_or_truncated_wav(audio, reason):
    with pytest.raises(SpeechProviderError) as raised:
        validate_tts_wav(audio)
    assert raised.value.reason == reason


def test_tts_reads_stream_completely_and_reports_metadata_only():
    audio = synthetic_wav(riff_size=0xffffffff, data_size=0xffffffff)
    diagnostics = []

    class SplitStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield audio[:35]
            yield audio[35:]

    provider = GnaniTTSProvider("test-secret", "timbre-v3.0", "Shivani",
                                transport=httpx.MockTransport(lambda _request: httpx.Response(
                                    200, headers={"content-type": "audio/wav", "content-length": str(len(audio))}, stream=SplitStream())),
                                diagnostics=diagnostics.append)
    assert asyncio.run(provider.synthesize("Hello", "en-IN")) == ("audio/wav", normalize_tts_wav(audio))
    assert diagnostics == [{
        "http_status": 200, "content_type": "audio/wav", "content_length": len(audio), "content_encoding": None,
        "received_bytes": len(audio), "riff_signature": True, "wave_signature": True,
        "riff_size": 0xffffffff, "data_size": 0xffffffff, "failure_reason": None,
    }]


def test_voice_route_returns_standard_wav_from_streaming_tts():
    raw = synthetic_wav(riff_size=0xffffffff, data_size=0xffffffff)
    stt = stt_provider(lambda _request: httpx.Response(200, json={"success": True, "transcript": "Hello"}))
    tts = tts_provider(lambda _request: httpx.Response(200, headers={"content-type": "audio/wav"}, content=raw))
    response = asyncio.run(post(create_app(stt=stt, tts=tts)))
    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "Hello"
    assert body["reply"].startswith("Mock response [en-IN]")
    assert body["audio"]["mime_type"] == "audio/wav"
    normalized = base64.b64decode(body["audio"]["base64"])
    assert normalized == normalize_tts_wav(raw)
    assert struct.unpack_from("<I", normalized, 4)[0] == len(normalized) - 8


@pytest.mark.parametrize(("response", "code"), [
    (httpx.Response(200, headers={"content-type": "application/json"}, json={"error": "bad"}), "GNANI_TTS_INVALID_RESPONSE"),
    (httpx.Response(200, headers={"content-type": "audio/wav"}, content=b"not a wav"), "GNANI_TTS_INVALID_RESPONSE"),
    (httpx.Response(200, headers={"content-type": "audio/wav"}, content=b""), "GNANI_TTS_INVALID_RESPONSE"),
    (httpx.Response(403), "GNANI_TTS_AUTH_FAILED"),
    (httpx.Response(429), "GNANI_TTS_RATE_LIMITED"),
])
def test_tts_rejects_failed_responses(response, code):
    with pytest.raises(SpeechProviderError) as raised:
        asyncio.run(tts_provider(lambda _request: response).synthesize("Hello", "en-IN"))
    assert raised.value.code == code


def test_tts_timeout_has_no_retry():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(SpeechProviderError) as raised:
        asyncio.run(tts_provider(handler).synthesize("Hello", "en-IN"))
    assert raised.value.code == "GNANI_TTS_TIMEOUT"
    assert calls == 1


def test_configuration_defaults_to_mock_without_credentials(monkeypatch):
    assert SpeechSettings.from_env({}).provider == "mock"
    monkeypatch.delenv("SPEECH_PROVIDER", raising=False)
    app = create_app()
    assert asyncio.run(post(app)).json()["estimated_speech_credits"] == 0


@pytest.mark.parametrize("settings", [
    {"SPEECH_PROVIDER": "gnani"},
    {"SPEECH_PROVIDER": "gnani", "GNANI_API_KEY": "key"},
    {"SPEECH_PROVIDER": "gnani", "GNANI_API_KEY": "key", "GNANI_TTS_VOICE": "Nalini"},
    {"SPEECH_PROVIDER": "gnani", "GNANI_API_KEY": "key", "GNANI_TTS_VOICE": "Shivani", "GNANI_TTS_MODEL": "timbre-v2.5"},
])
def test_gnani_configuration_requires_key_and_v3_voice(settings):
    with pytest.raises(ValueError, match="GNANI_"):
        SpeechSettings.from_env(settings)


def test_gnani_configuration_selects_valid_voice():
    settings = SpeechSettings.from_env({"SPEECH_PROVIDER": "gnani", "GNANI_API_KEY": "key", "GNANI_TTS_VOICE": "Shivani"})
    assert settings.tts_model == "timbre-v3.0"
    assert settings.tts_voice == "Shivani"


def test_app_startup_rejects_missing_gnani_configuration(monkeypatch):
    monkeypatch.setenv("SPEECH_PROVIDER", "gnani")
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GNANI_API_KEY"):
        create_app()


def test_real_provider_contract_through_route_and_partial_tts_failure():
    stt = stt_provider(lambda _request: httpx.Response(200, json={"success": True, "transcript": "Hello"}))
    tts = tts_provider(lambda _request: httpx.Response(429))
    app = create_app(stt=stt, tts=tts)
    first = asyncio.run(post(app)).json()
    assert first["transcript"] == "Hello"
    assert first["audio"] is None
    assert "rate limit" in first["warning"]
    assert first["estimated_speech_credits"] is None
    second = asyncio.run(post(app, session_id=first["session_id"])).json()
    assert second["session_id"] == first["session_id"]
    assert "Prior messages supplied: 2." in second["reply"]


def test_stt_failure_does_not_start_a_turn():
    stt = stt_provider(lambda _request: httpx.Response(403))
    tts = tts_provider(lambda _request: httpx.Response(200, headers={"content-type": "audio/wav"}, content=tts_wav()))
    response = asyncio.run(post(create_app(stt=stt, tts=tts)))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GNANI_STT_AUTH_FAILED"
