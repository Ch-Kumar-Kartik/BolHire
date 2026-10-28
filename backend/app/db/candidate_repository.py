"""Internal storage operations. Callers must supply authorization and consent."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.database import DatabaseUnavailableError
from app.db.mapping import SavedProfile, profile_values, saved_profile
from app.db.models import Candidate, CandidateProfileRow
from app.services.candidate_profile import CandidateProfile


class ProfileNotFoundError(Exception):
    """No unexpired profile exists for this candidate."""


class ProfileRevisionConflictError(Exception):
    """Another save changed the profile revision."""


class DatabaseConstraintError(Exception):
    def __init__(self) -> None:
        super().__init__("Candidate data violates a database constraint.")


class StoredProfileInvalidError(Exception):
    def __init__(self) -> None:
        super().__init__("The stored candidate profile is invalid.")


def _metadata(consent_version: str, consented_at: datetime, expires_at: datetime) -> dict[str, object]:
    if not isinstance(consent_version, str) or not consent_version.strip() or len(consent_version) > 80:
        raise ValueError("A non-empty consent version of at most 80 characters is required.")
    for value in (consented_at, expires_at):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("Consent and expiration timestamps must be timezone-aware.")
    return dict(consent_version=consent_version, consented_at=consented_at, expires_at=expires_at)


class CandidateRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self, profile: CandidateProfile, *, recovery_secret_hash: bytes,
        consent_version: str, consented_at: datetime, expires_at: datetime,
    ) -> SavedProfile:
        values = profile_values(profile)
        metadata = _metadata(consent_version, consented_at, expires_at)
        candidate_id = uuid4()
        try:
            async with self._sessions.begin() as session:
                session.add(Candidate(id=candidate_id, recovery_secret_hash=recovery_secret_hash))
                await session.flush()
                row = CandidateProfileRow(candidate_id=candidate_id, **values, **metadata)
                session.add(row)
                await session.flush()
                result = saved_profile(row)
            return result
        except (IntegrityError, DataError):
            raise DatabaseConstraintError() from None
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None

    async def get(self, candidate_id: UUID) -> SavedProfile:
        try:
            async with self._sessions() as session:
                row = await session.scalar(select(CandidateProfileRow).where(
                    CandidateProfileRow.candidate_id == candidate_id,
                    CandidateProfileRow.expires_at > func.statement_timestamp(),
                ))
                if row is None:
                    raise ProfileNotFoundError
                return saved_profile(row)
        except ValidationError:
            raise StoredProfileInvalidError() from None
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None

    async def replace(
        self, candidate_id: UUID, profile: CandidateProfile, *, expected_revision: int,
        consent_version: str, consented_at: datetime, expires_at: datetime,
    ) -> SavedProfile:
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("Expected revision must be a positive integer.")
        values = profile_values(profile)
        metadata = _metadata(consent_version, consented_at, expires_at)
        try:
            async with self._sessions.begin() as session:
                row = await session.scalar(update(CandidateProfileRow).where(
                    CandidateProfileRow.candidate_id == candidate_id,
                    CandidateProfileRow.revision == expected_revision,
                    CandidateProfileRow.expires_at > func.statement_timestamp(),
                ).values(**values, **metadata, revision=CandidateProfileRow.revision + 1,
                         updated_at=func.statement_timestamp()).returning(CandidateProfileRow))
                if row is None:
                    active = await session.scalar(select(CandidateProfileRow.candidate_id).where(
                        CandidateProfileRow.candidate_id == candidate_id,
                        CandidateProfileRow.expires_at > func.statement_timestamp(),
                    ))
                    if active is None:
                        raise ProfileNotFoundError
                    raise ProfileRevisionConflictError
                result = saved_profile(row)
            return result
        except (IntegrityError, DataError):
            raise DatabaseConstraintError() from None
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None

    async def delete(self, candidate_id: UUID) -> bool:
        try:
            async with self._sessions.begin() as session:
                deleted = await session.scalar(delete(Candidate).where(
                    Candidate.id == candidate_id,
                ).returning(Candidate.id))
            return deleted is not None
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None
