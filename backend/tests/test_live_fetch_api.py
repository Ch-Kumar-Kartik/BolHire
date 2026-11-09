"""POST /api/v1/candidate/jobs/fetch-live with fakes and mocked HTTP: no database, no network, no real token."""

import asyncio
import logging
import types
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.api.live_fetch import LiveFetchService
from app.db.auth_repository import CandidatePrincipal
from app.db.candidate_repository import ProfileNotFoundError
from app.jobs.live_fetch import ApifyClient
from app.jobs.live_fetch_config import ApifySettings, ApifyToken
from app.services.candidate_profile import CandidateProfile
from test_candidate_api import client_for, cookie, error_code, make, saved  # noqa: F401  (make is a fixture)

URL = "/api/v1/candidate/jobs/fetch-live"
ORIGIN = "https://app.example.com"
TOKEN = "apify_api_TEST-TOKEN-never-real-0123456789"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
UPSTREAM_SECRET = "UPSTREAM-DETAIL-must-not-leak"
APPLY = "https://careers.example.com/apply/secret"


@pytest.fixture(autouse=True)
def no_real_token(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)


def item(n, **changes):
    base = {
        "id": f"41000000{n:02d}", "title": "Data Analyst", "companyName": f"Live Co {n}", "location": "Pune, Maharashtra, India",
        "link": f"https://www.linkedin.com/jobs/view/41000000{n:02d}?trackingId=t", "descriptionText": "SQL\nExcel\nPython",
        "employmentType": "Full-time", "applyUrl": APPLY, "jobPosterName": "Synthetic Poster",
    }
    base.update(changes)
    return base


@dataclass
class Counts:
    inserted: int
    updated: int
    unchanged: int


class FakeImporter:
    def __init__(self):
        self.calls = []
        self.error = None

    async def __call__(self, engine, jobs):
        self.calls.append(list(jobs))
        if self.error:
            raise self.error
        return None, Counts(inserted=len(jobs) - 1, updated=1, unchanged=0) if len(jobs) > 1 else Counts(len(jobs), 0, 0)


class Apify:
    """A mocked Apify endpoint. `response` is what it answers; `requests` records every call."""

    def __init__(self, response=None):
        self.requests = []
        self.response = response or httpx.Response(201, json=[item(1), item(2), item(3, title="Chef")])
        self.gate: asyncio.Event | None = None

    async def handler(self, request):
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        return self.response


@pytest.fixture
def live(make):
    apify, importer = Apify(), FakeImporter()
    service = LiveFetchService(ApifyClient(ApifySettings(ApifyToken(TOKEN)), transport=httpx.MockTransport(apify.handler)),
                               importer=importer, clock=lambda: NOW)
    app, auth, candidates = make(live_fetch_service=service)
    app.state.database = types.SimpleNamespace(engine=object())
    candidates.saved = with_profile(auth.principal.candidate_id)
    return types.SimpleNamespace(app=app, auth=auth, candidates=candidates, apify=apify, importer=importer, service=service)


def with_profile(candidate_id, **fields):
    base = dict(name="Asha", target_role="Data analyst", preferred_location="Pune", technical_skills=("Python", "SQL"))
    record = saved(candidate_id)
    return type(record)(**{**record.__dict__, "profile": CandidateProfile(**{**base, **fields})})


def post(app, headers=None, **kwargs):
    async def scenario():
        async with client_for(app) as client:
            return await client.post(URL, headers={"Origin": ORIGIN, **cookie()} if headers is None else headers, **kwargs)
    return asyncio.run(scenario())


# ---- success ------------------------------------------------------------------------------------------------------------------
def test_a_fetch_maps_imports_and_reports_counts(live):
    response = post(live.app)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {"status": "ok", "fetched": 3, "imported_new": 1, "already_present": 1, "dropped": 1}
    (jobs,) = live.importer.calls
    assert [job.external_id for job in jobs] == ["4100000001", "4100000002"]
    assert all(job.retrieved_at == NOW and job.source == "linkedin" for job in jobs)
    assert all(APPLY not in repr(job) and "Synthetic Poster" not in repr(job) for job in jobs)


def test_retrieved_at_is_the_fetch_time_even_when_items_carry_their_own(live):
    live.apify.response = httpx.Response(201, json=[item(1, scrapedAt="2020-01-01T00:00:00Z")])
    post(live.app)
    assert live.importer.calls[0][0].retrieved_at == NOW


def test_the_search_uses_the_saved_role_and_location(live):
    import json
    post(live.app)
    body = json.loads(live.apify.requests[0].content)
    assert body["urls"] == ["https://www.linkedin.com/jobs/search/?keywords=Data%20analyst&location=Pune%2C%20India"]
    assert body["limitPerSource"] == 25


@pytest.mark.parametrize("payload", [[], [item(1, title="Chef")], [{"unrelated": True}]])
def test_nothing_usable_is_a_nothing_found_result_and_imports_nothing(live, payload):
    live.apify.response = httpx.Response(201, json=payload)
    response = post(live.app)
    assert response.status_code == 200 and response.json()["status"] == "nothing_found"
    assert response.json()["imported_new"] == 0 and live.importer.calls == []


def test_the_response_has_exactly_the_five_fields(live):
    assert set(post(live.app).json()) == {"status", "fetched", "imported_new", "already_present", "dropped"}


# ---- auth, origin, body -----------------------------------------------------------------------------------------------------------
def test_without_a_session_cookie_the_fetch_is_refused_and_apify_is_never_called(live):
    response = post(live.app, headers={"Origin": ORIGIN})
    assert response.status_code == 401 and error_code(response) == "UNAUTHENTICATED"
    assert live.apify.requests == [] and live.service.budget_used == 0


@pytest.mark.parametrize("origin", [None, "https://evil.example.com", "null"])
def test_a_missing_or_foreign_origin_is_refused(live, origin):
    headers = {**cookie(), **({} if origin is None else {"Origin": origin})}
    response = post(live.app, headers=headers)
    assert response.status_code == 403 and error_code(response) == "ORIGIN_NOT_ALLOWED"
    assert live.apify.requests == []


def test_a_body_with_a_candidate_id_is_ignored(live):
    other = uuid4()
    response = post(live.app, json={"candidate_id": str(other), "target_role": "CEO"})
    assert response.status_code == 200 and live.candidates.get_calls == [live.auth.principal.candidate_id]
    import json
    assert "CEO" not in json.loads(live.apify.requests[0].content)["urls"][0]


def test_an_expired_profile_is_unauthenticated(live):
    live.candidates.get_error = ProfileNotFoundError()
    response = post(live.app)
    assert response.status_code == 401 and live.apify.requests == []


# ---- configuration and profile ------------------------------------------------------------------------------------------------
def test_without_a_token_the_endpoint_is_a_safe_503(make):
    service = LiveFetchService(ApifyClient(ApifySettings()), importer=FakeImporter())
    app, auth, candidates = make(live_fetch_service=service)
    app.state.database = types.SimpleNamespace(engine=object())
    candidates.saved = with_profile(auth.principal.candidate_id)
    response = post(app)
    assert response.status_code == 503 and error_code(response) == "LIVE_FETCH_NOT_CONFIGURED"
    assert service.budget_used == 0


def test_the_default_app_reads_no_token_from_an_empty_environment(make):
    app, auth, candidates = make()
    app.state.database = types.SimpleNamespace(engine=object())
    candidates.saved = with_profile(auth.principal.candidate_id)
    assert error_code(post(app)) == "LIVE_FETCH_NOT_CONFIGURED"


def test_without_a_database_the_endpoint_is_unavailable(live):
    del live.app.state.database
    response = post(live.app)
    assert response.status_code == 503 and error_code(response) == "LIVE_FETCH_UNAVAILABLE" and live.apify.requests == []


@pytest.mark.parametrize("fields", [
    dict(target_role=None), dict(preferred_location=None), dict(target_role=None, preferred_location=None),
    dict(target_role=None, declined_fields=frozenset({"target_role"})),
    dict(preferred_location=None, declined_fields=frozenset({"preferred_location"})),
])
def test_a_missing_or_declined_role_or_location_is_a_409(live, fields):
    live.candidates.saved = with_profile(live.auth.principal.candidate_id, **fields)
    response = post(live.app)
    assert response.status_code == 409 and error_code(response) == "PROFILE_INCOMPLETE"
    assert live.apify.requests == [] and live.service.budget_used == 0


# ---- limits ---------------------------------------------------------------------------------------------------------------------
def test_three_fetches_per_candidate_per_hour(live):
    codes = [post(live.app).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert len(live.apify.requests) == 3
    other = CandidatePrincipal(uuid4(), uuid4())
    live.auth.principal = other
    live.candidates.saved = with_profile(other.candidate_id)
    assert post(live.app).status_code == 200  # another candidate has its own hourly allowance


def test_the_hourly_limit_answers_with_retry_after(live):
    for _ in range(3):
        post(live.app)
    response = post(live.app)
    assert error_code(response) == "LIVE_FETCH_RATE_LIMITED" and int(response.headers["retry-after"]) >= 1


def switch_candidate(live):
    principal = CandidatePrincipal(uuid4(), uuid4())
    live.auth.principal = principal
    live.candidates.saved = with_profile(principal.candidate_id)


def test_five_fetches_fit_the_budget_and_the_sixth_is_refused_without_calling_apify(live):
    for _ in range(5):
        switch_candidate(live)  # avoid the per-candidate hourly limit, so only the global budget is under test
        assert post(live.app).status_code == 200
    assert live.service.budget_used == 125 and len(live.apify.requests) == 5
    switch_candidate(live)
    response = post(live.app)
    assert response.status_code == 429 and error_code(response) == "LIVE_FETCH_LIMIT_REACHED"
    assert len(live.apify.requests) == 5 and live.service.budget_used == 125


def test_failed_fetches_still_consume_the_budget(live):
    live.apify.response = httpx.Response(500, text=UPSTREAM_SECRET)
    for _ in range(5):
        switch_candidate(live)
        assert post(live.app).status_code == 503
    switch_candidate(live)
    assert error_code(post(live.app)) == "LIVE_FETCH_LIMIT_REACHED"


def test_a_second_concurrent_fetch_for_the_same_candidate_is_refused(live):
    live.apify.gate = asyncio.Event()

    async def scenario():
        async with client_for(live.app) as client:
            headers = {"Origin": ORIGIN, **cookie()}
            first = asyncio.create_task(client.post(URL, headers=headers))
            while not live.apify.requests:
                await asyncio.sleep(0.01)
            second = await client.post(URL, headers=headers)
            live.apify.gate.set()
            return await first, second

    first, second = asyncio.run(scenario())
    assert first.status_code == 200
    assert second.status_code == 409 and error_code(second) == "LIVE_FETCH_IN_PROGRESS"
    assert len(live.apify.requests) == 1 and live.service.budget_used == 25


def test_the_running_flag_is_released_after_a_failure(live):
    live.apify.response = httpx.Response(500, text=UPSTREAM_SECRET)
    assert post(live.app).status_code == 503
    live.apify.response = httpx.Response(201, json=[item(1)])
    assert post(live.app).status_code == 200


# ---- failures and secrecy -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("response", [
    httpx.Response(408, json={"error": {"type": "run-timeout-exceeded", "message": UPSTREAM_SECRET}}),
    httpx.Response(402, json={"error": {"type": "x402-payment-required", "message": UPSTREAM_SECRET}}),
    httpx.Response(500, text=UPSTREAM_SECRET),
    httpx.Response(201, text=UPSTREAM_SECRET),
])
def test_upstream_failures_are_a_safe_503(live, response):
    live.apify.response = response
    result = post(live.app)
    assert result.status_code == 503 and result.json() == {
        "error": {"code": "LIVE_FETCH_UNAVAILABLE", "message": "Live job search is temporarily unavailable."}}


def test_an_import_failure_is_a_safe_503(live):
    live.importer.error = RuntimeError(f"database said {UPSTREAM_SECRET}")
    result = post(live.app)
    assert result.status_code == 503 and UPSTREAM_SECRET not in result.text


def test_the_token_search_url_and_upstream_text_never_reach_a_response_or_a_log(live, caplog):
    search = "https://www.linkedin.com/jobs/search/?keywords=Data%20analyst&location=Pune%2C%20India"
    caplog.set_level(logging.DEBUG)
    responses = [post(live.app)]
    live.apify.response = httpx.Response(402, json={"error": {"message": UPSTREAM_SECRET}})
    responses.append(post(live.app))
    live.apify.response = httpx.Response(201, json=[])
    responses.append(post(live.app))
    responses.append(post(live.app))  # the 4th for this candidate: rate limited
    for response in responses:
        text = response.text + str(dict(response.headers))
        for secret in (TOKEN, search, "keywords=", UPSTREAM_SECRET, "Data analyst", "Pune"):
            assert secret not in text, secret
    log = caplog.text
    for secret in (TOKEN, search, "keywords=", UPSTREAM_SECRET, "Bearer"):
        assert secret not in log, secret
    assert "live_fetch outcome=ok" in log and "live_fetch outcome=LIVE_FETCH_UNAVAILABLE" in log
    assert "live_fetch outcome=LIVE_FETCH_RATE_LIMITED" in log
