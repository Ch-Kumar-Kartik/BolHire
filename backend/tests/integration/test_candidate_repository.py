import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import DataError, IntegrityError

from app.db.candidate_repository import (
    CandidateRepository, DatabaseConstraintError, ProfileNotFoundError, ProfileRevisionConflictError,
    StoredProfileInvalidError,
)
from app.db.config import DatabaseSettings
from app.db.database import Database
from app.db.models import Candidate, CandidateAuthSession, CandidateProfileRow
from app.services.candidate_profile import CandidateProfile

pytestmark = pytest.mark.integration


def metadata():
    return dict(consent_version="test-v1", consented_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1))


async def create(repo, profile=None, **kwargs):
    return await repo.create(profile if profile is not None else CandidateProfile(),
        recovery_secret_hash=uuid4().bytes + uuid4().bytes, **(metadata() | kwargs))


@pytest.mark.parametrize("skills", [None, (), ("Python", "SQL", "MongoDB", "हिंदी")])
def test_roundtrip_and_engine_recreation(db_url, skills):
    async def scenario():
        database = Database(DatabaseSettings(True, db_url))
        try:
            await database.check_connection()
            repo = CandidateRepository(database.sessions)
            profile = CandidateProfile(name="रीना", target_role="Python backend developer",
                experience_years=1.23456789012345, experience_level="junior",
                technical_skills=skills, preferred_location="दिल्ली",
                declined_fields=frozenset({"work_mode"}))
            saved = await create(repo, profile)
            assert saved.profile == profile and saved.revision == 1
            assert (await repo.get(saved.candidate_id)) == saved
        finally:
            await database.close()
        second = Database(DatabaseSettings(True, db_url))
        try:
            assert (await CandidateRepository(second.sessions).get(saved.candidate_id)) == saved
        finally:
            await second.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["remote", "hybrid", "onsite", "no_preference"])
def test_profile_limits_and_work_modes_roundtrip(db_url, mode):
    async def scenario():
        database = Database(DatabaseSettings(True, db_url))
        try:
            repo = CandidateRepository(database.sessions)
            profile = CandidateProfile(name="र" * 80, target_role="र" * 120,
                experience_level="र" * 60, experience_years=0,
                technical_skills=tuple(f"skill,{i}'" for i in range(20)),
                preferred_location="र" * 120, work_mode=mode)
            saved = await create(repo, profile)
            assert (await repo.get(saved.candidate_id)).profile == profile
        finally:
            await database.close()
    asyncio.run(scenario())


def test_revision_race_and_failed_update_rollback(db_url):
    async def scenario():
        database = Database(DatabaseSettings(True, db_url))
        try:
            repo = CandidateRepository(database.sessions)
            first = await create(repo, CandidateProfile(name="Asha"))
            async def save(name):
                return await repo.replace(first.candidate_id, CandidateProfile(name=name),
                    expected_revision=1, **metadata())
            results = await asyncio.gather(save("Riya"), save("Maya"), return_exceptions=True)
            assert sum(isinstance(result, ProfileRevisionConflictError) for result in results) == 1
            winner = next(result for result in results if not isinstance(result, Exception))
            assert winner.revision == 2
            assert await repo.get(first.candidate_id) == winner
            with pytest.raises(DatabaseConstraintError):
                await repo.replace(first.candidate_id, CandidateProfile(name="Failed"),
                    expected_revision=2, **(metadata() | {"expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)}))
            assert await repo.get(first.candidate_id) == winner
            final = await repo.replace(first.candidate_id, CandidateProfile(name="Final"),
                expected_revision=2, **metadata())
            assert final.revision == 3
        finally:
            await database.close()
    asyncio.run(scenario())


def test_atomic_create_rollback_and_constraint_error_privacy(db_url, caplog):
    async def scenario():
        database = Database(DatabaseSettings(True, db_url))
        try:
            repo = CandidateRepository(database.sessions)
            with pytest.raises(DatabaseConstraintError) as error:
                await create(repo, CandidateProfile(name="Private fixture"),
                    expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
            assert "Private fixture" not in str(error.value) + caplog.text
            async with database.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(Candidate)) == 0
                assert await session.scalar(select(func.count()).select_from(CandidateProfileRow)) == 0
            assert (await create(repo)).revision == 1
        finally:
            await database.close()
    asyncio.run(scenario())


def test_expired_and_missing_profiles_are_not_returned_or_revived(db_url):
    async def scenario():
        database = Database(DatabaseSettings(True, db_url))
        try:
            repo = CandidateRepository(database.sessions)
            first = await create(repo)
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            async with database.sessions.begin() as session:
                await session.execute(update(CandidateProfileRow).values(created_at=yesterday,
                    updated_at=yesterday, expires_at=func.statement_timestamp()).where(
                        CandidateProfileRow.candidate_id == first.candidate_id))
            for candidate_id in (first.candidate_id, uuid4()):
                with pytest.raises(ProfileNotFoundError):
                    await repo.get(candidate_id)
                with pytest.raises(ProfileNotFoundError):
                    await repo.replace(candidate_id, CandidateProfile(name="Do not revive"),
                        expected_revision=1, **metadata())
            async with database.sessions() as session:
                row = await session.get(CandidateProfileRow, first.candidate_id)
                assert row.revision == 1 and row.name is None and row.updated_at == yesterday
        finally:
            await database.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("values", [
    {"name": ""}, {"name": " "}, {"name": "x" * 81},
    {"target_role": "x" * 121}, {"experience_level": "x" * 61},
    {"preferred_location": "x" * 121}, {"work_mode": "unknown"},
    {"experience_years": -1}, {"experience_years": 81},
    {"experience_years": float("nan")}, {"experience_years": float("inf")},
    {"technical_skills": [None]}, {"technical_skills": [""]},
    {"technical_skills": [" "]}, {"technical_skills": ["x" * 61]},
    {"technical_skills": ["Python", "Python"]},
    {"technical_skills": [str(i) for i in range(21)]},
    {"technical_skills": [["Python"]]},
    {"declined_fields": None}, {"declined_fields": [None]},
    {"declined_fields": ["unknown"]}, {"declined_fields": ["name", "name"]},
    {"declined_fields": ["name"], "name": "Asha"},
    {"declined_fields": ["experience"], "experience_years": 0},
    {"declined_fields": ["experience"], "experience_level": "fresher"},
    {"declined_fields": ["technical_skills"], "technical_skills": []},
    {"declined_fields": ["work_mode"], "work_mode": "remote"},
    {"declined_fields": ["target_role"], "target_role": "developer"},
    {"declined_fields": ["preferred_location"], "preferred_location": "Pune"},
    {"revision": 0}, {"consent_version": ""},
    {"consent_version": None}, {"consented_at": None}, {"revision": None},
    {"created_at": None}, {"updated_at": None}, {"expires_at": None},
    {"expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)},
    {"updated_at": datetime(2000, 1, 1, tzinfo=timezone.utc)},
])
def test_direct_invalid_sql_profile_writes_are_rejected(db_url, values):
    async def scenario():
        database = Database(DatabaseSettings(True, db_url))
        try:
            repo = CandidateRepository(database.sessions)
            first = await create(repo)
            with pytest.raises((IntegrityError, DataError)):
                async with database.sessions.begin() as session:
                    await session.execute(update(CandidateProfileRow).where(
                        CandidateProfileRow.candidate_id == first.candidate_id).values(**values))
            assert await repo.get(first.candidate_id) == first
        finally:
            await database.close()
    asyncio.run(scenario())


def test_array_bounds_and_validation_on_read(db_url):
    async def scenario():
        database = Database(DatabaseSettings(True, db_url))
        try:
            repo = CandidateRepository(database.sessions)
            saved = await create(repo)
            with pytest.raises(IntegrityError):
                async with database.sessions.begin() as session:
                    await session.execute(text("UPDATE candidate_profiles SET technical_skills = '[0:0]={Python}'::text[]"))
            # PostgreSQL structural checks deliberately do not emulate Unicode casefold.
            async with database.sessions.begin() as session:
                await session.execute(update(CandidateProfileRow).values(technical_skills=["Straße", "STRASSE"]))
            with pytest.raises(StoredProfileInvalidError) as error:
                await repo.get(saved.candidate_id)
            assert "Straße" not in str(error.value)
        finally:
            await database.close()
    asyncio.run(scenario())


def test_direct_hash_fk_unique_constraints_and_cascade(db_url):
    async def scenario():
        database = Database(DatabaseSettings(True, db_url))
        try:
            repo = CandidateRepository(database.sessions)
            saved = await create(repo)
            for values in (
                {"id": uuid4(), "recovery_secret_hash": b"short"},
                {"id": saved.candidate_id, "recovery_secret_hash": b"z" * 32},
            ):
                with pytest.raises(IntegrityError):
                    async with database.sessions.begin() as session:
                        await session.execute(insert(Candidate).values(**values))
            async with database.sessions() as session:
                original_hash = (await session.get(Candidate, saved.candidate_id)).recovery_secret_hash
                source_row = await session.get(CandidateProfileRow, saved.candidate_id)
                orphan_values = {column.name: getattr(source_row, column.name)
                                 for column in CandidateProfileRow.__table__.columns}
                orphan_values["candidate_id"] = uuid4()
            with pytest.raises(IntegrityError):
                async with database.sessions.begin() as session:
                    await session.execute(insert(CandidateProfileRow).values(**orphan_values))
            with pytest.raises(IntegrityError):
                async with database.sessions.begin() as session:
                    await session.execute(insert(Candidate).values(id=uuid4(), recovery_secret_hash=original_hash))
            for values in (
                {"candidate_id": uuid4(), "token_hash": b"a" * 32, "expires_at": metadata()["expires_at"]},
                {"candidate_id": saved.candidate_id, "token_hash": b"short", "expires_at": metadata()["expires_at"]},
                {"candidate_id": saved.candidate_id, "token_hash": b"a" * 32, "expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)},
            ):
                with pytest.raises(IntegrityError):
                    async with database.sessions.begin() as session:
                        await session.execute(insert(CandidateAuthSession).values(id=uuid4(), **values))
            async with database.sessions.begin() as session:
                session.add(CandidateAuthSession(candidate_id=saved.candidate_id, token_hash=b"a" * 32,
                    expires_at=metadata()["expires_at"]))
            with pytest.raises(IntegrityError):
                async with database.sessions.begin() as session:
                    session.add(CandidateAuthSession(candidate_id=saved.candidate_id, token_hash=b"a" * 32,
                        expires_at=metadata()["expires_at"]))
            assert await repo.delete(saved.candidate_id)
            assert not await repo.delete(saved.candidate_id)
            async with database.sessions() as session:
                for model in (Candidate, CandidateProfileRow, CandidateAuthSession):
                    assert await session.scalar(select(func.count()).select_from(model)) == 0
        finally:
            await database.close()
    asyncio.run(scenario())
