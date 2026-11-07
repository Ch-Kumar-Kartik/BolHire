"""The catalogue seed against real PostgreSQL. Every database here is the fixture's isolated, uniquely named test schema:
no test uses, or can reach, an application or demo database. Statements are captured to prove what was (and was not) issued."""

import asyncio
import re
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import event, func, insert, select

from app.db import job_catalogue_seed as seed
from app.db.config import DatabaseSettings
from app.db.database import Database
from app.db.job_catalogue_seed import CatalogueInvalidError, check_catalogue, run_seed, seed_catalogue
from app.db.models import Candidate, Job, JobInterest
from app.jobs.catalogue_data import CATALOGUE, slugify
from conftest import migration
from scripts import seed_job_catalogue as script

pytestmark = pytest.mark.integration
JOBS = list(CATALOGUE)
COLUMNS = list(Job.__table__.columns)
WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.I)


def run(db_url, scenario):
    async def main():
        database = Database(DatabaseSettings(True, db_url))
        statements: list[str] = []

        @event.listens_for(database.engine.sync_engine, "before_cursor_execute")
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        try:
            return await scenario(database, statements)
        finally:
            await database.close()
    return asyncio.run(main())


async def snapshot(database):
    async with database.engine.connect() as connection:
        rows = (await connection.execute(select(*COLUMNS).order_by(Job.slug))).all()
    return [dict(zip([c.name for c in COLUMNS], row)) for row in rows]


def by_slug(rows):
    return {row["slug"]: row for row in rows}


async def count(database, model):
    async with database.engine.connect() as connection:
        return await connection.scalar(select(func.count()).select_from(model))


def writes(statements):
    return [s for s in statements if WRITE.match(s)]


# ---- idempotency --------------------------------------------------------------------------------------------------------------------------------
def test_first_apply_inserts_24_and_a_second_apply_changes_nothing(db_url):
    async def scenario(database, statements):
        _, first = await run_seed(database.engine, JOBS)
        assert first == seed.SeedCounts(inserted=24)
        before = await snapshot(database)
        assert len(before) == 24 and len({row["id"] for row in before}) == 24
        assert all(row["is_sample"] is True and row["is_active"] is True for row in before)
        stored = by_slug(before)
        assert set(stored) == {job.slug for job in JOBS}
        for job in JOBS:  # compared by slug: PostgreSQL's collation order is not Python's
            row = stored[job.slug]
            assert row["required_skills"] == list(job.required_skills)  # skill order is preserved
            assert row["created_at"] == row["updated_at"]

        del statements[:]
        _, second = await run_seed(database.engine, JOBS)
        assert second == seed.SeedCounts(unchanged=24) and second.changes == 0
        assert await snapshot(database) == before  # every column, including updated_at and created_at
        assert writes(statements) == []  # no INSERT, UPDATE or DELETE was even issued
        assert any("pg_advisory_xact_lock" in s for s in statements)  # the lock is taken, nothing else is written
    run(db_url, scenario)


def test_the_seed_never_issues_a_delete_or_touches_job_interests(db_url):
    async def scenario(database, statements):
        await run_seed(database.engine, JOBS)
        await seed_catalogue(database.engine, JOBS[:-1])  # retirement
        await seed_catalogue(database.engine, JOBS)  # reactivation
        assert not [s for s in statements if re.match(r"^\s*DELETE\b", s, re.I)]
        assert not [s for s in statements if "job_interests" in s]
    run(db_url, scenario)


# ---- update, retire, reactivate ---------------------------------------------------------------------------------------------------------------
def test_changing_one_job_updates_exactly_that_row(db_url):
    async def scenario(database, statements):
        await run_seed(database.engine, JOBS)
        before = by_slug(await snapshot(database))
        changed = list(JOBS)
        changed[3] = replace(JOBS[3], min_experience_years=2)  # the slug is unaffected: same title and company
        _, counts = await run_seed(database.engine, changed)
        assert counts == seed.SeedCounts(updated=1, unchanged=23)
        after = by_slug(await snapshot(database))
        slug = JOBS[3].slug
        assert after[slug]["min_experience_years"] == 2 and after[slug]["updated_at"] > before[slug]["updated_at"]
        assert after[slug]["id"] == before[slug]["id"] and after[slug]["created_at"] == before[slug]["created_at"]
        assert {k: v for k, v in after[slug].items() if k not in {"min_experience_years", "updated_at"}} == \
               {k: v for k, v in before[slug].items() if k not in {"min_experience_years", "updated_at"}}
        assert {s: r for s, r in after.items() if s != slug} == {s: r for s, r in before.items() if s != slug}
    run(db_url, scenario)


def test_a_job_dropped_from_the_catalogue_is_retired_and_comes_back_with_the_same_id(db_url):
    async def scenario(database, statements):
        await run_seed(database.engine, JOBS)
        before = by_slug(await snapshot(database))
        dropped = JOBS[-1]
        _, counts = await seed_catalogue(database.engine, JOBS[:-1])
        assert counts == seed.SeedCounts(retired=1, unchanged=23)
        retired = by_slug(await snapshot(database))
        assert len(retired) == 24 and retired[dropped.slug]["is_active"] is False  # the row still exists
        assert retired[dropped.slug]["id"] == before[dropped.slug]["id"]
        assert retired[dropped.slug]["updated_at"] > before[dropped.slug]["updated_at"]
        assert retired[dropped.slug]["created_at"] == before[dropped.slug]["created_at"]

        _, counts = await seed_catalogue(database.engine, JOBS[:-1])  # a second retirement run changes nothing
        assert counts == seed.SeedCounts(unchanged=23)

        _, counts = await seed_catalogue(database.engine, JOBS)
        assert counts == seed.SeedCounts(reactivated=1, unchanged=23)
        back = by_slug(await snapshot(database))
        assert back[dropped.slug]["is_active"] is True and back[dropped.slug]["id"] == before[dropped.slug]["id"]
        assert {k: v for k, v in back[dropped.slug].items() if k not in {"updated_at"}} == \
               {k: v for k, v in before[dropped.slug].items() if k not in {"updated_at"}}

        _, counts = await run_seed(database.engine, JOBS)
        assert counts == seed.SeedCounts(unchanged=24)
    run(db_url, scenario)


def test_a_job_interest_survives_its_job_being_retired_and_reactivated(db_url):
    async def scenario(database, statements):
        await run_seed(database.engine, JOBS)
        target = JOBS[0]
        async with database.engine.begin() as connection:
            candidate_id = await connection.scalar(insert(Candidate).values(recovery_secret_hash=uuid4().bytes * 2).returning(Candidate.id))
            job_id = await connection.scalar(select(Job.id).where(Job.slug == target.slug))
            await connection.execute(insert(JobInterest).values(candidate_id=candidate_id, job_id=job_id, consent_version="job-interest-v1:en-IN"))
        issued_by_the_seed: list[str] = []  # only the seed's own statements, not the test's setup or verification queries
        mark = len(statements)
        await seed_catalogue(database.engine, JOBS[1:])  # retires the job the candidate is interested in
        issued_by_the_seed += statements[mark:]
        assert await count(database, JobInterest) == 1
        assert by_slug(await snapshot(database))[target.slug]["is_active"] is False
        mark = len(statements)
        await seed_catalogue(database.engine, JOBS)
        issued_by_the_seed += statements[mark:]
        assert await count(database, JobInterest) == 1 and await count(database, Job) == 24
        assert issued_by_the_seed and not [s for s in issued_by_the_seed if "job_interests" in s]
    run(db_url, scenario)


# ---- read-only check ----------------------------------------------------------------------------------------------------------------------------
def test_check_reads_only_and_takes_no_lock(db_url):
    async def scenario(database, statements):
        empty_plan = await check_catalogue(database.engine, JOBS)
        assert empty_plan.counts() == seed.SeedCounts(inserted=24) and await count(database, Job) == 0

        await run_seed(database.engine, JOBS)
        before = await snapshot(database)
        changed = list(JOBS[1:])  # one retirement planned
        changed[0] = replace(changed[0], min_experience_years=3)  # and one update
        del statements[:]
        plan = await check_catalogue(database.engine, changed)
        assert plan.counts() == seed.SeedCounts(updated=1, retired=1, unchanged=22)
        assert await snapshot(database) == before  # nothing changed
        assert writes(statements) == []
        assert any("SET TRANSACTION READ ONLY" in s for s in statements)
        assert not any("pg_advisory" in s for s in statements)
    run(db_url, scenario)


# ---- failure behaviour ----------------------------------------------------------------------------------------------------------------------------
def test_an_invalid_catalogue_writes_nothing_and_never_touches_the_database(db_url):
    async def scenario(database, statements):
        for bad in (JOBS[:19], [replace(JOBS[0], is_sample=False)] + JOBS[1:], [replace(JOBS[0], required_skills=("python", "PYTHON", "git"))] + JOBS[1:]):
            with pytest.raises(CatalogueInvalidError) as error:
                await run_seed(database.engine, bad)
            assert error.value.errors
        assert statements == []  # not even a SELECT: validation precedes all database access
        assert await count(database, Job) == 0
    run(db_url, scenario)


def test_a_failure_in_the_middle_of_the_apply_rolls_everything_back(db_url, monkeypatch):
    async def scenario(database, statements):
        await run_seed(database.engine, JOBS)
        before = await snapshot(database)
        new_job = replace(JOBS[0], title="Extra Sample Developer", slug=slugify("Extra Sample Developer", JOBS[0].company))
        modified = [replace(JOBS[0], min_experience_years=3)] + JOBS[2:] + [new_job]  # one update, one retirement (JOBS[1]), one insert

        real_retire = seed._retire

        async def failing(connection, job_ids):
            raise RuntimeError("injected failure after the insert and update statements ran")

        monkeypatch.setattr(seed, "_retire", failing)
        with pytest.raises(RuntimeError, match="injected"):
            await seed_catalogue(database.engine, modified)
        assert await snapshot(database) == before  # the insert and the update were rolled back too
        assert await count(database, Job) == 24

        monkeypatch.setattr(seed, "_retire", real_retire)  # the same catalogue applies cleanly, so the test did reach every step
        _, counts = await seed_catalogue(database.engine, modified)
        assert counts == seed.SeedCounts(inserted=1, updated=1, retired=1, unchanged=22)
    run(db_url, scenario)


def test_two_concurrent_seeders_produce_one_catalogue_without_errors(db_url):
    async def scenario(database, statements):
        (_, a), (_, b) = await asyncio.gather(run_seed(database.engine, JOBS), run_seed(database.engine, JOBS))
        assert sorted([a.inserted, b.inserted]) == [0, 24] and sorted([a.unchanged, b.unchanged]) == [0, 24]
        assert await count(database, Job) == 24
    run(db_url, scenario)


# ---- the CLI end to end, against the isolated schema only -----------------------------------------------------------------------------
@pytest.fixture
def isolated_cli(db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", db_url.render_as_string(hide_password=False))
    return db_url


def test_the_cli_checks_then_seeds_then_reports_no_changes(isolated_cli, capsys):
    async def state(database, statements):
        return await snapshot(database)

    assert script.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "Target: host=127.0.0.1" in out and "24 inserted" in out and "nothing was written" in out
    assert isolated_cli.password not in out
    assert run(isolated_cli, state) == []  # --check wrote nothing

    assert script.main(["--run", "--confirm-seed"]) == 0
    assert "Seeded: 24 inserted, 0 updated, 0 reactivated, 0 retired, 0 unchanged." in capsys.readouterr().out
    first = run(isolated_cli, state)
    assert len(first) == 24

    assert script.main(["--check"]) == 0
    assert "0 inserted, 0 updated, 0 reactivated, 0 retired, 24 unchanged" in capsys.readouterr().out
    assert script.main(["--run", "--confirm-seed"]) == 0
    assert "Seeded: 0 inserted, 0 updated, 0 reactivated, 0 retired, 24 unchanged." in capsys.readouterr().out
    assert run(isolated_cli, state) == first  # identical, including updated_at


def test_the_cli_reports_a_missing_jobs_table_and_creates_nothing(isolated_cli, capsys):
    from sqlalchemy import create_engine, inspect

    engine = create_engine(isolated_cli, hide_parameters=True)
    try:
        with engine.begin() as connection:
            migration(connection, "0001_candidate_foundation", downgrade=True)  # drops jobs and job_interests in the isolated schema only
        assert script.main(["--check"]) == 2
        assert "alembic upgrade head" in capsys.readouterr().out
        assert script.main(["--run", "--confirm-seed"]) == 2
        assert "alembic upgrade head" in capsys.readouterr().out
        with engine.connect() as connection:
            assert "jobs" not in inspect(connection).get_table_names()  # the script never creates schema
    finally:
        engine.dispose()
