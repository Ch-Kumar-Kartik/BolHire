"""Read-only access to the active sample job catalogue. Never writes; never raises database detail."""

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job
from app.jobs.matching import MatchableJob


class JobsUnavailableError(Exception):
    """The jobs could not be read (database down, table missing, query failure). Carries no SQL or driver text."""

    def __init__(self) -> None:
        super().__init__("The job catalogue is unavailable.")


@dataclass(frozen=True)
class ActiveJob:
    job: MatchableJob
    is_sample: bool


async def load_active_jobs(sessions: async_sessionmaker[AsyncSession]) -> list[ActiveJob]:
    """Every job with is_active = true, ordered by slug, in one query inside a READ ONLY transaction."""
    try:
        async with sessions() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            rows = (await session.execute(select(Job).where(Job.is_active.is_(True)).order_by(Job.slug))).scalars().all()
            return [
                ActiveJob(MatchableJob(
                    id=str(row.id), slug=row.slug, title=row.title, company=row.company, location_label=row.location_label,
                    description=row.description, employment_type=row.employment_type, role_key=row.role_key, city_key=row.city_key,
                    work_mode=row.work_mode, min_experience_years=row.min_experience_years,
                    max_experience_years=row.max_experience_years, required_skills=tuple(row.required_skills),
                ), row.is_sample)
                for row in rows
            ]
    except (SQLAlchemyError, OSError):
        raise JobsUnavailableError() from None
