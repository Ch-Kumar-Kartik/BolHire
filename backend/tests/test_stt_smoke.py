import asyncio

from scripts import stt_smoke
from test_voice_chat import wav_bytes


def test_check_validates_without_a_request(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SPEECH_PROVIDER", "gnani")
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    monkeypatch.setenv("GNANI_TTS_VOICE", "Shivani")
    audio = tmp_path / "speech.wav"
    audio.write_bytes(wav_bytes())

    class ForbiddenProvider:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("--check must not construct a live provider")

    monkeypatch.setattr(stt_smoke, "GnaniSTTProvider", ForbiddenProvider)
    assert stt_smoke.main(["--check", "--audio", str(audio)]) == 0
    assert "No API request made." in capsys.readouterr().out


def test_run_requires_explicit_confirmation(monkeypatch, capsys):
    monkeypatch.setenv("SPEECH_PROVIDER", "gnani")
    assert stt_smoke.main(["--run", "--audio", "speech.wav"]) == 1
    assert "--confirm-billable" in capsys.readouterr().out


def test_run_invokes_stt_once_without_tts_or_llm(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SPEECH_PROVIDER", "gnani")
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    monkeypatch.setenv("GNANI_TTS_VOICE", "Shivani")
    audio = tmp_path / "speech.wav"
    audio.write_bytes(wav_bytes())
    calls = []

    class FakeSTT:
        def __init__(self, _key):
            pass

        async def transcribe(self, payload, language):
            calls.append((payload, language))
            await asyncio.sleep(0)
            return "hello"

    monkeypatch.setattr(stt_smoke, "GnaniSTTProvider", FakeSTT)
    assert stt_smoke.main(["--run", "--confirm-billable", "--audio", str(audio), "--language", "en-IN"]) == 0
    assert calls == [(wav_bytes(), "en-IN")]
    output = capsys.readouterr().out
    assert "Transcript: hello" in output
    assert "Latency:" in output
    assert "test-key" not in output
