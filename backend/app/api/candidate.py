"""Candidate profile save flow and authenticated retrieval (Task 9.3).

Flow: preview (profile, digest, consent notice) -> issue a recovery key bound to the intake session -> save. The profile always comes
from the server-side intake session, never from the request. Identity for retrieval comes only from the authentication cookie.
Nothing here accepts a candidate ID. The recovery key travels only in the issue response and the save request body.
"""

import hmac
from datetime import datetime
from math import ceil
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool

from app.api.auth import CandidateAuthError, enforce_trusted_origin, require_candidate
from app.db.auth_repository import CandidatePrincipal
from app.db.candidate_repository import (
    CandidateRepository, DatabaseConstraintError, ProfileNotFoundError, RecoveryKeyUnavailableError, StoredProfileInvalidError,
)
from app.db.database import DatabaseUnavailableError
from app.db.mapping import profile_values
from app.services.candidate_auth import AuthSettings, MalformedRecoveryKeyError, RecoveryKey
from app.services.candidate_consent import RETENTION, has_saveable_content, notice_by_version, notice_for, profile_digest
from app.services.candidate_profile import CandidateProfile
from app.services.rate_limit import SlidingWindowLimiter, client_key
from app.services.sessions import InMemorySessionStore, SessionNotFoundError

NO_STORE = {"Cache-Control": "no-store"}
Language = Literal["en-IN", "hi-IN"]


class ProfileView(BaseModel):
    name: str | None
    target_role: str | None
    experience_years: float | None
    experience_level: str | None
    technical_skills: list[str] | None
    preferred_location: str | None
    work_mode: str | None
    declined_fields: list[str]


class ConsentView(BaseModel):
    version: str
    language: Language
    retention_days: int
    text: str


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str = Field(min_length=1, max_length=128)
    language: Language


class PreviewResponse(BaseModel):
    profile: ProfileView
    profile_digest: str
    can_save: bool
    consent: ConsentView


class IssueKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str = Field(min_length=1, max_length=128)


class IssueKeyResponse(BaseModel):
    recovery_key: str


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str = Field(min_length=1, max_length=128)
    recovery_key: SecretStr = Field(max_length=64)
    profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    consent_version: str = Field(min_length=1, max_length=80)
    consent_accepted: StrictBool


class SaveResponse(BaseModel):
    created: bool


class SavedProfileResponse(BaseModel):
    profile: ProfileView
    expires_at: datetime
    consent_version: str
    consented_at: datetime


def _view(profile: CandidateProfile) -> ProfileView:
    return ProfileView(**profile_values(profile))  # canonical form: sorted declines, ordered skills


def _repository(request: Request) -> CandidateRepository:
    repository = request.app.state.candidate_repository
    if repository is None:
        raise CandidateAuthError(503, "CANDIDATE_STORAGE_DISABLED", "Candidate accounts are not available.")
    return repository


def _storage_unavailable() -> CandidateAuthError:
    return CandidateAuthError(503, "CANDIDATE_STORAGE_UNAVAILABLE", "Candidate accounts are temporarily unavailable.")


def create_candidate_router(
    settings: AuthSettings, sessions: InMemorySessionStore, session_limiter: SlidingWindowLimiter, save_limiter: SlidingWindowLimiter,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/candidate", dependencies=[Depends(enforce_trusted_origin)])

    def limit(request: Request, limiter: SlidingWindowLimiter) -> None:
        peer = request.client.host if request.client else None
        wait = limiter.hit(client_key(peer, request.headers, settings.trusted_proxy_hops))
        if wait is not None:
            raise CandidateAuthError(429, "RATE_LIMITED", "Too many attempts. Try again shortly.", retry_after=max(1, ceil(wait)))

    @router.post("/intake/preview", response_model=PreviewResponse)
    async def preview(body: PreviewRequest, request: Request, response: Response) -> PreviewResponse:
        limit(request, session_limiter)
        session = await sessions.resolve(body.session_id, workflow="candidate_intake")
        await sessions.touch(session, workflow="candidate_intake")
        profile = await sessions.candidate_profile_for(session)
        notice = notice_for(body.language)
        response.headers.update(NO_STORE)
        return PreviewResponse(
            profile=_view(profile), profile_digest=profile_digest(profile), can_save=has_saveable_content(profile),
            consent=ConsentView(version=notice.version, language=notice.language, retention_days=notice.retention_days, text=notice.text),
        )

    @router.post("/recovery-key", response_model=IssueKeyResponse)
    async def issue_recovery_key(body: IssueKeyRequest, request: Request, response: Response) -> IssueKeyResponse:
        limit(request, session_limiter)
        session = await sessions.resolve(body.session_id, workflow="candidate_intake")
        async with session.lock:  # serialized with a save in progress, so an issue cannot interleave with one
            if not has_saveable_content(await sessions.candidate_profile_for(session)):
                raise CandidateAuthError(422, "PROFILE_EMPTY", "There is no profile information to save yet.")
            key = RecoveryKey.generate()
            await sessions.issue_recovery_key(session, key.verifier())  # replaces (invalidates) any earlier key for this session
        response.headers.update(NO_STORE)
        return IssueKeyResponse(recovery_key=key.display())

    @router.post("/profile", response_model=SaveResponse)
    async def save_profile(body: SaveRequest, request: Request, response: Response) -> SaveResponse:
        limit(request, save_limiter)
        try:
            key = RecoveryKey.parse(body.recovery_key.get_secret_value())
        except MalformedRecoveryKeyError:
            raise CandidateAuthError(422, "MALFORMED_RECOVERY_KEY", "The recovery key format is not valid.") from None
        if body.consent_accepted is not True:
            raise CandidateAuthError(422, "CONSENT_REQUIRED", "Consent must be given before the profile is saved.")
        if notice_by_version(body.consent_version) is None:
            raise CandidateAuthError(409, "CONSENT_VERSION_OUTDATED", "The consent notice has changed. Review it again.")
        verifier = key.verifier()
        candidates = _repository(request)
        response.headers.update(NO_STORE)
        try:
            # A retry after a lost response must succeed even though the intake session was discarded by the first save.
            if await candidates.active_identity_exists(verifier):
                await _discard_if_bound(sessions, body.session_id, verifier)
                return SaveResponse(created=False)
        except (DatabaseUnavailableError, DatabaseConstraintError):
            raise _storage_unavailable() from None
        session = await sessions.resolve(body.session_id, workflow="candidate_intake")
        async with session.lock:  # no intake turn can change the profile between verification and the write
            try:
                stored = await sessions.recovery_key_hash_for(session)
            except SessionNotFoundError:
                # A concurrent identical save won while this one waited for the lock and discarded the session: that is success.
                try:
                    if await candidates.active_identity_exists(verifier):
                        return SaveResponse(created=False)
                except (DatabaseUnavailableError, DatabaseConstraintError):
                    raise _storage_unavailable() from None
                raise
            if stored is None or not hmac.compare_digest(stored, verifier):
                raise CandidateAuthError(409, "RECOVERY_KEY_NOT_ISSUED", "That recovery key was not issued for this conversation.")
            profile = await sessions.candidate_profile_for(session)
            if not has_saveable_content(profile):
                raise CandidateAuthError(422, "PROFILE_EMPTY", "There is no profile information to save yet.")
            if not hmac.compare_digest(profile_digest(profile), body.profile_digest):
                raise CandidateAuthError(409, "PROFILE_CHANGED", "The profile changed after you reviewed it. Review it again.")
            try:
                result = await candidates.register(
                    profile, recovery_secret_hash=verifier, consent_version=body.consent_version, retention=RETENTION,
                )
            except RecoveryKeyUnavailableError:
                raise CandidateAuthError(409, "RECOVERY_KEY_UNAVAILABLE", "That recovery key cannot be used. Request a new one.") from None
            except (DatabaseUnavailableError, DatabaseConstraintError):
                raise _storage_unavailable() from None  # nothing was written and the intake session is kept for a retry
            await sessions.discard(session)  # after a successful commit: the temporary conversation and profile are dropped
        return SaveResponse(created=result.created)

    @router.get("/profile", response_model=SavedProfileResponse)
    async def get_profile(
        request: Request, response: Response, principal: CandidatePrincipal = Depends(require_candidate),
    ) -> SavedProfileResponse:
        candidates = _repository(request)
        try:
            saved = await candidates.get(principal.candidate_id)  # the identity comes only from the session cookie
        except ProfileNotFoundError:
            raise CandidateAuthError(401, "UNAUTHENTICATED", "Sign in to continue.", clear_cookie=True) from None  # expired in between
        except StoredProfileInvalidError:
            raise CandidateAuthError(500, "STORED_PROFILE_INVALID", "The saved profile could not be read.") from None
        except (DatabaseUnavailableError, DatabaseConstraintError):
            raise _storage_unavailable() from None
        response.headers.update(NO_STORE)
        return SavedProfileResponse(
            profile=_view(saved.profile), expires_at=saved.expires_at, consent_version=saved.consent_version,
            consented_at=saved.consented_at,
        )

    return router


async def _discard_if_bound(sessions: InMemorySessionStore, session_id: str, verifier: bytes) -> None:
    """Best effort: drop a still-existing intake session whose issued key is this one. Missing or mismatched sessions are ignored."""
    try:
        session = await sessions.resolve(session_id, workflow="candidate_intake")
        async with session.lock:
            stored = await sessions.recovery_key_hash_for(session)
            if stored is not None and hmac.compare_digest(stored, verifier):
                await sessions.discard(session)
    except Exception:  # noqa: BLE001 - the identity already exists; failing to tidy a temporary session must not fail the retry
        return
