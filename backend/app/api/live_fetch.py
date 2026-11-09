"""POST /api/v1/candidate/jobs/fetch-live: fetch live LinkedIn listings for the signed-in candidate's saved profile (local demo).

Identity comes only from the session cookie; the route takes no request body. The saved target role and preferred location are
read server-side. Nothing is spent until every check passes, in this order: configured, database present, profile readable,
role and location present, not already running for this candidate, the process-wide budget has room for a full fetch, and the
per-candidate hourly limit. Budget is reserved before the Apify call and never refunded (Apify may already have charged).

Responses and the single log line carry only fixed codes and counts: never the token, the search URL, profile text or any
upstream body. The process-wide budget is in memory, so a backend restart resets it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from math import ceil
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from app.api.auth import CandidateAuthError, enforce_trusted_origin, require_candidate
from app.db.auth_repository import CandidatePrincipal
from app.db.candidate_repository import DatabaseConstraintError, ProfileNotFoundError, StoredProfileInvalidError
from app.db.database import DatabaseUnavailableError
from app.db.job_import import import_jobs
from app.jobs.linkedin_import import ImportedJob, normalize_records
from app.jobs.live_fetch import (
    FETCHES_PER_CANDIDATE_PER_HOUR, ITEMS_PER_FETCH, MAX_ITEMS_SINCE_START, ApifyClient, LiveFetchUnavailable, search_url,
)
from app.services.rate_limit import SlidingWindowLimiter

NO_STORE = {"Cache-Control": "no-store"}
logger = logging.getLogger("bolhire.live_fetch")

Importer = Callable[[object, list[ImportedJob]], Awaitable[tuple[object, object]]]


class LiveFetchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "nothing_found"]
    fetched: int
    imported_new: int
    already_present: int
    dropped: int


class LiveFetchService:
    """Per-process state: the spending budget, who is running, and the per-candidate hourly limiter."""

    def __init__(
        self, client: ApifyClient, *, importer: Importer = import_jobs, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        candidate_limiter: SlidingWindowLimiter | None = None,
    ) -> None:
        self.client = client
        self.importer = importer
        self.clock = clock
        self.limiter = candidate_limiter or SlidingWindowLimiter(max_attempts=FETCHES_PER_CANDIDATE_PER_HOUR, window_seconds=3600)
        self.budget_used = 0
        self._running: set[UUID] = set()
        self._lock = asyncio.Lock()

    async def reserve(self, candidate_id: UUID) -> None:
        """Atomically: refuse a second concurrent fetch, refuse past the budget, apply the hourly limit, then reserve."""
        async with self._lock:
            if candidate_id in self._running:
                raise _error(409, "LIVE_FETCH_IN_PROGRESS")
            if self.budget_used + ITEMS_PER_FETCH > MAX_ITEMS_SINCE_START:
                raise _error(429, "LIVE_FETCH_LIMIT_REACHED")
            wait = self.limiter.hit(str(candidate_id))
            if wait is not None:
                raise _error(429, "LIVE_FETCH_RATE_LIMITED", retry_after=max(1, ceil(wait)))
            self.budget_used += ITEMS_PER_FETCH
            self._running.add(candidate_id)

    async def release(self, candidate_id: UUID) -> None:
        async with self._lock:
            self._running.discard(candidate_id)


_MESSAGES = {
    "LIVE_FETCH_NOT_CONFIGURED": "Live job search is not configured.",
    "LIVE_FETCH_UNAVAILABLE": "Live job search is temporarily unavailable.",
    "PROFILE_INCOMPLETE": "A target role and a preferred location are needed for a live search.",
    "LIVE_FETCH_IN_PROGRESS": "A live search is already running.",
    "LIVE_FETCH_LIMIT_REACHED": "The live search limit has been reached.",
    "LIVE_FETCH_RATE_LIMITED": "Too many live searches. Try again later.",
}


def _error(status: int, code: str, *, retry_after: int | None = None) -> CandidateAuthError:
    logger.info("live_fetch outcome=%s", code)
    return CandidateAuthError(status, code, _MESSAGES[code], retry_after=retry_after)


def _count_already_present(counts) -> int:
    return counts.updated + counts.unchanged


def _accepted_now(items: Iterable[dict], now: datetime):
    report = normalize_records(list(items), retrieved_fallback=now, now=now)
    # retrieved_at is the moment of this fetch, whatever the item itself says.
    return [replace(job, retrieved_at=now) for job in report.accepted], sum(report.dropped.values())


def create_live_fetch_router(service: LiveFetchService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/candidate", dependencies=[Depends(enforce_trusted_origin)])

    @router.post("/jobs/fetch-live", response_model=LiveFetchResponse)
    async def fetch_live(
        request: Request, response: Response, principal: CandidatePrincipal = Depends(require_candidate),
    ) -> LiveFetchResponse:
        if not service.client.enabled:
            raise _error(503, "LIVE_FETCH_NOT_CONFIGURED")
        database = getattr(request.app.state, "database", None)
        candidates = request.app.state.candidate_repository
        if database is None or candidates is None:
            raise _error(503, "LIVE_FETCH_UNAVAILABLE")
        try:
            saved = await candidates.get(principal.candidate_id)  # the identity comes only from the session cookie
        except ProfileNotFoundError:
            raise CandidateAuthError(401, "UNAUTHENTICATED", "Sign in to continue.", clear_cookie=True) from None
        except StoredProfileInvalidError:
            raise CandidateAuthError(500, "STORED_PROFILE_INVALID", "The saved profile could not be read.") from None
        except (DatabaseUnavailableError, DatabaseConstraintError):
            raise CandidateAuthError(503, "CANDIDATE_STORAGE_UNAVAILABLE", "Candidate accounts are temporarily unavailable.") from None
        url = search_url(saved.profile.target_role, saved.profile.preferred_location)  # declined fields are None, so missing
        if url is None:
            raise _error(409, "PROFILE_INCOMPLETE")

        await service.reserve(principal.candidate_id)
        try:
            try:
                items = await service.client.fetch(url)
            except LiveFetchUnavailable:
                raise _error(503, "LIVE_FETCH_UNAVAILABLE") from None
            accepted, dropped = _accepted_now(items, service.clock())
            imported_new = already_present = 0
            if accepted:
                try:
                    _, counts = await service.importer(database.engine, accepted)
                except Exception:  # noqa: BLE001 - any storage failure is reported only as a fixed code
                    raise _error(503, "LIVE_FETCH_UNAVAILABLE") from None
                imported_new, already_present = counts.inserted, _count_already_present(counts)
        finally:
            await service.release(principal.candidate_id)

        status = "ok" if accepted else "nothing_found"
        logger.info("live_fetch outcome=%s fetched=%d new=%d", status, len(items), imported_new)
        response.headers.update(NO_STORE)
        return LiveFetchResponse(status=status, fetched=len(items), imported_new=imported_new,
                                 already_present=already_present, dropped=dropped)

    return router
