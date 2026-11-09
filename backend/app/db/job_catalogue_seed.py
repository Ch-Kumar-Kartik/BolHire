"""Idempotent sync of the SAMPLE job catalogue into the `jobs` table.

Layers: `plan_sync` is a pure planner; `apply_plan` issues the writes (the caller owns the transaction); `seed_catalogue` and
`run_seed` read, plan and apply inside ONE transaction guarded by an advisory lock. Nothing here deletes a row, changes `id` or
`created_at`, or touches `job_interests`. `required_skills` order is part of the data: a reordered list counts as a change.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.models import Job
from app.jobs.catalogue_data import CatalogueJob
from app.jobs.catalogue_validation import validate_catalogue

LOCK_NAME = "bolhire.job_catalogue_seed"
_UNDEFINED_TABLE = "42P01"
CONTENT_FIELDS = (
    "title", "company", "location_label", "description", "employment_type", "role_key", "city_key", "work_mode",
    "min_experience_years", "max_experience_years", "required_skills", "is_sample",
)


class CatalogueInvalidError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(f"The job catalogue is invalid ({len(errors)} errors).")
        self.errors = errors


class JobsTableMissingError(Exception):
    """The `jobs` table does not exist: run `alembic upgrade head` first."""


class SeedError(Exception):
    """A write did not affect the rows it was planned for (the transaction is rolled back by the caller)."""


@dataclass(frozen=True)
class ExistingJob:
    id: UUID
    job: CatalogueJob  # its is_active reflects the database row


@dataclass(frozen=True)
class SeedCounts:
    inserted: int = 0
    updated: int = 0
    reactivated: int = 0
    retired: int = 0
    unchanged: int = 0

    @property
    def changes(self) -> int:
        return self.inserted + self.updated + self.reactivated + self.retired


@dataclass(frozen=True)
class Plan:
    inserts: tuple[CatalogueJob, ...] = ()
    updates: tuple[tuple[UUID, CatalogueJob], ...] = ()
    reactivations: tuple[tuple[UUID, CatalogueJob], ...] = ()
    retirements: tuple[UUID, ...] = ()
    unchanged: int = 0

    def counts(self) -> SeedCounts:
        return SeedCounts(len(self.inserts), len(self.updates), len(self.reactivations), len(self.retirements), self.unchanged)


def _comparable(value):
    if isinstance(value, (list, tuple)):
        return tuple(value)  # order-sensitive
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return value


def _content(job: CatalogueJob) -> tuple:
    return tuple(_comparable(getattr(job, field)) for field in CONTENT_FIELDS)


def _differs(existing: CatalogueJob, desired: CatalogueJob) -> bool:
    return _content(existing) != _content(desired)


def _needs_reactivation(existing: CatalogueJob) -> bool:
    return not existing.is_active


def plan_sync(existing_rows, catalogue) -> Plan:
    """Pure. For each catalogue job by slug: insert when absent; reactivate when the stored row is inactive (its content is
    refreshed in the same write); update when any content field differs; otherwise leave unchanged. Stored active rows whose slug is
    not in the catalogue are retired, never deleted. Stored inactive rows outside the catalogue are left alone and not counted."""
    existing_rows, catalogue = list(existing_rows), list(catalogue)
    stored = {row.job.slug: row for row in existing_rows}
    if len(stored) != len(existing_rows):
        raise ValueError("Stored job slugs must be unique.")
    desired_slugs = [job.slug for job in catalogue]
    if len(set(desired_slugs)) != len(desired_slugs):
        raise ValueError("Catalogue slugs must be unique.")
    inserts, updates, reactivations, unchanged = [], [], [], 0
    for job in catalogue:
        row = stored.get(job.slug)
        if row is None:
            inserts.append(job)
        elif _needs_reactivation(row.job):
            reactivations.append((row.id, job))
        elif _differs(row.job, job):
            updates.append((row.id, job))
        else:
            unchanged += 1
    wanted = set(desired_slugs)
    retirements = [row.id for slug, row in stored.items() if slug not in wanted and row.job.is_active]
    return Plan(tuple(inserts), tuple(updates), tuple(reactivations), tuple(retirements), unchanged)


def _row_values(job: CatalogueJob) -> dict[str, object]:
    return {field: (list(getattr(job, field)) if field == "required_skills" else getattr(job, field)) for field in CONTENT_FIELDS}


async def _take_lock(connection: AsyncConnection) -> None:
    await connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"), {"name": LOCK_NAME})


async def load_existing(connection: AsyncConnection) -> list[ExistingJob]:
    columns = [Job.id, Job.slug, Job.is_active, *(getattr(Job, field) for field in CONTENT_FIELDS)]
    try:
        # Sample rows only (Task 11b): imported real listings are never read, updated or retired by the sample seed.
        rows = (await connection.execute(select(*columns).where(Job.source.is_(None)).order_by(Job.slug))).all()
    except ProgrammingError as error:
        if getattr(error.orig, "sqlstate", None) == _UNDEFINED_TABLE:
            raise JobsTableMissingError from None
        raise
    return [
        ExistingJob(row.id, CatalogueJob(
            slug=row.slug, is_active=row.is_active,
            **{field: (tuple(row.required_skills) if field == "required_skills" else getattr(row, field)) for field in CONTENT_FIELDS},
        )) for row in rows
    ]


async def _insert(connection: AsyncConnection, jobs) -> None:
    if jobs:
        await connection.execute(insert(Job), [{"id": uuid4(), "slug": job.slug, "is_active": True, **_row_values(job)} for job in jobs])


async def _update(connection: AsyncConnection, changes, *, reactivate: bool) -> None:
    for job_id, job in changes:
        values = {**_row_values(job), "updated_at": func.statement_timestamp()}
        if reactivate:
            values["is_active"] = True
        result = await connection.execute(update(Job).where(Job.id == job_id).values(**values))
        if result.rowcount != 1:
            raise SeedError("A planned job update did not match exactly one row.")


async def _retire(connection: AsyncConnection, job_ids) -> None:
    if job_ids:
        result = await connection.execute(update(Job).where(Job.id.in_(job_ids), Job.is_active.is_(True)).values(
            is_active=False, updated_at=func.statement_timestamp()))
        if result.rowcount != len(job_ids):
            raise SeedError("A planned job retirement did not match every row.")


async def apply_plan(connection: AsyncConnection, plan: Plan) -> SeedCounts:
    """Write the plan inside the caller's transaction. Takes the advisory lock first (re-entrant within a transaction) and
    issues no statement for unchanged rows."""
    await _take_lock(connection)
    await _insert(connection, plan.inserts)
    await _update(connection, plan.updates, reactivate=False)
    await _update(connection, plan.reactivations, reactivate=True)
    await _retire(connection, plan.retirements)
    return plan.counts()


async def seed_catalogue(engine: AsyncEngine, catalogue) -> tuple[Plan, SeedCounts]:
    """Lock, read, plan and apply in ONE transaction, so a concurrent seeder cannot plan from stale rows. Does NOT validate:
    callers that accept a catalogue from outside must use `run_seed`."""
    async with engine.begin() as connection:
        await _take_lock(connection)
        plan = plan_sync(await load_existing(connection), catalogue)
        return plan, await apply_plan(connection, plan)


async def run_seed(engine: AsyncEngine, catalogue) -> tuple[Plan, SeedCounts]:
    """Validate first (no database access happens before this passes), then sync."""
    errors = validate_catalogue(catalogue)
    if errors:
        raise CatalogueInvalidError(errors)
    return await seed_catalogue(engine, catalogue)


async def check_catalogue(engine: AsyncEngine, catalogue) -> Plan:
    """Read-only comparison: the transaction is declared READ ONLY, no lock is taken, nothing is written."""
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        return plan_sync(await load_existing(connection), catalogue)
