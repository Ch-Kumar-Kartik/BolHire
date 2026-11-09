"""The live fetch against real PostgreSQL (the fixture's isolated schema), a real signed-in candidate and a mocked Apify endpoint.
No network: the Apify transport is httpx.MockTransport and the token is a fake test value."""

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.api.live_fetch import LiveFetchService
from app.db.models import Job
from app.jobs.live_fetch import ApifyClient
from app.jobs.live_fetch_config import ApifySettings, ApifyToken
from app.main import create_app
from app.services.candidate_profile import CandidateProfileUpdate
from test_candidate_flow import EXCHANGE, ORIGIN, login, post, run, save_body, store_of
from test_job_recommendations_database import seed

pytestmark = pytest.mark.integration
URL = "/api/v1/candidate/jobs/fetch-live"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
APPLY = "https://careers.example.com/apply/secret"


def item(n, **changes):
    base = {
        "id": f"42000000{n:02d}", "title": "Data Analyst", "companyName": f"Live Analytics {n}", "location": "Pune, Maharashtra, India",
        "link": f"https://www.linkedin.com/jobs/view/42000000{n:02d}?refId=r", "descriptionText": "SQL\nExcel\nPython dashboards",
        "employmentType": "Full-time", "applyUrl": APPLY, "jobPosterName": "Synthetic Poster", "jobPosterProfileUrl": "https://x.example/p",
    }
    base.update(changes)
    return base


ITEMS = [item(1), item(2), item(3, title="Senior Data Analyst"), item(4, title="Chef")]


@pytest.fixture
def live_app(db_url, monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", db_url.render_as_string(hide_password=False))
    monkeypatch.setenv("AUTH_TRUSTED_ORIGIN", ORIGIN)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("SPEECH_PROVIDER", "mock")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(201, json=ITEMS)

    service = LiveFetchService(ApifyClient(ApifySettings(ApifyToken("apify_api_TEST-ONLY")), transport=httpx.MockTransport(handler)),
                               clock=lambda: NOW)
    return create_app(live_fetch_service=service), calls


async def sign_in_with_location(client, app):
    """The real intake -> preview -> key -> save -> login flow, for a profile that has a role AND a preferred location."""
    update = CandidateProfileUpdate(name="Asha", target_role="Data analyst", preferred_location="Pune", skills_add=["Python", "SQL"])
    session = await store_of(app).create_candidate_exchange(update, EXCHANGE)
    digest = (await post(client, "/intake/preview", {"session_id": session.session_id, "language": "en-IN"})).json()["profile_digest"]
    key = (await post(client, "/recovery-key", {"session_id": session.session_id})).json()["recovery_key"]
    assert (await post(client, "/profile", save_body(session, key, digest))).status_code == 200
    response, cookie = await login(client, key)
    assert response.status_code == 200
    return cookie


async def real_rows(app):
    async with app.state.database.sessions() as session:
        return (await session.execute(select(Job).where(Job.source.is_not(None)).order_by(Job.external_id))).scalars().all()


def test_a_fetch_imports_real_rows_and_a_second_identical_fetch_inserts_nothing(live_app):
    app, calls = live_app

    async def scenario(client):
        await seed(app)
        cookie = await sign_in_with_location(client, app)
        headers = {"Origin": ORIGIN, **cookie}
        first = await client.post(URL, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json() == {"status": "ok", "fetched": 4, "imported_new": 3, "already_present": 0, "dropped": 1}
        rows = await real_rows(app)
        assert [row.external_id for row in rows] == ["4200000001", "4200000002", "4200000003"]
        assert all(row.retrieved_at == NOW and not row.is_sample and row.source_url.startswith("https://") for row in rows)
        assert all("?" not in row.source_url for row in rows)
        stored = " ".join(f"{row.description} {row.source_url} {row.company}" for row in rows)
        assert APPLY not in stored and "Synthetic Poster" not in stored and "x.example" not in stored
        snapshot = [(row.id, row.updated_at, row.title) for row in rows]

        second = await client.post(URL, headers=headers)
        assert second.status_code == 200
        assert second.json() == {"status": "ok", "fetched": 4, "imported_new": 0, "already_present": 3, "dropped": 1}
        assert [(row.id, row.updated_at, row.title) for row in await real_rows(app)] == snapshot

        recommendations = (await client.get("/api/v1/candidate/jobs/recommendations", headers=cookie)).json()
        real = [m["job"] for m in recommendations["matches"] if not m["job"]["is_sample"]]
        assert real and all(job["source"] == "linkedin" for job in real)
        assert len(calls) == 2
        import json
        assert json.loads(calls[0].content)["urls"] == [
            "https://www.linkedin.com/jobs/search/?keywords=Data%20analyst&location=Pune%2C%20India"]

    run(app, scenario)
