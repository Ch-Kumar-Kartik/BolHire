from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db.mapping import profile_values, saved_profile
from app.db.models import CandidateProfileRow
from app.services.candidate_profile import CandidateProfile


@pytest.mark.parametrize("skills", [None, (), ("Python", "MongoDB", "हिंदी")])
def test_mapping_preserves_profile_types_and_values(skills):
    profile = CandidateProfile(name="रीना", experience_years=1.23456789012345,
        technical_skills=skills, declined_fields=frozenset({"work_mode"}))
    now = datetime.now(timezone.utc)
    row = CandidateProfileRow(candidate_id=uuid4(), **profile_values(profile), revision=1,
        consent_version="v1", consented_at=now, created_at=now, updated_at=now, expires_at=now)
    assert saved_profile(row).profile == profile
    assert profile_values(profile)["technical_skills"] == (None if skills is None else list(skills))


def test_mapping_revalidates_constructed_invalid_models():
    with pytest.raises(ValidationError):
        profile_values(CandidateProfile.model_construct(experience_years=-1))


def test_mapping_rejects_invalid_database_values():
    row = CandidateProfileRow(**profile_values(CandidateProfile()))
    row.work_mode = "invalid"
    with pytest.raises(ValidationError):
        saved_profile(row)
