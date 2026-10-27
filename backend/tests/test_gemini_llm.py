import asyncio
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors, types

from app.main import create_app
from app.models import ConversationMessage
from app.providers.gemini import GeminiLLMProvider, GeminiProviderError
from app.providers.llm import MockLLMProvider
from app.providers.llm_config import DEFAULT_SYSTEM_PROMPT, LLMSettings
from app.providers.speech import MockTTSProvider
from app.services.pipeline import TextConversationPipeline
from app.services.sessions import InMemorySessionStore
from test_text_chat import post_json
from test_voice_chat import post


def settings(**overrides: str) -> LLMSettings:
    values = {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "test-secret"}
    values.update(overrides)
    return LLMSettings.from_env(values)


def reply_response(text: str = "Hello") -> types.GenerateContentResponse:
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
        finish_reason=types.FinishReason.STOP,
    )])


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.closed = False
        self.aio = SimpleNamespace(models=self)
        self.aio.aclose = self.aclose

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def aclose(self):
        self.closed = True


def test_configuration_defaults_and_custom_values():
    assert LLMSettings.from_env({}).provider == "mock"
    assert settings().model == "gemini-3.5-flash-lite"
    assert settings().thinking_level == "minimal"
    assert settings().thinking_budget is None
    configured = settings(
        GEMINI_MODEL="gemini-2.5-flash", GEMINI_TIMEOUT_SECONDS="12.5",
        GEMINI_MAX_OUTPUT_TOKENS="256", GEMINI_THINKING_BUDGET="-1",
        GEMINI_SYSTEM_PROMPT="Short custom prompt.",
    )
    assert (configured.timeout_seconds, configured.max_output_tokens, configured.thinking_budget) == (12.5, 256, -1)
    assert configured.thinking_level is None
    assert configured.system_prompt == "Short custom prompt."
    assert settings(GEMINI_MODEL="gemini-2.5-flash").thinking_budget == 0
    assert settings(GEMINI_MODEL="gemini-3.8-flash").thinking_budget is None
    assert settings(GEMINI_THINKING_LEVEL="LOW").thinking_level == "low"


def test_sdk_client_uses_one_attempt_and_explicit_timeout(monkeypatch):
    captured = {}
    client = FakeClient(reply_response())

    def make_client(*, api_key, http_options):
        captured.update(api_key=api_key, http_options=http_options)
        return client

    monkeypatch.setattr("app.providers.gemini.genai.Client", make_client)
    provider = GeminiLLMProvider(settings(GEMINI_TIMEOUT_SECONDS="12.5"))
    assert captured["api_key"] == "test-secret"
    assert captured["http_options"].api_version == "v1"
    assert captured["http_options"].timeout == 12500
    assert captured["http_options"].retry_options.attempts == 1
    assert asyncio.run(provider.generate([ConversationMessage(role="user", content="Hi")], "en-IN")) == "Hello"
    assert len(client.calls) == 1


@pytest.mark.parametrize(("name", "value"), [
    ("LLM_PROVIDER", "unknown"),
    ("GEMINI_API_KEY", " "),
    ("GEMINI_MODEL", ""),
    ("GEMINI_MODEL", "bad model"),
    ("GEMINI_TIMEOUT_SECONDS", "NaN"),
    ("GEMINI_TIMEOUT_SECONDS", "0"),
    ("GEMINI_TIMEOUT_SECONDS", "121"),
    ("GEMINI_MAX_OUTPUT_TOKENS", "0"),
    ("GEMINI_MAX_OUTPUT_TOKENS", "not-a-number"),
    ("GEMINI_THINKING_LEVEL", "none"),
    ("GEMINI_SYSTEM_PROMPT", " "),
])
def test_invalid_configuration_fails_without_secret_in_error(name, value):
    with pytest.raises(ValueError) as raised:
        settings(**{name: value})
    assert "test-secret" not in str(raised.value)


def test_thinking_settings_reject_incompatible_model_options():
    for values in (
        {"GEMINI_THINKING_BUDGET": "0"},
        {"GEMINI_MODEL": "gemini-2.5-flash", "GEMINI_THINKING_LEVEL": "minimal"},
        {"GEMINI_MODEL": "gemini-2.5-flash", "GEMINI_THINKING_BUDGET": "-2"},
    ):
        with pytest.raises(ValueError):
            settings(**values)


@pytest.mark.parametrize(("language", "instruction"), [
    ("en-IN", "Reply in English."),
    ("hi-IN", "Reply in Hindi."),
])
def test_history_and_system_instruction_are_sent_once_in_order(language, instruction):
    client = FakeClient(reply_response("  A short answer.  "))
    provider = GeminiLLMProvider(settings(), client=client)
    history = [
        ConversationMessage(role="user", content="first"),
        ConversationMessage(role="assistant", content="response"),
        ConversationMessage(role="user", content="second"),
    ]
    assert asyncio.run(provider.generate(history, language)) == "A short answer."
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request["model"] == "gemini-3.5-flash-lite"
    assert [content.role for content in request["contents"]] == ["user", "model", "user"]
    assert [content.parts[0].text for content in request["contents"]] == ["first", "response", "second"]
    config = request["config"]
    assert DEFAULT_SYSTEM_PROMPT in config.system_instruction
    assert instruction in config.system_instruction
    assert "Romanized Hindi" in config.system_instruction
    assert config.max_output_tokens == 1024
    assert config.thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    assert config.thinking_config.thinking_budget is None
    assert config.automatic_continuation is False
    asyncio.run(provider.aclose())
    assert client.closed


def test_gemini_2_5_keeps_numeric_thinking_budget():
    client = FakeClient(reply_response())
    provider = GeminiLLMProvider(settings(GEMINI_MODEL="gemini-2.5-flash"), client=client)
    assert asyncio.run(provider.generate([ConversationMessage(role="user", content="Hi")], "en-IN")) == "Hello"
    assert client.calls[0]["model"] == "gemini-2.5-flash"
    thinking = client.calls[0]["config"].thinking_config
    assert thinking.thinking_budget == 0
    assert thinking.thinking_level is None
    assert len(client.calls) == 1


def test_custom_gemini_3_thinking_level_reaches_sdk():
    client = FakeClient(reply_response())
    provider = GeminiLLMProvider(settings(GEMINI_THINKING_LEVEL="low"), client=client)
    assert asyncio.run(provider.generate([ConversationMessage(role="user", content="Hi")], "en-IN")) == "Hello"
    thinking = client.calls[0]["config"].thinking_config
    assert thinking.thinking_level == types.ThinkingLevel.LOW
    assert thinking.thinking_budget is None
    assert len(client.calls) == 1


@pytest.mark.parametrize(("status", "code", "http_status"), [
    (401, "GEMINI_AUTH_FAILED", 502),
    (403, "GEMINI_AUTH_FAILED", 502),
    (404, "GEMINI_MODEL_UNAVAILABLE", 502),
    (429, "GEMINI_RATE_LIMITED", 429),
    (500, "GEMINI_UNAVAILABLE", 502),
    (504, "GEMINI_TIMEOUT", 504),
])
def test_upstream_errors_are_safe_and_single_attempt(status, code, http_status):
    client = FakeClient(errors.APIError(status, {"error": {"message": "test-secret upstream details"}}))
    provider = GeminiLLMProvider(settings(), client=client)
    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate([ConversationMessage(role="user", content="Hello")], "en-IN"))
    assert (raised.value.code, raised.value.status_code) == (code, http_status)
    assert "test-secret" not in str(raised.value)
    assert len(client.calls) == 1


@pytest.mark.parametrize(("result", "code"), [
    (reply_response("   "), "GEMINI_EMPTY_RESPONSE"),
    (types.GenerateContentResponse(candidates=[]), "GEMINI_INVALID_RESPONSE"),
    (types.GenerateContentResponse(candidates=[types.Candidate(finish_reason=types.FinishReason.SAFETY)]), "GEMINI_BLOCKED_OUTPUT"),
    (types.GenerateContentResponse(candidates=[types.Candidate(finish_reason=types.FinishReason.MAX_TOKENS)]), "GEMINI_INVALID_RESPONSE"),
    (SimpleNamespace(prompt_feedback=None, candidates=[None]), "GEMINI_INVALID_RESPONSE"),
    (httpx.ReadTimeout("test-secret"), "GEMINI_TIMEOUT"),
])
def test_empty_blocked_malformed_and_timeout_responses(result, code):
    client = FakeClient(result)
    provider = GeminiLLMProvider(settings(), client=client)
    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate([ConversationMessage(role="user", content="Hello")], "en-IN"))
    assert raised.value.code == code
    assert len(client.calls) == 1
    assert "test-secret" not in str(raised.value)


def test_session_continuity_and_failure_does_not_mutate_history():
    client = FakeClient(reply_response("First reply"))
    provider = GeminiLLMProvider(settings(), client=client)
    pipeline = TextConversationPipeline(llm=provider, sessions=InMemorySessionStore())
    app = create_app(pipeline)
    first = asyncio.run(post_json(app, {"text": "first", "language": "en-IN"})).json()
    client.result = errors.APIError(429, {"error": {"message": "test-secret"}})
    failed = asyncio.run(post_json(app, {
        "session_id": first["session_id"], "text": "failed", "language": "en-IN",
    }))
    client.result = reply_response("Second reply")
    second = asyncio.run(post_json(app, {
        "session_id": first["session_id"], "text": "second", "language": "en-IN",
    })).json()
    assert failed.status_code == 429
    assert failed.json()["error"]["code"] == "GEMINI_RATE_LIMITED"
    assert "test-secret" not in failed.text
    assert second["session_id"] == first["session_id"]
    assert [item.parts[0].text for item in client.calls[-1]["contents"]] == ["first", "First reply", "second"]
    assert second["reply"] == "Second reply"


def test_provider_selection_is_independent_of_speech(monkeypatch):
    created = []

    class FakeGemini:
        def __init__(self, config):
            created.append(self)
            assert config.model == "gemini-3.5-flash-lite"
            self.closed = False

        async def generate(self, messages, language):
            return f"Real reply [{language}]"

        async def aclose(self):
            self.closed = True

    class FakeSTT:
        def __init__(self, key):
            assert key == "speech-secret"

        async def transcribe(self, audio, language):
            return "Hello"

    class FakeTTS:
        def __init__(self, key, model, voice):
            assert (key, model, voice) == ("speech-secret", "timbre-v3.0", "Shivani")

        async def synthesize(self, text, language):
            return await MockTTSProvider().synthesize(text, language)

    monkeypatch.setattr("app.main.GeminiLLMProvider", FakeGemini)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.setenv("SPEECH_PROVIDER", "mock")
    mock_speech_app = create_app()
    text = asyncio.run(post_json(mock_speech_app, {"text": "Hello", "language": "en-IN"})).json()
    assert text["reply"] == "Real reply [en-IN]"
    assert asyncio.run(post(mock_speech_app)).json()["usage"]["speech_provider"] == "mock"

    monkeypatch.setenv("SPEECH_PROVIDER", "gnani")
    monkeypatch.setenv("GNANI_API_KEY", "speech-secret")
    monkeypatch.setenv("GNANI_TTS_VOICE", "Shivani")
    monkeypatch.setattr("app.main.GnaniSTTProvider", FakeSTT)
    monkeypatch.setattr("app.main.GnaniTTSProvider", FakeTTS)
    real_speech_app = create_app()
    voice = asyncio.run(post(real_speech_app)).json()
    assert voice["reply"] == "Real reply [en-IN]"
    assert voice["usage"]["speech_provider"] == "gnani"
    assert voice["estimated_speech_credits"] is None
    assert len(created) == 2

    async def close_apps():
        async with mock_speech_app.router.lifespan_context(mock_speech_app):
            pass
        async with real_speech_app.router.lifespan_context(real_speech_app):
            pass

    asyncio.run(close_apps())
    assert all(provider.closed for provider in created)


def test_mock_llm_remains_the_default(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("SPEECH_PROVIDER", "mock")
    app = create_app()
    assert isinstance(app.state.conversation_pipeline._llm, MockLLMProvider)
    assert asyncio.run(post_json(app, {"text": "Hello", "language": "en-IN"})).json()["reply"].startswith("Mock response")
