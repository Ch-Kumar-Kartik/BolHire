"""Storage structure only; authentication behavior is outside Task 9.1."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, DOUBLE_PRECISION, TIMESTAMP, UUID as PG_UUID
from sqlalchemy import BigInteger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    recovery_secret_hash: Mapped[bytes] = mapped_column(BYTEA, unique=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("statement_timestamp()"))
    __table_args__ = (
        CheckConstraint("octet_length(recovery_secret_hash) = 32", name="candidate_hash_length"),
    )


class CandidateProfileRow(Base):
    __tablename__ = "candidate_profiles"
    candidate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(80))
    target_role: Mapped[str | None] = mapped_column(String(120))
    experience_years: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    experience_level: Mapped[str | None] = mapped_column(String(60))
    technical_skills: Mapped[list[str] | None] = mapped_column(ARRAY(Text()))
    preferred_location: Mapped[str | None] = mapped_column(String(120))
    work_mode: Mapped[str | None] = mapped_column(String(20))
    declined_fields: Mapped[list[str]] = mapped_column(ARRAY(Text()), server_default=text("'{}'::text[]"))
    revision: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    consent_version: Mapped[str] = mapped_column(String(80))
    consented_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("statement_timestamp()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("statement_timestamp()"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        *(CheckConstraint(f"{column} IS NULL OR length(btrim({column})) > 0", name=f"profile_{column}_nonempty")
          for column in ("name", "target_role", "experience_level", "preferred_location")),
        CheckConstraint("experience_years IS NULL OR (experience_years >= 0 AND experience_years <= 80)", name="profile_years_range"),
        CheckConstraint("work_mode IS NULL OR work_mode IN ('remote','hybrid','onsite','no_preference')", name="profile_work_mode"),
        CheckConstraint("bolhire_valid_skills(technical_skills)", name="profile_skills_structure"),
        CheckConstraint("bolhire_valid_declines(declined_fields)", name="profile_declines_structure"),
        *(CheckConstraint(f"NOT ('{column}' = ANY(declined_fields)) OR {column} IS NULL", name=f"profile_declined_{column}")
          for column in ("name", "target_role", "technical_skills", "preferred_location", "work_mode")),
        CheckConstraint("NOT ('experience' = ANY(declined_fields)) OR (experience_years IS NULL AND experience_level IS NULL)", name="profile_declined_experience"),
        CheckConstraint("revision > 0", name="profile_positive_revision"),
        CheckConstraint("length(btrim(consent_version)) > 0", name="profile_consent_version"),
        CheckConstraint("expires_at > created_at AND updated_at >= created_at", name="profile_timestamps"),
        Index("ix_candidate_profiles_expires_at", "expires_at"),
    )


class CandidateAuthSession(Base):
    __tablename__ = "candidate_auth_sessions"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    candidate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"))
    token_hash: Mapped[bytes] = mapped_column(BYTEA, unique=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("statement_timestamp()"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint("octet_length(token_hash) = 32", name="auth_hash_length"),
        CheckConstraint("expires_at > created_at", name="auth_timestamps"),
        Index("ix_candidate_auth_sessions_candidate_id", "candidate_id"),
        Index("ix_candidate_auth_sessions_expires_at", "expires_at"),
    )
