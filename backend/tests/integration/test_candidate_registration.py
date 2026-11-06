import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.db.auth_repository import AuthRepository
from app.db.candidate_repository import (
    CandidateRepository, DatabaseConstraintError, RecoveryKeyUnavailableError, ProfileNotFoundError,
)
from app.db.config import DatabaseSettings
from app.db.database import Database, DatabaseUnavailableError
from app.db.models import Candidate, CandidateProfileRow
from app.services.candidate_consent import RETENTION
from app.services.candidate_profile import CandidateProfile
from test_auth_concurrency import PausingSessions, wait_for_lock_waiter

pytestmark = pytest.mark.integration
VERSION = "candidate-profile-v1:en-IN"


def run(db_url, scenario):
    async def main():
        database = Database(DatabaseSettings(True, db_url))
        try:
            return await scenario(database, CandidateRepository(database.sessions))
        finally:
            await database.close()
    return asyncio.run(main())


def verifier():
    return uuid4().bytes * 2


async def register(repo, name="Person", *, key=None, version=VERSION, retention=RETENTION, **profile):
    return await repo.register(CandidateProfile(name=name, **profile), recovery_secret_hash=key or verifier(),
                               consent_version=version, retention=retention)


async def candidate_id_for(database, key):
    async with database.sessions() as session:
        return await session.scalar(select(Candidate.id).where(Candidate.recovery_secret_hash == key))


async def profile_row(database, candidate_id):
    async with database.sessions() as session:
        row = await session.get(CandidateProfileRow, candidate_id)
        return None if row is None else {c.name: getattr(row, c.name) for c in CandidateProfileRow.__table__.columns}


async def counts(database):
    async with database.sessions() as session:
        return (await session.scalar(select(func.count()).select_from(Candidate)),
                await session.scalar(select(func.count()).select_from(CandidateProfileRow)))


async def expire(database, candidate_id):
    async with database.sessions.begin() as session:
        past = func.statement_timestamp() - timedelta(days=2)
        await session.execute(update(CandidateProfileRow).where(CandidateProfileRow.candidate_id == candidate_id).values(
            created_at=past, updated_at=past, expires_at=func.statement_timestamp() - timedelta(days=1)))


def test_the_engine_pins_read_committed_even_when_the_server_default_differs(db_url):
    """Connect with a server-side default of SERIALIZABLE (a per-connection option; nothing in the database is altered) and show the
    engine still runs its transactions at READ COMMITTED, so the locking arguments never depend on the server default."""
    hostile = db_url.update_query_dict({"options": f"{db_url.query['options']} -cdefault_transaction_isolation=serializable"})

    async def scenario():
        database = Database(DatabaseSettings(True, hostile))
        try:
            async with database.sessions() as session:
                assert await session.scalar(text("SHOW default_transaction_isolation")) == "serializable"  # the trap is really set
                assert await session.scalar(text("SHOW transaction_isolation")) == "read committed"
        finally:
            await database.close()
    asyncio.run(scenario())


def test_first_save_creates_identity_and_profile_with_database_clock_retention(db_url):
    async def scenario(database, repo):
        key = verifier()
        result = await register(repo, "रीना", key=key, technical_skills=("Python", "SQL"), declined_fields=frozenset({"work_mode"}))
        assert result.created
        row = await profile_row(database, await candidate_id_for(database, key))
        assert row["name"] == "रीना" and row["technical_skills"] == ["Python", "SQL"] and row["declined_fields"] == ["work_mode"]
        assert row["consent_version"] == VERSION and row["revision"] == 1
        # One statement timestamp stamps all three, so retention is exactly the configured period and no application clock is involved.
        assert row["consented_at"] == row["created_at"] and row["expires_at"] - row["created_at"] == timedelta(days=90)
        assert (await repo.get(await candidate_id_for(database, key))).profile.name == "रीना"
    run(db_url, scenario)


def test_retention_is_whatever_the_caller_configures_in_the_same_statement(db_url):
    async def scenario(database, repo):
        key = verifier()
        await register(repo, key=key, retention=timedelta(hours=36))
        row = await profile_row(database, await candidate_id_for(database, key))
        assert row["expires_at"] - row["created_at"] == timedelta(hours=36)
    run(db_url, scenario)


def test_retry_with_an_active_identity_writes_nothing_even_when_the_data_differs(db_url):
    async def scenario(database, repo):
        key = verifier()
        await register(repo, "First", key=key)
        candidate_id = await candidate_id_for(database, key)
        before = await profile_row(database, candidate_id)
        again = await register(repo, "Different", key=key, version="candidate-profile-v1:hi-IN", retention=timedelta(days=400),
                               target_role="Other", technical_skills=("Go",))
        assert not again.created
        assert await profile_row(database, candidate_id) == before  # every column, including consent, revision and expiry
        assert await counts(database) == (1, 1)
        assert await repo.active_identity_exists(key) and not await repo.active_identity_exists(verifier())
    run(db_url, scenario)


def test_an_expired_identity_is_never_reclaimed_or_overwritten(db_url):
    async def scenario(database, repo):
        key = verifier()
        await register(repo, "OldName", key=key, technical_skills=("COBOL",))
        candidate_id = await candidate_id_for(database, key)
        await expire(database, candidate_id)
        before = await profile_row(database, candidate_id)
        assert not await repo.active_identity_exists(key)
        with pytest.raises(RecoveryKeyUnavailableError):
            await register(repo, "NewName", key=key)
        assert await profile_row(database, candidate_id) == before and await candidate_id_for(database, key) == candidate_id
        assert await counts(database) == (1, 1)
        with pytest.raises(ProfileNotFoundError):  # nothing exposes the old data
            await repo.get(candidate_id)
    run(db_url, scenario)


def test_a_profileless_candidate_fails_closed_too(db_url):
    async def scenario(database, repo):
        key = verifier()
        async with database.sessions.begin() as session:
            await session.execute(insert(Candidate).values(id=uuid4(), recovery_secret_hash=key))
        with pytest.raises(RecoveryKeyUnavailableError):
            await register(repo, key=key)
        assert await counts(database) == (1, 0)
    run(db_url, scenario)


def test_after_cleanup_deletes_the_expired_identity_the_key_can_register_fresh(db_url):
    async def scenario(database, repo):
        key = verifier()
        await register(repo, "Old", key=key)
        old_id = await candidate_id_for(database, key)
        await expire(database, old_id)
        assert (await AuthRepository(database.sessions).purge_expired()).candidates == 1
        assert (await register(repo, "New", key=key)).created
        new_id = await candidate_id_for(database, key)
        assert new_id != old_id and (await repo.get(new_id)).profile.name == "New"
    run(db_url, scenario)


def test_registration_while_cleanup_holds_the_expired_row_fails_closed_then_succeeds_after_the_delete(db_url):
    async def scenario(database, repo):
        key = verifier()
        await register(repo, "Old", key=key)
        old_id = await candidate_id_for(database, key)
        await expire(database, old_id)
        seen = {}

        async def hook(session):  # cleanup holds its locks and has not deleted yet
            with pytest.raises(RecoveryKeyUnavailableError):
                await asyncio.wait_for(register(repo, "Intruder", key=key), 10)
            seen["old_row_intact"] = await candidate_id_for(database, key) == old_id

        purger = AuthRepository(PausingSessions(database.sessions, hook))
        assert (await purger.purge_expired()).candidates == 1 and seen["old_row_intact"]
        assert (await register(repo, "New", key=key)).created
    run(db_url, scenario)


def test_a_second_save_waits_for_an_uncommitted_first_save_then_sees_it_exists(db_url):
    async def scenario(database, repo):
        key = verifier()
        first = database.sessions()
        await first.begin()
        candidate_id = uuid4()
        await first.execute(insert(Candidate).values(id=candidate_id, recovery_secret_hash=key))
        second = asyncio.create_task(register(repo, "Second", key=key))
        await wait_for_lock_waiter(database)
        assert not second.done()  # blocked on the first transaction's unique-index entry
        await first.execute(insert(CandidateProfileRow).values(candidate_id=candidate_id, name="First", declined_fields=[],
            consent_version=VERSION, consented_at=func.statement_timestamp(),
            expires_at=func.statement_timestamp() + RETENTION))
        await first.commit()
        await first.close()
        assert not (await asyncio.wait_for(second, 10)).created
        assert (await repo.get(candidate_id)).profile.name == "First" and await counts(database) == (1, 1)
    run(db_url, scenario)


def test_a_second_save_proceeds_and_creates_when_the_first_save_rolls_back(db_url):
    async def scenario(database, repo):
        key = verifier()
        first = database.sessions()
        await first.begin()
        await first.execute(insert(Candidate).values(id=uuid4(), recovery_secret_hash=key))
        second = asyncio.create_task(register(repo, "Second", key=key))
        await wait_for_lock_waiter(database)
        assert not second.done()
        await first.rollback()
        await first.close()
        assert (await asyncio.wait_for(second, 10)).created
        assert (await repo.get(await candidate_id_for(database, key))).profile.name == "Second"
    run(db_url, scenario)


def test_concurrent_identical_saves_create_exactly_one_identity(db_url):
    async def scenario(database, repo):
        key = verifier()
        results = await asyncio.gather(*(register(repo, f"Racer{i}", key=key) for i in range(6)))
        assert sum(r.created for r in results) == 1 and await counts(database) == (1, 1)
        name = (await repo.get(await candidate_id_for(database, key))).profile.name
        assert name.startswith("Racer")
    run(db_url, scenario)


def test_a_failure_after_the_candidate_insert_rolls_everything_back(db_url):
    async def scenario(database, repo):
        key = verifier()

        class Proxy:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            async def execute(self, *a, **k):  # the profile insert is the first execute() of a first save
                raise SQLAlchemyError("simulated failure after the candidate insert")

        class Sessions:
            def __init__(self, inner):
                self._inner = inner

            def begin(self):
                outer = self._inner.begin()

                class Context:
                    async def __aenter__(self_inner):
                        return Proxy(await outer.__aenter__())

                    async def __aexit__(self_inner, *exc):
                        return await outer.__aexit__(*exc)
                return Context()

        with pytest.raises(DatabaseUnavailableError):
            await CandidateRepository(Sessions(database.sessions)).register(
                CandidateProfile(name="X"), recovery_secret_hash=key, consent_version=VERSION, retention=RETENTION)
        assert await counts(database) == (0, 0)  # no half-created identity
        assert (await register(repo, "Retry", key=key)).created
    run(db_url, scenario)


def test_invalid_input_is_rejected_without_writing(db_url):
    async def scenario(database, repo):
        with pytest.raises(DatabaseConstraintError):
            await register(repo, key=b"short")
        assert await counts(database) == (0, 0)
        for kwargs in (dict(version=" "), dict(version="x" * 81), dict(retention=timedelta(0)), dict(retention=-timedelta(days=1))):
            with pytest.raises(ValueError):
                await register(repo, **kwargs)
        assert await counts(database) == (0, 0)
    run(db_url, scenario)


def test_a_lost_response_after_commit_is_recovered_by_retrying_the_same_key(db_url):
    async def scenario(database, repo):
        key = verifier()
        await register(repo, "Committed", key=key)  # the caller never saw this result
        retry = await register(repo, "Committed", key=key)
        assert not retry.created and (await repo.get(await candidate_id_for(database, key))).profile.name == "Committed"
    run(db_url, scenario)
