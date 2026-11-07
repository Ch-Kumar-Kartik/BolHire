"""GET /api/v1/candidate/jobs/recommendations with fakes: no database, no provider calls."""

import asyncio
import json
import types
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError, ProgrammingError

import app.api.job_recommendations as route
from app.api.job_recommendations import (
    SAMPLE_DATA_NOTICE, CriterionView, JobView, MatchView, RecommendationsResponse,
)
from app.db.candidate_repository import DatabaseConstraintError, ProfileNotFoundError, StoredProfileInvalidError
from app.db.database import DatabaseUnavailableError
from app.db.job_queries import ActiveJob, JobsUnavailableError, load_active_jobs
from app.jobs.matching import MatchableJob
from app.services.candidate_profile import CandidateProfile
from app.services.rate_limit import SlidingWindowLimiter
from test_candidate_api import client_for, cookie, error_code, make, saved  # noqa: F401  (make is a fixture)

URL = "/api/v1/candidate/jobs/recommendations"
JOB_KEYS = {"id", "title", "company", "location_label", "work_mode", "employment_type", "min_experience_years",
            "max_experience_years", "required_skills", "description", "is_sample"}
CRITERION_KEYS = {"name", "weight", "state", "score_percent", "reason", "matched_skills", "missing_skills"}


def matchable(slug="data-analyst-acme", **changes):
    base = dict(
        id=str(uuid4()), slug=slug, title="Data Analyst", company="Acme Sample", location_label="Pune, Maharashtra",
        description="A fictional sample listing.", employment_type="full_time", role_key="data-analyst", city_key="pune",
        work_mode="hybrid", min_experience_years=1.0, max_experience_years=None, required_skills=("sql", "excel", "python"),
    )
    return MatchableJob(**{**base, **changes})


@pytest.fixture
def wired(make, monkeypatch):
    """An app whose auth, candidate repository and job loader are fakes; `state.database` is a stand-in with no real connection."""
    app, auth, candidates = make()
    app.state.database = types.SimpleNamespace(sessions=object())
    candidates.saved = saved(auth.principal.candidate_id)
    jobs = [ActiveJob(matchable(), True)]
    calls = []

    async def fake_loader(sessions):
        calls.append(sessions)
        if isinstance(jobs, Exception):
            raise jobs
        return list(jobs)

    monkeypatch.setattr(route, "load_active_jobs", fake_loader)
    return types.SimpleNamespace(app=app, auth=auth, candidates=candidates, set_jobs=lambda value: jobs.__setitem__(slice(None), value),
                                 calls=calls, fail_with=lambda error: monkeypatch.setattr(route, "load_active_jobs", _raiser(error)))


def _raiser(error):
    async def raise_it(_sessions):
        raise error
    return raise_it


def get(app, headers=None, path=URL):
    async def scenario():
        async with client_for(app) as client:
            return await client.get(path, headers=cookie() if headers is None else headers)
    return asyncio.run(scenario())


# ---- authentication: identical to GET /candidate/profile -----------------------------------------------------------------------------------------
def test_unauthenticated_requests_get_the_same_401_envelope_as_the_profile_route(wired):
    jobs, profile = get(wired.app, {}, URL), get(wired.app, {}, "/api/v1/candidate/profile")
    assert jobs.status_code == profile.status_code == 401
    assert jobs.json() == profile.json() == {"error": {"code": "UNAUTHENTICATED", "message": "Sign in to continue."}}
    assert jobs.headers["cache-control"] == "no-store" and not wired.calls and not wired.candidates.get_calls


def test_an_invalid_session_clears_the_cookie_like_the_profile_route(wired):
    from app.db.auth_repository import SessionInvalidError
    wired.auth.resolve_error = SessionInvalidError()
    response = get(wired.app)
    assert response.status_code == 401 and "max-age=0" in response.headers["set-cookie"].lower() and not wired.calls


def test_with_the_database_disabled_the_route_answers_like_the_profile_route(make):
    app, _, _ = make(enabled=False)
    jobs, profile = get(app, {}), get(app, {}, "/api/v1/candidate/profile")
    assert jobs.status_code == profile.status_code == 503 and jobs.json() == profile.json()
    assert error_code(jobs) == "CANDIDATE_STORAGE_DISABLED"


def test_the_identity_is_the_cookies_not_a_query_parameter(wired):
    other = uuid4()
    response = get(wired.app, path=f"{URL}?candidate_id={other}")
    assert response.status_code == 200
    assert wired.candidates.get_calls == [wired.auth.principal.candidate_id]
    assert str(other) not in response.text and str(wired.auth.principal.candidate_id) not in response.text


@pytest.mark.parametrize("error, status, code", [
    (ProfileNotFoundError(), 401, "UNAUTHENTICATED"),
    (StoredProfileInvalidError(), 500, "STORED_PROFILE_INVALID"),
    (DatabaseUnavailableError(), 503, "CANDIDATE_STORAGE_UNAVAILABLE"),
    (DatabaseConstraintError(), 503, "CANDIDATE_STORAGE_UNAVAILABLE"),
])
def test_profile_errors_map_like_the_profile_route(wired, error, status, code):
    wired.candidates.get_error = error
    response = get(wired.app)
    assert (response.status_code, error_code(response)) == (status, code) and not wired.calls


# ---- jobs unavailable ---------------------------------------------------------------------------------------------------------------------------
def test_no_database_object_gives_jobs_unavailable(wired):
    del wired.app.state.database  # the lifespan never ran
    response = get(wired.app)
    assert response.status_code == 503 and error_code(response) == "JOBS_UNAVAILABLE"
    wired.app.state.database = None
    assert error_code(get(wired.app)) == "JOBS_UNAVAILABLE"


def test_a_failing_job_query_gives_jobs_unavailable_without_detail(wired):
    wired.fail_with(JobsUnavailableError())
    response = get(wired.app)
    assert response.status_code == 503 and response.json() == {
        "error": {"code": "JOBS_UNAVAILABLE", "message": "Job recommendations are temporarily unavailable."}}
    assert response.headers["cache-control"] == "no-store"


class FailingSessions:
    def __init__(self, error):
        self.error = error

    def __call__(self):
        return self

    async def __aenter__(self):
        raise self.error

    async def __aexit__(self, *_):
        return False


@pytest.mark.parametrize("error", [
    ProgrammingError("SELECT secret_sql FROM jobs", {}, Exception('relation "jobs" does not exist')),
    OperationalError("SELECT 1", {}, Exception("connection refused password=hunter2")),
    OSError("socket secret"),
])
def test_the_loader_maps_database_failures_to_a_generic_error(error):
    with pytest.raises(JobsUnavailableError) as raised:
        asyncio.run(load_active_jobs(FailingSessions(error)))
    assert "secret" not in str(raised.value) and "jobs" not in str(raised.value).replace("job catalogue", "")
    assert raised.value.__cause__ is None


# ---- success shape ------------------------------------------------------------------------------------------------------------------------------
def test_a_success_returns_exactly_the_whitelisted_fields(wired, caplog):
    wired.candidates.saved = saved(wired.auth.principal.candidate_id)
    wired.candidates.saved = type(wired.candidates.saved)(**{**wired.candidates.saved.__dict__, "profile": CandidateProfile(
        target_role="data analyst", technical_skills=("Python", "SQL"), experience_years=2)})
    with caplog.at_level("DEBUG"):
        response = get(wired.app)
    body = response.json()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert set(body) == {"status", "coverage_percent", "sample_data_notice", "matches"}
    assert body["status"] == "ok" and body["coverage_percent"] == 85 and body["sample_data_notice"] == SAMPLE_DATA_NOTICE
    (match,) = body["matches"]
    assert set(match) == {"job", "score_percent", "criteria"} and set(match["job"]) == JOB_KEYS
    assert [c["name"] for c in match["criteria"]] == ["skills", "role", "experience", "location", "work_mode"]
    assert all(set(c) == CRITERION_KEYS for c in match["criteria"])
    assert match["job"]["is_sample"] is True and match["job"]["required_skills"] == ["sql", "excel", "python"]
    skills = match["criteria"][0]
    assert (skills["state"], skills["matched_skills"], skills["missing_skills"]) == ("partial", ["sql", "python"], ["excel"])
    assert "python" not in caplog.text.lower() and "analyst" not in caplog.text.lower()


def test_the_job_object_never_carries_internal_fields(wired):
    text = get(wired.app).text
    for forbidden in ("slug", "role_key", "city_key", "is_active", "created_at", "updated_at", "candidate_id", "data-analyst-acme"):
        assert forbidden not in text, forbidden


def test_an_insufficient_profile_returns_no_matches(wired):
    wired.candidates.saved = type(wired.candidates.saved)(**{**wired.candidates.saved.__dict__, "profile": CandidateProfile(name="Asha")})
    response = get(wired.app)
    assert response.json() == {"status": "insufficient_profile", "coverage_percent": 0, "sample_data_notice": SAMPLE_DATA_NOTICE, "matches": []}


def test_no_active_jobs_returns_no_jobs(wired):
    wired.set_jobs([])
    body = get(wired.app).json()
    assert body["status"] == "no_jobs" and body["matches"] == [] and body["coverage_percent"] == 50


def test_the_result_is_capped_at_ten(wired):
    wired.set_jobs([ActiveJob(matchable(f"job-{n:02d}"), True) for n in range(15)])
    assert len(get(wired.app).json()["matches"]) == 10


# ---- rate limit ----------------------------------------------------------------------------------------------------------------------------------
def test_the_route_is_rate_limited_per_client(make):
    app, auth, candidates = make(job_recommendations_limiter=SlidingWindowLimiter(max_attempts=2, window_seconds=60))
    app.state.database = None
    candidates.saved = saved(auth.principal.candidate_id)
    statuses = [get(app) for _ in range(3)]
    assert [r.status_code for r in statuses] == [503, 503, 429]
    assert error_code(statuses[2]) == "RATE_LIMITED" and int(statuses[2].headers["retry-after"]) >= 1


def test_the_default_limit_is_thirty_per_minute(make):
    app, auth, candidates = make()
    app.state.database = None
    candidates.saved = saved(auth.principal.candidate_id)
    codes = [get(app).status_code for _ in range(31)]
    assert codes[:30] == [503] * 30 and codes[30] == 429


# ---- schema --------------------------------------------------------------------------------------------------------------------------------------
def test_the_response_models_reject_extra_fields():
    job = {key: None for key in JOB_KEYS} | {"required_skills": [], "is_sample": True, "id": "x", "title": "t", "company": "c",
                                              "location_label": "l", "work_mode": "remote", "employment_type": "full_time", "description": "d"}
    JobView(**job)
    with pytest.raises(ValidationError):
        JobView(**job, slug="s")
    criterion = dict(name="skills", weight=50, state="matched", score_percent=100, reason=None, matched_skills=[], missing_skills=[])
    CriterionView(**criterion)
    with pytest.raises(ValidationError):
        CriterionView(**criterion, extra=1)
    with pytest.raises(ValidationError):
        MatchView(job=job, score_percent=1, criteria=[], extra=1)
    with pytest.raises(ValidationError):
        RecommendationsResponse(status="ok", coverage_percent=1, sample_data_notice="n", matches=[], extra=1)


def test_the_model_fields_are_exactly_the_whitelist():
    assert set(JobView.model_fields) == JOB_KEYS and set(CriterionView.model_fields) == CRITERION_KEYS


def test_the_sample_notice_is_the_specified_text():
    assert SAMPLE_DATA_NOTICE == ("These are fictional sample listings for demonstration. "
                                  "Showing interest does not apply to a job or contact any employer.")


def test_json_roundtrip_of_a_response_has_no_unexpected_keys(wired):
    body = get(wired.app).json()
    assert json.loads(json.dumps(body)) == body
