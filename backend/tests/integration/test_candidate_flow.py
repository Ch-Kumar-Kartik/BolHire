"""The Task 9.3 vertical slice against real PostgreSQL: intake session -> preview -> key -> save -> login -> retrieval."""

import asyncio
import contextlib
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from app.db.auth_repository import AuthRepository
from app.db.database import DatabaseUnavailableError
from app.db.models import Candidate, CandidateAuthSession, CandidateProfileRow
from app.main import create_app
from app.models import ConversationMessage
from app.services.candidate_auth import RecoveryKey
from app.services.candidate_profile import CandidateProfileUpdate

pytestmark = pytest.mark.integration

ORIGIN = "https://app.example.com"
COOKIE = "__Host-bolhire_session"
VERSION = "candidate-profile-v1:en-IN"
EXCHANGE = [ConversationMessage(role="user", content="hello"), ConversationMessage(role="assistant", content="hi")]


def update_for(name, skill="Python"):
    return CandidateProfileUpdate(name=name, target_role="Backend developer", skills_add=[skill], experience_years=2)


@pytest.fixture
def app(db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", db_url.render_as_string(hide_password=False))
    monkeypatch.setenv("AUTH_TRUSTED_ORIGIN", ORIGIN)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("SPEECH_PROVIDER", "mock")
    return create_app()


@contextlib.asynccontextmanager
async def running(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            yield client


def run(app, scenario):
    async def main():
        async with running(app) as client:
            return await scenario(client)
    return asyncio.run(main())


def store_of(app):
    return app.state.candidate_intake_pipeline.sessions


async def post(client, path, body):
    return await client.post(f"/api/v1/candidate{path}", json=body, headers={"Origin": ORIGIN})


async def start(client, app, name="Asha", skill="Python"):
    session = await store_of(app).create_candidate_exchange(update_for(name, skill), EXCHANGE)
    digest = (await post(client, "/intake/preview", {"session_id": session.session_id, "language": "en-IN"})).json()["profile_digest"]
    key = (await post(client, "/recovery-key", {"session_id": session.session_id})).json()["recovery_key"]
    return session, key, digest


def save_body(session, key, digest, version=VERSION):
    return {"session_id": session.session_id, "recovery_key": key, "profile_digest": digest, "consent_version": version, "consent_accepted": True}


async def login(client, key):
    response = await client.post("/api/v1/auth/login", json={"recovery_key": key}, headers={"Origin": ORIGIN})
    cookie = None
    if "set-cookie" in response.headers:
        cookie = {"Cookie": f"{COOKIE}={response.headers['set-cookie'].split(';')[0].split('=', 1)[1]}"}
    return response, cookie


async def database_rows(app):
    async with app.state.database.sessions() as session:
        candidates = await session.scalar(select(func.count()).select_from(Candidate))
        profiles = (await session.execute(select(CandidateProfileRow))).scalars().all()
        return candidates, profiles


async def expire_all(app):
    async with app.state.database.sessions.begin() as session:
        past = func.statement_timestamp() - timedelta(days=2)
        await session.execute(update(CandidateProfileRow).values(created_at=past, updated_at=past,
                                                                 expires_at=func.statement_timestamp() - timedelta(days=1)))


def test_full_flow_save_login_and_retrieve_with_database_clock_retention(app):
    async def scenario(client):
        session, key, digest = await start(client, app)
        saved = await post(client, "/profile", save_body(session, key, digest))
        assert saved.status_code == 200 and saved.json() == {"created": True}
        candidates, [row] = await database_rows(app)
        assert candidates == 1 and row.consent_version == VERSION and row.revision == 1
        assert row.consented_at == row.created_at and row.expires_at - row.created_at == timedelta(days=90)
        assert row.name == "Asha" and row.technical_skills == ["Python"] and row.experience_years == 2.0
        assert session.session_id not in store_of(app)._sessions  # the temporary conversation was discarded
        logged_in, cookie = await login(client, key)
        assert logged_in.status_code == 200
        profile = await client.get("/api/v1/candidate/profile", headers=cookie)
        body = profile.json()
        assert profile.status_code == 200 and body["profile"]["name"] == "Asha" and body["profile"]["technical_skills"] == ["Python"]
        assert body["consent_version"] == VERSION and body["expires_at"] > body["consented_at"] and "candidate_id" not in profile.text
    run(app, scenario)


def test_a_lost_response_is_recovered_by_retrying_the_same_save_or_by_logging_in(app):
    async def scenario(client):
        session, key, digest = await start(client, app)
        body = save_body(session, key, digest)
        assert (await post(client, "/profile", body)).json() == {"created": True}  # the client never saw this
        retry = await post(client, "/profile", body)
        assert retry.status_code == 200 and retry.json() == {"created": False}
        candidates, profiles = await database_rows(app)
        assert candidates == 1 and len(profiles) == 1 and profiles[0].revision == 1
        assert (await login(client, key))[0].status_code == 200
    run(app, scenario)


def test_a_failure_after_the_real_commit_keeps_the_session_and_the_retry_succeeds_idempotently(app):
    async def scenario(client):
        session, key, digest = await start(client, app)
        real = app.state.candidate_repository

        class CommitThenFail:
            armed = True

            async def register(self, *args, **kwargs):
                result = await real.register(*args, **kwargs)  # really commits
                if self.armed:
                    self.armed = False
                    raise DatabaseUnavailableError()
                return result

            def __getattr__(self, name):
                return getattr(real, name)

        app.state.candidate_repository = CommitThenFail()
        failed = await post(client, "/profile", save_body(session, key, digest))
        assert failed.status_code == 503 and session.session_id in store_of(app)._sessions
        assert (await database_rows(app))[0] == 1  # committed although the client was told 503
        retry = await post(client, "/profile", save_body(session, key, digest))
        assert retry.status_code == 200 and retry.json() == {"created": False}
        assert session.session_id not in store_of(app)._sessions and (await database_rows(app))[0] == 1
    run(app, scenario)


def test_concurrent_identical_saves_create_exactly_one_identity(app):
    async def scenario(client):
        session, key, digest = await start(client, app)
        body = save_body(session, key, digest)
        first, second = await asyncio.gather(post(client, "/profile", body), post(client, "/profile", body))
        assert sorted([first.json()["created"], second.json()["created"]]) == [False, True]
        candidates, profiles = await database_rows(app)
        assert candidates == 1 and len(profiles) == 1
    run(app, scenario)


def test_each_candidate_sees_only_their_own_profile_whatever_the_request_says(app):
    async def scenario(client):
        a_session, a_key, a_digest = await start(client, app, "Asha", "Python")
        b_session, b_key, b_digest = await start(client, app, "Ravi", "Go")
        await post(client, "/profile", save_body(a_session, a_key, a_digest))
        await post(client, "/profile", save_body(b_session, b_key, b_digest))
        (_, a_cookie), (_, b_cookie) = await login(client, a_key), await login(client, b_key)
        a = (await client.get("/api/v1/candidate/profile", headers=a_cookie)).json()["profile"]
        b = (await client.get("/api/v1/candidate/profile?candidate_id=" + str((await database_rows(app))[1][0].candidate_id), headers=b_cookie)).json()["profile"]
        assert (a["name"], a["technical_skills"]) == ("Asha", ["Python"]) and (b["name"], b["technical_skills"]) == ("Ravi", ["Go"])
        assert (await client.get("/api/v1/candidate/profile")).status_code == 401
    run(app, scenario)


def test_an_expired_profile_is_inaccessible_before_cleanup_and_its_key_fails_closed_until_cleanup_deletes_it(app):
    async def scenario(client):
        session, key, digest = await start(client, app, "OldName", "COBOL")
        await post(client, "/profile", save_body(session, key, digest))
        _, cookie = await login(client, key)
        assert (await client.get("/api/v1/candidate/profile", headers=cookie)).status_code == 200
        await expire_all(app)
        assert (await database_rows(app))[0] == 1  # nothing was physically deleted
        expired = await client.get("/api/v1/candidate/profile", headers=cookie)
        assert expired.status_code == 401 and "OldName" not in expired.text
        refused, _ = await login(client, key)
        assert refused.status_code == 401 and refused.json()["error"]["code"] == "INVALID_RECOVERY_KEY"

        # Collision: a new intake session is (artificially) bound to the same key. It must fail closed and touch nothing.
        new_session = await store_of(app).create_candidate_exchange(update_for("NewName", "Rust"), EXCHANGE)
        digest = (await post(client, "/intake/preview", {"session_id": new_session.session_id, "language": "en-IN"})).json()["profile_digest"]
        await store_of(app).issue_recovery_key(new_session, RecoveryKey.parse(key).verifier())
        _, [old_row] = await database_rows(app)
        collided = await post(client, "/profile", save_body(new_session, key, digest))
        assert collided.status_code == 409 and collided.json()["error"]["code"] == "RECOVERY_KEY_UNAVAILABLE"
        candidates, [after] = await database_rows(app)
        assert candidates == 1 and after.name == "OldName" and after.technical_skills == ["COBOL"] and after.expires_at == old_row.expires_at
        assert new_session.session_id in store_of(app)._sessions

        # After cleanup deleted the expired identity the same key registers as a brand-new identity with none of the old data.
        assert (await AuthRepository(app.state.database.sessions).purge_expired()).candidates == 1
        created = await post(client, "/profile", save_body(new_session, key, digest))
        assert created.json() == {"created": True}
        _, fresh_cookie = await login(client, key)
        profile = (await client.get("/api/v1/candidate/profile", headers=fresh_cookie)).json()["profile"]
        assert profile["name"] == "NewName" and profile["technical_skills"] == ["Rust"]
    run(app, scenario)


def test_logging_in_does_not_renew_retention_and_a_save_retry_does_not_change_it(app):
    async def scenario(client):
        session, key, digest = await start(client, app)
        await post(client, "/profile", save_body(session, key, digest))
        before = (await database_rows(app))[1][0]
        await login(client, key)
        await login(client, key)
        await post(client, "/profile", save_body(session, key, digest))
        after = (await database_rows(app))[1][0]
        assert (after.expires_at, after.consented_at, after.updated_at, after.revision) == (before.expires_at, before.consented_at, before.updated_at, before.revision)
        async with app.state.database.sessions() as db:
            assert await db.scalar(select(func.count()).select_from(CandidateAuthSession)) == 2  # sessions only; the profile is untouched
    run(app, scenario)
