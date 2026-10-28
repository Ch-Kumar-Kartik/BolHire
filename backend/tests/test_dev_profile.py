import asyncio

import httpx
import pytest

from app.main import create_app
from app.models import ConversationMessage
from app.services.candidate_profile import CandidateProfileUpdate
from app.services.pipeline import TextConversationPipeline
from app.services.sessions import InMemorySessionStore

PATH = "/api/v1/dev/candidate-profile"


class NoProviderCalls:
    async def generate(self, *args, **kwargs):
        pytest.fail("Unexpected LLM call")

    async def generate_intake(self, *args, **kwargs):
        pytest.fail("Unexpected intake call")

    async def transcribe(self, *args, **kwargs):
        pytest.fail("Unexpected STT call")

    async def synthesize(self, *args, **kwargs):
        pytest.fail("Unexpected TTS call")


def make_app(monkeypatch, enabled=None):
    monkeypatch.delenv("BOLHIRE_DEV_PROFILE_INSPECTION", raising=False)
    if enabled is not None:
        monkeypatch.setenv("BOLHIRE_DEV_PROFILE_INSPECTION", enabled)
    clock = [0.0]
    sessions = InMemorySessionStore(clock=lambda: clock[0])
    provider = NoProviderCalls()
    app = create_app(TextConversationPipeline(llm=provider, sessions=sessions), stt=provider, tts=provider)
    return app, sessions, clock


async def inspect(app, session_id, peer=("127.0.0.1", 1234), headers=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=peer), base_url="http://local",
    ) as client:
        return await client.post(PATH, json={"session_id": session_id}, headers=headers)


def exchange():
    return [ConversationMessage(role="user", content="synthetic fixture"),
            ConversationMessage(role="assistant", content="fixture reply")]


@pytest.mark.parametrize("enabled", [None, "0", "true", "yes"])
def test_disabled_unless_explicitly_enabled(monkeypatch, enabled):
    app, _, _ = make_app(monkeypatch, enabled)
    assert asyncio.run(inspect(app, "missing")).status_code == 404


@pytest.mark.parametrize("peer", [("127.0.0.1", 1234), ("::1", 1234)])
def test_profile_snapshot_without_mutation_or_provider_calls(monkeypatch, caplog, peer):
    app, sessions, clock = make_app(monkeypatch, "1")

    async def scenario():
        session = await sessions.create_candidate_exchange(CandidateProfileUpdate(
            name="Asha", target_role="Python backend developer", experience_level="fresher",
        ), exchange())
        original_profile, original_history = session.candidate_profile, list(session.messages)
        clock[0] = 1799
        response = await inspect(app, session.session_id, peer)
        assert response.status_code == 200
        assert response.json() == {
            "name": "Asha", "target_role": "Python backend developer",
            "experience_level": "fresher", "experience_years": None,
            "technical_skills": None, "preferred_location": None, "work_mode": None,
            "declined_fields": [],
        }
        assert response.headers["cache-control"] == "no-store"
        assert session.candidate_profile is original_profile
        assert session.messages == original_history
        assert session.last_activity == 0
        assert session.session_id not in caplog.text
        assert "Asha" not in caplog.text
        assert PATH not in app.openapi()["paths"]
        clock[0] = 1800
        expired = await inspect(app, session.session_id, peer)
        assert expired.status_code == 410
        assert expired.json()["error"]["code"] == "SESSION_EXPIRED"
        assert session.candidate_profile is original_profile
        assert session.messages == original_history and session.last_activity == 0
        assert session.session_id not in sessions._sessions
        assert (await inspect(app, session.session_id, peer)).status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize("peer,headers", [
    (("192.0.2.1", 1234), {}), (("localhost", 1234), {}), (None, {}),
    (("127.0.0.1", 1234), {"X-Forwarded-For": "127.0.0.1"}),
    (("127.0.0.1", 1234), {"Forwarded": "for=127.0.0.1"}),
    (("127.0.0.1", 1234), {"X-Forwarded-Proto": "https"}),
    (("192.0.2.1", 1234), {"Origin": "http://localhost:5173"}),
])
def test_rejects_nonlocal_and_forwarded_requests(monkeypatch, peer, headers):
    app, _, _ = make_app(monkeypatch, "1")
    assert asyncio.run(inspect(app, "missing", peer, headers)).status_code == 403


def test_missing_general_and_declined_fields(monkeypatch):
    app, sessions, _ = make_app(monkeypatch, "1")

    async def scenario():
        missing = await inspect(app, "missing")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "SESSION_NOT_FOUND"
        general = await sessions.create(exchange())
        response = await inspect(app, general.session_id)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SESSION_WORKFLOW_MISMATCH"
        assert general.messages == exchange() and general.last_activity == 0
        intake = await sessions.create_candidate_exchange(
            CandidateProfileUpdate(decline_fields=["work_mode"]), exchange(),
        )
        response = await inspect(app, intake.session_id)
        assert response.json()["declined_fields"] == ["work_mode"]
        assert response.json()["work_mode"] is None

    asyncio.run(scenario())
