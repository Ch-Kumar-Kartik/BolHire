"""Candidate save flow and retrieval routes with a fake candidate repository and the REAL in-memory intake session store.
No database, no provider calls. Sessions are created straight in the store so no model is involved."""

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from app.db.auth_repository import CandidatePrincipal, SessionInvalidError
from app.db.candidate_repository import (
    DatabaseConstraintError, ProfileNotFoundError, RecoveryKeyUnavailableError, RegistrationResult, StoredProfileInvalidError,
)
from app.db.database import DatabaseUnavailableError
from app.db.mapping import SavedProfile
from app.main import create_app
from app.models import ConversationMessage
from app.services.candidate_auth import RecoveryKey, SessionToken
from app.services.candidate_consent import RETENTION, notice_for, profile_digest
from app.services.candidate_profile import CandidateProfile, CandidateProfileUpdate
from app.services.rate_limit import SlidingWindowLimiter
from test_candidate_auth_api import FakeAuthRepository

ORIGIN = "https://app.example.com"
COOKIE = "__Host-bolhire_session"
VERSION_EN, VERSION_HI = "candidate-profile-v1:en-IN", "candidate-profile-v1:hi-IN"
EXCHANGE = [ConversationMessage(role="user", content="hello"), ConversationMessage(role="assistant", content="hi")]
UPDATE = CandidateProfileUpdate(name="Asha", target_role="Backend developer", skills_add=["Python", "SQL"], decline_fields=["work_mode"])


class FakeCandidateRepository:
    def __init__(self):
        self.registered, self.identities = [], set()
        self.register_error = self.exists_error = self.get_error = None
        self.register_delay = 0.0
        self.get_calls = []
        self.saved = None

    async def active_identity_exists(self, verifier):
        if self.exists_error:
            raise self.exists_error
        return verifier in self.identities

    async def register(self, profile, *, recovery_secret_hash, consent_version, retention):
        if self.register_delay:
            await asyncio.sleep(self.register_delay)
        if self.register_error:
            raise self.register_error
        self.registered.append(dict(profile=profile, verifier=recovery_secret_hash, consent_version=consent_version, retention=retention))
        if recovery_secret_hash in self.identities:
            return RegistrationResult(created=False)
        self.identities.add(recovery_secret_hash)
        return RegistrationResult(created=True)

    async def get(self, candidate_id):
        self.get_calls.append(candidate_id)
        if self.get_error:
            raise self.get_error
        return self.saved


@pytest.fixture
def make(monkeypatch):
    def build(*, enabled=True, **kwargs):
        monkeypatch.setenv("DATABASE_ENABLED", "1" if enabled else "0")
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@localhost/db")
        monkeypatch.setenv("LLM_PROVIDER", "mock")
        monkeypatch.setenv("SPEECH_PROVIDER", "mock")
        monkeypatch.setenv("AUTH_TRUSTED_ORIGIN", ORIGIN)
        app = create_app(**kwargs)
        auth, candidates = FakeAuthRepository(), FakeCandidateRepository()
        if enabled:  # the lifespan (and so a real connection) is never started here
            app.state.auth_repository, app.state.candidate_repository = auth, candidates
        return app, auth, candidates
    return build


@contextlib.asynccontextmanager
async def client_for(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


def store_of(app):
    return app.state.candidate_intake_pipeline.sessions


async def new_session(app, update=UPDATE):
    return await store_of(app).create_candidate_exchange(update, EXCHANGE)


def post(client, path, body, origin=ORIGIN):
    return client.post(f"/api/v1/candidate{path}", json=body, headers={} if origin is None else {"Origin": origin})


async def preview(client, session, language="en-IN"):
    response = await post(client, "/intake/preview", {"session_id": session.session_id, "language": language})
    assert response.status_code == 200, response.text
    return response.json()


async def issue(client, session):
    response = await post(client, "/recovery-key", {"session_id": session.session_id})
    assert response.status_code == 200, response.text
    return response.json()["recovery_key"]


def save_body(session, key, digest, version=VERSION_EN, accepted=True, **extra):
    return {"session_id": session.session_id, "recovery_key": key, "profile_digest": digest, "consent_version": version,
            "consent_accepted": accepted, **extra}


async def ready(client, app, update=UPDATE):
    session = await new_session(app, update)
    digest = (await preview(client, session))["profile_digest"]
    return session, await issue(client, session), digest


def error_code(response):
    return response.json()["error"]["code"]


# ---- preview --------------------------------------------------------------------------------------------------------------------------------------------
def test_preview_returns_the_session_profile_its_digest_and_the_language_specific_consent(make):
    app, _, _ = make()

    async def scenario():
        async with client_for(app) as client:
            session = await new_session(app)
            body = await preview(client, session)
            profile = await store_of(app).candidate_profile_for(session)
            assert body["profile"] == {"name": "Asha", "target_role": "Backend developer", "experience_years": None, "experience_level": None,
                                       "technical_skills": ["Python", "SQL"], "preferred_location": None, "work_mode": None,
                                       "declined_fields": ["work_mode"]}
            assert body["profile_digest"] == profile_digest(profile) and body["can_save"] is True
            assert body["consent"] == {"version": VERSION_EN, "language": "en-IN", "retention_days": 90, "text": notice_for("en-IN").text}
            hindi = await preview(client, session, "hi-IN")
            assert hindi["consent"]["version"] == VERSION_HI and hindi["consent"]["text"] == notice_for("hi-IN").text
            assert hindi["profile_digest"] == body["profile_digest"]
            raw = await post(client, "/intake/preview", {"session_id": session.session_id, "language": "en-IN"})
            assert raw.headers["cache-control"] == "no-store"
    asyncio.run(scenario())


def test_preview_of_an_unsaveable_profile_says_so_and_bad_requests_are_safe(make):
    app, _, _ = make()

    async def scenario():
        async with client_for(app) as client:
            empty = await new_session(app, CandidateProfileUpdate(decline_fields=["name", "work_mode"]))
            assert (await preview(client, empty))["can_save"] is False
            general = await store_of(app).create(EXCHANGE)
            assert (await post(client, "/intake/preview", {"session_id": general.session_id, "language": "en-IN"})).status_code == 409
            assert (await post(client, "/intake/preview", {"session_id": "nope", "language": "en-IN"})).status_code == 404
            assert error_code(await post(client, "/intake/preview", {"session_id": empty.session_id, "language": "fr-FR"})) == "UNSUPPORTED_LANGUAGE"
            assert (await post(client, "/intake/preview", {"session_id": empty.session_id, "language": "en-IN", "candidate_id": "x"})).status_code == 422
    asyncio.run(scenario())


def test_preview_keeps_a_session_alive_and_an_expired_one_is_refused(make):
    app, _, _ = make()

    async def scenario():
        async with client_for(app) as client:
            clock = {"now": 0.0}
            store = store_of(app)
            store._clock = lambda: clock["now"]
            session = await new_session(app)
            clock["now"] = 1500
            await preview(client, session)  # refreshes the idle clock
            clock["now"] = 3000  # 1500 s after the refresh, 3000 s after creation
            await preview(client, session)
            clock["now"] = 5000
            response = await post(client, "/intake/preview", {"session_id": session.session_id, "language": "en-IN"})
            assert response.status_code == 410
    asyncio.run(scenario())


# ---- key issuance ---------------------------------------------------------------------------------------------------------------------------------
def test_issuing_a_key_binds_only_its_hash_to_the_session_and_reissue_invalidates_the_old_key(make):
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session = await new_session(app)
            digest = (await preview(client, session))["profile_digest"]
            first = await issue(client, session)
            assert RecoveryKey.parse(first).verifier() == await store_of(app).recovery_key_hash_for(session)
            assert first not in repr(session.__dict__) and first.replace("-", "") not in repr(session.__dict__)
            raw = await post(client, "/recovery-key", {"session_id": session.session_id})
            assert raw.headers["cache-control"] == "no-store"
            second = raw.json()["recovery_key"]
            assert second != first and RecoveryKey.parse(second).verifier() == await store_of(app).recovery_key_hash_for(session)
            stale = await post(client, "/profile", save_body(session, first, digest))
            assert stale.status_code == 409 and error_code(stale) == "RECOVERY_KEY_NOT_ISSUED" and not candidates.registered
            fresh = await post(client, "/profile", save_body(session, second, digest))
            assert fresh.status_code == 200 and fresh.json() == {"created": True}
    asyncio.run(scenario())


def test_no_key_is_issued_for_an_empty_profile_or_a_chat_session(make):
    app, _, _ = make()

    async def scenario():
        async with client_for(app) as client:
            empty = await new_session(app, CandidateProfileUpdate(decline_fields=["name"]))
            response = await post(client, "/recovery-key", {"session_id": empty.session_id})
            assert response.status_code == 422 and error_code(response) == "PROFILE_EMPTY"
            assert await store_of(app).recovery_key_hash_for(empty) is None
            general = await store_of(app).create(EXCHANGE)
            assert (await post(client, "/recovery-key", {"session_id": general.session_id})).status_code == 409
    asyncio.run(scenario())


# ---- save -----------------------------------------------------------------------------------------------------------------------------------------
def test_save_persists_the_server_side_profile_with_the_consent_and_retention_then_discards_the_session(make):
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            profile = await store_of(app).candidate_profile_for(session)
            response = await post(client, "/profile", save_body(session, key, digest, VERSION_HI))
            assert response.status_code == 200 and response.json() == {"created": True}
            assert response.headers["cache-control"] == "no-store" and key not in response.text
            [call] = candidates.registered
            assert call["profile"] == profile and call["consent_version"] == VERSION_HI and call["retention"] == RETENTION == timedelta(days=90)
            assert call["verifier"] == RecoveryKey.parse(key).verifier()
            assert session.session_id not in store_of(app)._sessions and session.messages == [] and session.recovery_key_hash is None
            gone = await post(client, "/intake/preview", {"session_id": session.session_id, "language": "en-IN"})
            assert gone.status_code == 404
    asyncio.run(scenario())


def test_a_retry_after_a_lost_response_is_an_idempotent_success_without_another_write(make):
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            body = save_body(session, key, digest)
            assert (await post(client, "/profile", body)).json() == {"created": True}  # the client never saw this response
            retry = await post(client, "/profile", body)  # the session no longer exists
            assert retry.status_code == 200 and retry.json() == {"created": False}
            assert len(candidates.registered) == 1
            assert (await post(client, "/profile", body)).json() == {"created": False}
    asyncio.run(scenario())


def test_a_retry_after_a_commit_whose_success_was_not_reported_discards_the_surviving_session(make):
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            candidates.identities.add(RecoveryKey.parse(key).verifier())  # committed, but the server never reached its discard
            other, other_key, _ = await ready(client, app)
            retry = await post(client, "/profile", save_body(session, key, digest))
            assert retry.json() == {"created": False} and session.session_id not in store_of(app)._sessions
            assert other.session_id in store_of(app)._sessions and not candidates.registered  # an unrelated session is untouched
    asyncio.run(scenario())


def test_concurrent_identical_saves_register_once_and_both_succeed(make):
    app, _, candidates = make()
    candidates.register_delay = 0.05

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            body = save_body(session, key, digest)
            first, second = await asyncio.gather(post(client, "/profile", body), post(client, "/profile", body))
            assert sorted([first.status_code, second.status_code]) == [200, 200]
            assert sorted([first.json()["created"], second.json()["created"]]) == [False, True]
            assert len(candidates.registered) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("make_body, status, code", [
    (lambda s, k, d: save_body(s, k, "0" * 64), 409, "PROFILE_CHANGED"),
    (lambda s, k, d: save_body(s, RecoveryKey.generate().display(), d), 409, "RECOVERY_KEY_NOT_ISSUED"),
    (lambda s, k, d: save_body(s, k, d, accepted=False), 422, "CONSENT_REQUIRED"),
    (lambda s, k, d: save_body(s, k, d, version="candidate-profile-v0:en-IN"), 409, "CONSENT_VERSION_OUTDATED"),
    (lambda s, k, d: save_body(s, k, d, version="candidate-profile-v1"), 409, "CONSENT_VERSION_OUTDATED"),
])
def test_a_save_that_fails_verification_writes_nothing_and_keeps_the_session_for_a_retry(make, make_body, status, code):
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            response = await post(client, "/profile", make_body(session, key, digest))
            assert response.status_code == status and error_code(response) == code and key not in response.text
            assert not candidates.registered and session.session_id in store_of(app)._sessions
            assert (await post(client, "/profile", save_body(session, key, digest))).json() == {"created": True}  # still saveable
    asyncio.run(scenario())


def test_a_profile_changed_by_a_later_intake_turn_after_review_is_refused(make):
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            async with session.lock:
                await store_of(app).commit_candidate_exchange(session, CandidateProfileUpdate(name="Different"), EXCHANGE)
            response = await post(client, "/profile", save_body(session, key, digest))
            assert response.status_code == 409 and error_code(response) == "PROFILE_CHANGED" and not candidates.registered
            fresh = (await preview(client, session))["profile_digest"]
            assert (await post(client, "/profile", save_body(session, key, fresh))).json() == {"created": True}
            assert candidates.registered[0]["profile"].name == "Different"
    asyncio.run(scenario())


def test_a_save_waits_for_an_intake_turn_in_progress_and_then_sees_its_result(make):
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            await session.lock.acquire()  # an intake turn is generating
            pending = asyncio.create_task(post(client, "/profile", save_body(session, key, digest)))
            await asyncio.sleep(0.05)
            assert not pending.done()
            await store_of(app).commit_candidate_exchange(session, CandidateProfileUpdate(name="Changed"), EXCHANGE)
            session.lock.release()
            response = await asyncio.wait_for(pending, 5)
            assert response.status_code == 409 and error_code(response) == "PROFILE_CHANGED" and not candidates.registered
    asyncio.run(scenario())


def test_a_profile_emptied_after_the_key_was_issued_cannot_be_saved(make):
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            async with session.lock:
                await store_of(app).commit_candidate_exchange(
                    session, CandidateProfileUpdate(clear_fields=["name", "target_role", "technical_skills"]), EXCHANGE)
            response = await post(client, "/profile", save_body(session, key, digest))
            assert response.status_code == 422 and error_code(response) == "PROFILE_EMPTY" and not candidates.registered
    asyncio.run(scenario())


@pytest.mark.parametrize("extra", [{"profile": {"name": "Forged"}}, {"candidate_id": "00000000-0000-0000-0000-000000000000"}, {"name": "Forged"}])
def test_a_client_cannot_submit_a_profile_or_a_candidate_id(make, extra):
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            response = await post(client, "/profile", save_body(session, key, digest, **extra))
            assert response.status_code == 422 and error_code(response) == "INVALID_REQUEST" and "Forged" not in response.text
            assert not candidates.registered
    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["not-a-key-SECRETMARKER", "A" * 200, "", "0" * 25 + "1"])
def test_a_malformed_key_is_rejected_without_echo(make, key):
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, _, digest = await ready(client, app)
            response = await post(client, "/profile", save_body(session, key, digest))
            assert response.status_code == 422 and "SECRETMARKER" not in response.text and not candidates.registered
    asyncio.run(scenario())


def test_malformed_digest_and_missing_fields_are_422(make):
    app, _, _ = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            for body in (save_body(session, key, "xyz"), save_body(session, key, digest.upper()),
                         {k: v for k, v in save_body(session, key, digest).items() if k != "consent_accepted"},
                         save_body(session, key, digest, accepted="yes")):
                assert (await post(client, "/profile", body)).status_code == 422
    asyncio.run(scenario())


def test_an_expired_collision_fails_closed_and_keeps_the_session(make):
    app, _, candidates = make()
    candidates.register_error = RecoveryKeyUnavailableError()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            response = await post(client, "/profile", save_body(session, key, digest))
            assert response.status_code == 409 and error_code(response) == "RECOVERY_KEY_UNAVAILABLE"
            assert session.session_id in store_of(app)._sessions
            fresh_key = await issue(client, session)  # the candidate asks for a new key and tries again
            candidates.register_error = None
            assert (await post(client, "/profile", save_body(session, fresh_key, digest))).json() == {"created": True}
    asyncio.run(scenario())


@pytest.mark.parametrize("failing", ["register", "exists"])
@pytest.mark.parametrize("error", [DatabaseUnavailableError(), DatabaseConstraintError()])
def test_a_database_failure_during_save_is_a_503_that_keeps_the_session_and_key_for_a_retry(make, failing, error):
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            setattr(candidates, f"{failing}_error", error)
            response = await post(client, "/profile", save_body(session, key, digest))
            assert response.status_code == 503 and error_code(response) == "CANDIDATE_STORAGE_UNAVAILABLE" and not candidates.registered
            assert session.session_id in store_of(app)._sessions
            setattr(candidates, f"{failing}_error", None)
            assert (await post(client, "/profile", save_body(session, key, digest))).json() == {"created": True}
    asyncio.run(scenario())


def test_failure_after_the_commit_is_recovered_by_login_semantics_on_retry(make):
    """Register commits, then the response is lost AND the server restarts (empty session store): the same key still succeeds."""
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            assert (await post(client, "/profile", save_body(session, key, digest))).json() == {"created": True}
            store_of(app)._sessions.clear()
            retry = await post(client, "/profile", save_body(session, key, digest))
            assert retry.status_code == 200 and retry.json() == {"created": False} and len(candidates.registered) == 1
    asyncio.run(scenario())


# ---- retrieval ------------------------------------------------------------------------------------------------------------------------------------
def saved(candidate_id, name="Asha"):
    now = datetime.now(timezone.utc)
    return SavedProfile(candidate_id=candidate_id, profile=CandidateProfile(name=name, technical_skills=("Python",), declined_fields=frozenset({"work_mode", "preferred_location"})),
                        revision=1, consent_version=VERSION_EN, consented_at=now, created_at=now, updated_at=now, expires_at=now + timedelta(days=90))


def cookie():
    return {"Cookie": f"{COOKIE}={SessionToken.generate().reveal()}"}


def test_retrieval_returns_the_principals_profile_and_never_a_candidate_id(make):
    app, auth, candidates = make()
    candidates.saved = saved(auth.principal.candidate_id)

    async def scenario():
        async with client_for(app) as client:
            response = await client.get("/api/v1/candidate/profile?candidate_id=" + str(uuid4()), headers=cookie())
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            body = response.json()
            assert body["profile"]["name"] == "Asha" and body["profile"]["declined_fields"] == ["preferred_location", "work_mode"]
            assert body["profile"]["technical_skills"] == ["Python"] and body["consent_version"] == VERSION_EN
            assert set(body) == {"profile", "expires_at", "consent_version", "consented_at"}
            assert str(auth.principal.candidate_id) not in response.text
            assert candidates.get_calls == [auth.principal.candidate_id]  # the query parameter is ignored: identity is the cookie's
            other = CandidatePrincipal(uuid4(), uuid4())
            auth.principal = other
            candidates.saved = saved(other.candidate_id, "Other")
            second = await client.get("/api/v1/candidate/profile", headers=cookie())
            assert second.json()["profile"]["name"] == "Other" and candidates.get_calls[-1] == other.candidate_id
    asyncio.run(scenario())


def test_retrieval_requires_a_valid_session_and_expired_profiles_are_inaccessible(make):
    app, auth, candidates = make()
    candidates.saved = saved(auth.principal.candidate_id)

    async def scenario():
        async with client_for(app) as client:
            none = await client.get("/api/v1/candidate/profile")
            assert none.status_code == 401 and not candidates.get_calls
            auth.resolve_error = SessionInvalidError()
            stale = await client.get("/api/v1/candidate/profile", headers=cookie())
            assert stale.status_code == 401 and "max-age=0" in stale.headers["set-cookie"].lower() and not candidates.get_calls
            auth.resolve_error = None
            candidates.get_error = ProfileNotFoundError()  # expired between the session check and the read
            expired = await client.get("/api/v1/candidate/profile", headers=cookie())
            assert expired.status_code == 401 and "max-age=0" in expired.headers["set-cookie"].lower()
            candidates.get_error = StoredProfileInvalidError()
            assert error_code(await client.get("/api/v1/candidate/profile", headers=cookie())) == "STORED_PROFILE_INVALID"
            candidates.get_error = DatabaseUnavailableError()
            assert (await client.get("/api/v1/candidate/profile", headers=cookie())).status_code == 503
    asyncio.run(scenario())


# ---- guards, limits, hygiene, regressions -------------------------------------------------------------------------------------------------
def test_every_state_changing_candidate_route_requires_the_trusted_origin(make):
    app, _, candidates = make()
    schema = app.openapi()
    writes = [(m, p) for p, item in schema["paths"].items() if p.startswith("/api/v1/candidate") for m in item if m in {"post", "put", "patch", "delete"}]
    assert {p for _, p in writes} == {
        "/api/v1/candidate/intake/preview", "/api/v1/candidate/recovery-key", "/api/v1/candidate/profile",
        "/api/v1/candidate/jobs/fetch-live",  # Task 12
    }

    async def scenario():
        async with client_for(app) as client:
            for method, path in writes:
                for headers in ({}, {"Origin": "https://evil.example.com"}, {"Origin": "null"}):
                    response = await client.request(method.upper(), path, json={}, headers=headers)
                    assert response.status_code == 403 and error_code(response) == "ORIGIN_NOT_ALLOWED", (method, path, headers)
            assert not candidates.registered
    asyncio.run(scenario())


def test_no_candidate_route_accepts_or_exposes_a_candidate_identifier(make):
    app, _, _ = make()
    schema = app.openapi()
    candidate_paths = {p: v for p, v in schema["paths"].items() if p.startswith("/api/v1/candidate")}
    assert "candidate_id" not in json.dumps(candidate_paths) and "candidate_id" not in json.dumps(schema.get("components", {}).get("schemas", {}))


def test_with_the_database_disabled_candidate_routes_are_a_detectable_503_and_chat_still_works(make):
    app, _, _ = make(enabled=False)

    async def scenario():
        async with client_for(app) as client:
            for path in ("/intake/preview", "/recovery-key", "/profile"):
                response = await post(client, path, {})
                assert response.status_code == 503 and error_code(response) == "CANDIDATE_STORAGE_DISABLED"
            assert (await client.get("/api/v1/candidate/profile", headers=cookie())).status_code == 503
            chat = await client.post("/api/v1/chat/text", json={"text": "hello", "language": "en-IN"})
            intake = await client.post("/api/v1/chat/text", json={"text": "hello", "language": "en-IN", "workflow": "candidate_intake"})
            assert chat.status_code == 200 and intake.status_code == 200
    asyncio.run(scenario())


def test_the_new_routes_are_rate_limited_per_client(make):
    app, _, _ = make(candidate_session_limiter=SlidingWindowLimiter(max_attempts=2, window_seconds=60),
                     candidate_save_limiter=SlidingWindowLimiter(max_attempts=1, window_seconds=60))

    async def scenario():
        async with client_for(app) as client:
            session = await new_session(app)
            await preview(client, session)
            await issue(client, session)
            blocked = await post(client, "/intake/preview", {"session_id": session.session_id, "language": "en-IN"})
            assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1 and error_code(blocked) == "RATE_LIMITED"
            digest = "0" * 64
            assert (await post(client, "/profile", save_body(session, RecoveryKey.generate().display(), digest))).status_code == 409
            limited = await post(client, "/profile", save_body(session, RecoveryKey.generate().display(), digest))
            assert limited.status_code == 429
    asyncio.run(scenario())


def test_the_recovery_key_and_its_hash_never_reach_logs_or_error_bodies(make, caplog):
    caplog.set_level(logging.DEBUG)
    app, _, candidates = make()

    async def scenario():
        async with client_for(app) as client:
            session, key, digest = await ready(client, app)
            verifier = RecoveryKey.parse(key).verifier()
            bodies = []
            for body in (save_body(session, key, "0" * 64), save_body(session, key, digest, accepted=False),
                         save_body(session, key, digest, version="x"), save_body(session, "garbage" + key, digest)):
                bodies.append((await post(client, "/profile", body)).text)
            candidates.register_error = DatabaseUnavailableError()
            bodies.append((await post(client, "/profile", save_body(session, key, digest))).text)
            candidates.register_error = None
            bodies.append((await post(client, "/profile", save_body(session, key, digest))).text)
            bodies.append((await post(client, "/profile", save_body(session, key, digest))).text)
            for secret in (key, key.replace("-", ""), verifier.hex()):
                assert secret not in caplog.text and not any(secret in text for text in bodies)
    asyncio.run(scenario())
