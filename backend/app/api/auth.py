"""Candidate authentication routes: login, logout, session status. No profile data and no candidate IDs."""

from math import ceil

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.db.auth_repository import AuthRepository, CandidatePrincipal, InvalidCredentialsError, SessionInvalidError
from app.db.candidate_repository import DatabaseConstraintError
from app.db.database import DatabaseUnavailableError
from app.services.candidate_auth import (
    AuthSettings, InvalidSessionTokenError, MalformedRecoveryKeyError, RecoveryKey, SessionToken, origin_allowed,
)
from app.services.rate_limit import SlidingWindowLimiter, client_key

NO_STORE = {"Cache-Control": "no-store"}


class CandidateAuthError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, *, clear_cookie: bool = False, retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.clear_cookie = clear_cookie
        self.retry_after = retry_after


def _storage_unavailable(*, clear_cookie: bool = False) -> CandidateAuthError:
    return CandidateAuthError(503, "CANDIDATE_STORAGE_UNAVAILABLE", "Candidate accounts are temporarily unavailable.",
                              clear_cookie=clear_cookie)


def _unauthenticated(*, clear_cookie: bool) -> CandidateAuthError:
    return CandidateAuthError(401, "UNAUTHENTICATED", "Sign in to continue.", clear_cookie=clear_cookie)


def _repository(request: Request) -> AuthRepository:
    repository = request.app.state.auth_repository
    if repository is None:
        raise CandidateAuthError(503, "CANDIDATE_STORAGE_DISABLED", "Candidate accounts are not available.")
    return repository


async def get_auth_repository(request: Request) -> AuthRepository:
    return _repository(request)


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def enforce_trusted_origin(request: Request) -> None:
    """Router-level guard: install it once on every router that holds state-changing candidate routes.

    Any method outside GET/HEAD/OPTIONS (so an unexpected method fails closed too) must carry an
    `Origin` header exactly equal to the configured one, login included (blocks login CSRF). Routes
    therefore cannot forget the check, and routes added later inherit it. Storage availability is
    checked first so a disabled deployment is detectable; with the database enabled but no trusted
    origin configured, every state-changing request fails closed.
    """
    _repository(request)
    if request.method not in _SAFE_METHODS and not origin_allowed(
        request.headers.get("origin"), request.app.state.auth_settings.trusted_origin,
    ):
        raise CandidateAuthError(403, "ORIGIN_NOT_ALLOWED", "The request origin is not allowed.")


async def require_candidate(request: Request) -> CandidatePrincipal:
    """The only source of candidate identity for routes. Never accept a candidate ID from the client."""
    repository = _repository(request)
    settings: AuthSettings = request.app.state.auth_settings
    raw = request.cookies.get(settings.cookie_name)
    if raw is None:
        raise _unauthenticated(clear_cookie=False)
    try:
        return await repository.resolve(SessionToken(raw))
    except (InvalidSessionTokenError, SessionInvalidError):
        raise _unauthenticated(clear_cookie=True) from None
    except (DatabaseUnavailableError, DatabaseConstraintError):
        raise _storage_unavailable() from None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    recovery_key: SecretStr = Field(max_length=64)


def create_auth_router(settings: AuthSettings, client_limiter: SlidingWindowLimiter) -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth", dependencies=[Depends(enforce_trusted_origin)])

    def limit_attempts(request: Request) -> None:
        # One bucket per derived client address. There is deliberately no global bucket: a shared
        # limit would let any single caller lock every candidate out of login.
        peer = request.client.host if request.client else None
        wait = client_limiter.hit(client_key(peer, request.headers, settings.trusted_proxy_hops))
        if wait is not None:
            raise CandidateAuthError(429, "RATE_LIMITED", "Too many attempts. Try again shortly.",
                                     retry_after=max(1, ceil(wait)))

    @router.post("/login")
    async def login(
        body: LoginRequest, request: Request, response: Response,
        repository: AuthRepository = Depends(get_auth_repository),
    ) -> dict[str, bool]:
        limit_attempts(request)
        try:
            key = RecoveryKey.parse(body.recovery_key.get_secret_value())
        except MalformedRecoveryKeyError:
            raise CandidateAuthError(422, "MALFORMED_RECOVERY_KEY", "The recovery key format is not valid.") from None
        token = SessionToken.generate()
        try:
            await repository.create_session(key.verifier(), token)
        except InvalidCredentialsError:
            raise CandidateAuthError(401, "INVALID_RECOVERY_KEY", "The recovery key was not accepted.") from None
        except (DatabaseUnavailableError, DatabaseConstraintError):
            raise _storage_unavailable() from None
        response.set_cookie(**settings.set_cookie_args(token))
        response.headers.update(NO_STORE)
        return {"authenticated": True}

    @router.post("/logout")
    async def logout(
        request: Request, response: Response, repository: AuthRepository = Depends(get_auth_repository),
    ) -> dict[str, bool]:
        raw = request.cookies.get(settings.cookie_name)
        if raw is not None:
            try:
                await repository.delete_session(SessionToken(raw))
            except InvalidSessionTokenError:
                pass
            except (DatabaseUnavailableError, DatabaseConstraintError):
                # The browser is still signed out locally; the stored row expires on its own.
                raise _storage_unavailable(clear_cookie=True) from None
        response.delete_cookie(**settings.clear_cookie_args())
        response.headers.update(NO_STORE)
        return {"authenticated": False}

    @router.get("/session")
    async def session_status(
        response: Response, _principal: CandidatePrincipal = Depends(require_candidate),
    ) -> dict[str, bool]:
        response.headers.update(NO_STORE)
        return {"authenticated": True}

    return router
