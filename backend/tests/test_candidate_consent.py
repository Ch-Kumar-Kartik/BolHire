import pytest

from app.services.candidate_consent import (
    NOTICES, RETENTION, RETENTION_DAYS, has_saveable_content, notice_by_version, notice_for, profile_digest,
)
from app.services.candidate_profile import CandidateProfile


def test_notices_are_versioned_per_language_and_state_the_material_terms():
    assert set(NOTICES) == {"candidate-profile-v1:en-IN", "candidate-profile-v1:hi-IN"}
    assert RETENTION_DAYS == 90 and RETENTION.days == 90
    for version, notice in NOTICES.items():
        assert notice.version == version and len(version) <= 80 and notice.retention_days == 90
        assert "90" in notice.text and "recovery key" in notice.text
    assert notice_for("en-IN").language == "en-IN" and notice_for("hi-IN").text != notice_for("en-IN").text
    assert "cannot be recovered" in notice_for("en-IN").text and "separate consent" in notice_for("en-IN").text
    assert "दोबारा नहीं मिल सकती" in notice_for("hi-IN").text and "अलग सहमति" in notice_for("hi-IN").text


def test_unknown_languages_and_versions_are_rejected():
    with pytest.raises(ValueError):
        notice_for("fr-FR")
    for version in ("candidate-profile-v1", "candidate-profile-v1:fr-FR", "candidate-profile-v2:en-IN", "", None, 3):
        assert notice_by_version(version) is None
    assert notice_by_version("candidate-profile-v1:hi-IN") is notice_for("hi-IN")


@pytest.mark.parametrize("profile, saveable", [
    (CandidateProfile(), False),
    (CandidateProfile(declined_fields=frozenset({"name", "target_role", "experience", "technical_skills",
                                                 "preferred_location", "work_mode"})), False),
    (CandidateProfile(technical_skills=()), False),  # an explicitly empty skill list is not a meaningful value
    (CandidateProfile(name="Asha"), True),
    (CandidateProfile(target_role="Backend developer"), True),
    (CandidateProfile(experience_years=0), True),
    (CandidateProfile(experience_level="fresher"), True),
    (CandidateProfile(technical_skills=("Python",)), True),
    (CandidateProfile(preferred_location="Pune"), True),
    (CandidateProfile(work_mode="no_preference"), True),
    (CandidateProfile(name="Asha", declined_fields=frozenset({"work_mode"})), True),
])
def test_only_a_meaningful_stored_value_makes_a_profile_saveable(profile, saveable):
    assert has_saveable_content(profile) is saveable


def test_digest_is_stable_canonical_and_sensitive_to_every_field():
    base = CandidateProfile(name="आशा", target_role="Python developer", experience_years=2.5, experience_level="junior",
                            technical_skills=("Python", "SQL"), preferred_location="Pune", work_mode="hybrid",
                            declined_fields=frozenset())
    digest = profile_digest(base)
    assert len(digest) == 64 and digest == profile_digest(CandidateProfile.model_validate(base.model_dump()))
    changes = [dict(name="Asha"), dict(target_role="Go developer"), dict(experience_years=3.0), dict(experience_level="senior"),
               dict(technical_skills=("SQL", "Python")), dict(technical_skills=("Python",)), dict(technical_skills=None),
               dict(preferred_location="Delhi"), dict(work_mode="remote"), dict(work_mode=None)]
    digests = {profile_digest(base.model_copy(update=change)) for change in changes}
    assert len(digests) == len(changes) and digest not in digests
    declined = CandidateProfile(name="Asha", declined_fields=frozenset({"work_mode", "preferred_location"}))
    same = CandidateProfile(name="Asha", declined_fields=frozenset({"preferred_location", "work_mode"}))
    assert profile_digest(declined) == profile_digest(same) != profile_digest(CandidateProfile(name="Asha"))
    assert profile_digest(CandidateProfile(technical_skills=())) != profile_digest(CandidateProfile())  # known-empty is not unknown
