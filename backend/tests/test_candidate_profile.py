import asyncio

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from app.models import ConversationMessage
from app.services.candidate_profile import CandidateProfile, CandidateProfileUpdate, apply_candidate_update
from app.services.sessions import (
    InMemorySessionStore, SessionExpiredError, SessionWorkflowMismatchError,
)


def exchange(number: int) -> list[ConversationMessage]:
    return [
        ConversationMessage(role="user", content=f"user {number}"),
        ConversationMessage(role="assistant", content=f"reply {number}"),
    ]


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_partial_updates_preserve_unknowns_and_known_values():
    empty = CandidateProfile()
    assert empty.name is None and empty.technical_skills is None
    assert empty.declined_fields == frozenset()

    named = apply_candidate_update(empty, CandidateProfileUpdate(name="  रीना  "))
    updated = apply_candidate_update(named, CandidateProfileUpdate(work_mode="no_preference"))
    assert named.name == "रीना"
    assert updated.name == "रीना"
    assert updated.work_mode == "no_preference"
    assert updated.target_role is None
    assert empty.name is None


def test_corrections_clears_and_declines_are_distinct():
    profile = apply_candidate_update(CandidateProfile(), CandidateProfileUpdate(
        name="Asha", target_role="Designer", experience_years=4,
        experience_level="mid", preferred_location="Pune",
    ))
    corrected = apply_candidate_update(profile, CandidateProfileUpdate(
        name="Anya", experience_level="fresher", clear_fields=["experience_years"],
        decline_fields=["preferred_location"],
    ))
    assert corrected.name == "Anya"
    assert corrected.experience_years is None
    assert corrected.experience_level == "fresher"
    assert corrected.preferred_location is None
    assert corrected.declined_fields == frozenset({"preferred_location"})
    assert profile.name == "Asha" and profile.experience_years == 4

    cleared = apply_candidate_update(corrected, CandidateProfileUpdate(clear_fields=["name", "experience"]))
    assert cleared.name is None and cleared.experience_level is None
    assert "name" not in cleared.declined_fields
    restored = apply_candidate_update(cleared, CandidateProfileUpdate(preferred_location="Delhi"))
    assert restored.preferred_location == "Delhi"
    assert restored.declined_fields == frozenset()


def test_declined_experience_does_not_invent_zero_years():
    level = apply_candidate_update(CandidateProfile(), CandidateProfileUpdate(experience_level="fresher"))
    assert level.experience_years is None
    assert "experience" not in level.declined_fields
    declined = apply_candidate_update(level, CandidateProfileUpdate(decline_fields=["experience"]))
    assert declined.experience_years is None and declined.experience_level is None
    assert declined.declined_fields == frozenset({"experience"})
    answered = apply_candidate_update(declined, CandidateProfileUpdate(experience_years=0))
    assert answered.experience_years == 0
    assert "experience" not in answered.declined_fields


def test_skill_add_remove_replace_and_known_empty():
    profile = CandidateProfile()
    assert apply_candidate_update(profile, CandidateProfileUpdate(skills_remove=["Python"])).technical_skills is None
    profile = apply_candidate_update(profile, CandidateProfileUpdate(skills_add=["Python", "React"]))
    profile = apply_candidate_update(profile, CandidateProfileUpdate(skills_add=["python", "SQL"]))
    assert profile.technical_skills == ("Python", "React", "SQL")
    profile = apply_candidate_update(profile, CandidateProfileUpdate(skills_remove=["PYTHON"]))
    assert profile.technical_skills == ("React", "SQL")
    profile = apply_candidate_update(profile, CandidateProfileUpdate(skills_replace=[]))
    assert profile.technical_skills == ()
    profile = apply_candidate_update(profile, CandidateProfileUpdate(clear_fields=["technical_skills"]))
    assert profile.technical_skills is None
    profile = apply_candidate_update(profile, CandidateProfileUpdate(decline_fields=["technical_skills"]))
    assert "technical_skills" in profile.declined_fields
    profile = apply_candidate_update(profile, CandidateProfileUpdate(skills_add=["Java"]))
    assert profile.technical_skills == ("Java",)
    assert "technical_skills" not in profile.declined_fields


@pytest.mark.parametrize("data", [
    {"name": 123}, {"name": "  "}, {"target_role": "x" * 121},
    {"experience_years": "3"}, {"experience_years": True},
    {"experience_years": -1}, {"experience_years": float("inf")},
    {"work_mode": "sometimes"}, {"unknown_field": "x"},
    {"skills_add": [42]},
    {"name": None}, {"skills_replace": None},
    {"skills_add": ["Python", "python"]},
    {"skills_add": ["Python"], "skills_remove": ["python"]},
    {"skills_replace": ["Python"], "skills_add": ["React"]},
    {"skills_add": [f"skill-{number}" for number in range(21)]},
    {"clear_fields": ["name"], "name": "Asha"},
    {"decline_fields": ["experience"], "experience_years": 3},
    {"clear_fields": ["experience"], "experience_level": "senior"},
    {"clear_fields": ["name"], "decline_fields": ["name"]},
])
def test_invalid_updates_are_rejected(data):
    with pytest.raises(ValidationError):
        CandidateProfileUpdate.model_validate(data)


def test_profile_itself_rejects_invalid_states():
    with pytest.raises(ValidationError):
        CandidateProfile(name="Asha", declined_fields=frozenset({"name"}))
    with pytest.raises(ValidationError):
        CandidateProfile(technical_skills=("Python", "python"))


def test_profile_survives_history_truncation_and_expires_with_session():
    async def scenario():
        clock = FakeClock()
        store = InMemorySessionStore(clock=clock)
        session = await store.create_candidate_exchange(
            CandidateProfileUpdate(name="Asha"), exchange(1)
        )
        for number in range(2, 23):
            async with session.lock:
                await store.commit_candidate_exchange(
                    session, CandidateProfileUpdate(), exchange(number)
                )
        assert len(await store.history_for(session, workflow="candidate_intake")) == 40
        assert (await store.history_for(session, workflow="candidate_intake"))[0].content == "user 3"
        assert (await store.candidate_profile_for(session)).name == "Asha"
        clock.value = 1800
        with pytest.raises(SessionExpiredError):
            await store.resolve(session.session_id, workflow="candidate_intake")
        assert session.session_id not in store._sessions

    asyncio.run(scenario())


def test_concurrent_candidate_turns_keep_both_updates_and_ordered_exchanges():
    async def scenario():
        store = InMemorySessionStore()
        session = await store.create_candidate_exchange(CandidateProfileUpdate(), exchange(1))
        seen_lengths = []

        async def turn(number: int, skill: str):
            async with session.lock:
                seen_lengths.append(len(await store.history_for(session, workflow="candidate_intake")))
                await asyncio.sleep(0)
                await store.commit_candidate_exchange(
                    session, CandidateProfileUpdate(skills_add=[skill]), exchange(number)
                )

        await asyncio.gather(turn(2, "Python"), turn(3, "React"))
        assert seen_lengths == [2, 4]
        assert set((await store.candidate_profile_for(session)).technical_skills) == {"Python", "React"}
        assert len(await store.history_for(session, workflow="candidate_intake")) == 6

    asyncio.run(scenario())


def test_failed_candidate_commit_changes_nothing():
    async def scenario():
        clock = FakeClock()
        store = InMemorySessionStore(clock=clock)
        session = await store.create_candidate_exchange(
            CandidateProfileUpdate(name="Asha", skills_add=[f"s{number}" for number in range(20)]),
            exchange(1),
        )
        before = session.candidate_profile
        clock.value = 5
        with pytest.raises(ValidationError):
            await store.commit_candidate_exchange(
                session, CandidateProfileUpdate(name="Anya", skills_add=["extra"]), exchange(2)
            )
        with pytest.raises(ValueError):
            await store.commit_candidate_exchange(
                session, CandidateProfileUpdate(name="Anya"), [exchange(2)[0]]
            )
        assert session.candidate_profile is before
        assert session.messages == exchange(1)
        assert session.last_activity == 0

        bad = CandidateProfileUpdate(skills_add=["Python"])
        bad.skills_add.append(5)
        with pytest.raises(PydanticSerializationError):
            await store.commit_candidate_exchange(session, bad, exchange(2))
        assert session.messages == exchange(1)

    asyncio.run(scenario())


def test_failed_first_turn_and_expired_commit_do_not_store_updates():
    async def scenario():
        clock = FakeClock()
        store = InMemorySessionStore(clock=clock)
        with pytest.raises(ValueError):
            await store.create_candidate_exchange(
                CandidateProfileUpdate(name="Asha"), [exchange(1)[0]]
            )
        assert store._sessions == {}

        session = await store.create_candidate_exchange(
            CandidateProfileUpdate(name="Asha"), exchange(1)
        )
        clock.value = 1800
        with pytest.raises(SessionExpiredError):
            await store.commit_candidate_exchange(
                session, CandidateProfileUpdate(name="Anya"), exchange(2)
            )
        assert session.candidate_profile.name == "Asha"
        assert session.messages == exchange(1)

    asyncio.run(scenario())


def test_workflow_switch_is_rejected_without_changing_state():
    async def scenario():
        store = InMemorySessionStore()
        general = await store.create(exchange(1))
        intake = await store.create_candidate_exchange(CandidateProfileUpdate(name="Asha"), exchange(1))
        with pytest.raises(SessionWorkflowMismatchError):
            await store.resolve(general.session_id, workflow="candidate_intake")
        with pytest.raises(SessionWorkflowMismatchError):
            await store.resolve(intake.session_id)
        with pytest.raises(SessionWorkflowMismatchError):
            await store.append_exchange(intake, exchange(2))
        with pytest.raises(SessionWorkflowMismatchError):
            await store.commit_candidate_exchange(general, CandidateProfileUpdate(), exchange(2))
        assert general.messages == exchange(1)
        assert intake.messages == exchange(1)
        assert (await store.candidate_profile_for(intake)).name == "Asha"

    asyncio.run(scenario())
