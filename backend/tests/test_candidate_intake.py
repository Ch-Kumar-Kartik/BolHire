import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors, types

from app.models import ConversationMessage
from app.providers.gemini import GeminiLLMProvider, GeminiProviderError
from app.providers.llm import LLMProviderError, MockLLMProvider
from app.providers.llm_config import LLMSettings
from app.services.candidate_intake import (
    CandidateIntakePipeline, CandidateIntakeProposal, IntakeEvidence, missing_fields, _reply,
)
from app.services.candidate_profile import CandidateProfile, CandidateProfileUpdate
from app.services.pipeline import LLMGenerationError, TextConversationPipeline
from app.services.sessions import InMemorySessionStore, SessionExpiredError


def proposal(update=None, evidence=(), acknowledgement="Thanks for sharing."):
    return CandidateIntakeProposal(
        update=update or CandidateProfileUpdate(),
        acknowledgement=acknowledgement,
        evidence=[IntakeEvidence(field=field, quote=quote) for field, quote in evidence],
    )


class FakeIntakeGenerator:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def generate_intake(self, messages, profile, language):
        self.calls.append((list(messages), profile, language))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize("language", ["en-IN", "hi-IN"])
@pytest.mark.parametrize("mode", ["remote", "hybrid", "onsite", "no_preference"])
def test_spoken_summary_preserves_every_known_value(language, mode):
    profile = CandidateProfile(
        name="Asha", target_role="backend developer", experience_years=1,
        experience_level="junior", technical_skills=("Python", "SQL", "React", "MongoDB"),
        preferred_location="Pune", work_mode=mode,
    )
    reply = _reply(proposal(), profile, language)
    for value in ("Asha", "backend developer", "1", "junior", "Python", "SQL", "React", "MongoDB", "Pune"):
        assert value in reply
    assert ":" not in reply and "+" not in reply and "?" not in reply
    if language == "en-IN":
        assert "1 year of experience" in reply
        assert ("no work mode preference" if mode == "no_preference" else f"{mode} work") in reply


@pytest.mark.parametrize("language", ["en-IN", "hi-IN"])
def test_summary_with_declines_and_explicit_empty_skills(language):
    profile = CandidateProfile(technical_skills=(), declined_fields=frozenset({
        "name", "target_role", "experience", "preferred_location", "work_mode",
    }))
    reply = _reply(proposal(), profile, language)
    assert ("haven't listed any technical skills" if language == "en-IN" else "कोई तकनीकी कौशल नहीं बताया") in reply
    assert "None" not in reply and "?" not in reply
    assert missing_fields(profile) == ()


def test_arbitrary_order_multiple_fields_and_one_call_per_turn():
    async def scenario():
        store = InMemorySessionStore()
        fake = FakeIntakeGenerator(
            proposal(
                CandidateProfileUpdate(skills_add=["Python", "React"], preferred_location="Pune"),
                [("technical_skills", "Python and React"), ("preferred_location", "Pune")],
            ),
            proposal(
                CandidateProfileUpdate(name="Asha", target_role="backend developer"),
                [("name", "Asha"), ("target_role", "backend developer")],
            ),
            proposal(
                CandidateProfileUpdate(experience_years=3, work_mode="hybrid"),
                [("experience", "three years experience"), ("work_mode", "hybrid")],
            ),
        )
        intake = CandidateIntakePipeline(generator=fake, sessions=store)
        first = await intake.respond(
            session_id=None, text="I know Python and React and prefer Pune.", language="en-IN"
        )
        assert first.reply.endswith("What should I call you?")
        second = await intake.respond(
            session_id=first.session_id,
            text="I'm Asha, looking for a backend developer role.", language="en-IN",
        )
        assert second.reply.endswith("How much experience do you have, or are you a fresher?")
        third = await intake.respond(
            session_id=first.session_id,
            text="I have three years experience and prefer hybrid work.", language="en-IN",
        )
        assert "Your name is Asha." in third.reply and "You prefer hybrid work." in third.reply
        assert third.reply.endswith("Let me know if anything needs correcting.")
        assert [len(call[0]) for call in fake.calls] == [1, 3, 5]
        assert fake.calls[1][1].technical_skills == ("Python", "React")
        profile = await store.candidate_profile_for(
            await store.resolve(first.session_id, workflow="candidate_intake")
        )
        assert profile.experience_years == 3
        assert missing_fields(profile) == ()
        assert all(reply.timings_ms.llm >= 0 for reply in (first, second, third))

    asyncio.run(scenario())


def test_corrections_skill_removal_and_clear():
    async def scenario():
        store = InMemorySessionStore()
        fake = FakeIntakeGenerator(
            proposal(CandidateProfileUpdate(skills_add=["Python", "React"]),
                     [("technical_skills", "Python and React")]),
            proposal(CandidateProfileUpdate(name="Riya", skills_add=["Java"], skills_remove=["Python"]),
                     [("name", "Riya"), ("technical_skills", "Java, not Python")]),
            proposal(CandidateProfileUpdate(name="Maya", clear_fields=["technical_skills"]),
                     [("name", "Maya"), ("technical_skills", "clear my skills")]),
        )
        intake = CandidateIntakePipeline(generator=fake, sessions=store)
        first = await intake.respond(session_id=None, text="I know Python and React.", language="en-IN")
        await intake.respond(session_id=first.session_id,
                             text="Actually I'm Riya and know Java, not Python.", language="en-IN")
        session = await store.resolve(first.session_id, workflow="candidate_intake")
        assert (await store.candidate_profile_for(session)).technical_skills == ("React", "Java")
        await intake.respond(session_id=first.session_id,
                             text="Call me Maya, and clear my skills.", language="en-IN")
        profile = await store.candidate_profile_for(session)
        assert profile.name == "Maya" and profile.technical_skills is None
        assert "technical_skills" in missing_fields(profile)

    asyncio.run(scenario())


def test_declines_and_no_preference_skip_known_fields():
    async def scenario():
        store = InMemorySessionStore()
        fake = FakeIntakeGenerator(proposal(
            CandidateProfileUpdate(decline_fields=["name"], work_mode="no_preference"),
            [("name", "rather not share my name"), ("work_mode", "Any work mode is fine")],
        ))
        result = await CandidateIntakePipeline(generator=fake, sessions=store).respond(
            session_id=None,
            text="I'd rather not share my name. Any work mode is fine.", language="en-IN",
        )
        profile = await store.candidate_profile_for(
            await store.resolve(result.session_id, workflow="candidate_intake")
        )
        assert profile.name is None and "name" in profile.declined_fields
        assert profile.work_mode == "no_preference"
        assert "work_mode" not in missing_fields(profile)
        assert result.reply.endswith("What kind of role are you looking for?")
        assert "?" not in result.reply[:-1]

    asyncio.run(scenario())


@pytest.mark.parametrize(("text", "update", "evidence", "expected"), [
    (
        "मेरा नाम रीना है, मुझे दिल्ली में काम चाहिए।",
        CandidateProfileUpdate(name="रीना", preferred_location="दिल्ली"),
        [("name", "रीना"), ("preferred_location", "दिल्ली")],
        "आप किस तरह की नौकरी चाहते हैं?",
    ),
    (
        "main teen saal kaam kiya hai aur Python aata hai",
        CandidateProfileUpdate(experience_years=3, skills_add=["Python"]),
        [("experience", "teen saal"), ("technical_skills", "Python")],
        "आपको किस नाम से बुलाऊँ?",
    ),
])
def test_hindi_and_romanized_hindi(text, update, evidence, expected):
    async def scenario():
        fake = FakeIntakeGenerator(proposal(update, evidence, "धन्यवाद।"))
        result = await CandidateIntakePipeline(generator=fake, sessions=InMemorySessionStore()).respond(
            session_id=None, text=text, language="hi-IN",
        )
        assert result.reply.endswith(expected)
        assert fake.calls[0][2] == "hi-IN"

    asyncio.run(scenario())


@pytest.mark.parametrize("bad", [
    proposal(CandidateProfileUpdate(name="Invented"), [("name", "I")]),
    proposal(CandidateProfileUpdate(name="Python"), [("name", "I")]),
    proposal(CandidateProfileUpdate(experience_years=5), [("experience", "I have experience")]),
    proposal(CandidateProfileUpdate(skills_add=["Rust"]), [("technical_skills", "I know Python")]),
    proposal(CandidateProfileUpdate(work_mode="remote"), [("work_mode", "I am flexible")]),
    proposal(CandidateProfileUpdate(name="Asha")),
    proposal(acknowledgement="What is your name?"),
    {"update": {"work_mode": "floating"}, "acknowledgement": "Thanks.", "evidence": []},
])
def test_invalid_or_ungrounded_proposals_do_not_create_session(bad):
    async def scenario():
        store = InMemorySessionStore()
        fake = FakeIntakeGenerator(bad)
        with pytest.raises(LLMProviderError) as raised:
            await CandidateIntakePipeline(generator=fake, sessions=store).respond(
                session_id=None, text="I know Python.", language="en-IN",
            )
        assert raised.value.code == "CANDIDATE_INTAKE_INVALID_RESPONSE"
        assert store._sessions == {}
        assert len(fake.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(("candidate", "profile", "text", "reason"), [
    (proposal(acknowledgement="What is your name?"), CandidateProfile(), "Hello", "acknowledgement_invalid"),
    (proposal(CandidateProfileUpdate(name="Sam")), CandidateProfile(), "I am Sam", "evidence_fields"),
    (proposal(CandidateProfileUpdate(name="Sam"), [("name", "Sam"), ("name", "Sam")]),
     CandidateProfile(), "I am Sam", "evidence_fields"),
    (proposal(CandidateProfileUpdate(name="Sam"), [("name", "Sam elsewhere")]),
     CandidateProfile(), "I am Sam", "quote_not_literal"),
    (proposal(CandidateProfileUpdate(name="Sam"), [("name", "I am")]),
     CandidateProfile(), "I am Sam", "value_not_grounded"),
    (proposal(CandidateProfileUpdate(skills_add=["Rust"]), [("technical_skills", "I know")]),
     CandidateProfile(), "I know Rust", "value_not_grounded"),
    (proposal(CandidateProfileUpdate(experience_years=0), [("experience", "fresher")]),
     CandidateProfile(), "I am a fresher", "years_not_stated"),
    (proposal(CandidateProfileUpdate(work_mode="remote"), [("work_mode", "flexible")]),
     CandidateProfile(), "I am flexible", "work_mode_not_grounded"),
    (proposal(CandidateProfileUpdate(skills_add=["Rust"]), [("technical_skills", "Rust")]),
     CandidateProfile(technical_skills=tuple(f"skill-{index}" for index in range(20))),
     "I know Rust", "profile_apply"),
    ({"update": {"name": "Sam"}}, CandidateProfile(), "I am Sam", "unknown"),
])
def test_intake_validation_logs_only_fixed_reason(caplog, candidate, profile, text, reason):
    with caplog.at_level("WARNING", logger="app.services.candidate_intake"):
        with pytest.raises(LLMProviderError) as raised:
            CandidateIntakePipeline._validate_and_apply(candidate, profile, text)
    assert raised.value.code == "CANDIDATE_INTAKE_INVALID_RESPONSE"
    assert str(raised.value) == "The conversation provider returned an invalid intake reply."
    assert [record.getMessage() for record in caplog.records] == [
        f"component=candidate_intake reason={reason}"
    ]
    assert "Sam" not in caplog.text
    assert "Rust" not in caplog.text


def test_empty_update_commits_a_clarification_turn_without_profile_change(caplog):
    async def scenario():
        store = InMemorySessionStore()
        fake = FakeIntakeGenerator(proposal())
        intake = CandidateIntakePipeline(generator=fake, sessions=store)
        with caplog.at_level("WARNING", logger="app.services.candidate_intake"):
            reply = await intake.respond(session_id=None, text="Could you explain?", language="en-IN")
        session = await store.resolve(reply.session_id, workflow="candidate_intake")
        assert await store.candidate_profile_for(session) == CandidateProfile()
        assert len(await store.history_for(session, workflow="candidate_intake")) == 2
        assert reply.reply.endswith("What should I call you?")
        assert caplog.records == []

    asyncio.run(scenario())


def test_provider_failures_and_atomic_rollback_on_existing_session(caplog):
    async def scenario():
        store = InMemorySessionStore()
        fake = FakeIntakeGenerator(
            proposal(CandidateProfileUpdate(name="Asha"), [("name", "Asha")]),
            GeminiProviderError("GEMINI_TIMEOUT", "Gemini did not reply in time.", 504),
            proposal(CandidateProfileUpdate(name="Invented"), [("name", "I")]),
            RuntimeError("fake failure"),
        )
        intake = CandidateIntakePipeline(generator=fake, sessions=store)
        first = await intake.respond(session_id=None, text="I'm Asha.", language="en-IN")
        session = await store.resolve(first.session_id, workflow="candidate_intake")
        original = await store.candidate_profile_for(session)
        with caplog.at_level("WARNING", logger="app.services.candidate_intake"):
            for error_type in (GeminiProviderError, LLMProviderError, LLMGenerationError):
                with pytest.raises(error_type):
                    await intake.respond(session_id=first.session_id, text="I have Python.", language="en-IN")
                assert await store.candidate_profile_for(session) is original
                assert len(await store.history_for(session, workflow="candidate_intake")) == 2
        assert len(fake.calls) == 4
        assert [record.getMessage() for record in caplog.records] == [
            "component=candidate_intake reason=value_not_grounded"
        ]

    asyncio.run(scenario())


def test_unsupported_acknowledgement_is_replaced_without_changing_profile():
    async def scenario():
        store = InMemorySessionStore()
        fake = FakeIntakeGenerator(proposal(acknowledgement="You have ten years of experience."))
        result = await CandidateIntakePipeline(generator=fake, sessions=store).respond(
            session_id=None, text="Hello", language="en-IN",
        )
        assert result.reply == "Got it. What should I call you?"
        profile = await store.candidate_profile_for(
            await store.resolve(result.session_id, workflow="candidate_intake")
        )
        assert profile.experience_years is None

    asyncio.run(scenario())


def test_concurrent_intake_turns_use_serialized_profile_state():
    class WaitingGenerator:
        def __init__(self):
            self.calls = []

        async def generate_intake(self, messages, profile, language):
            self.calls.append((len(messages), profile))
            await asyncio.sleep(0)
            return proposal()

    async def scenario():
        store = InMemorySessionStore()
        fake = WaitingGenerator()
        intake = CandidateIntakePipeline(generator=fake, sessions=store)
        first = await intake.respond(session_id=None, text="Hello", language="en-IN")
        await asyncio.gather(*(
            intake.respond(session_id=first.session_id, text=f"More {index}", language="en-IN")
            for index in range(2)
        ))
        assert [length for length, _ in fake.calls] == [1, 3, 5]
        session = await store.resolve(first.session_id, workflow="candidate_intake")
        assert len(await store.history_for(session, workflow="candidate_intake")) == 6

    asyncio.run(scenario())


def test_expired_intake_turn_does_not_call_generator():
    class Clock:
        value = 0

        def __call__(self):
            return self.value

    async def scenario():
        clock = Clock()
        store = InMemorySessionStore(clock=clock)
        fake = FakeIntakeGenerator(proposal())
        intake = CandidateIntakePipeline(generator=fake, sessions=store)
        first = await intake.respond(session_id=None, text="Hello", language="en-IN")
        clock.value = 1800
        with pytest.raises(SessionExpiredError):
            await intake.respond(session_id=first.session_id, text="Still here", language="en-IN")
        assert len(fake.calls) == 1

    asyncio.run(scenario())


def test_general_chat_pipeline_remains_separate_from_intake():
    async def scenario():
        store = InMemorySessionStore()
        general = TextConversationPipeline(llm=MockLLMProvider(), sessions=store)
        fake = FakeIntakeGenerator(proposal())
        intake = CandidateIntakePipeline(generator=fake, sessions=store)
        general_reply = await general.respond(session_id=None, text="Hello", language="en-IN")
        intake_reply = await intake.respond(session_id=None, text="Hello", language="en-IN")
        assert general_reply.reply.startswith("Mock response")
        assert intake_reply.reply.endswith("What should I call you?")
        assert general_reply.session_id != intake_reply.session_id
        assert len(fake.calls) == 1

    asyncio.run(scenario())


class FakeSDKClient:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.aio = SimpleNamespace(models=self)

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def sdk_response(body):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part.from_text(text=body)]),
        finish_reason=types.FinishReason.STOP,
    )])


def gemini_settings():
    return LLMSettings.from_env({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "test-secret"})


def test_gemini_structured_request_uses_existing_limits_and_one_call():
    response_body = json.dumps({
        "update": {"name": "Asha"}, "acknowledgement": "Thanks for sharing.",
        "evidence": [{"field": "name", "quote": "Asha"}],
    })
    client = FakeSDKClient(sdk_response(response_body))
    provider = GeminiLLMProvider(gemini_settings(), client=client)
    result = asyncio.run(provider.generate_intake(
        [ConversationMessage(role="user", content="I'm Asha")], CandidateProfile(), "en-IN"
    ))
    assert result.update.name == "Asha"
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request["model"] == "gemini-3.5-flash-lite"
    assert request["config"].response_mime_type == "application/json"
    schema = request["config"].response_schema
    assert isinstance(schema, dict)
    assert set(schema["properties"]) == {"update", "acknowledgement", "evidence"}
    assert set(schema["properties"]["update"]["properties"]) == set(CandidateProfileUpdate.model_fields)
    assert set(schema["properties"]["update"]["required"]) == {
        "skills_add", "skills_remove", "clear_fields", "decline_fields",
    }
    assert schema["properties"]["update"]["properties"]["work_mode"]["enum"] == [
        "remote", "hybrid", "onsite", "no_preference",
    ]
    allowed_keys = {"type", "properties", "items", "enum", "required"}
    def check_schema(node):
        assert set(node) <= allowed_keys
        assert node["type"] in {"OBJECT", "STRING", "NUMBER", "ARRAY"}
        for child in node.get("properties", {}).values():
            check_schema(child)
        if "items" in node:
            check_schema(node["items"])
    check_schema(schema)
    assert request["config"].max_output_tokens == 1024
    assert request["config"].thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    assert request["config"].automatic_continuation is False
    assert "latest user utterance" in request["config"].system_instruction
    assert "Romanized Hindi" in request["config"].system_instruction or "Romanized Hindi" in gemini_settings().system_prompt


@pytest.mark.parametrize("update", [
    {
        "name": "Asha", "target_role": "Python backend developer", "experience_level": "fresher",
        "skills_add": ["Python"], "skills_remove": ["React"],
        "clear_fields": ["preferred_location"], "decline_fields": ["work_mode"],
    },
    {
        "experience_years": 3, "work_mode": "remote", "skills_replace": ["Go"],
        "skills_add": [], "skills_remove": [], "clear_fields": [], "decline_fields": [],
    },
])
def test_structured_output_preserves_all_supported_update_operations(update):
    client = FakeSDKClient(sdk_response(json.dumps({
        "update": update, "acknowledgement": "Got it.", "evidence": [],
    })))
    result = asyncio.run(GeminiLLMProvider(gemini_settings(), client=client).generate_intake(
        [ConversationMessage(role="user", content="Example")], CandidateProfile(), "en-IN"
    ))
    assert result.update.model_dump(exclude_unset=True) == update
    assert len(client.calls) == 1


@pytest.mark.parametrize(("result", "code"), [
    (sdk_response("{"), "GEMINI_INVALID_RESPONSE"),
    (sdk_response(json.dumps({"update": {"work_mode": "floating"}, "acknowledgement": "Thanks."})), "GEMINI_INVALID_RESPONSE"),
    (sdk_response(json.dumps({"update": {"rename": "Asha"}, "acknowledgement": "Got it.", "evidence": []})), "GEMINI_INVALID_RESPONSE"),
    (sdk_response(json.dumps({"update": {"name": None}, "acknowledgement": "Got it.", "evidence": []})), "GEMINI_INVALID_RESPONSE"),
    (errors.APIError(429, {"error": {"message": "test-secret"}}), "GEMINI_RATE_LIMITED"),
    (httpx.ReadTimeout("test-secret"), "GEMINI_TIMEOUT"),
])
def test_gemini_invalid_json_values_and_provider_errors_are_safe(result, code):
    client = FakeSDKClient(result)
    provider = GeminiLLMProvider(gemini_settings(), client=client)
    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate_intake(
            [ConversationMessage(role="user", content="Hello")], CandidateProfile(), "en-IN"
        ))
    assert raised.value.code == code
    assert "test-secret" not in str(raised.value)
    assert len(client.calls) == 1


def test_invalid_gemini_output_keeps_existing_intake_profile_and_history():
    async def scenario():
        first = sdk_response(json.dumps({
            "update": {"name": "Asha"}, "acknowledgement": "Got it.",
            "evidence": [{"field": "name", "quote": "Asha"}],
        }))
        client = FakeSDKClient(first)
        store = InMemorySessionStore()
        intake = CandidateIntakePipeline(
            generator=GeminiLLMProvider(gemini_settings(), client=client), sessions=store,
        )
        response = await intake.respond(session_id=None, text="I am Asha.", language="en-IN")
        session = await store.resolve(response.session_id, workflow="candidate_intake")
        original = await store.candidate_profile_for(session)
        client.result = sdk_response(json.dumps({
            "update": {"skills_add": ["Python"], "unsupported_operation": "erase"},
            "acknowledgement": "Got it.", "evidence": [],
        }))
        with pytest.raises(GeminiProviderError) as raised:
            await intake.respond(session_id=response.session_id, text="I know Python.", language="en-IN")
        assert raised.value.code == "GEMINI_INVALID_RESPONSE"
        assert await store.candidate_profile_for(session) is original
        assert len(await store.history_for(session, workflow="candidate_intake")) == 2
        assert len(client.calls) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(("status", "message", "category", "logged_status"), [
    ("INVALID_ARGUMENT", "Invalid responseSchema: too many states. test-secret Asha", "response_schema", "INVALID_ARGUMENT"),
    ("INVALID_ARGUMENT", "thinkingConfig.thinkingLevel is invalid. test-secret Asha", "thinking_config", "INVALID_ARGUMENT"),
    ("INVALID_ARGUMENT", "maxOutputTokens is unsupported. test-secret Asha", "model_parameter", "INVALID_ARGUMENT"),
    ("INVALID_ARGUMENT", "Request rejected. test-secret Asha", "unknown", "INVALID_ARGUMENT"),
    ("test-secret Asha", "Request rejected. test-secret Asha", "unknown", "unavailable"),
    (None, None, "unknown", "unavailable"),
])
def test_structured_http_400_logs_only_safe_category(
    caplog, status, message, category, logged_status,
):
    client = FakeSDKClient(errors.APIError(400, {"error": {"status": status, "message": message}}))
    provider = GeminiLLMProvider(gemini_settings(), client=client)
    with caplog.at_level("WARNING", logger="app.providers.gemini"):
        with pytest.raises(GeminiProviderError) as raised:
            asyncio.run(provider.generate_intake(
                [ConversationMessage(role="user", content="Asha")], CandidateProfile(), "en-IN"
            ))
    assert (raised.value.code, str(raised.value), raised.value.status_code) == (
        "GEMINI_REJECTED", "Gemini rejected the server request.", 502,
    )
    assert len(client.calls) == 1
    assert [record.getMessage() for record in caplog.records] == [
        f"Gemini structured intake rejected: http_status=400 api_status={logged_status} category={category}"
    ]
    assert "test-secret" not in caplog.text
    assert "Asha" not in caplog.text


def test_general_chat_http_400_does_not_log_intake_diagnostic(caplog):
    client = FakeSDKClient(errors.APIError(400, {"error": {
        "status": "INVALID_ARGUMENT", "message": "responseSchema test-secret Asha",
    }}))
    provider = GeminiLLMProvider(gemini_settings(), client=client)
    with caplog.at_level("WARNING", logger="app.providers.gemini"):
        with pytest.raises(GeminiProviderError) as raised:
            asyncio.run(provider.generate([ConversationMessage(role="user", content="Asha")], "en-IN"))
    assert raised.value.code == "GEMINI_REJECTED"
    assert caplog.records == []
