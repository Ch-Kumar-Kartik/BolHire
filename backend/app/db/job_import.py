"""Idempotent import of real (non-sample) job listings into `jobs`. Keyed by (source, external_id).

Layers mirror the sample seed: `plan_import` is pure; `apply_import` writes inside the caller's transaction under the SAME
advisory lock as the seed (so the two writers serialize); `import_jobs` locks, reads, plans and applies in one transaction;
`check_import` is a READ ONLY comparison that takes no lock.

There is deliberately NO retirement: a stored real row that is missing from the file is left exactly as it is. Nothing here reads
or writes sample rows (source IS NULL) or `job_interests`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.job_catalogue_seed import JobsTableMissingError, SeedError, _comparable, _take_lock
from app.db.models import Job
from app.jobs.linkedin_import import ImportedJob

# Compared to decide whether a stored row needs an update. retrieved_at is written with every update but is not itself a reason
# to update: re-importing the same export must change nothing.
CONTENT_FIELDS = (
    "slug", "title", "company", "location_label", "description", "employment_type", "role_key", "city_key", "work_mode",
    "min_experience_years", "max_experience_years", "required_skills", "source_url", "posted_at",
)
_UNDEFINED_TABLE, _UNDEFINED_COLUMN = "42P01", "42703"


class JobsSchemaOutdatedError(Exception):
    """The jobs table lacks the provenance columns: run `alembic upgrade head` (migration 0003) first."""


@dataclass(frozen=True)
class StoredRealJob:
    id: UUID
    external_id: str
    values: dict[str, object]


@dataclass(frozen=True)
class ImportCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass(frozen=True)
class ImportPlan:
    inserts: tuple[ImportedJob, ...] = ()
    updates: tuple[tuple[UUID, ImportedJob], ...] = ()
    unchanged: int = 0

    def counts(self) -> ImportCounts:
        return ImportCounts(len(self.inserts), len(self.updates), self.unchanged)


def _content(values) -> tuple:
    return tuple(_comparable(values[field] if isinstance(values, dict) else getattr(values, field)) for field in CONTENT_FIELDS)


def plan_import(stored: Iterable[StoredRealJob], jobs: Iterable[ImportedJob]) -> ImportPlan:
    """Pure. Insert unknown external ids, update changed ones, count the rest. Never plans a retirement or a delete."""
    by_id = {row.external_id: row for row in stored}
    jobs = list(jobs)
    ids = [job.external_id for job in jobs]
    if len(set(ids)) != len(ids):
        raise ValueError("External ids must be unique within one import.")
    inserts, updates, unchanged = [], [], 0
    for job in jobs:
        row = by_id.get(job.external_id)
        if row is None:
            inserts.append(job)
        elif _content(row.values) != _content(job):
            updates.append((row.id, job))
        else:
            unchanged += 1
    return ImportPlan(tuple(inserts), tuple(updates), unchanged)


async def load_real_jobs(connection: AsyncConnection, source: str) -> list[StoredRealJob]:
    columns = [Job.id, Job.external_id, *(getattr(Job, field) for field in CONTENT_FIELDS)]
    try:
        rows = (await connection.execute(select(*columns).where(Job.source == source))).all()
    except ProgrammingError as error:
        code = getattr(error.orig, "sqlstate", None)
        if code == _UNDEFINED_TABLE:
            raise JobsTableMissingError from None
        if code == _UNDEFINED_COLUMN:
            raise JobsSchemaOutdatedError from None
        raise
    return [
        StoredRealJob(row.id, row.external_id, {field: getattr(row, field) for field in CONTENT_FIELDS})
        for row in rows
    ]


def _row_values(job: ImportedJob) -> dict[str, object]:
    values = {field: getattr(job, field) for field in CONTENT_FIELDS}
    values["required_skills"] = list(job.required_skills)
    values["retrieved_at"] = job.retrieved_at
    return values


async def apply_import(connection: AsyncConnection, plan: ImportPlan) -> ImportCounts:
    await _take_lock(connection)
    if plan.inserts:
        await connection.execute(insert(Job), [
            {"id": uuid4(), "source": job.source, "external_id": job.external_id, "is_sample": False, "is_active": True,
             **_row_values(job)}
            for job in plan.inserts
        ])
    for job_id, job in plan.updates:
        result = await connection.execute(
            update(Job).where(Job.id == job_id, Job.source == job.source)
            .values(**_row_values(job), updated_at=func.statement_timestamp())
        )
        if result.rowcount != 1:
            raise SeedError("A planned job update did not match exactly one row.")
    return plan.counts()


def _single_source(jobs: list[ImportedJob]) -> str:
    sources = {job.source for job in jobs}
    if len(sources) > 1:
        raise ValueError("One import must come from one source.")
    return next(iter(sources), "")


async def import_jobs(engine: AsyncEngine, jobs: Iterable[ImportedJob]) -> tuple[ImportPlan, ImportCounts]:
    jobs = list(jobs)
    source = _single_source(jobs)
    async with engine.begin() as connection:
        await _take_lock(connection)
        plan = plan_import(await load_real_jobs(connection, source), jobs)
        return plan, await apply_import(connection, plan)


async def check_import(engine: AsyncEngine, jobs: Iterable[ImportedJob]) -> ImportPlan:
    jobs = list(jobs)
    source = _single_source(jobs)
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        return plan_import(await load_real_jobs(connection, source), jobs)
