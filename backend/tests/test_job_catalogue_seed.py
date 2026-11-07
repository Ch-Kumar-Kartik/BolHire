"""plan_sync (pure) and the seed CLI's argument handling. No database is used or reachable here."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from app.db import job_catalogue_seed as seed
from app.db.job_catalogue_seed import CONTENT_FIELDS, CatalogueInvalidError, ExistingJob, JobsTableMissingError, Plan, SeedCounts, plan_sync
from app.jobs.catalogue_data import CATALOGUE, LOCATION_LABELS
from scripts import seed_job_catalogue as script

JOBS = list(CATALOGUE)


def stored(jobs=JOBS):
    return [ExistingJob(uuid4(), job) for job in jobs]


# ---- plan_sync ------------------------------------------------------------------------------------------------------------------------------------
def test_an_empty_database_plans_24_inserts_and_nothing_else():
    plan = plan_sync([], JOBS)
    assert plan.counts() == SeedCounts(inserted=24, updated=0, reactivated=0, retired=0, unchanged=0)
    assert [job.slug for job in plan.inserts] == [job.slug for job in JOBS]


def test_identical_rows_plan_nothing():
    plan = plan_sync(stored(), JOBS)
    assert plan == Plan(unchanged=24)
    assert plan.counts().changes == 0


@pytest.mark.parametrize("field", [f for f in CONTENT_FIELDS if f != "is_sample"])
def test_one_changed_content_field_plans_exactly_one_update(field):
    rows = stored()
    changed = {
        "title": "Another Title", "company": "Another Company", "location_label": "Elsewhere", "description": "Another description.",
        "employment_type": "contract", "role_key": "qa-engineer", "city_key": "delhi", "work_mode": "onsite",
        "min_experience_years": 9, "max_experience_years": 60, "required_skills": ("git", "docker", "sql"),
    }[field]
    target = 5
    if getattr(rows[target].job, field) == changed:
        changed = None if changed is not None else "x"
    rows[target] = ExistingJob(rows[target].id, replace(rows[target].job, **{field: changed}))
    plan = plan_sync(rows, JOBS)
    assert plan.counts() == SeedCounts(inserted=0, updated=1, reactivated=0, retired=0, unchanged=23)
    assert plan.updates[0][0] == rows[target].id and plan.updates[0][1] == JOBS[target]


def test_a_stored_job_missing_from_the_catalogue_is_retired_not_deleted():
    stray = ExistingJob(uuid4(), replace(JOBS[0], slug="old-listing-gone", title="Old Listing", company="Gone Co"))
    plan = plan_sync(stored() + [stray], JOBS)
    assert plan.retirements == (stray.id,) and plan.counts() == SeedCounts(0, 0, 0, 1, 24)
    assert not hasattr(plan, "deletions") and not hasattr(plan, "deletes")


def test_an_already_retired_stray_row_is_left_alone_and_not_counted():
    stray = ExistingJob(uuid4(), replace(JOBS[0], slug="old-listing-gone", is_active=False))
    plan = plan_sync(stored() + [stray], JOBS)
    assert plan.counts() == SeedCounts(unchanged=24)


def test_a_previously_retired_slug_that_is_back_in_the_catalogue_is_reactivated():
    rows = stored()
    rows[3] = ExistingJob(rows[3].id, replace(rows[3].job, is_active=False))
    plan = plan_sync(rows, JOBS)
    assert plan.counts() == SeedCounts(inserted=0, updated=0, reactivated=1, retired=0, unchanged=23)
    assert plan.reactivations == ((rows[3].id, JOBS[3]),)  # the SAME id comes back


def test_a_retired_row_whose_content_also_differs_is_reactivated_and_refreshed_in_one_write():
    rows = stored()
    rows[3] = ExistingJob(rows[3].id, replace(rows[3].job, is_active=False, title="Stale Title"))
    plan = plan_sync(rows, JOBS)
    assert plan.counts() == SeedCounts(0, 0, 1, 0, 23) and plan.reactivations[0][1].title == JOBS[3].title


def test_a_reordered_skill_list_is_a_change():
    rows = stored()
    rows[7] = ExistingJob(rows[7].id, replace(rows[7].job, required_skills=tuple(reversed(rows[7].job.required_skills))))
    assert plan_sync(rows, JOBS).counts().updated == 1


def test_integer_and_float_experience_values_compare_equal():
    rows = stored()
    rows[0] = ExistingJob(rows[0].id, replace(rows[0].job, min_experience_years=float(JOBS[0].min_experience_years)))
    assert plan_sync(rows, JOBS).counts().unchanged == 24


def test_the_planner_never_plans_a_change_to_identity_or_creation_time():
    job = JOBS[0]
    assert set(seed._row_values(job)) == set(CONTENT_FIELDS)
    assert not {"id", "created_at", "slug", "updated_at", "is_active"} & set(seed._row_values(job))
    plan = plan_sync(stored(), [replace(job, min_experience_years=4)] + JOBS[1:])
    (row_id, desired), = plan.updates
    assert isinstance(row_id, UUID) and not hasattr(desired, "id") and not hasattr(desired, "created_at")


def test_mixed_changes_are_counted_independently():
    rows = stored()
    rows[0] = ExistingJob(rows[0].id, replace(rows[0].job, title="Stale"))
    rows[1] = ExistingJob(rows[1].id, replace(rows[1].job, is_active=False))
    rows.append(ExistingJob(uuid4(), replace(JOBS[2], slug="orphan-listing")))
    # The catalogue now has 23 jobs: the 24th stored job and the orphan are both outside it and active, so both are retired.
    plan = plan_sync(rows, JOBS[:-1])
    assert plan.counts() == SeedCounts(inserted=0, updated=1, reactivated=1, retired=2, unchanged=21)


def test_duplicate_slugs_are_refused_on_either_side():
    with pytest.raises(ValueError):
        plan_sync([], JOBS + [JOBS[0]])
    rows = stored()
    with pytest.raises(ValueError):
        plan_sync(rows + [rows[0]], JOBS)


def test_plan_sync_accepts_generators_and_is_pure():
    rows = stored()
    before = list(rows)
    assert plan_sync((r for r in rows), (j for j in JOBS)) == plan_sync(rows, JOBS) == Plan(unchanged=24)
    assert rows == before


def test_validation_precedes_database_access():
    class Boom:
        def begin(self):
            raise AssertionError("the database must not be touched")
        connect = begin

    import asyncio
    with pytest.raises(CatalogueInvalidError) as error:
        asyncio.run(seed.run_seed(Boom(), JOBS[:19]))
    assert any("between 20 and 30 jobs" in e for e in error.value.errors)


# ---- CLI argument handling and failure reporting (no database) ------------------------------------------------------------------------------------
URL = "postgresql+psycopg://seeduser:s3cret-pass@db.example.invalid:5432/jobsdb"


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.delenv("DATABASE_ENABLED", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", URL)


def test_no_arguments_prints_usage_and_exits_nonzero(disabled, capsys):
    with pytest.raises(SystemExit) as exit_:
        script.main([])
    assert exit_.value.code == 2 and "usage" in capsys.readouterr().err.lower()


def test_check_and_run_together_are_refused(disabled, capsys):
    with pytest.raises(SystemExit) as exit_:
        script.main(["--check", "--run"])
    assert exit_.value.code == 2 and "not allowed" in capsys.readouterr().err


def test_run_without_confirmation_is_refused_before_anything_else(enabled, monkeypatch, capsys):
    monkeypatch.setattr(script, "Database", lambda *_: pytest.fail("no database object may be created"))
    assert script.main(["--run"]) == 1
    assert "--confirm-seed" in capsys.readouterr().out


def test_check_with_the_database_disabled_validates_only(disabled, monkeypatch, capsys):
    monkeypatch.setattr(script, "Database", lambda *_: pytest.fail("no database object may be created"))
    assert script.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "Catalogue: PASS" in out and "validation only; database comparison skipped" in out


def test_run_with_the_database_disabled_is_refused(disabled, capsys):
    assert script.main(["--run", "--confirm-seed"]) == 1
    assert "DATABASE_ENABLED must be 1" in capsys.readouterr().out


def test_an_invalid_catalogue_exits_nonzero_before_any_database_access(enabled, monkeypatch, capsys):
    monkeypatch.setattr(script, "CATALOGUE", tuple(JOBS[:19]))
    monkeypatch.setattr(script, "Database", lambda *_: pytest.fail("no database object may be created"))
    for argv in (["--check"], ["--run", "--confirm-seed"]):
        assert script.main(argv) == 1
    out = capsys.readouterr().out
    assert "validation errors; no database access was made" in out and "between 20 and 30 jobs" in out


def test_a_malformed_database_url_is_a_preflight_failure(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", "not a url")
    assert script.main(["--check"]) == 1
    assert "Preflight FAIL" in capsys.readouterr().out


def test_check_prints_the_target_and_the_plan_but_never_the_password(enabled, monkeypatch, capsys):
    async def fake_check(settings):
        return Plan(inserts=tuple(JOBS), unchanged=0)

    monkeypatch.setattr(script, "_check", fake_check)
    assert script.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "host=db.example.invalid port=5432 database=jobsdb" in out and "nothing was written" in out
    assert "24 inserted" in out and "s3cret-pass" not in out and "seeduser" not in out


def test_a_missing_jobs_table_says_to_migrate_and_exits_nonzero(enabled, monkeypatch, capsys):
    async def missing(settings):
        raise JobsTableMissingError

    monkeypatch.setattr(script, "_check", missing)
    assert script.main(["--check"]) == 2
    assert "alembic upgrade head" in capsys.readouterr().out


def test_run_reports_counts_on_success(enabled, monkeypatch, capsys):
    async def fake_run(settings):
        return SeedCounts(inserted=24)

    monkeypatch.setattr(script, "_run", fake_run)
    assert script.main(["--run", "--confirm-seed"]) == 0
    out = capsys.readouterr().out
    assert "Seeded: 24 inserted, 0 updated, 0 reactivated, 0 retired, 0 unchanged." in out and "s3cret-pass" not in out


@pytest.mark.parametrize("error, expected", [
    (script.SeedStageError("connecting", "DatabaseUnavailableError"), "Seed FAIL during connecting: DatabaseUnavailableError"),
    (script.SeedStageError("seeding", "OperationalError"), "Seed FAIL during seeding: OperationalError"),
    (RuntimeError(f"could not connect to {URL}"), "Seed FAIL: unexpected RuntimeError"),
])
def test_failures_report_the_stage_and_class_but_never_the_message(enabled, monkeypatch, capsys, error, expected):
    async def failing(settings):
        raise error

    monkeypatch.setattr(script, "_run", failing)
    assert script.main(["--run", "--confirm-seed"]) == 2
    out = capsys.readouterr().out
    assert expected in out and "s3cret-pass" not in out and "could not connect" not in out


def test_the_cli_validates_before_reading_the_environment_for_a_database(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_ENABLED", "maybe")  # an invalid setting must not hide a catalogue error, nor be reached when invalid
    monkeypatch.setattr(script, "CATALOGUE", tuple(JOBS[:19]))
    assert script.main(["--check"]) == 1
    assert "validation errors" in capsys.readouterr().out
