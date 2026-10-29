import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, insert, select, text, update

from app.db.auth_repository import AuthRepository, InvalidCredentialsError, SessionInvalidError
from app.db.candidate_repository import CandidateRepository, DatabaseConstraintError
from app.db.config import DatabaseSettings
from app.db.database import Database
from app.db.models import Candidate, CandidateAuthSession, CandidateProfileRow
from app.services.candidate_auth import RecoveryKey, SessionToken
from app.services.candidate_profile import CandidateProfile

pytestmark = pytest.mark.integration


def metadata(**kwargs):
    return dict(consent_version="test-v1", consented_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1)) | kwargs


def run(db_url, scenario):
    async def main():
        database = Database(DatabaseSettings(True, db_url))
        try:
            return await scenario(database, CandidateRepository(database.sessions), AuthRepository(database.sessions))
        finally:
            await database.close()
    return asyncio.run(main())


async def identity(repo, name="Person"):
    key = RecoveryKey.generate()
    saved = await repo.create(CandidateProfile(name=name), recovery_secret_hash=key.verifier(), **metadata())
    return key, saved.candidate_id


async def expire_profile(database, candidate_id):
    async with database.sessions.begin() as session:
        past = func.statement_timestamp() - timedelta(days=2)
        await session.execute(update(CandidateProfileRow).where(CandidateProfileRow.candidate_id == candidate_id).values(
            created_at=past, updated_at=past, expires_at=func.statement_timestamp() - timedelta(days=1)))


async def expire_sessions(database, candidate_id):
    async with database.sessions.begin() as session:
        await session.execute(update(CandidateAuthSession).where(CandidateAuthSession.candidate_id == candidate_id).values(
            created_at=func.statement_timestamp() - timedelta(days=9), expires_at=func.statement_timestamp() - timedelta(days=2)))


async def count(database, model):
    async with database.sessions() as session:
        return await session.scalar(select(func.count()).select_from(model))


def test_login_stores_only_hashes_and_resolves(db_url):
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        token = SessionToken.generate()
        assert await auth.create_session(key.verifier(), token) == candidate_id
        principal = await auth.resolve(token)
        assert principal.candidate_id == candidate_id
        async with database.sessions() as session:
            row = (await session.execute(select(CandidateAuthSession))).scalar_one()
            assert row.token_hash == token.verifier() and row.id == principal.session_id
            assert row.expires_at - row.created_at == timedelta(days=7)
            dump = " ".join([await session.scalar(text(f"SELECT string_agg(t::text, ' ') FROM {name} t")) or ""
                             for name in ("candidates", "candidate_profiles", "candidate_auth_sessions")])
        for secret in (token.reveal(), key.display(), key.display().replace("-", "")):
            assert secret not in dump
    run(db_url, scenario)


def test_login_rejects_unknown_and_expired_identities_generically(db_url):
    async def scenario(database, repo, auth):
        with pytest.raises(InvalidCredentialsError):
            await auth.create_session(RecoveryKey.generate().verifier(), SessionToken.generate())
        key, candidate_id = await identity(repo)
        await expire_profile(database, candidate_id)
        with pytest.raises(InvalidCredentialsError):
            await auth.create_session(key.verifier(), SessionToken.generate())
        assert await count(database, CandidateAuthSession) == 0
    run(db_url, scenario)


def test_resolve_enforces_session_and_profile_expiry_without_purge(db_url):
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        token, other = SessionToken.generate(), SessionToken.generate()
        await auth.create_session(key.verifier(), token)
        await auth.create_session(key.verifier(), other)
        with pytest.raises(SessionInvalidError):
            await auth.resolve(SessionToken.generate())
        await expire_profile(database, candidate_id)  # sessions still unexpired, nothing purged
        for value in (token, other):
            with pytest.raises(SessionInvalidError):
                await auth.resolve(value)
        assert await count(database, CandidateAuthSession) == 2
    run(db_url, scenario)


def test_resolve_rejects_expired_session(db_url):
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        token = SessionToken.generate()
        await auth.create_session(key.verifier(), token)
        await auth.resolve(token)
        await expire_sessions(database, candidate_id)
        with pytest.raises(SessionInvalidError):
            await auth.resolve(token)
    run(db_url, scenario)


def test_revocation(db_url):
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        a, b, c = (SessionToken.generate() for _ in range(3))
        for token in (a, b, c):
            await auth.create_session(key.verifier(), token)
        assert await auth.delete_session(a) is True
        assert await auth.delete_session(a) is False  # idempotent
        with pytest.raises(SessionInvalidError):
            await auth.resolve(a)
        kept = (await auth.resolve(b)).session_id
        assert await auth.delete_candidate_sessions(candidate_id, except_session_id=kept) == 1
        await auth.resolve(b)
        with pytest.raises(SessionInvalidError):
            await auth.resolve(c)
        assert await auth.delete_candidate_sessions(candidate_id) == 1
        assert await auth.delete_candidate_sessions(candidate_id) == 0
        other_key, other_id = await identity(repo, "Other")
        other_token = SessionToken.generate()
        await auth.create_session(other_key.verifier(), other_token)
        await auth.delete_candidate_sessions(candidate_id)
        await auth.resolve(other_token)  # another candidate is untouched
        await expire_sessions(database, other_id)
        assert await auth.delete_session(other_token) is True  # expired sessions can still be revoked
    run(db_url, scenario)


def test_candidate_delete_cascades_to_sessions(db_url):
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        token = SessionToken.generate()
        await auth.create_session(key.verifier(), token)
        assert await repo.delete(candidate_id)
        with pytest.raises(SessionInvalidError):
            await auth.resolve(token)
        assert await count(database, CandidateAuthSession) == 0
    run(db_url, scenario)


def test_simultaneous_logins_create_distinct_working_sessions(db_url):
    async def scenario(database, repo, auth):
        key, _ = await identity(repo)
        tokens = [SessionToken.generate() for _ in range(12)]
        await asyncio.gather(*(auth.create_session(key.verifier(), token) for token in tokens))
        principals = await asyncio.gather(*(auth.resolve(token) for token in tokens))
        assert len({principal.session_id for principal in principals}) == 12
        assert await count(database, CandidateAuthSession) == 12
    run(db_url, scenario)


def test_lost_login_response_leaves_an_orphan_that_does_not_block_retry(db_url):
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        await auth.create_session(key.verifier(), SessionToken.generate())  # response "lost": token unknown
        retry = SessionToken.generate()
        await auth.create_session(key.verifier(), retry)
        assert (await auth.resolve(retry)).candidate_id == candidate_id
        await expire_sessions(database, candidate_id)
        assert (await auth.purge_expired()).sessions == 2
    run(db_url, scenario)


def test_login_racing_delete_never_leaves_orphans_or_unexpected_errors(db_url):
    """Stress only: the interleaving is left to scheduling. The deterministic foreign-key case is in test_auth_concurrency.py."""
    async def scenario(database, repo, auth):
        for _ in range(15):
            key, candidate_id = await identity(repo)
            outcomes = await asyncio.gather(
                *(auth.create_session(key.verifier(), SessionToken.generate()) for _ in range(3)),
                repo.delete(candidate_id), return_exceptions=True)
            assert all(isinstance(item, (bool, type(candidate_id), InvalidCredentialsError)) for item in outcomes), outcomes
        async with database.sessions() as session:
            orphans = await session.scalar(select(func.count()).select_from(CandidateAuthSession).where(
                ~select(Candidate.id).where(Candidate.id == CandidateAuthSession.candidate_id).exists()))
        assert orphans == 0
    run(db_url, scenario)


def test_login_for_an_expired_identity_while_cleanup_runs_fails_generically_and_cleanup_completes(db_url):
    """Adjacent case, not the foreign-key race: an expired identity is refused by login's WHERE clause, so login never reaches the
    foreign-key check here. It shows cleanup and a refused login coexist without errors or orphan sessions."""
    async def scenario(database, repo, auth):
        for _ in range(10):
            key, candidate_id = await identity(repo)
            await expire_profile(database, candidate_id)
            outcomes = await asyncio.gather(
                auth.create_session(key.verifier(), SessionToken.generate()), auth.purge_expired(), return_exceptions=True)
            assert isinstance(outcomes[0], InvalidCredentialsError) and not isinstance(outcomes[1], Exception), outcomes
        assert await count(database, Candidate) == 0
    run(db_url, scenario)


def test_purge_removes_only_expired_identities_and_their_sessions(db_url):
    async def scenario(database, repo, auth):
        active_key, active_id = await identity(repo, "Active")
        active_token = SessionToken.generate()
        await auth.create_session(active_key.verifier(), active_token)
        expired = []
        for index in range(3):
            key, candidate_id = await identity(repo, f"Expired{index}")
            await auth.create_session(key.verifier(), SessionToken.generate())
            await expire_profile(database, candidate_id)
            expired.append(candidate_id)
        async with database.sessions.begin() as session:  # a profile-less candidate (manual SQL only)
            await session.execute(insert(Candidate).values(recovery_secret_hash=b"p" * 32))
        result = await auth.purge_expired(batch_size=2)  # forces multiple batches
        assert result.candidates == 4
        assert await count(database, Candidate) == 1 and await count(database, CandidateProfileRow) == 1
        assert await count(database, CandidateAuthSession) == 1
        assert (await auth.resolve(active_token)).candidate_id == active_id
        assert (await auth.purge_expired()) == type(result)()  # idempotent
    run(db_url, scenario)


def test_purge_removes_expired_sessions_of_active_identities(db_url):
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        await auth.create_session(key.verifier(), SessionToken.generate())
        fresh = SessionToken.generate()
        await auth.create_session(key.verifier(), fresh)
        async with database.sessions.begin() as session:
            first = await session.scalar(select(CandidateAuthSession.id).order_by(CandidateAuthSession.created_at).limit(1))
            await session.execute(update(CandidateAuthSession).where(CandidateAuthSession.id == first).values(
                created_at=func.statement_timestamp() - timedelta(days=9), expires_at=func.statement_timestamp() - timedelta(days=2)))
        assert (await auth.purge_expired()).sessions == 1
        await auth.resolve(fresh)
    run(db_url, scenario)


def test_purge_skips_locked_rows_and_never_deletes_a_renewed_identity(db_url):
    async def scenario(database, repo, auth):
        key, candidate_id = await identity(repo)
        await expire_profile(database, candidate_id)
        renewal = database.sessions()
        await renewal.begin()
        await renewal.execute(text("SELECT 1 FROM candidate_profiles WHERE candidate_id = :id FOR UPDATE"), {"id": candidate_id})
        # Profile locked by an in-flight renewal: purge must neither wait nor delete.
        result = await asyncio.wait_for(auth.purge_expired(), 5)
        assert result.candidates == 0 and await count(database, Candidate) == 1
        await renewal.execute(update(CandidateProfileRow).where(CandidateProfileRow.candidate_id == candidate_id).values(
            expires_at=func.statement_timestamp() + timedelta(days=1)))
        await renewal.commit()
        await renewal.close()
        assert (await auth.purge_expired()).candidates == 0  # renewed: no longer expired
        assert (await repo.get(candidate_id)).profile.name == "Person"
        await auth.create_session(key.verifier(), SessionToken.generate())

        # A locked candidate row (login/registration in flight) is skipped as well.
        await expire_profile(database, candidate_id)
        holder = database.sessions()
        await holder.begin()
        await holder.execute(text("SELECT 1 FROM candidates WHERE id = :id FOR UPDATE"), {"id": candidate_id})
        assert (await asyncio.wait_for(auth.purge_expired(), 5)).candidates == 0
        await holder.rollback()
        await holder.close()
        assert (await auth.purge_expired()).candidates == 1
    run(db_url, scenario)


def test_concurrent_purges_do_not_error_or_double_count(db_url):
    async def scenario(database, repo, auth):
        for index in range(6):
            _, candidate_id = await identity(repo, f"E{index}")
            await expire_profile(database, candidate_id)
        results = await asyncio.gather(*(auth.purge_expired(batch_size=2) for _ in range(4)))
        assert sum(result.candidates for result in results) == 6
        assert await count(database, Candidate) == 0
    run(db_url, scenario)


def test_an_expired_identity_reserves_its_key_hash_until_cleanup_releases_it(db_url):
    """Documents the constraint Task 9.3's identity creation must handle (see docs/progress.md): the unique hash
    outlives the profile, so the 9.1 `create` cannot reuse a key until cleanup has run."""
    async def scenario(database, repo, auth):
        key = RecoveryKey.generate()
        old = await repo.create(CandidateProfile(name="Old"), recovery_secret_hash=key.verifier(), **metadata())
        await expire_profile(database, old.candidate_id)
        with pytest.raises(DatabaseConstraintError):
            await repo.create(CandidateProfile(name="New"), recovery_secret_hash=key.verifier(), **metadata())
        assert (await auth.purge_expired()).candidates == 1
        new = await repo.create(CandidateProfile(name="New"), recovery_secret_hash=key.verifier(), **metadata())
        assert new.candidate_id != old.candidate_id and new.profile.name == "New"
        assert await count(database, Candidate) == 1
    run(db_url, scenario)
