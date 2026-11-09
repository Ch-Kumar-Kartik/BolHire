"""GET /api/v1/candidate/jobs/recommendations: read-only matches of the signed-in candidate's saved profile against the active
job catalogue (fictional samples and, in a local demo, imported real listings). Identity comes only from the session cookie;
no candidate ID is accepted or returned. Nothing is persisted, and no profile content is logged or sent to a provider."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from math import ceil
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, model_validator

from app.api.auth import CandidateAuthError, enforce_trusted_origin, require_candidate
from app.db.auth_repository import CandidatePrincipal
from app.db.candidate_repository import DatabaseConstraintError, ProfileNotFoundError, StoredProfileInvalidError
from app.db.database import DatabaseUnavailableError
from app.db.job_queries import ActiveJob, JobsUnavailableError, load_active_jobs
from app.jobs.matching import JobMatch, MatchOutcome, match_jobs
from app.services.candidate_auth import AuthSettings
from app.services.rate_limit import SlidingWindowLimiter, client_key

NO_STORE = {"Cache-Control": "no-store"}
RESULT_LIMIT = 10
SAMPLE_DATA_NOTICE = (
    "These are fictional sample listings for demonstration. Showing interest does not apply to a job or contact any employer."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
REAL_ONLY_TAIL = "They may be out of date, so check the original posting before relying on them."


def _long_date(moment: datetime) -> str:
    moment = moment.astimezone(UTC)
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year}"


def _is_https(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith("https://") and len(value) <= 2048


def data_notice(listings: Sequence[ActiveJob]) -> str:
    """What the response says about its own data, decided by the listings actually returned. Sample-only (and empty) responses
    keep the original notice verbatim. Responses with real rows state the retrieval date(s) and the staleness caveat."""
    real = [item for item in listings if not item.is_sample]
    if not real:
        return SAMPLE_DATA_NOTICE
    days = sorted({item.retrieved_at.astimezone(UTC).date() for item in real if item.retrieved_at is not None})
    if not days:
        when = "earlier"
    elif len(days) == 1:
        when = f"on {_long_date(datetime(days[0].year, days[0].month, days[0].day, tzinfo=UTC))}"
    else:
        first, last = (_long_date(datetime(day.year, day.month, day.day, tzinfo=UTC)) for day in (days[0], days[-1]))
        when = f"between {first} and {last}"
    if len(real) == len(listings):
        return f"These listings were retrieved {when}. {REAL_ONLY_TAIL}"
    return (f"Listings marked Sample are fictional examples for demonstration. The other listings were retrieved {when}. "
            f"{REAL_ONLY_TAIL}")


class JobView(_Strict):
    id: str
    title: str
    company: str
    location_label: str
    work_mode: str
    employment_type: str
    min_experience_years: float | None
    max_experience_years: float | None
    required_skills: list[str]
    description: str
    is_sample: bool
    # Provenance of a real listing; always null for a sample. source_url is the posting link only (https), never an apply link.
    source: str | None
    source_url: str | None
    retrieved_at: datetime | None

    @model_validator(mode="after")
    def provenance_matches_the_listing_kind(self) -> "JobView":
        if self.is_sample and (self.source is not None or self.source_url is not None or self.retrieved_at is not None):
            raise ValueError("A sample listing carries no provenance.")
        if self.source_url is not None and not _is_https(self.source_url):
            raise ValueError("source_url must be https.")
        return self


class CriterionView(_Strict):
    name: Literal["skills", "role", "experience", "location", "work_mode"]
    weight: int
    state: Literal["matched", "partial", "unmatched", "not_evaluated"]
    score_percent: int | None
    reason: Literal["missing", "declined", "unrecognized"] | None
    matched_skills: list[str]
    missing_skills: list[str]


class MatchView(_Strict):
    job: JobView
    score_percent: int
    criteria: list[CriterionView]


class RecommendationsResponse(_Strict):
    status: Literal["ok", "insufficient_profile", "no_jobs"]
    coverage_percent: int
    sample_data_notice: str
    matches: list[MatchView]


def _match_view(match: JobMatch, item: ActiveJob) -> MatchView:
    job = match.job
    real = not item.is_sample  # provenance is exposed for real rows only, whatever the row holds
    return MatchView(
        job=JobView(
            id=job.id, title=job.title, company=job.company, location_label=job.location_label, work_mode=job.work_mode,
            employment_type=job.employment_type, min_experience_years=job.min_experience_years,
            max_experience_years=job.max_experience_years, required_skills=list(job.required_skills),
            description=job.description, is_sample=item.is_sample, source=item.source if real else None,
            source_url=item.source_url if real and _is_https(item.source_url) else None,
            retrieved_at=item.retrieved_at if real else None,
        ),
        score_percent=match.score_percent,
        criteria=[
            CriterionView(
                name=c.name, weight=c.weight, state=c.state, score_percent=c.score_percent, reason=c.reason,
                matched_skills=list(c.matched_skills), missing_skills=list(c.missing_skills),
            ) for c in match.criteria
        ],
    )


def _response(outcome: MatchOutcome, listings: Mapping[str, ActiveJob]) -> RecommendationsResponse:
    returned = [listings[match.job.id] for match in outcome.matches]
    return RecommendationsResponse(
        status=outcome.status, coverage_percent=outcome.coverage_percent, sample_data_notice=data_notice(returned),
        matches=[_match_view(match, item) for match, item in zip(outcome.matches, returned)],
    )


def _jobs_unavailable() -> CandidateAuthError:
    return CandidateAuthError(503, "JOBS_UNAVAILABLE", "Job recommendations are temporarily unavailable.")


def create_job_recommendations_router(settings: AuthSettings, limiter: SlidingWindowLimiter) -> APIRouter:
    router = APIRouter(prefix="/api/v1/candidate", dependencies=[Depends(enforce_trusted_origin)])

    @router.get("/jobs/recommendations", response_model=RecommendationsResponse)
    async def recommendations(
        request: Request, response: Response, principal: CandidatePrincipal = Depends(require_candidate),
    ) -> RecommendationsResponse:
        peer = request.client.host if request.client else None
        wait = limiter.hit(client_key(peer, request.headers, settings.trusted_proxy_hops))
        if wait is not None:
            raise CandidateAuthError(429, "RATE_LIMITED", "Too many attempts. Try again shortly.", retry_after=max(1, ceil(wait)))
        candidates = request.app.state.candidate_repository
        if candidates is None:
            raise CandidateAuthError(503, "CANDIDATE_STORAGE_DISABLED", "Candidate accounts are not available.")
        try:
            saved = await candidates.get(principal.candidate_id)  # the identity comes only from the session cookie
        except ProfileNotFoundError:
            raise CandidateAuthError(401, "UNAUTHENTICATED", "Sign in to continue.", clear_cookie=True) from None  # expired in between
        except StoredProfileInvalidError:
            raise CandidateAuthError(500, "STORED_PROFILE_INVALID", "The saved profile could not be read.") from None
        except (DatabaseUnavailableError, DatabaseConstraintError):
            raise CandidateAuthError(503, "CANDIDATE_STORAGE_UNAVAILABLE", "Candidate accounts are temporarily unavailable.") from None
        database = getattr(request.app.state, "database", None)  # absent until the lifespan has connected
        if database is None:
            raise _jobs_unavailable()
        try:
            active: list[ActiveJob] = await load_active_jobs(database.sessions)
        except JobsUnavailableError:
            raise _jobs_unavailable() from None
        outcome = match_jobs(saved.profile, [item.job for item in active], limit=RESULT_LIMIT)
        response.headers.update(NO_STORE)
        return _response(outcome, {item.job.id: item for item in active})

    return router
