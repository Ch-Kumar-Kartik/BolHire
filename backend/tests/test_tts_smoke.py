import asyncio

from app.providers.gnani import SpeechProviderError
from scripts import tts_smoke
from test_gnani_speech import tts_wav


def configure(monkeypatch):
    monkeypatch.setenv("SPEECH_PROVIDER", "gnani")
    monkeypatch.setenv("GNANI_API_KEY", "test-secret")
    monkeypatch.setenv("GNANI_TTS_MODEL", "timbre-v3.0")
    monkeypatch.setenv("GNANI_TTS_VOICE", "Shivani")


def test_check_never_constructs_provider_or_writes_file(monkeypatch, tmp_path, capsys):
    configure(monkeypatch)

    class ForbiddenProvider:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("--check must not construct a provider")

    monkeypatch.setattr(tts_smoke, "GnaniTTSProvider", ForbiddenProvider)
    output = tmp_path / "output.wav"
    assert tts_smoke.main(["--check", "--output", str(output)]) == 0
    assert not output.exists()
    assert "No API request made" in capsys.readouterr().out


def test_run_requires_explicit_confirmation(monkeypatch, tmp_path, capsys):
    configure(monkeypatch)
    assert tts_smoke.main(["--run", "--output", str(tmp_path / "output.wav")]) == 1
    assert "--confirm-billable" in capsys.readouterr().out


def test_run_calls_tts_once_and_saves_playable_wav(monkeypatch, tmp_path, capsys):
    configure(monkeypatch)
    calls = []

    class FakeTTS:
        def __init__(self, key, model, voice, *, diagnostics=None):
            calls.append((key, model, voice))

        async def synthesize(self, text, language):
            calls.append((text, language))
            await asyncio.sleep(0)
            return "audio/wav", tts_wav()

    monkeypatch.setattr(tts_smoke, "GnaniTTSProvider", FakeTTS)
    output = tmp_path / "output.wav"
    assert tts_smoke.main(["--run", "--confirm-billable", "--output", str(output)]) == 0
    assert calls == [("test-secret", "timbre-v3.0", "Shivani"), ("Hello, welcome to BolHire.", "en-IN")]
    assert output.read_bytes() == tts_wav()
    printed = capsys.readouterr().out
    assert "Latency:" in printed
    assert "0.01 s" in printed
    assert "test-secret" not in printed


def test_existing_output_prevents_request(monkeypatch, tmp_path, capsys):
    configure(monkeypatch)
    output = tmp_path / "output.wav"
    output.write_bytes(b"keep")

    class ForbiddenProvider:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("existing output must prevent a request")

    monkeypatch.setattr(tts_smoke, "GnaniTTSProvider", ForbiddenProvider)
    assert tts_smoke.main(["--run", "--confirm-billable", "--output", str(output)]) == 1
    assert output.read_bytes() == b"keep"
    assert "already exists" in capsys.readouterr().out


def test_invalid_audio_is_not_saved(monkeypatch, tmp_path, capsys):
    configure(monkeypatch)

    class FakeTTS:
        def __init__(self, *_args, **_kwargs):
            pass

        async def synthesize(self, _text, _language):
            return "audio/wav", b"not a WAV"

    monkeypatch.setattr(tts_smoke, "GnaniTTSProvider", FakeTTS)
    output = tmp_path / "output.wav"
    assert tts_smoke.main(["--run", "--confirm-billable", "--output", str(output)]) == 2
    assert not output.exists()
    assert "GNANI_TTS_INVALID_RESPONSE" in capsys.readouterr().out


def test_provider_failure_is_reported_without_file_or_secret(monkeypatch, tmp_path, capsys):
    configure(monkeypatch)
    calls = []

    class FakeTTS:
        def __init__(self, *_args, **_kwargs):
            pass

        async def synthesize(self, _text, _language):
            calls.append(1)
            raise SpeechProviderError("GNANI_TTS_RATE_LIMITED", "Gnani rate limit reached.", 429)

    monkeypatch.setattr(tts_smoke, "GnaniTTSProvider", FakeTTS)
    output = tmp_path / "output.wav"
    assert tts_smoke.main(["--run", "--confirm-billable", "--output", str(output)]) == 2
    assert calls == [1]
    assert not output.exists()
    printed = capsys.readouterr().out
    assert "GNANI_TTS_RATE_LIMITED" in printed
    assert "test-secret" not in printed


def test_diagnostics_require_explicit_billable_run(monkeypatch, capsys):
    configure(monkeypatch)
    assert tts_smoke.main(["--check", "--diagnostic"]) == 1
    assert "requires --run" in capsys.readouterr().out
