"""Authentication-session storage. Expiry is enforced by the database clock in every statement."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, exists, func, literal, not_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.candidate_repository import DatabaseConstraintError
from app.db.database import DatabaseUnavailableError
from app.db.models import Candidate, CandidateAuthSession, CandidateProfileRow
from app.services.candidate_auth import SESSION_TTL, SessionToken

_FOREIGN_KEY_VIOLATION = "23503"


class InvalidCredentialsError(Exception):
    """Unknown key, or the identity's profile has expired or been deleted."""


class SessionInvalidError(Exception):
    """Unknown, expired, revoked, or orphaned session."""


@dataclass(frozen=True)
class CandidatePrincipal:
    candidate_id: UUID
    session_id: UUID


@dataclass(frozen=True)
class PurgeResult:
    candidates: int = 0
    sessions: int = 0


class AuthRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_session(
        self, recovery_verifier: bytes, token: SessionToken, *, ttl: timedelta = SESSION_TTL,
    ) -> UUID:
        """Single statement: the session exists only if the key matches an unexpired identity.

        The foreign-key check locks the candidate row, so a concurrent delete or purge either
        waits for this insert or makes it fail with a foreign-key violation (reported as
        invalid credentials). No orphan session can be committed.
        """
        now = func.statement_timestamp()
        statement = pg_insert(CandidateAuthSession).from_select(
            ["id", "candidate_id", "token_hash", "expires_at"],
            select(literal(uuid4()), Candidate.id, literal(token.verifier()), now + ttl)
            .join(CandidateProfileRow, CandidateProfileRow.candidate_id == Candidate.id)
            .where(Candidate.recovery_secret_hash == recovery_verifier, CandidateProfileRow.expires_at > now),
        ).returning(CandidateAuthSession.candidate_id)
        try:
            async with self._sessions.begin() as session:
                candidate_id = await session.scalar(statement)
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == _FOREIGN_KEY_VIOLATION:
                raise InvalidCredentialsError from None
            raise DatabaseConstraintError() from None
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None
        if candidate_id is None:
            raise InvalidCredentialsError
        return candidate_id

    async def resolve(self, token: SessionToken) -> CandidatePrincipal:
        """Requires both the session and its candidate's profile to be unexpired."""
        now = func.statement_timestamp()
        try:
            async with self._sessions() as session:
                row = (await session.execute(
                    select(CandidateAuthSession.id, CandidateAuthSession.candidate_id)
                    .join(CandidateProfileRow, CandidateProfileRow.candidate_id == CandidateAuthSession.candidate_id)
                    .where(CandidateAuthSession.token_hash == token.verifier(),
                           CandidateAuthSession.expires_at > now, CandidateProfileRow.expires_at > now)
                )).one_or_none()
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None
        if row is None:
            raise SessionInvalidError
        return CandidatePrincipal(candidate_id=row.candidate_id, session_id=row.id)

    async def delete_session(self, token: SessionToken) -> bool:
        """Revocation is idempotent and needs no expiry check; it only removes authority."""
        try:
            async with self._sessions.begin() as session:
                deleted = await session.scalar(delete(CandidateAuthSession).where(
                    CandidateAuthSession.token_hash == token.verifier()).returning(CandidateAuthSession.id))
            return deleted is not None
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None

    async def delete_candidate_sessions(self, candidate_id: UUID, *, except_session_id: UUID | None = None) -> int:
        condition = CandidateAuthSession.candidate_id == candidate_id
        if except_session_id is not None:
            condition = and_(condition, CandidateAuthSession.id != except_session_id)
        try:
            async with self._sessions.begin() as session:
                deleted = await session.scalars(delete(CandidateAuthSession).where(condition).returning(CandidateAuthSession.id))
                return len(deleted.all())
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None

    async def purge_expired(self, *, batch_size: int = 500, max_batches: int = 1000) -> PurgeResult:
        """Physically delete expired identities and sessions. Safe to run concurrently and repeatedly.

        Correctness does not depend on this running: login and `resolve` check expiry themselves,
        with the database clock. Each batch is one transaction of two statements:

        1. `SELECT ... FOR UPDATE OF candidate, profile SKIP LOCKED` picks expired identities. A row
           another transaction holds (a profile update, a login's foreign-key check, another
           cleanup) is skipped, never waited for, and is retried by the next run. For a row that
           was updated and committed after this statement's snapshot, PostgreSQL's READ COMMITTED
           locking re-evaluates the WHERE clause on the latest version before locking it. That
           window is not reproducible in a test (see tests/integration/test_auth_concurrency.py).
        2. `DELETE ... WHERE id IN (...) AND the profile is still expired`. Both rows are already
           locked by this transaction, so nothing can change them between the statements; the
           expiry predicate is defence in depth, not what serialises cleanup against renewals.

        An update that starts after step 1 blocks on the profile lock, then finds the row deleted
        and changes nothing: cleanup can neither lose nor resurrect it. Cleanup never waits on a
        candidate or profile lock, but its DELETE cascade can wait on a session-row lock held by
        a concurrent logout; that holder waits on nothing cleanup owns, so no deadlock cycle forms.
        """
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer.")
        candidates = sessions = 0
        try:
            for _ in range(max_batches):
                removed = await self._purge_expired_identities(batch_size) + await self._purge_profileless(batch_size)
                candidates += removed
                expired_sessions = await self._purge_expired_sessions(batch_size)
                sessions += expired_sessions
                if removed < batch_size and expired_sessions < batch_size:
                    break
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None
        return PurgeResult(candidates=candidates, sessions=sessions)

    async def _purge_expired_identities(self, limit: int) -> int:
        now = func.statement_timestamp()
        async with self._sessions.begin() as session:
            ids = (await session.scalars(
                select(Candidate.id).join(CandidateProfileRow, CandidateProfileRow.candidate_id == Candidate.id)
                .where(CandidateProfileRow.expires_at <= now).order_by(CandidateProfileRow.expires_at).limit(limit)
                .with_for_update(of=[Candidate, CandidateProfileRow], skip_locked=True)
            )).all()
            if not ids:
                return 0
            deleted = await session.scalars(delete(Candidate).where(
                Candidate.id.in_(ids),
                exists().where(CandidateProfileRow.candidate_id == Candidate.id, CandidateProfileRow.expires_at <= now),
            ).returning(Candidate.id))
            return len(deleted.all())

    async def _purge_profileless(self, limit: int) -> int:
        has_profile = exists().where(CandidateProfileRow.candidate_id == Candidate.id)
        async with self._sessions.begin() as session:
            ids = (await session.scalars(
                select(Candidate.id).where(not_(has_profile)).limit(limit)
                .with_for_update(of=Candidate, skip_locked=True)
            )).all()
            if not ids:
                return 0
            deleted = await session.scalars(delete(Candidate).where(
                Candidate.id.in_(ids), not_(has_profile)).returning(Candidate.id))
            return len(deleted.all())

    async def _purge_expired_sessions(self, limit: int) -> int:
        async with self._sessions.begin() as session:
            ids = (await session.scalars(
                select(CandidateAuthSession.id).where(CandidateAuthSession.expires_at <= func.statement_timestamp())
                .limit(limit).with_for_update(skip_locked=True)
            )).all()
            if not ids:
                return 0
            deleted = await session.scalars(delete(CandidateAuthSession).where(
                CandidateAuthSession.id.in_(ids)).returning(CandidateAuthSession.id))
            return len(deleted.all())
