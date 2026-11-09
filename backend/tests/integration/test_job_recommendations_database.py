"""GET /api/v1/candidate/jobs/recommendations against real PostgreSQL: real seed, real saved profiles, real sessions."""

import json

import pytest
from sqlalchemy import func, select, text, update

from app.api.job_recommendations import SAMPLE_DATA_NOTICE
from app.db.job_catalogue_seed import run_seed
from app.db.job_queries import load_active_jobs
from app.db.models import Base, Candidate, Job
from app.jobs.catalogue_data import CATALOGUE
from app.jobs.matching import match_jobs
from app.services.candidate_profile import CandidateProfile, CandidateProfileUpdate
from test_candidate_flow import (  # noqa: F401  (app is a fixture)
    COOKIE, EXCHANGE, ORIGIN, VERSION, app, login, post, run, save_body, store_of,
)

pytestmark = pytest.mark.integration

URL = "/api/v1/candidate/jobs/recommendations"
ANALYST = dict(name="Asha", target_role="Data analyst", skills=["Python", "SQL"], experience_years=2)
ANALYST_PROFILE = CandidateProfile(name="Asha", target_role="Data analyst", technical_skills=("Python", "SQL"), experience_years=2)
BACKEND = dict(name="Ravi", target_role="Backend developer", skills=["Python", "Django", "PostgreSQL"], experience_years=1)
BACKEND_PROFILE = CandidateProfile(name="Ravi", target_role="Backend developer", technical_skills=("Python", "Django", "PostgreSQL"), experience_years=1)
DATA_ANALYST_TITLES = {"Data Analyst", "Data Analyst Intern", "Senior Data Analyst"}


async def seed(app):
    await run_seed(app.state.database.engine, CATALOGUE)


async def sign_in(client, app, name="Asha", target_role="Data analyst", skills=("Python", "SQL"), experience_years=2):
    """Register a candidate through the real intake -> key -> save -> login flow; returns (cookie headers, recovery key)."""
    update_ = CandidateProfileUpdate(name=name, target_role=target_role, skills_add=list(skills), experience_years=experience_years)
    session = await store_of(app).create_candidate_exchange(update_, EXCHANGE)
    digest = (await post(client, "/intake/preview", {"session_id": session.session_id, "language": "en-IN"})).json()["profile_digest"]
    key = (await post(client, "/recovery-key", {"session_id": session.session_id})).json()["recovery_key"]
    assert (await post(client, "/profile", save_body(session, key, digest))).status_code == 200
    response, cookie = await login(client, key)
    assert response.status_code == 200
    return cookie, key


async def sign_in_profile(client, app, profile):
    values = dict(profile)
    return await sign_in(client, app, values["name"], values["target_role"], values["skills"], values["experience_years"])


async def expected_for(app, profile):
    jobs = [item.job for item in await load_active_jobs(app.state.database.sessions)]
    return match_jobs(profile, jobs, limit=10)


def shape(outcome):
    return [(m.job.title, m.score_percent) for m in outcome.matches]


async def table_counts(app):
    async with app.state.database.sessions() as session:
        return {table.name: await session.scalar(select(func.count()).select_from(table)) for table in Base.metadata.sorted_tables}


# ---- behaviour -----------------------------------------------------------------------------------------------------------------------------------
def test_a_data_analyst_profile_gets_the_data_analyst_jobs_first_and_matches_the_matcher(app):
    async def scenario(client):
        await seed(app)
        cookie, _ = await sign_in_profile(client, app, ANALYST)
        response = await client.get(URL, headers=cookie)
        body = response.json()
        expected = await expected_for(app, ANALYST_PROFILE)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert body["status"] == "ok" and body["sample_data_notice"] == SAMPLE_DATA_NOTICE
        assert body["coverage_percent"] == expected.coverage_percent == 85
        assert {m["job"]["title"] for m in body["matches"][:3]} == DATA_ANALYST_TITLES
        assert [(m["job"]["title"], m["score_percent"]) for m in body["matches"]] == shape(expected)
        assert len(body["matches"]) == len(expected.matches) <= 10
        assert all(m["job"]["is_sample"] is True for m in body["matches"])
    run(app, scenario)


def test_a_name_only_profile_is_insufficient(app):
    async def scenario(client):
        await seed(app)
        update_ = CandidateProfileUpdate(name="Asha")
        session = await store_of(app).create_candidate_exchange(update_, EXCHANGE)
        digest = (await post(client, "/intake/preview", {"session_id": session.session_id, "language": "en-IN"})).json()["profile_digest"]
        key = (await post(client, "/recovery-key", {"session_id": session.session_id})).json()["recovery_key"]
        assert (await post(client, "/profile", save_body(session, key, digest))).status_code == 200
        _, cookie = await login(client, key)
        body = (await client.get(URL, headers=cookie)).json()
        assert body == {"status": "insufficient_profile", "coverage_percent": 0, "sample_data_notice": SAMPLE_DATA_NOTICE, "matches": []}
    run(app, scenario)


def test_a_retired_job_never_appears(app):
    async def scenario(client):
        await seed(app)
        cookie, _ = await sign_in_profile(client, app, ANALYST)
        before = (await client.get(URL, headers=cookie)).json()
        assert "Data Analyst Intern" in {m["job"]["title"] for m in before["matches"]}
        async with app.state.database.sessions.begin() as session:
            retired = await session.scalar(select(Job.id).where(Job.slug == "data-analyst-intern-quillgrid-data"))
            await session.execute(update(Job).where(Job.id == retired).values(is_active=False))
        after = (await client.get(URL, headers=cookie)).json()
        assert str(retired) not in {m["job"]["id"] for m in after["matches"]}
        assert "Data Analyst Intern" not in {m["job"]["title"] for m in after["matches"]}
        assert {m["job"]["title"] for m in after["matches"][:2]} == {"Data Analyst", "Senior Data Analyst"}
        assert [(m["job"]["title"], m["score_percent"]) for m in after["matches"]] == shape(await expected_for(app, ANALYST_PROFILE))
    run(app, scenario)


def test_an_empty_jobs_table_gives_no_jobs(app):
    async def scenario(client):
        cookie, _ = await sign_in_profile(client, app, ANALYST)  # nothing seeded
        body = (await client.get(URL, headers=cookie)).json()
        assert body["status"] == "no_jobs" and body["matches"] == []
        assert body["coverage_percent"] == match_jobs(ANALYST_PROFILE, [], limit=10).coverage_percent == 85
    run(app, scenario)


def test_a_missing_jobs_table_gives_jobs_unavailable_without_detail(app):
    async def scenario(client):
        await seed(app)
        cookie, _ = await sign_in_profile(client, app, ANALYST)
        async with app.state.database.sessions.begin() as session:
            await session.execute(text("DROP TABLE job_interests"))
            await session.execute(text("DROP TABLE jobs"))
        response = await client.get(URL, headers=cookie)
        assert response.status_code == 503
        assert response.json() == {"error": {"code": "JOBS_UNAVAILABLE", "message": "Job recommendations are temporarily unavailable."}}
        for leak in ("relation", "does not exist", "select", "psycopg", "sqlalchemy", "undefined", "from jobs"):
            assert leak not in response.text.lower(), leak
        assert response.headers["cache-control"] == "no-store"
        profile = await client.get("/api/v1/candidate/profile", headers=cookie)  # the rest of the API is unaffected
        assert profile.status_code == 200
    run(app, scenario)


def test_unauthenticated_requests_are_rejected(app):
    async def scenario(client):
        await seed(app)
        none = await client.get(URL)
        garbage = await client.get(URL, headers={"Cookie": f"{COOKIE}=not-a-token"})
        for response in (none, garbage):
            assert response.status_code == 401 and response.json()["error"]["code"] == "UNAUTHENTICATED"
    run(app, scenario)


# ---- privacy and read-only -----------------------------------------------------------------------------------------------------------------------
def test_no_identifier_or_internal_field_appears_anywhere_in_the_response(app):
    async def scenario(client):
        await seed(app)
        cookie, key = await sign_in_profile(client, app, ANALYST)
        response = await client.get(URL, headers=cookie)
        async with app.state.database.sessions() as session:
            candidate_id = str(await session.scalar(select(Candidate.id)))
            slugs = list((await session.scalars(select(Job.slug))).all())
        raw = json.dumps(response.json())
        forbidden = [candidate_id, key, key.replace("-", ""), cookie["Cookie"].split("=", 1)[1],
                     '"candidate_id"', '"slug"', '"role_key"', '"city_key"', '"is_active"', '"created_at"', '"updated_at"', *slugs]
        for item in forbidden:
            assert item not in raw and item not in response.text, item
        assert candidate_id not in str(response.headers) and key not in str(response.headers)
        assert all(set(match["job"]) == {"id", "title", "company", "location_label", "work_mode", "employment_type", "min_experience_years",
                                         "max_experience_years", "required_skills", "description", "is_sample", "source", "source_url", "retrieved_at"}
                   for match in response.json()["matches"])
        assert all((m["job"]["source"], m["job"]["source_url"], m["job"]["retrieved_at"]) == (None, None, None) for m in response.json()["matches"])
    run(app, scenario)


def test_the_call_is_read_only(app):
    async def scenario(client):
        await seed(app)
        cookie, _ = await sign_in_profile(client, app, ANALYST)
        before = await table_counts(app)
        async with app.state.database.sessions() as session:
            snapshot = (await session.execute(text("SELECT id, is_active, updated_at FROM jobs ORDER BY id"))).all()
            profile = (await session.execute(text("SELECT candidate_id, revision, updated_at, expires_at FROM candidate_profiles"))).all()
        for _ in range(3):
            assert (await client.get(URL, headers=cookie)).status_code == 200
        assert await table_counts(app) == before and before["jobs"] == 24 and before["job_interests"] == 0
        async with app.state.database.sessions() as session:
            assert (await session.execute(text("SELECT id, is_active, updated_at FROM jobs ORDER BY id"))).all() == snapshot
            assert (await session.execute(text("SELECT candidate_id, revision, updated_at, expires_at FROM candidate_profiles"))).all() == profile
    run(app, scenario)


def test_the_loader_runs_in_a_read_only_transaction(app):
    async def scenario(client):
        await seed(app)
        async with app.state.database.sessions() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            with pytest.raises(Exception) as raised:
                await session.execute(update(Job).values(is_active=False))
            assert "read-only" in str(raised.value).lower()
        assert len(await load_active_jobs(app.state.database.sessions)) == 24
    run(app, scenario)


def test_two_candidates_each_get_results_for_their_own_profile(app):
    async def scenario(client):
        await seed(app)
        analyst_cookie, _ = await sign_in_profile(client, app, ANALYST)
        backend_cookie, _ = await sign_in_profile(client, app, BACKEND)
        analyst = (await client.get(URL, headers=analyst_cookie)).json()
        backend = (await client.get(URL, headers=backend_cookie)).json()
        assert [(m["job"]["title"], m["score_percent"]) for m in analyst["matches"]] == shape(await expected_for(app, ANALYST_PROFILE))
        assert [(m["job"]["title"], m["score_percent"]) for m in backend["matches"]] == shape(await expected_for(app, BACKEND_PROFILE))
        assert {m["job"]["title"] for m in analyst["matches"][:3]} == DATA_ANALYST_TITLES
        assert "Backend" in backend["matches"][0]["job"]["title"] and analyst["matches"][0]["job"]["title"] != backend["matches"][0]["job"]["title"]
        again = (await client.get(URL, headers=analyst_cookie)).json()
        assert again == analyst  # the other candidate's request did not leak into this one
    run(app, scenario)
