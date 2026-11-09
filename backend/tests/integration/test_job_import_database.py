"""Real-listing import against real PostgreSQL (the fixture's isolated test schema only; never an application or demo database).
All listings are synthetic."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.api.job_recommendations import _response
from app.db.job_catalogue_seed import check_catalogue, run_seed, seed_catalogue
from app.db.job_import import check_import, import_jobs
from app.db.job_queries import load_active_jobs
from app.db.models import Job
from app.jobs.catalogue_data import CATALOGUE
from app.jobs.linkedin_import import normalize_records
from app.jobs.matching import match_jobs
from app.services.candidate_profile import CandidateProfile
from conftest import migration
from test_job_catalogue_seed_database import run, snapshot, writes

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)
RETRIEVED = datetime(2026, 11, 8, 9, 30, tzinfo=UTC)


def record(n, **fields):
    base = {
        "id": f"40000000{n:02d}", "title": "Backend Developer", "companyName": f"Real Co {n}", "location": "Pune, India",
        "link": f"https://www.linkedin.com/jobs/view/40000000{n:02d}", "descriptionText": "Django\nPostgreSQL\nDocker",
        "workplaceType": "Hybrid", "employmentType": "Full-time",
    }
    base.update(fields)
    return base


def imported(*records):
    report = normalize_records(list(records), retrieved_fallback=RETRIEVED, now=NOW)
    assert not report.dropped, report.dropped
    return list(report.accepted)


REAL = imported(record(1), record(2, title="Data Analyst", descriptionText="SQL\nExcel", location="Mumbai"),
                record(3, title="QA Engineer", descriptionText="Selenium\nPytest", workplaceType="Remote", location="India"))


def real_row(**changes):
    values = dict(
        id=uuid4(), slug=f"linkedin-{uuid4().hex[:12]}", title="Backend Developer", company="Real Co", location_label="Pune",
        description="Django work.", employment_type="full_time", role_key="backend-developer", city_key="pune", work_mode="hybrid",
        min_experience_years=None, max_experience_years=None, required_skills=["django"], is_sample=False, is_active=True,
        source="linkedin", external_id=uuid4().hex, source_url="https://www.linkedin.com/jobs/view/1", retrieved_at=RETRIEVED,
        posted_at=None,
    )
    values.update(changes)
    return values


async def constraint_violated(database, values):
    try:
        async with database.engine.begin() as connection:
            await connection.execute(insert(Job).values(**values))
    except IntegrityError as error:
        return error.orig.diag.constraint_name
    return None


async def real_rows(database):
    return [row for row in await snapshot(database) if row["source"] is not None]


# ---- migration 0003 constraints -----------------------------------------------------------------------------------------------
def test_a_complete_real_row_is_accepted_and_the_seeded_samples_stay_valid(db_url):
    async def scenario(database, _):
        await run_seed(database.engine, CATALOGUE)
        assert await constraint_violated(database, real_row()) is None
        rows = await snapshot(database)
        samples = [row for row in rows if row["source"] is None]
        assert len(samples) == 24 and all(row["is_sample"] and row["external_id"] is None for row in samples)
        assert len(rows) == 25
    run(db_url, scenario)


@pytest.mark.parametrize("changes, constraint", [
    (dict(source_url=None), "job_is_sample"),
    (dict(source_url="http://www.linkedin.com/jobs/view/1"), "job_is_sample"),
    (dict(source_url="ftp://x/y"), "job_is_sample"),
    (dict(retrieved_at=None), "job_is_sample"),
    (dict(external_id=None), "job_is_sample"),
    (dict(external_id="   "), "job_is_sample"),
    (dict(is_sample=True), "job_is_sample"),
    (dict(source=None), "job_is_sample"),                                   # a sample may not carry provenance
    (dict(source=None, external_id=None, source_url=None, retrieved_at=None, is_sample=False), "job_is_sample"),
    (dict(source=None, external_id=None, source_url=None, retrieved_at=None, is_sample=True, posted_at=RETRIEVED), "job_is_sample"),
    (dict(source="LinkedIn"), "job_source_format"),
])
def test_provenance_rules_are_enforced(db_url, changes, constraint):
    async def scenario(database, _):
        assert await constraint_violated(database, real_row(**changes)) == constraint
    run(db_url, scenario)


def test_source_and_external_id_are_unique_together(db_url):
    async def scenario(database, _):
        assert await constraint_violated(database, real_row(external_id="same")) is None
        assert await constraint_violated(database, real_row(external_id="same")) == "uq_jobs_source_external_id"
        assert await constraint_violated(database, real_row(external_id="same", source="other-source")) is None
    run(db_url, scenario)


# ---- import -------------------------------------------------------------------------------------------------------------------
def test_import_inserts_then_changes_nothing_on_a_repeat(db_url):
    async def scenario(database, statements):
        plan, counts = await import_jobs(database.engine, REAL)
        assert (counts.inserted, counts.updated, counts.unchanged) == (3, 0, 0)
        rows = await real_rows(database)
        assert {row["external_id"] for row in rows} == {"4000000001", "4000000002", "4000000003"}
        assert all(not row["is_sample"] and row["is_active"] and row["source"] == "linkedin" for row in rows)
        assert all(row["source_url"].startswith("https://") and row["retrieved_at"] == RETRIEVED for row in rows)
        before = await snapshot(database)
        statements.clear()
        _, again = await import_jobs(database.engine, REAL)
        assert (again.inserted, again.updated, again.unchanged) == (0, 0, 3)
        assert writes(statements) == [] and await snapshot(database) == before
    run(db_url, scenario)


def test_a_changed_listing_updates_exactly_that_row(db_url):
    async def scenario(database, statements):
        await import_jobs(database.engine, REAL)
        before = {row["external_id"]: row for row in await real_rows(database)}
        changed = [replace(REAL[0], title="Senior Backend Developer"), *REAL[1:]]
        statements.clear()
        _, counts = await import_jobs(database.engine, changed)
        assert (counts.inserted, counts.updated, counts.unchanged) == (0, 1, 2)
        after = {row["external_id"]: row for row in await real_rows(database)}
        assert after["4000000001"]["title"] == "Senior Backend Developer" and after["4000000001"]["id"] == before["4000000001"]["id"]
        assert after["4000000001"]["updated_at"] > before["4000000001"]["updated_at"]
        assert after["4000000002"] == before["4000000002"] and after["4000000003"] == before["4000000003"]
        assert len([s for s in writes(statements) if s.lstrip().upper().startswith("UPDATE")]) == 1
    run(db_url, scenario)


def test_a_listing_missing_from_a_later_file_is_kept_unchanged(db_url):
    async def scenario(database, _):
        await import_jobs(database.engine, REAL)
        before = {row["external_id"]: row for row in await real_rows(database)}
        _, counts = await import_jobs(database.engine, REAL[:1])
        assert (counts.inserted, counts.updated, counts.unchanged) == (0, 0, 1)
        assert {row["external_id"]: row for row in await real_rows(database)} == before  # nothing retired or deleted
    run(db_url, scenario)


def test_check_import_reads_only(db_url):
    async def scenario(database, statements):
        await import_jobs(database.engine, REAL[:1])
        statements.clear()
        plan = await check_import(database.engine, REAL)
        assert (len(plan.inserts), len(plan.updates), plan.unchanged) == (2, 0, 1)
        assert writes(statements) == [] and not any("pg_advisory" in s for s in statements)
        assert len(await real_rows(database)) == 1
    run(db_url, scenario)


# ---- the sample seed never touches real rows ----------------------------------------------------------------------------------
def test_the_sample_seed_neither_retires_nor_modifies_real_rows(db_url):
    async def scenario(database, statements):
        await run_seed(database.engine, CATALOGUE)
        await import_jobs(database.engine, REAL)
        real_before = await real_rows(database)
        plan = await check_catalogue(database.engine, CATALOGUE)
        assert (len(plan.inserts), len(plan.updates), len(plan.reactivations), len(plan.retirements), plan.unchanged) == (0, 0, 0, 0, 24)
        statements.clear()
        _, counts = await run_seed(database.engine, CATALOGUE)
        assert counts.changes == 0 and counts.unchanged == 24 and writes(statements) == []
        assert await real_rows(database) == real_before
    run(db_url, scenario)


def test_the_seed_still_retires_a_dropped_sample_and_only_that_sample(db_url):
    async def scenario(database, _):
        await run_seed(database.engine, CATALOGUE)
        await import_jobs(database.engine, REAL)
        real_before = await real_rows(database)
        _, counts = await seed_catalogue(database.engine, list(CATALOGUE)[:-1])
        assert (counts.retired, counts.unchanged) == (1, 23)
        rows = await snapshot(database)
        assert [row["slug"] for row in rows if not row["is_active"]] == [CATALOGUE[-1].slug]
        assert await real_rows(database) == real_before
    run(db_url, scenario)


# ---- read path (Part C evidence) ----------------------------------------------------------------------------------------------
def test_real_rows_reach_the_matcher_and_the_response_builder_without_internal_fields(db_url):
    async def scenario(database, _):
        await run_seed(database.engine, CATALOGUE)
        await import_jobs(database.engine, REAL)
        active = await load_active_jobs(database.sessions)
        assert len(active) == 27
        real = {item.job.id: item for item in active if not item.is_sample}
        assert len(real) == 3
        profile = CandidateProfile(target_role="Backend developer", technical_skills=("Django", "PostgreSQL"), preferred_location="Pune")
        outcome = match_jobs(profile, [item.job for item in active], limit=50)
        body = _response(outcome, {item.job.id: item.is_sample for item in active}).model_dump_json()
        matched_real = [m for m in outcome.matches if m.job.id in real]
        assert matched_real and '"is_sample":false' in body
        for leaked in ("linkedin", "source_url", '"source"', '"slug"', "4000000001", "retrieved_at"):
            assert leaked not in body, leaked
    run(db_url, scenario)


# ---- downgrade safety ---------------------------------------------------------------------------------------------------------
def test_the_downgrade_refuses_while_real_rows_exist_and_round_trips_when_only_samples_remain(db_url):
    engine = create_engine(db_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(insert(Job).values(**real_row(external_id="keep-me")))
        with pytest.raises(ProgrammingError, match="Downgrade refused"):
            with engine.begin() as connection:
                migration(connection, "0002_job_catalogue", downgrade=True)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM jobs WHERE source = 'linkedin'")) == 1
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003_real_job_sources"
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM jobs WHERE source IS NOT NULL"))
            migration(connection, "0002_job_catalogue", downgrade=True)
            columns = {row[0] for row in connection.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'jobs' AND table_schema = current_schema()"))}
            assert "source" not in columns and "source_url" not in columns
        with pytest.raises(IntegrityError, match="job_is_sample"):
            with engine.begin() as connection:
                values = {k: v for k, v in real_row().items() if k not in ("source", "external_id", "source_url", "retrieved_at", "posted_at")}
                connection.execute(text(
                    "INSERT INTO jobs (id, slug, title, company, location_label, description, employment_type, role_key, city_key,"
                    " work_mode, required_skills, is_sample) VALUES (:id, :slug, :title, :company, :location_label, :description,"
                    " :employment_type, :role_key, :city_key, :work_mode, :required_skills, false)"), values)
        with engine.begin() as connection:
            migration(connection, "head")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003_real_job_sources"
    finally:
        engine.dispose()
