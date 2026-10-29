import json
import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.auth import enforce_trusted_origin
from app.db.auth_repository import CandidatePrincipal, InvalidCredentialsError, SessionInvalidError
from app.db.candidate_repository import DatabaseConstraintError
from app.db.database import DatabaseUnavailableError
from app.main import create_app
from app.services.candidate_auth import RecoveryKey, SessionToken
from app.services.rate_limit import SlidingWindowLimiter

ORIGIN = "https://app.example.com"
COOKIE = "__Host-bolhire_session"


class FakeAuthRepository:
    def __init__(self):
        self.created, self.deleted, self.resolved = [], [], []
        self.login_error = self.resolve_error = self.delete_error = None
        self.principal = CandidatePrincipal(uuid4(), uuid4())

    async def create_session(self, verifier, token, **_):
        self.created.append((verifier, token.verifier()))
        if self.login_error:
            raise self.login_error

    async def resolve(self, token):
        self.resolved.append(token.verifier())
        if self.resolve_error:
            raise self.resolve_error
        return self.principal

    async def delete_session(self, token):
        self.deleted.append(token.verifier())
        if self.delete_error:
            raise self.delete_error
        return True


@pytest.fixture
def make(monkeypatch):
    def build(*, enabled=True, origin=ORIGIN, hops=None, **kwargs):
        monkeypatch.setenv("DATABASE_ENABLED", "1" if enabled else "0")
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@localhost/db")
        monkeypatch.setenv("LLM_PROVIDER", "mock")
        monkeypatch.setenv("SPEECH_PROVIDER", "mock")
        if origin:
            monkeypatch.setenv("AUTH_TRUSTED_ORIGIN", origin)
        else:
            monkeypatch.delenv("AUTH_TRUSTED_ORIGIN", raising=False)
        if hops is not None:
            monkeypatch.setenv("AUTH_TRUSTED_PROXY_HOPS", str(hops))
        app = create_app(**kwargs)
        repo = FakeAuthRepository()
        if enabled:  # the lifespan (and therefore a real connection) is never started here
            app.state.auth_repository = repo
        return TestClient(app), repo, app
    return build


def login(client, key=None, origin=ORIGIN, headers=None):
    headers = ({} if origin is None else {"Origin": origin}) | (headers or {})
    return client.post("/api/v1/auth/login", json={"recovery_key": key or RecoveryKey.generate().display()},
                       headers=headers)


def cookie_header(value):
    return {"Cookie": f"{COOKIE}={value}"}


def test_login_sets_hardened_cookie_and_returns_no_data(make):
    client, repo, _ = make()
    key = RecoveryKey.generate()
    response = login(client, key.display().lower())
    assert response.status_code == 200 and response.json() == {"authenticated": True}
    assert response.headers["cache-control"] == "no-store"
    set_cookie = response.headers["set-cookie"]
    value = set_cookie.split(";")[0].split("=", 1)[1]
    flags = {part.strip().lower() for part in set_cookie.split(";")[1:]}
    assert set_cookie.startswith(COOKIE + "=")
    assert {"httponly", "secure", "samesite=strict", "path=/", "max-age=604800"} <= flags
    assert not any(flag.startswith("domain") for flag in flags)
    assert value not in response.text and key.display() not in response.text
    verifier, token_verifier = repo.created[0]
    assert verifier == key.verifier() and token_verifier == SessionToken(value).verifier()


def test_login_failures_are_generic_and_never_set_a_cookie(make):
    client, repo, _ = make()
    repo.login_error = InvalidCredentialsError()
    unknown = login(client)
    assert unknown.status_code == 401 and unknown.json()["error"]["code"] == "INVALID_RECOVERY_KEY"
    assert "set-cookie" not in unknown.headers and unknown.headers["cache-control"] == "no-store"
    expired = login(client)  # an expired identity raises the same error and produces an identical response
    assert expired.json() == unknown.json() and expired.status_code == unknown.status_code
    for error in (DatabaseUnavailableError(), DatabaseConstraintError()):
        repo.login_error = error
        down = login(client)
        assert down.status_code == 503 and down.json()["error"]["code"] == "CANDIDATE_STORAGE_UNAVAILABLE"
        assert "set-cookie" not in down.headers


@pytest.mark.parametrize("body", [
    {"recovery_key": "not-a-key-SECRETMARKER"}, {"recovery_key": "A" * 200 + "SECRETMARKER"},
    {"recovery_key": 12345}, {"recovery_key": "x", "extra": "SECRETMARKER"}, {"other": "SECRETMARKER"}, "SECRETMARKER",
])
def test_invalid_login_bodies_never_echo_input(make, body):
    client, repo, _ = make()
    response = client.post("/api/v1/auth/login", json=body, headers={"Origin": ORIGIN})
    assert response.status_code == 422 and "SECRETMARKER" not in response.text and "set-cookie" not in response.headers
    assert not repo.created


def test_malformed_json_is_rejected_without_echo(make):
    client, repo, _ = make()
    response = client.post("/api/v1/auth/login", content=b'{"recovery_key": "SECRETMARKER', headers={
        "Origin": ORIGIN, "Content-Type": "application/json"})
    assert response.status_code == 400 and "SECRETMARKER" not in response.text and not repo.created


def test_malformed_key_gets_distinct_non_echoing_error(make):
    client, repo, _ = make()
    response = login(client, "ZZZZ-SECRETMARKER-0000")
    assert response.status_code == 422 and response.json()["error"]["code"] == "MALFORMED_RECOVERY_KEY"
    assert "SECRETMARKER" not in response.text and not repo.created


@pytest.mark.parametrize("origin", [None, "null", "https://evil.example.com", ORIGIN + "/", "http://app.example.com",
                                    "https://app.example.com:444", "HTTPS://APP.EXAMPLE.COM"])
def test_origin_is_enforced_on_login_and_logout(make, origin):
    client, repo, _ = make()
    cookie = cookie_header(SessionToken.generate().reveal())
    assert login(client, origin=origin).status_code == 403
    headers = dict(cookie) | ({} if origin is None else {"Origin": origin})
    response = client.post("/api/v1/auth/logout", headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    assert "set-cookie" not in response.headers and not repo.created and not repo.deleted


def test_unset_trusted_origin_fails_closed(make):
    client, repo, _ = make(origin=None)
    assert login(client).status_code == 403 and login(client, origin="https://app.example.com").status_code == 403
    assert not repo.created


def test_disabled_database_returns_detectable_503_and_never_touches_anything(make):
    client, _, _ = make(enabled=False)
    for response in (login(client), client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}),
                     client.get("/api/v1/auth/session", headers=cookie_header(SessionToken.generate().reveal()))):
        assert response.status_code == 503 and response.json()["error"]["code"] == "CANDIDATE_STORAGE_DISABLED"
        assert "set-cookie" not in response.headers


def test_session_status(make):
    client, repo, _ = make()
    token = SessionToken.generate()
    none = client.get("/api/v1/auth/session")
    assert none.status_code == 401 and "set-cookie" not in none.headers
    ok = client.get("/api/v1/auth/session", headers=cookie_header(token.reveal()))
    assert ok.status_code == 200 and ok.json() == {"authenticated": True} and ok.headers["cache-control"] == "no-store"
    assert str(repo.principal.candidate_id) not in ok.text and repo.resolved == [token.verifier()]
    garbage = client.get("/api/v1/auth/session", headers=cookie_header("not a token"))
    assert garbage.status_code == 401 and "max-age=0" in garbage.headers["set-cookie"].lower()
    repo.resolve_error = SessionInvalidError()
    stale = client.get("/api/v1/auth/session", headers=cookie_header(token.reveal()))
    assert stale.status_code == 401 and stale.json()["error"]["code"] == "UNAUTHENTICATED"
    assert stale.headers["set-cookie"].startswith(COOKIE + '=""') or "max-age=0" in stale.headers["set-cookie"].lower()


def test_database_failure_during_authentication_fails_closed(make):
    client, repo, _ = make()
    repo.resolve_error = DatabaseUnavailableError()
    response = client.get("/api/v1/auth/session", headers=cookie_header(SessionToken.generate().reveal()))
    assert response.status_code == 503 and response.json()["error"]["code"] == "CANDIDATE_STORAGE_UNAVAILABLE"


def test_logout_revokes_clears_and_is_idempotent(make):
    client, repo, _ = make()
    token = SessionToken.generate()
    response = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN} | cookie_header(token.reveal()))
    assert response.status_code == 200 and response.json() == {"authenticated": False}
    assert repo.deleted == [token.verifier()] and "max-age=0" in response.headers["set-cookie"].lower()
    assert response.headers["cache-control"] == "no-store"
    for headers in ({"Origin": ORIGIN}, {"Origin": ORIGIN} | cookie_header("garbage")):
        again = client.post("/api/v1/auth/logout", headers=headers)
        assert again.status_code == 200 and "max-age=0" in again.headers["set-cookie"].lower()
    assert len(repo.deleted) == 1


def test_logout_database_failure_still_clears_the_cookie(make):
    client, repo, _ = make()
    repo.delete_error = DatabaseUnavailableError()
    response = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN} | cookie_header(SessionToken.generate().reveal()))
    assert response.status_code == 503 and "max-age=0" in response.headers["set-cookie"].lower()


def test_login_is_rate_limited_per_client_and_ignores_spoofed_forwarding(make):
    client, repo, _ = make(login_limiter=SlidingWindowLimiter(max_attempts=2, window_seconds=60))
    for index in range(2):
        assert login(client, headers={"X-Forwarded-For": f"9.9.9.{index}"}).status_code == 200
    blocked = login(client, headers={"X-Forwarded-For": "1.2.3.4"})
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert int(blocked.headers["retry-after"]) >= 1 and "set-cookie" not in blocked.headers
    assert len(repo.created) == 2


def test_malformed_and_failed_attempts_count_toward_the_limit(make):
    client, repo, _ = make(login_limiter=SlidingWindowLimiter(max_attempts=2, window_seconds=60))
    repo.login_error = InvalidCredentialsError()
    assert [login(client, "bad").status_code, login(client).status_code, login(client).status_code] == [422, 401, 429]


def test_trusted_proxy_hops_separate_clients(make):
    client, _, _ = make(hops=1, login_limiter=SlidingWindowLimiter(max_attempts=1, window_seconds=60))
    assert login(client, headers={"X-Forwarded-For": "6.6.6.6, 1.1.1.1"}).status_code == 200
    assert login(client, headers={"X-Forwarded-For": "6.6.6.6, 2.2.2.2"}).status_code == 200
    assert login(client, headers={"X-Forwarded-For": "7.7.7.7, 1.1.1.1"}).status_code == 429


def test_there_is_no_shared_login_bucket(make):
    """Each derived client address has its own bucket; exhausting one never blocks another (no global limiter)."""
    client, repo, _ = make(hops=1, login_limiter=SlidingWindowLimiter(max_attempts=1, window_seconds=60))
    assert login(client, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert login(client, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    for index in range(2, 60):  # many other clients are unaffected by the exhausted one
        assert login(client, headers={"X-Forwarded-For": f"2.2.2.{index}"}).status_code == 200
    assert len(repo.created) == 59


def test_shared_bucket_without_trusted_hops_is_the_documented_proxy_hazard(make):
    """With hops=0 every request shares the socket peer's bucket. This pins the behaviour the deployment gate in
    docs/database-development.md warns about: behind a proxy, one caller can exhaust the bucket for all users."""
    client, _, _ = make(hops=None, login_limiter=SlidingWindowLimiter(max_attempts=2, window_seconds=60))
    assert [login(client, headers={"X-Forwarded-For": f"9.9.9.{i}"}).status_code for i in range(3)] == [200, 200, 429]


def test_origin_guard_covers_every_state_changing_operation_of_the_auth_router(make):
    client, repo, app = make()
    operations = [(method, path) for path, item in app.openapi()["paths"].items() if path.startswith("/api/v1/auth")
                  for method in item if method in {"post", "put", "patch", "delete"}]
    assert {path for _, path in operations} >= {"/api/v1/auth/login", "/api/v1/auth/logout"}
    for method, path in operations:
        refused = client.request(method.upper(), path, json={"recovery_key": RecoveryKey.generate().display()})
        assert refused.status_code == 403, (method, path)
        forged = client.request(method.upper(), path, json={}, headers={"Origin": "https://evil.example.com"})
        assert forged.status_code == 403, (method, path)
    assert not repo.created and not repo.deleted


def test_a_write_route_added_to_a_guarded_router_inherits_the_origin_check(make):
    from fastapi import APIRouter, Depends

    client, repo, app = make()
    extra = APIRouter(prefix="/api/v1/auth-future", dependencies=[Depends(enforce_trusted_origin)])
    for method in ("post", "put", "patch", "delete"):
        getattr(extra, method)("/thing")(lambda: {"ok": True})
    extra.get("/thing")(lambda: {"ok": True})
    app.include_router(extra)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert client.request(method, "/api/v1/auth-future/thing").status_code == 403
        assert client.request(method, "/api/v1/auth-future/thing", headers={"Origin": ORIGIN}).status_code == 200
    assert client.get("/api/v1/auth-future/thing").status_code == 200  # safe methods carry no Origin requirement
    disabled, _, disabled_app = make(enabled=False)
    disabled_app.include_router(extra)
    assert disabled.post("/api/v1/auth-future/thing", headers={"Origin": ORIGIN}).json()["error"]["code"] == "CANDIDATE_STORAGE_DISABLED"


def test_no_route_accepts_or_exposes_a_candidate_identifier(make):
    _, _, app = make()
    schema = app.openapi()
    assert "candidate_id" not in json.dumps(schema)
    assert not [path for path in schema["paths"] if "candidate" in path and "/auth/" in path]
    assert {path for path in schema["paths"] if path.startswith("/api/v1/auth")} == {
        "/api/v1/auth/login", "/api/v1/auth/logout", "/api/v1/auth/session"}


def test_secrets_never_reach_logs(make, caplog):
    caplog.set_level(logging.DEBUG)
    client, repo, _ = make()
    key, token = RecoveryKey.generate(), SessionToken.generate()
    login(client, key.display())
    repo.login_error = InvalidCredentialsError()
    login(client, key.display())
    repo.login_error = DatabaseUnavailableError()
    login(client, key.display())
    login(client, key.display() + "SECRETMARKER")
    client.get("/api/v1/auth/session", headers=cookie_header(token.reveal()))
    repo.resolve_error = DatabaseUnavailableError()
    client.get("/api/v1/auth/session", headers=cookie_header(token.reveal()))
    repo.delete_error = DatabaseUnavailableError()
    client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN} | cookie_header(token.reveal()))
    for secret in (key.display(), key.display().replace("-", ""), token.reveal(), "SECRETMARKER"):
        assert secret not in caplog.text


@pytest.mark.parametrize("cookie", [None, "garbage", SessionToken.generate().reveal()])
def test_anonymous_chat_is_unaffected_with_or_without_an_auth_cookie(make, cookie):
    for enabled in (False, True):
        client, repo, _ = make(enabled=enabled)
        headers = {} if cookie is None else cookie_header(cookie)
        response = client.post("/api/v1/chat/text", json={"text": "hello", "language": "en-IN"}, headers=headers)
        assert response.status_code == 200 and response.json()["session_id"]
        assert not repo.resolved and "set-cookie" not in response.headers
        health = client.get("/api/v1/health", headers=headers)
        assert health.status_code == 200
