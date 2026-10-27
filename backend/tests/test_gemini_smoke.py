import asyncio

from app.providers.gemini import GeminiProviderError
from scripts import gemini_smoke


def configure(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")


def test_check_validates_without_constructing_client(monkeypatch, capsys):
    configure(monkeypatch)

    class ForbiddenProvider:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("--check must not construct a provider")

    monkeypatch.setattr(gemini_smoke, "GeminiLLMProvider", ForbiddenProvider)
    assert gemini_smoke.main(["--check"]) == 0
    printed = capsys.readouterr().out
    assert "No API request made." in printed
    assert "test-secret" not in printed


def test_run_requires_explicit_confirmation(monkeypatch, capsys):
    configure(monkeypatch)
    assert gemini_smoke.main(["--run"]) == 1
    assert "--confirm-billable" in capsys.readouterr().out


def test_run_makes_one_text_request_and_closes_client(monkeypatch, capsys):
    configure(monkeypatch)
    calls = []

    class FakeProvider:
        def __init__(self, settings):
            assert settings.provider == "gemini"

        async def generate(self, messages, language):
            calls.append((messages[0].content, language))
            await asyncio.sleep(0)
            return "A short reply."

        async def aclose(self):
            calls.append("closed")

    monkeypatch.setattr(gemini_smoke, "GeminiLLMProvider", FakeProvider)
    assert gemini_smoke.main(["--run", "--confirm-billable", "--text", "नमस्ते", "--language", "hi-IN"]) == 0
    assert calls == [("नमस्ते", "hi-IN"), "closed"]
    printed = capsys.readouterr().out
    assert "A short reply." in printed
    assert "Latency:" in printed
    assert "test-secret" not in printed


def test_provider_failure_stays_safe_and_closes_client(monkeypatch, capsys):
    configure(monkeypatch)
    calls = []

    class FakeProvider:
        def __init__(self, _settings):
            pass

        async def generate(self, _messages, _language):
            calls.append("called")
            raise GeminiProviderError("GEMINI_RATE_LIMITED", "Gemini rate limit reached.", 429)

        async def aclose(self):
            calls.append("closed")

    monkeypatch.setattr(gemini_smoke, "GeminiLLMProvider", FakeProvider)
    assert gemini_smoke.main(["--run", "--confirm-billable"]) == 2
    assert calls == ["called", "closed"]
    printed = capsys.readouterr().out
    assert "GEMINI_RATE_LIMITED" in printed
    assert "test-secret" not in printed
