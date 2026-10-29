import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.db.auth_repository import AuthRepository
from app.db.candidate_repository import CandidateRepository
from app.db.config import DatabaseSettings
from app.db.database import Database
from app.db.database import DatabaseUnavailableError
from app.db.models import Candidate, CandidateAuthSession, CandidateProfileRow
from app.main import create_app
from app.services.candidate_auth import RecoveryKey
from app.services.candidate_profile import CandidateProfile

pytestmark = pytest.mark.integration

ORIGIN = "https://app.example.com"
COOKIE = "__Host-bolhire_session"


@pytest.fixture
def app(db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", db_url.render_as_string(hide_password=False))
    monkeypatch.setenv("AUTH_TRUSTED_ORIGIN", ORIGIN)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("SPEECH_PROVIDER", "mock")
    return create_app()


def database_call(db_url, operation):
    async def main():
        database = Database(DatabaseSettings(True, db_url))
        try:
            return await operation(database)
        finally:
            await database.close()
    return asyncio.run(main())


def seed(db_url, name="Person"):
    key = RecoveryKey.generate()

    async def operation(database):
        meta = dict(consent_version="v1", consented_at=datetime.now(timezone.utc),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        saved = await CandidateRepository(database.sessions).create(
            CandidateProfile(name=name), recovery_secret_hash=key.verifier(), **meta)
        return saved.candidate_id
    return key, database_call(db_url, operation)


def expire_profile(db_url, candidate_id):
    async def operation(database):
        async with database.sessions.begin() as session:
            past = func.statement_timestamp() - timedelta(days=2)
            await session.execute(update(CandidateProfileRow).where(CandidateProfileRow.candidate_id == candidate_id).values(
                created_at=past, updated_at=past, expires_at=func.statement_timestamp() - timedelta(days=1)))
    database_call(db_url, operation)


def count(db_url, model):
    async def operation(database):
        async with database.sessions() as session:
            return await session.scalar(select(func.count()).select_from(model))
    return database_call(db_url, operation)


def login(client, key):
    response = client.post("/api/v1/auth/login", json={"recovery_key": key.display()}, headers={"Origin": ORIGIN})
    value = response.headers["set-cookie"].split(";")[0].split("=", 1)[1] if "set-cookie" in response.headers else None
    return response, value


def session(client, value):
    return client.get("/api/v1/auth/session", headers={"Cookie": f"{COOKIE}={value}"})


def test_lifespan_wires_and_clears_the_auth_repository(app):
    assert app.state.auth_repository is None
    with TestClient(app):
        assert isinstance(app.state.auth_repository, AuthRepository)
    assert app.state.auth_repository is None


def test_full_lifecycle_login_session_logout_and_replay(app, db_url):
    key, candidate_id = seed(db_url)
    with TestClient(app) as client:
        response, value = login(client, key)
        assert response.status_code == 200 and session(client, value).json() == {"authenticated": True}
        assert count(db_url, CandidateAuthSession) == 1
        out = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN, "Cookie": f"{COOKIE}={value}"})
        assert out.status_code == 200 and count(db_url, CandidateAuthSession) == 0
        assert session(client, value).status_code == 401  # replaying the revoked cookie fails
        assert client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}).status_code == 200
        wrong, _ = login(client, RecoveryKey.generate())
        assert wrong.status_code == 401 and "set-cookie" not in wrong.headers


def test_expired_identity_is_inaccessible_before_any_cleanup(app, db_url):
    key, candidate_id = seed(db_url)
    with TestClient(app) as client:
        _, value = login(client, key)
        assert session(client, value).status_code == 200
        expire_profile(db_url, candidate_id)
        assert count(db_url, Candidate) == 1  # nothing was purged
        stale = session(client, value)
        assert stale.status_code == 401 and "max-age=0" in stale.headers["set-cookie"].lower()
        refused, _ = login(client, key)
        assert refused.status_code == 401 and refused.json()["error"]["code"] == "INVALID_RECOVERY_KEY"


def test_failure_after_commit_leaves_an_orphan_and_retry_succeeds(app, db_url):
    key, candidate_id = seed(db_url)

    class CommitThenFail:
        def __init__(self, inner):
            self.inner, self.armed = inner, True

        async def create_session(self, verifier, token, **kwargs):
            result = await self.inner.create_session(verifier, token, **kwargs)
            if self.armed:
                self.armed = False
                raise DatabaseUnavailableError()
            return result

        def __getattr__(self, name):
            return getattr(self.inner, name)

    with TestClient(app) as client:
        app.state.auth_repository = CommitThenFail(app.state.auth_repository)
        failed, _ = login(client, key)
        assert failed.status_code == 503 and "set-cookie" not in failed.headers
        assert count(db_url, CandidateAuthSession) == 1  # committed, but the client never received the cookie
        retry, value = login(client, key)
        assert retry.status_code == 200 and session(client, value).status_code == 200
        assert count(db_url, CandidateAuthSession) == 2


def test_simultaneous_logins_each_get_a_working_session(app, db_url):
    key, _ = seed(db_url)
    with TestClient(app) as client:
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _: login(client, key), range(8)))
        assert all(response.status_code == 200 for response, _ in results)
        values = [value for _, value in results]
        assert len(set(values)) == 8 and all(session(client, value).status_code == 200 for value in values)


def test_cleanup_between_requests_does_not_break_other_identities(app, db_url):
    kept_key, _ = seed(db_url, "Kept")
    gone_key, gone_id = seed(db_url, "Gone")
    with TestClient(app) as client:
        _, kept_cookie = login(client, kept_key)
        expire_profile(db_url, gone_id)

        async def purge(database):
            return await AuthRepository(database.sessions).purge_expired()
        assert database_call(db_url, purge).candidates == 1
        assert session(client, kept_cookie).status_code == 200
        assert login(client, gone_key)[0].status_code == 401
