import asyncio
from collections.abc import Sequence

import httpx

from app.main import create_app
from app.models import ConversationMessage
from app.providers.llm import MockLLMProvider
from app.services.pipeline import TextConversationPipeline
from app.services.sessions import InMemorySessionStore


class RecordingLLM:
    def __init__(self) -> None:
        self.calls: list[list[ConversationMessage]] = []
        self.fail = False

    async def generate(
        self, messages: Sequence[ConversationMessage], language: str
    ) -> str:
        self.calls.append(list(messages))
        if self.fail:
            raise RuntimeError("configured mock failure")
        return f"reply:{messages[-1].content}:{language}"


class SlowRecordingLLM(RecordingLLM):
    async def generate(
        self, messages: Sequence[ConversationMessage], language: str
    ) -> str:
        await asyncio.sleep(0.01)
        return await super().generate(messages, language)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def make_app(
    llm: RecordingLLM | None = None,
    *,
    idle_seconds: float = 30 * 60,
    clock: FakeClock | None = None,
):
    provider = llm or RecordingLLM()
    sessions = InMemorySessionStore(
        idle_seconds=idle_seconds, clock=clock or FakeClock()
    )
    pipeline = TextConversationPipeline(llm=provider, sessions=sessions)
    return create_app(pipeline), provider


async def post_json(app, payload: dict[str, object]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        return await client.post("/api/v1/chat/text", json=payload)


def test_creates_a_session_with_text_response_shape() -> None:
    app, _ = make_app()

    response = asyncio.run(
        post_json(app, {"session_id": None, "text": "Hello", "language": "en-IN"})
    )

    body = response.json()
    assert response.status_code == 200
    assert isinstance(body["session_id"], str)
    assert body["transcript"] == "Hello"
    assert body["reply"] == "reply:Hello:en-IN"
    assert body["audio"] is None
    assert set(body["timings_ms"]) == {"llm", "total"}
    assert body["estimated_speech_credits"] == 0


def test_multiple_turns_pass_previous_messages_to_provider() -> None:
    app, provider = make_app()
    first = asyncio.run(
        post_json(app, {"text": "first", "language": "en-IN"})
    ).json()

    response = asyncio.run(
        post_json(
            app,
            {
                "session_id": first["session_id"],
                "text": "second",
                "language": "en-IN",
            },
        )
    )

    assert response.status_code == 200
    assert provider.calls[1] == [
        ConversationMessage(role="user", content="first"),
        ConversationMessage(role="assistant", content="reply:first:en-IN"),
        ConversationMessage(role="user", content="second"),
    ]


def test_default_mock_provider_reports_prior_message_count() -> None:
    sessions = InMemorySessionStore(clock=FakeClock())
    app = create_app(TextConversationPipeline(llm=MockLLMProvider(), sessions=sessions))
    first = asyncio.run(
        post_json(app, {"text": "one", "language": "en-IN"})
    ).json()

    response = asyncio.run(
        post_json(
            app,
            {"session_id": first["session_id"], "text": "two", "language": "en-IN"},
        )
    )

    assert "Prior messages supplied: 2." in response.json()["reply"]


def test_rejects_invalid_language_and_empty_message() -> None:
    app, _ = make_app()

    invalid_language = asyncio.run(
        post_json(app, {"text": "Hello", "language": "fr-FR"})
    )
    empty_message = asyncio.run(
        post_json(app, {"text": "   ", "language": "en-IN"})
    )

    assert invalid_language.status_code == 422
    assert invalid_language.json()["error"]["code"] == "UNSUPPORTED_LANGUAGE"
    assert empty_message.status_code == 422
    assert empty_message.json()["error"]["code"] == "EMPTY_MESSAGE"


def test_rejects_unknown_session_and_malformed_json() -> None:
    app, _ = make_app()

    unknown = asyncio.run(
        post_json(
            app,
            {"session_id": "not-a-session", "text": "Hello", "language": "en-IN"},
        )
    )

    async def malformed_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post(
                "/api/v1/chat/text",
                content="{",
                headers={"content-type": "application/json"},
            )

    malformed = asyncio.run(malformed_request())

    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "SESSION_NOT_FOUND"
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "MALFORMED_JSON"


def test_expires_sessions_lazily() -> None:
    clock = FakeClock()
    app, _ = make_app(clock=clock)
    first = asyncio.run(
        post_json(app, {"text": "Hello", "language": "en-IN"})
    ).json()
    clock.value = 30 * 60

    response = asyncio.run(
        post_json(
            app,
            {
                "session_id": first["session_id"],
                "text": "Still there?",
                "language": "en-IN",
            },
        )
    )

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "SESSION_EXPIRED"


def test_keeps_only_twenty_complete_exchanges() -> None:
    app, provider = make_app()
    first = asyncio.run(
        post_json(app, {"text": "turn-1", "language": "en-IN"})
    ).json()

    for turn in range(2, 23):
        response = asyncio.run(
            post_json(
                app,
                {
                    "session_id": first["session_id"],
                    "text": f"turn-{turn}",
                    "language": "en-IN",
                },
            )
        )
        assert response.status_code == 200

    last_call = provider.calls[-1]
    assert len(last_call) == 41
    assert last_call[0].content == "turn-2"
    assert last_call[-1].content == "turn-22"


def test_serializes_concurrent_requests_for_one_session() -> None:
    app, provider = make_app(SlowRecordingLLM())
    first = asyncio.run(
        post_json(app, {"text": "initial", "language": "en-IN"})
    ).json()

    async def send_concurrent_turns() -> list[httpx.Response]:
        return await asyncio.gather(
            post_json(
                app,
                {
                    "session_id": first["session_id"],
                    "text": "one",
                    "language": "en-IN",
                },
            ),
            post_json(
                app,
                {
                    "session_id": first["session_id"],
                    "text": "two",
                    "language": "en-IN",
                },
            ),
        )

    responses = asyncio.run(send_concurrent_turns())

    assert [response.status_code for response in responses] == [200, 200]
    assert sorted(len(call) for call in provider.calls[1:]) == [3, 5]


def test_provider_failure_does_not_commit_partial_exchange() -> None:
    provider = RecordingLLM()
    app, _ = make_app(provider)
    first = asyncio.run(
        post_json(app, {"text": "first", "language": "en-IN"})
    ).json()
    provider.fail = True

    failed = asyncio.run(
        post_json(
            app,
            {
                "session_id": first["session_id"],
                "text": "failed-turn",
                "language": "en-IN",
            },
        )
    )
    provider.fail = False
    recovered = asyncio.run(
        post_json(
            app,
            {
                "session_id": first["session_id"],
                "text": "recovered-turn",
                "language": "en-IN",
            },
        )
    )

    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "LLM_GENERATION_FAILED"
    assert recovered.status_code == 200
    assert [message.content for message in provider.calls[-1]] == [
        "first",
        "reply:first:en-IN",
        "recovered-turn",
    ]
