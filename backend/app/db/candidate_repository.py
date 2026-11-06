"""Internal storage operations. Callers must supply authorization and consent."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
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


class RecoveryKeyUnavailableError(Exception):
    """The recovery verifier belongs to an expired or profile-less identity that cleanup has not deleted yet."""


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


@dataclass(frozen=True)
class RegistrationResult:
    """`created` is False when an active identity already held the verifier (nothing was written)."""

    created: bool


class CandidateRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def active_identity_exists(self, recovery_secret_hash: bytes) -> bool:
        """Read-only: does this verifier hold an identity whose profile has not expired (database clock)?"""
        try:
            async with self._sessions() as session:
                return bool(await session.scalar(select(select(Candidate.id).join(
                    CandidateProfileRow, CandidateProfileRow.candidate_id == Candidate.id,
                ).where(
                    Candidate.recovery_secret_hash == recovery_secret_hash,
                    CandidateProfileRow.expires_at > func.statement_timestamp(),
                ).exists())))
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None

    async def register(
        self, profile: CandidateProfile, *, recovery_secret_hash: bytes, consent_version: str, retention: timedelta,
    ) -> RegistrationResult:
        """Idempotent identity creation keyed by the recovery verifier, in one transaction.

        A fresh verifier creates the candidate and profile together; `consented_at`, `created_at` and `expires_at` all come
        from one database `statement_timestamp()`, so retention is exactly `retention` and never depends on the application
        clock. A verifier held by an ACTIVE identity returns `created=False` without any write: no field, consent, revision or
        expiry is touched, so a retry (for example after a lost response) can never overwrite a profile. A verifier held by an
        expired or profile-less identity that cleanup has not yet deleted fails closed with `RecoveryKeyUnavailableError`:
        that identity is never reclaimed, overwritten, or revived.
        """
        values = profile_values(profile)
        if not isinstance(consent_version, str) or not consent_version.strip() or len(consent_version) > 80:
            raise ValueError("A non-empty consent version of at most 80 characters is required.")
        if not isinstance(retention, timedelta) or retention <= timedelta(0):
            raise ValueError("Retention must be a positive duration.")
        try:
            for _ in range(2):
                async with self._sessions.begin() as session:
                    result = await self._register_once(session, recovery_secret_hash, values, consent_version, retention)
                if result is not None:
                    return result
        except (IntegrityError, DataError):
            raise DatabaseConstraintError() from None
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None
        raise DatabaseUnavailableError()

    @staticmethod
    async def _register_once(
        session: AsyncSession, verifier: bytes, values: dict[str, object], consent_version: str, retention: timedelta,
    ) -> RegistrationResult | None:
        candidate_id = await session.scalar(pg_insert(Candidate).values(
            id=uuid4(), recovery_secret_hash=verifier,
        ).on_conflict_do_nothing(index_elements=[Candidate.recovery_secret_hash]).returning(Candidate.id))
        if candidate_id is None:
            row = (await session.execute(
                select(CandidateProfileRow.expires_at > func.statement_timestamp()).select_from(Candidate).outerjoin(
                    CandidateProfileRow, CandidateProfileRow.candidate_id == Candidate.id,
                ).where(Candidate.recovery_secret_hash == verifier)
            )).one_or_none()
            if row is None:
                return None  # the conflicting row was deleted while this request ran; the caller retries once
            if row[0]:
                return RegistrationResult(created=False)
            raise RecoveryKeyUnavailableError
        now = func.statement_timestamp()
        await session.execute(insert(CandidateProfileRow).values(
            candidate_id=candidate_id, **values, consent_version=consent_version, consented_at=now, expires_at=now + retention,
        ))
        return RegistrationResult(created=True)

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
