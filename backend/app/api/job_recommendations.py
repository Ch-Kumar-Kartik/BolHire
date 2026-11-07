"""GET /api/v1/candidate/jobs/recommendations: read-only matches of the signed-in candidate's saved profile against the active
sample catalogue. Identity comes only from the session cookie; no candidate ID is accepted or returned. Nothing is persisted,
and no profile content is logged or sent to a provider."""

from math import ceil
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict

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


def _match_view(match: JobMatch, is_sample: bool) -> MatchView:
    job = match.job
    return MatchView(
        job=JobView(
            id=job.id, title=job.title, company=job.company, location_label=job.location_label, work_mode=job.work_mode,
            employment_type=job.employment_type, min_experience_years=job.min_experience_years,
            max_experience_years=job.max_experience_years, required_skills=list(job.required_skills),
            description=job.description, is_sample=is_sample,
        ),
        score_percent=match.score_percent,
        criteria=[
            CriterionView(
                name=c.name, weight=c.weight, state=c.state, score_percent=c.score_percent, reason=c.reason,
                matched_skills=list(c.matched_skills), missing_skills=list(c.missing_skills),
            ) for c in match.criteria
        ],
    )


def _response(outcome: MatchOutcome, samples: dict[str, bool]) -> RecommendationsResponse:
    return RecommendationsResponse(
        status=outcome.status, coverage_percent=outcome.coverage_percent, sample_data_notice=SAMPLE_DATA_NOTICE,
        matches=[_match_view(match, samples[match.job.id]) for match in outcome.matches],
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
        return _response(outcome, {item.job.id: item.is_sample for item in active})

    return router
