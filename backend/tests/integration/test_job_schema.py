"""Behaviour of the Task 10 `jobs` and `job_interests` constraints on real PostgreSQL (isolated schema per test, real migrations).

Every rejecting case asserts WHICH constraint fired (PostgreSQL's diag.constraint_name), so a test cannot pass because some other
check caught the row. A few tests document what the database deliberately does NOT enforce, so T2's seed validation is not
assumed to be covered here.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, insert, select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import Candidate, Job, JobInterest
from test_auth_repository import expire_profile, identity, run

pytestmark = pytest.mark.integration

VERSION = "job-interest-v1:en-IN"
NAN, INF = float("nan"), float("inf")


@pytest.fixture
def engine(db_url):
    engine = create_engine(db_url, hide_parameters=True)
    yield engine
    engine.dispose()


def valid_job(**overrides):
    row = dict(slug=f"job-{uuid4().hex[:12]}", title="Backend Developer", company="Fictional Systems Pvt Ltd", location_label="Pune, Maharashtra",
               description="A sample listing for the demo.", employment_type="full_time", role_key="backend-developer", city_key="pune",
               work_mode="hybrid", required_skills=["Python", "SQL"])
    return row | overrides


def outcome(engine, statement):
    """None when the statement is accepted; otherwise the violated constraint name, `not-null:<column>`, or the error class."""
    try:
        with engine.begin() as connection:
            connection.execute(statement)
    except DBAPIError as error:
        diag = error.orig.diag
        return diag.constraint_name or (f"not-null:{diag.column_name}" if diag.column_name else type(error.orig).__name__)
    return None


def add_job(engine, **overrides):
    with engine.begin() as connection:
        return connection.scalar(insert(Job).values(**valid_job(**overrides)).returning(Job.id))


def add_candidate(engine):
    with engine.begin() as connection:
        return connection.scalar(insert(Candidate).values(recovery_secret_hash=uuid4().bytes * 2).returning(Candidate.id))


def interest(candidate_id, job_id, version=VERSION):
    return insert(JobInterest).values(candidate_id=candidate_id, job_id=job_id, consent_version=version)


def count(engine, model):
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(model))


# ---- jobs: every CHECK has a rejecting case ---------------------------------------------------------------------------------------------------------
REJECTED = [
    ("blank title", dict(title=""), "job_title_nonempty"),
    ("spaces-only title", dict(title="   "), "job_title_nonempty"),
    ("blank company", dict(company=""), "job_company_nonempty"),
    ("spaces-only company", dict(company="   "), "job_company_nonempty"),
    ("blank location_label", dict(location_label=""), "job_location_label_nonempty"),
    ("spaces-only location_label", dict(location_label="   "), "job_location_label_nonempty"),
    ("blank description", dict(description=""), "job_description_nonempty"),
    ("spaces-only description", dict(description="   "), "job_description_nonempty"),
    ("4001-character description", dict(description="x" * 4001), "job_description_length"),
    ("uppercase slug", dict(slug="Backend-Dev"), "job_slug_format"),
    ("slug with a space", dict(slug="backend dev"), "job_slug_format"),
    ("slug with an underscore", dict(slug="backend_dev"), "job_slug_format"),
    ("slug with a leading hyphen", dict(slug="-backend"), "job_slug_format"),
    ("slug with a trailing hyphen", dict(slug="backend-"), "job_slug_format"),
    ("slug with a double hyphen", dict(slug="backend--dev"), "job_slug_format"),
    ("empty slug", dict(slug=""), "job_slug_format"),
    ("slug with a trailing newline", dict(slug="backend\n"), "job_slug_format"),
    ("slug with a non-ASCII letter", dict(slug="bäckend"), "job_slug_format"),
    ("role_key in the wrong format", dict(role_key="Backend Developer"), "job_role_key_format"),
    ("empty role_key", dict(role_key=""), "job_role_key_format"),
    ("NULL role_key", dict(role_key=None), "not-null:role_key"),
    ("city_key with a space", dict(city_key="new delhi"), "job_city_key_format"),
    ("city_key with an underscore", dict(city_key="new_delhi"), "job_city_key_format"),
    ("city_key with uppercase", dict(city_key="Pune"), "job_city_key_format"),
    ("unknown work_mode", dict(work_mode="office"), "job_work_mode"),
    ("work_mode in the wrong case", dict(work_mode="Remote"), "job_work_mode"),
    ("unknown employment_type", dict(employment_type="freelance"), "job_employment_type"),
    ("min experience negative", dict(min_experience_years=-1), "job_min_years_range"),
    ("min experience above 80", dict(min_experience_years=81), "job_min_years_range"),
    ("min experience NaN", dict(min_experience_years=NAN), "job_min_years_range"),
    ("min experience Infinity", dict(min_experience_years=INF), "job_min_years_range"),
    ("min experience -Infinity", dict(min_experience_years=-INF), "job_min_years_range"),
    ("max experience negative", dict(max_experience_years=-1), "job_max_years_range"),
    ("max experience above 80", dict(max_experience_years=81), "job_max_years_range"),
    ("max experience NaN", dict(max_experience_years=NAN), "job_max_years_range"),
    ("max experience Infinity", dict(max_experience_years=INF), "job_max_years_range"),
    ("min greater than max", dict(min_experience_years=5, max_experience_years=2), "job_years_order"),
    ("empty skills array", dict(required_skills=[]), "job_skills_required"),
    ("NULL skills", dict(required_skills=None), "not-null:required_skills"),
    ("blank skill element", dict(required_skills=["Python", ""]), "job_skills_structure"),
    ("spaces-only skill element", dict(required_skills=["Python", "   "]), "job_skills_structure"),
    ("NULL skill element", dict(required_skills=["Python", None]), "job_skills_structure"),
    ("duplicate skill element", dict(required_skills=["Python", "Python"]), "job_skills_structure"),
    ("21 skills", dict(required_skills=[f"skill{i}" for i in range(21)]), "job_skills_structure"),
    ("61-character skill", dict(required_skills=["x" * 61]), "job_skills_structure"),
    ("hybrid job without a city", dict(work_mode="hybrid", city_key=None), "job_city_required"),
    ("onsite job without a city", dict(work_mode="onsite", city_key=None), "job_city_required"),
    ("is_sample false", dict(is_sample=False), "job_is_sample"),
    ("NULL is_sample", dict(is_sample=None), "not-null:is_sample"),
    ("updated_at before created_at", dict(created_at=datetime(2026, 1, 2, tzinfo=timezone.utc), updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc)), "job_timestamps"),
    ("121-character title", dict(title="x" * 121), "StringDataRightTruncation"),
]


@pytest.mark.parametrize("overrides, constraint", [pytest.param(o, c, id=i) for i, o, c in REJECTED])
def test_invalid_job_rows_are_rejected_by_the_intended_constraint(engine, overrides, constraint):
    assert outcome(engine, insert(Job).values(**valid_job(**overrides))) == constraint
    assert count(engine, Job) == 0  # nothing was stored


ACCEPTED = [
    ("description of exactly 4000 characters", dict(description="x" * 4000)),
    ("single-character slug", dict(slug="a")),
    ("slug with digits and single hyphens", dict(slug="backend-dev-2")),
    ("every employment type: part_time", dict(employment_type="part_time")),
    ("every employment type: contract", dict(employment_type="contract")),
    ("every employment type: internship", dict(employment_type="internship")),
    ("onsite job with a city", dict(work_mode="onsite", city_key="mumbai")),
    ("remote job without a city", dict(work_mode="remote", city_key=None)),
    ("remote job with a city", dict(work_mode="remote", city_key="pune")),
    ("both experience bounds zero", dict(min_experience_years=0, max_experience_years=0)),
    ("equal experience bounds", dict(min_experience_years=3, max_experience_years=3)),
    ("minimum only", dict(min_experience_years=2, max_experience_years=None)),
    ("maximum only", dict(min_experience_years=None, max_experience_years=5)),
    ("no experience bounds", dict(min_experience_years=None, max_experience_years=None)),
    ("fractional bounds at the limits", dict(min_experience_years=0.5, max_experience_years=80)),
    ("exactly 20 skills", dict(required_skills=[f"skill{i}" for i in range(20)])),
    ("a 60-character skill", dict(required_skills=["x" * 60])),
    ("Devanagari skill text", dict(required_skills=["पायथन"])),
    ("an inactive job", dict(is_active=False)),
    ("updated_at after created_at", dict(created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))),
    ("equal timestamps", dict(created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))),
]


@pytest.mark.parametrize("overrides", [pytest.param(o, id=i) for i, o in ACCEPTED])
def test_valid_job_rows_are_accepted(engine, overrides):
    assert outcome(engine, insert(Job).values(**valid_job(**overrides))) is None
    assert count(engine, Job) == 1


def test_a_duplicate_slug_is_rejected_but_a_different_slug_is_not(engine):
    add_job(engine, slug="same-slug")
    assert outcome(engine, insert(Job).values(**valid_job(slug="same-slug"))) == "jobs_slug_key"
    assert outcome(engine, insert(Job).values(**valid_job(slug="other-slug"))) is None
    assert count(engine, Job) == 2


def test_defaults_come_from_the_database(engine):
    values = valid_job()
    with engine.begin() as connection:
        connection.execute(insert(Job).values(**values))
        row = connection.execute(select(Job.is_sample, Job.is_active, Job.created_at, Job.updated_at)).one()
        db_now = connection.scalar(select(func.now()))
    assert row.is_sample is True and row.is_active is True  # sample data by default; listings start active
    assert row.created_at == row.updated_at and abs(row.created_at - db_now) < timedelta(seconds=30)


def read_job(engine, job_id):
    """The stored row's current values, or None if it does not exist."""
    with engine.connect() as connection:
        return connection.execute(select(Job.is_active, Job.is_sample, Job.work_mode, Job.city_key).where(Job.id == job_id)).one_or_none()


def test_a_job_can_be_retired(engine):
    job_id = add_job(engine)
    assert outcome(engine, text("UPDATE jobs SET is_active = false WHERE id = :id").bindparams(id=job_id)) is None
    retired = read_job(engine, job_id)
    assert retired is not None and retired.is_active is False  # retired, not deleted
    assert outcome(engine, text("UPDATE jobs SET is_active = true WHERE id = :id").bindparams(id=job_id)) is None
    reactivated = read_job(engine, job_id)
    assert reactivated is not None and reactivated.is_active is True


def test_a_stored_job_cannot_be_updated_to_not_sample(engine):
    job_id = add_job(engine)
    assert outcome(engine, text("UPDATE jobs SET is_sample = false WHERE id = :id").bindparams(id=job_id)) == "job_is_sample"
    row = read_job(engine, job_id)
    assert row is not None and row.is_sample is True  # the rejected update left the row unchanged


def test_a_stored_job_cannot_be_updated_to_onsite_without_a_city(engine):
    job_id = add_job(engine)
    assert outcome(engine, text("UPDATE jobs SET work_mode = 'onsite', city_key = NULL WHERE id = :id").bindparams(id=job_id)) == "job_city_required"
    row = read_job(engine, job_id)
    assert row is not None and (row.work_mode, row.city_key) == ("hybrid", "pune")  # unchanged by the rejected update


# ---- what the database deliberately does NOT enforce (T2 seed validation must) -----------------------------------------------------------------------
def test_documented_gap_case_variants_of_a_skill_are_not_duplicates_to_the_database(engine):
    """bolhire_valid_skills() compares exact bytes (COLLATE "C"), so `Python` and `python` both pass. The matching engine and the seed
    validator must treat them as one skill; do not assume the database prevents it."""
    assert outcome(engine, insert(Job).values(**valid_job(required_skills=["Python", "python"]))) is None


def test_documented_gap_btrim_strips_spaces_only_so_tab_only_text_passes(engine):
    """Same behaviour as the existing profile checks: btrim() removes spaces, not tabs or newlines."""
    assert outcome(engine, insert(Job).values(**valid_job(title="\t"))) is None


def test_documented_gap_the_database_does_not_check_that_a_company_is_fictional(engine):
    """Nothing here can know which companies are real. The seed data file and its validation own that rule (T2)."""
    assert outcome(engine, insert(Job).values(**valid_job(company="Any Name At All"))) is None


# ---- job_interests -----------------------------------------------------------------------------------------------------------------------------------
def test_a_valid_interest_is_stored_with_a_database_timestamp(engine):
    candidate_id, job_id = add_candidate(engine), add_job(engine)
    with engine.begin() as connection:
        connection.execute(interest(candidate_id, job_id))
        created = connection.scalar(select(JobInterest.created_at))
        db_now = connection.scalar(select(func.now()))
    assert abs(created - db_now) < timedelta(seconds=30)


def test_the_same_candidate_and_job_cannot_be_stored_twice_but_others_can(engine):
    a, b, job_id, other_job = add_candidate(engine), add_candidate(engine), add_job(engine), add_job(engine)
    assert outcome(engine, interest(a, job_id)) is None
    assert outcome(engine, interest(a, job_id)) == "job_interests_pkey"
    assert outcome(engine, interest(b, job_id)) is None  # another candidate, same job
    assert outcome(engine, interest(a, other_job)) is None  # same candidate, another job
    assert count(engine, JobInterest) == 3


@pytest.mark.parametrize("version, constraint", [("", "job_interest_consent_version"), ("   ", "job_interest_consent_version"), (None, "not-null:consent_version")])
def test_an_interest_needs_a_consent_version(engine, version, constraint):
    assert outcome(engine, interest(add_candidate(engine), add_job(engine), version)) == constraint
    assert count(engine, JobInterest) == 0


def test_an_interest_for_an_unknown_candidate_is_rejected_by_the_candidate_foreign_key(engine):
    job_id = add_job(engine)
    assert outcome(engine, interest(uuid4(), job_id)) == "job_interests_candidate_id_fkey"
    assert count(engine, JobInterest) == 0


def test_an_interest_for_an_unknown_job_is_rejected_by_the_job_foreign_key(engine):
    candidate_id = add_candidate(engine)
    assert outcome(engine, interest(candidate_id, uuid4())) == "job_interests_job_id_fkey"
    assert count(engine, JobInterest) == 0


def test_an_interest_without_a_candidate_is_rejected(engine):
    job_id = add_job(engine)
    assert outcome(engine, insert(JobInterest).values(candidate_id=None, job_id=job_id, consent_version=VERSION)) == "not-null:candidate_id"
    assert count(engine, JobInterest) == 0


def test_an_interest_without_a_job_is_rejected(engine):
    candidate_id = add_candidate(engine)
    assert outcome(engine, insert(JobInterest).values(candidate_id=candidate_id, job_id=None, consent_version=VERSION)) == "not-null:job_id"
    assert count(engine, JobInterest) == 0


def test_deleting_a_candidate_removes_their_interests_and_leaves_the_jobs(engine):
    a, b, job_id = add_candidate(engine), add_candidate(engine), add_job(engine)
    for candidate_id in (a, b):
        assert outcome(engine, interest(candidate_id, job_id)) is None
    with engine.begin() as connection:
        connection.execute(delete(Candidate).where(Candidate.id == a))
    with engine.connect() as connection:
        remaining = connection.scalars(select(JobInterest.candidate_id)).all()
    assert remaining == [b] and count(engine, Job) == 1


def test_a_job_with_interests_cannot_be_deleted_until_they_are_gone(engine):
    candidate_id, job_id = add_candidate(engine), add_job(engine)
    assert outcome(engine, interest(candidate_id, job_id)) is None
    assert outcome(engine, delete(Job).where(Job.id == job_id)) == "job_interests_job_id_fkey"  # RESTRICT
    assert count(engine, Job) == 1 and count(engine, JobInterest) == 1  # nothing was removed
    with engine.begin() as connection:
        connection.execute(delete(JobInterest).where(JobInterest.job_id == job_id))
    assert outcome(engine, delete(Job).where(Job.id == job_id)) is None
    assert count(engine, Job) == 0


def test_a_job_without_interests_can_be_deleted(engine):
    job_id = add_job(engine)
    assert outcome(engine, delete(Job).where(Job.id == job_id)) is None


def test_the_job_id_index_exists_and_the_primary_key_covers_candidate_lookups(engine):
    with engine.connect() as connection:
        indexes = {row[0]: row[1] for row in connection.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() AND tablename = 'job_interests'"))}
    assert "ix_job_interests_job_id" in indexes and "(job_id)" in indexes["ix_job_interests_job_id"]
    assert "(candidate_id, job_id)" in indexes["job_interests_pkey"]  # leading column candidate_id serves per-candidate listing


def test_cleanup_of_an_expired_identity_removes_its_interests_and_keeps_the_job(db_url):
    async def scenario(database, repo, auth):
        _, candidate_id = await identity(repo)
        async with database.sessions.begin() as session:
            job_id = await session.scalar(insert(Job).values(**valid_job()).returning(Job.id))
            await session.execute(interest(candidate_id, job_id))
        await expire_profile(database, candidate_id)
        assert (await auth.purge_expired()).candidates == 1
        async with database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(JobInterest)) == 0
            assert await session.scalar(select(func.count()).select_from(Job)) == 1
    run(db_url, scenario)
