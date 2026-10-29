"""Deterministic PostgreSQL interleavings for login and cleanup.

Every scenario holds real row locks in real transactions and proceeds only after PostgreSQL itself reports that a statement is
blocked on a lock (`pg_stat_activity.wait_event_type = 'Lock'`). Polling for that observed state is how the tests synchronize; no
test assumes that a sleep was long enough.

What is NOT established here (no deterministic reproduction exists without changing production SQL): a renewal that commits in the
instant between cleanup's snapshot and its lock attempt. PostgreSQL's READ COMMITTED row-lock re-check covers it, but no test
here exercises that exact window.
"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select, text, update

from app.db.auth_repository import AuthRepository, InvalidCredentialsError
from app.db.models import Candidate, CandidateAuthSession, CandidateProfileRow
from app.services.candidate_auth import SessionToken
from test_auth_repository import count, expire_profile, identity, run

pytestmark = pytest.mark.integration


async def wait_for_lock_waiter(database, timeout: float = 10.0) -> None:
    """Return once some statement of this database is blocked on a lock; fail if none ever is."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        async with database.sessions() as session:
            waiting = await session.scalar(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"))
        if waiting:
            return
        if loop.time() > deadline:
            raise AssertionError("no statement ever blocked on a lock: the interleaving was not reached")
        await asyncio.sleep(0.02)  # polling cadence only; progress is gated on the observed lock wait above


async def hold_candidate_lock(database, candidate_id):
    holder = database.sessions()
    await holder.begin()
    await holder.execute(text("SELECT 1 FROM candidates WHERE id = :id FOR UPDATE"), {"id": candidate_id})
    return holder


def test_login_blocked_in_the_foreign_key_check_fails_generically_when_the_delete_commits(db_url):
    """Login's SELECT sees the unexpired identity, then its foreign-key check waits on the candidate row lock. When the holder
    deletes the candidate and commits, the insert must fail with a foreign-key violation, reported as invalid credentials."""
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        holder = await hold_candidate_lock(database, candidate_id)
        login = asyncio.create_task(auth.create_session(key.verifier(), SessionToken.generate()))
        await wait_for_lock_waiter(database)
        assert not login.done()  # passed its WHERE clause (the identity was visible) and is parked in the foreign-key check
        await holder.execute(delete(Candidate).where(Candidate.id == candidate_id))
        await holder.commit()
        await holder.close()
        with pytest.raises(InvalidCredentialsError):  # not DatabaseConstraintError: the 23503 mapping is what this pins
            await asyncio.wait_for(login, 10)
        assert await count(database, CandidateAuthSession) == 0 and await count(database, Candidate) == 0
    run(db_url, scenario)


def test_control_the_same_login_succeeds_when_the_lock_holder_rolls_back(db_url):
    """Without the delete the identical blocked login completes: the failure above comes from the delete, not from the lock."""
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        holder = await hold_candidate_lock(database, candidate_id)
        token = SessionToken.generate()
        login = asyncio.create_task(auth.create_session(key.verifier(), token))
        await wait_for_lock_waiter(database)
        assert not login.done()
        await holder.rollback()
        await holder.close()
        assert await asyncio.wait_for(login, 10) == candidate_id
        assert (await auth.resolve(token)).candidate_id == candidate_id
    run(db_url, scenario)


# ---- cleanup interleavings ---------------------------------------------------------------------------------------------------------------------------
class PausingSessions:
    """A session factory for `AuthRepository` that runs `hook(session)` exactly once: right after the FIRST `scalars` call of the
    first transaction, which is cleanup's lock statement (`SELECT ... FOR UPDATE SKIP LOCKED`), and before its delete statement.
    The locks are held and nothing has been deleted when the hook runs. Nothing else is altered."""

    def __init__(self, inner, hook):
        self._inner, self._hook, self.fired = inner, hook, False

    def __call__(self):
        return self._inner()

    def begin(self):
        return _PausingTransaction(self._inner.begin(), self)


class _PausingTransaction:
    def __init__(self, context, owner):
        self._context, self._owner = context, owner

    async def __aenter__(self):
        return _PausingSession(await self._context.__aenter__(), self._owner)

    async def __aexit__(self, *exc):
        return await self._context.__aexit__(*exc)


class _PausingSession:
    def __init__(self, session, owner):
        self._session, self._owner = session, owner

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def scalars(self, *args, **kwargs):
        result = await self._session.scalars(*args, **kwargs)
        if not self._owner.fired:
            self._owner.fired = True
            await self._owner._hook(self._session)
        return result


async def unconditional_renewal(database, candidate_id) -> int:
    """Stands for a renewal that began before the expiry instant. (Today's `replace` refuses expired rows, so it cannot be used
    after expiry; this plain UPDATE is what any future renewal rule must survive.) Returns the number of rows it updated."""
    async with database.sessions.begin() as session:
        rows = await session.scalars(update(CandidateProfileRow).where(CandidateProfileRow.candidate_id == candidate_id).values(
            expires_at=func.statement_timestamp() + timedelta(days=1)).returning(CandidateProfileRow.candidate_id))
        return len(rows.all())


def test_a_renewal_arriving_after_cleanup_took_its_locks_waits_and_then_finds_nothing_to_revive(db_url):
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        await expire_profile(database, candidate_id)
        seen = {}

        async def hook(session):
            seen["renewal"] = asyncio.create_task(unconditional_renewal(database, candidate_id))
            await wait_for_lock_waiter(database)
            assert not seen["renewal"].done()  # blocked on the profile row lock cleanup holds

        purger = AuthRepository(PausingSessions(database.sessions, hook))
        assert (await purger.purge_expired()).candidates == 1
        assert seen["renewal"] is not None and await asyncio.wait_for(seen["renewal"], 10) == 0  # row gone: nothing updated, nothing revived
        assert await count(database, Candidate) == 0 and await count(database, CandidateProfileRow) == 0
    run(db_url, scenario)


def test_the_delete_statement_rechecks_expiry_and_keeps_an_identity_renewed_before_it_runs(db_url):
    """Unit test of the delete's own predicate. Across transactions this state cannot occur while cleanup holds the locks, so the
    renewal is applied inside cleanup's own transaction between its two statements. It shows the delete refuses a no-longer-expired
    identity (defence in depth); it does not by itself prove the cross-transaction protocol, which the test above and the
    lock-skipping test in test_auth_repository.py cover."""
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        await expire_profile(database, candidate_id)

        async def hook(session):
            await session.execute(update(CandidateProfileRow).where(CandidateProfileRow.candidate_id == candidate_id).values(
                expires_at=func.statement_timestamp() + timedelta(days=1)))

        purger = AuthRepository(PausingSessions(database.sessions, hook))
        assert (await purger.purge_expired()).candidates == 0
        saved = await repo.get(candidate_id)  # still present and active
        assert saved.profile.name == "Person" and saved.expires_at > (await _now(database))
        assert await count(database, Candidate) == 1
    run(db_url, scenario)


async def _now(database):
    async with database.sessions() as session:
        return await session.scalar(select(func.statement_timestamp()))
