from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.models import CandidateProfileRow
from app.services.candidate_profile import CandidateProfile


@dataclass(frozen=True)
class SavedProfile:
    candidate_id: UUID
    profile: CandidateProfile
    revision: int
    consent_version: str
    consented_at: datetime
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


def profile_values(profile: CandidateProfile) -> dict[str, object]:
    # Revalidate even model_construct/model_copy values; never normalize null to [].
    validated = CandidateProfile.model_validate(profile.model_dump(warnings="error"))
    values = validated.model_dump()
    skills = validated.technical_skills
    values["technical_skills"] = None if skills is None else list(skills)
    values["declined_fields"] = sorted(validated.declined_fields)
    return values


def saved_profile(row: CandidateProfileRow) -> SavedProfile:
    values = {name: getattr(row, name) for name in CandidateProfile.model_fields}
    skills = row.technical_skills
    values["technical_skills"] = None if skills is None else tuple(skills)
    values["declined_fields"] = frozenset(row.declined_fields)
    profile = CandidateProfile.model_validate(values)
    return SavedProfile(
        candidate_id=row.candidate_id, profile=profile, revision=row.revision,
        consent_version=row.consent_version, consented_at=row.consented_at,
        created_at=row.created_at, updated_at=row.updated_at, expires_at=row.expires_at,
    )
