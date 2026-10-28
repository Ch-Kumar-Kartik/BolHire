import asyncio
import io
import wave

import httpx
import pytest

from app.main import create_app
from app.providers.llm import MockLLMProvider
from app.providers.speech import MockSTTProvider, MockTTSProvider
from app.services.candidate_intake import CandidateIntakePipeline, CandidateIntakeProposal, IntakeEvidence
from app.services.candidate_profile import CandidateProfileUpdate
from app.services.pipeline import TextConversationPipeline
from app.services.sessions import InMemorySessionStore

def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0" * 3200)
    return output.getvalue()


class FakeIntakeGenerator:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def generate_intake(self, messages, profile, language):
        self.calls.append((list(messages), profile, language))
        if self.fail:
            raise RuntimeError("fake provider failure")
        latest = messages[-1].content
        if "Asha" in latest:
            return CandidateIntakeProposal(
                update=CandidateProfileUpdate(name="Asha"),
                acknowledgement="Got it.",
                evidence=[IntakeEvidence(field="name", quote="Asha")],
            )
        if "Python" in latest:
            return CandidateIntakeProposal(
                update=CandidateProfileUpdate(skills_add=["Python"]),
                acknowledgement="Got it.",
                evidence=[IntakeEvidence(field="technical_skills", quote="Python")],
            )
        return CandidateIntakeProposal(update=CandidateProfileUpdate(), acknowledgement="Got it.")


class FakeSTT(MockSTTProvider):
    text = "I am Asha."

    async def transcribe(self, audio, language):
        return self.text


class FakeTTS(MockTTSProvider):
    fail = False

    async def synthesize(self, text, language):
        if self.fail:
            raise RuntimeError("fake TTS failure")
        return await super().synthesize(text, language)


def setup():
    sessions = InMemorySessionStore()
    generator = FakeIntakeGenerator()
    stt, tts = FakeSTT(), FakeTTS()
    general = TextConversationPipeline(llm=MockLLMProvider(), sessions=sessions)
    intake = CandidateIntakePipeline(generator=generator, sessions=sessions)
    return create_app(general, stt=stt, tts=tts, intake=intake), sessions, generator, stt, tts


def test_app_rejects_separate_workflow_session_stores():
    general = TextConversationPipeline(llm=MockLLMProvider(), sessions=InMemorySessionStore())
    intake = CandidateIntakePipeline(generator=FakeIntakeGenerator(), sessions=InMemorySessionStore())
    with pytest.raises(ValueError, match="share one session store"):
        create_app(general, intake=intake)


async def text(client, message, *, session_id=None, workflow=None):
    body = {"text": message, "language": "en-IN"}
    if session_id is not None:
        body["session_id"] = session_id
    if workflow is not None:
        body["workflow"] = workflow
    return await client.post("/api/v1/chat/text", json=body)


async def voice(client, *, session_id=None, workflow=None):
    data = {"language": "en-IN"}
    if session_id is not None:
        data["session_id"] = session_id
    if workflow is not None:
        data["workflow"] = workflow
    return await client.post(
        "/api/v1/voice/chat", data=data,
        files={"audio": ("test.wav", wav_bytes(), "audio/wav")},
    )


def test_default_general_and_candidate_text_keep_response_contract():
    async def scenario():
        app, sessions, generator, _, _ = setup()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            general = await text(client, "Hello")
            intake = await text(client, "I am Asha.", workflow="candidate_intake")
            assert general.status_code == intake.status_code == 200
            assert general.json()["reply"].startswith("Mock response")
            assert intake.json()["reply"].endswith("What kind of role are you looking for?")
            assert set(intake.json()) == set(general.json())
            assert len(generator.calls) == 1
            session = await sessions.resolve(intake.json()["session_id"], workflow="candidate_intake")
            assert (await sessions.candidate_profile_for(session)).name == "Asha"

    asyncio.run(scenario())


def test_text_and_voice_share_intake_session_and_diagnostics():
    async def scenario():
        app, sessions, generator, stt, _ = setup()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = (await text(client, "I am Asha.", workflow="candidate_intake")).json()
            stt.text = "I know Python."
            second = await voice(client, session_id=first["session_id"], workflow="candidate_intake")
            assert second.status_code == 200
            body = second.json()
            assert body["session_id"] == first["session_id"]
            assert body["transcript"] == "I know Python."
            assert body["audio"]["mime_type"] == "audio/wav"
            assert set(body["timings_ms"]) == {"stt", "llm", "tts", "total"}
            assert body["usage"]["tts_characters"] == len(body["reply"])
            assert body["usage"]["speech_provider"] == "mock"
            assert body["estimated_speech_cost_inr"] == {"stt": 0, "tts": 0, "total": 0}
            assert [len(call[0]) for call in generator.calls] == [1, 3]
            session = await sessions.resolve(first["session_id"], workflow="candidate_intake")
            assert (await sessions.candidate_profile_for(session)).technical_skills == ("Python",)
            assert len(await sessions.history_for(session, workflow="candidate_intake")) == 4

    asyncio.run(scenario())


def test_voice_then_text_share_intake_session():
    async def scenario():
        app, sessions, _, _, _ = setup()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = (await voice(client, workflow="candidate_intake")).json()
            second = await text(client, "I know Python.", session_id=first["session_id"], workflow="candidate_intake")
            assert second.status_code == 200
            assert second.json()["session_id"] == first["session_id"]
            session = await sessions.resolve(first["session_id"], workflow="candidate_intake")
            assert (await sessions.candidate_profile_for(session)).name == "Asha"
            assert (await sessions.candidate_profile_for(session)).technical_skills == ("Python",)

    asyncio.run(scenario())


def test_workflow_mismatch_and_invalid_selection_are_safe():
    async def scenario():
        app, _, generator, _, _ = setup()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            intake = (await text(client, "I am Asha.", workflow="candidate_intake")).json()
            general = (await text(client, "Hello")).json()
            for response in (
                await text(client, "Hello", session_id=intake["session_id"]),
                await voice(client, session_id=intake["session_id"]),
                await text(client, "Hello", session_id=general["session_id"], workflow="candidate_intake"),
                await voice(client, session_id=general["session_id"], workflow="candidate_intake"),
            ):
                assert response.status_code == 409
                assert response.json() == {"error": {
                    "code": "SESSION_WORKFLOW_MISMATCH",
                    "message": "Start a new conversation to change workflow.",
                }}
            for response in (
                await text(client, "Hello", workflow="wrong"),
                await voice(client, workflow="wrong"),
            ):
                assert response.status_code == 422
                assert response.json()["error"]["code"] == "UNSUPPORTED_WORKFLOW"
            assert len(generator.calls) == 1

    asyncio.run(scenario())


def test_failed_intake_generation_preserves_profile_and_history():
    async def scenario():
        app, sessions, generator, _, _ = setup()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = (await text(client, "I am Asha.", workflow="candidate_intake")).json()
            session = await sessions.resolve(first["session_id"], workflow="candidate_intake")
            profile = await sessions.candidate_profile_for(session)
            generator.fail = True
            failed = await text(client, "I know Python.", session_id=first["session_id"], workflow="candidate_intake")
            assert failed.status_code == 502
            assert failed.json()["error"]["code"] == "LLM_GENERATION_FAILED"
            assert await sessions.candidate_profile_for(session) is profile
            assert len(await sessions.history_for(session, workflow="candidate_intake")) == 2

    asyncio.run(scenario())


def test_tts_failure_keeps_committed_intake_turn():
    async def scenario():
        app, sessions, generator, _, tts = setup()
        tts.fail = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await voice(client, workflow="candidate_intake")
            assert response.status_code == 200
            body = response.json()
            assert body["audio"] is None and body["warning"]
            assert body["usage"]["tts_characters"] == len(body["reply"])
            session = await sessions.resolve(body["session_id"], workflow="candidate_intake")
            assert (await sessions.candidate_profile_for(session)).name == "Asha"
            assert len(await sessions.history_for(session, workflow="candidate_intake")) == 2
            assert len(generator.calls) == 1

    asyncio.run(scenario())


def test_default_mock_intake_fixture_does_not_change_general_chat():
    async def scenario():
        sessions = InMemorySessionStore()
        app = create_app(TextConversationPipeline(llm=MockLLMProvider(), sessions=sessions))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            intake = await text(client, "Hello", workflow="candidate_intake")
            general = await text(client, "Hello")
            assert intake.status_code == general.status_code == 200
            assert intake.json()["reply"] == "Got it. What should I call you?"
            assert general.json()["reply"].startswith("Mock response")

    asyncio.run(scenario())
