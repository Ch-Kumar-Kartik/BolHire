"""Storage structure only; authentication behavior is outside Task 9.1."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text, text
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


# Task 10: curated SAMPLE job catalogue and private candidate interest. Check bodies are written identically in
# alembic/versions/0002_job_catalogue.py (the migration test compares them after PostgreSQL normalizes both).
_KEY = "'^[a-z0-9]+(-[a-z0-9]+)*$'"


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    company: Mapped[str] = mapped_column(String(120))
    location_label: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text())
    employment_type: Mapped[str] = mapped_column(String(20))
    role_key: Mapped[str] = mapped_column(String(60))
    city_key: Mapped[str | None] = mapped_column(String(60))
    work_mode: Mapped[str] = mapped_column(String(20))
    min_experience_years: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    max_experience_years: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    required_skills: Mapped[list[str]] = mapped_column(ARRAY(Text()))
    is_sample: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("statement_timestamp()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("statement_timestamp()"))
    __table_args__ = (
        CheckConstraint("btrim(title) <> ''", name="job_title_nonempty"),
        CheckConstraint("btrim(company) <> ''", name="job_company_nonempty"),
        CheckConstraint("btrim(location_label) <> ''", name="job_location_label_nonempty"),
        CheckConstraint("btrim(description) <> ''", name="job_description_nonempty"),
        CheckConstraint("char_length(description) <= 4000", name="job_description_length"),
        CheckConstraint(f"slug ~ {_KEY}", name="job_slug_format"),
        CheckConstraint(f"role_key ~ {_KEY}", name="job_role_key_format"),
        CheckConstraint(f"city_key IS NULL OR city_key ~ {_KEY}", name="job_city_key_format"),
        CheckConstraint("work_mode IN ('remote','hybrid','onsite')", name="job_work_mode"),
        CheckConstraint("employment_type IN ('full_time','part_time','contract','internship')", name="job_employment_type"),
        CheckConstraint("min_experience_years IS NULL OR (min_experience_years >= 0 AND min_experience_years <= 80)", name="job_min_years_range"),
        CheckConstraint("max_experience_years IS NULL OR (max_experience_years >= 0 AND max_experience_years <= 80)", name="job_max_years_range"),
        CheckConstraint("min_experience_years IS NULL OR max_experience_years IS NULL OR min_experience_years <= max_experience_years", name="job_years_order"),
        CheckConstraint("bolhire_valid_skills(required_skills)", name="job_skills_structure"),
        CheckConstraint("cardinality(required_skills) >= 1", name="job_skills_required"),
        CheckConstraint("work_mode = 'remote' OR city_key IS NOT NULL", name="job_city_required"),
        CheckConstraint("is_sample", name="job_is_sample"),
        CheckConstraint("updated_at >= created_at", name="job_timestamps"),
    )


class JobInterest(Base):
    __tablename__ = "job_interests"
    candidate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="RESTRICT"), primary_key=True)
    consent_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("statement_timestamp()"))
    __table_args__ = (
        CheckConstraint("btrim(consent_version) <> ''", name="job_interest_consent_version"),
        Index("ix_job_interests_job_id", "job_id"),
    )
