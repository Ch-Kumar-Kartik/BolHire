"""Versioned, language-specific consent notices and the profile digest for saving a candidate profile (Task 9.3).

Consent is only ever given by an explicit action in the client; it is never inferred from speech, a transcript, or model output.
The notice text lives in source control and the database stores only its version string, so changing the wording or the
retention period requires a new version.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta

from app.db.mapping import profile_values
from app.services.candidate_profile import CandidateProfile

RETENTION_DAYS = 90
RETENTION = timedelta(days=RETENTION_DAYS)
_BASE_VERSION = "candidate-profile-v1"


@dataclass(frozen=True)
class ConsentNotice:
    version: str
    language: str
    retention_days: int
    text: str


_TEXT = {
    "en-IN": (
        "I agree that BolHire saves the profile shown here and keeps it for 90 days from the day I save it. "
        "After 90 days nobody can access it, and the data is deleted after that. "
        "Anyone who has my recovery key can sign in and see this profile, and the recovery key cannot be recovered if I lose it. "
        "BolHire does not currently share profiles with employers or recruiters; sharing would need a separate consent."
    ),
    "hi-IN": (
        "मैं सहमत हूँ कि BolHire यहाँ दिखाई गई मेरी प्रोफ़ाइल सहेजे और सहेजने के दिन से 90 दिनों तक रखे। "
        "90 दिन बाद कोई भी इसे नहीं खोल सकेगा, और उसके बाद डेटा हटा दिया जाएगा। "
        "जिसके पास मेरी रिकवरी कुंजी (recovery key) है, वह साइन इन करके यह प्रोफ़ाइल देख सकता है, और रिकवरी कुंजी खो जाने पर दोबारा नहीं मिल सकती। "
        "BolHire अभी यह प्रोफ़ाइल नियोक्ताओं या रिक्रूटरों के साथ साझा नहीं करता; साझा करने के लिए अलग सहमति ली जाएगी।"
    ),
}
NOTICES: dict[str, ConsentNotice] = {
    f"{_BASE_VERSION}:{language}": ConsentNotice(f"{_BASE_VERSION}:{language}", language, RETENTION_DAYS, text)
    for language, text in _TEXT.items()
}


def notice_for(language: str) -> ConsentNotice:
    notice = NOTICES.get(f"{_BASE_VERSION}:{language}")
    if notice is None:
        raise ValueError("No consent notice exists for this language.")
    return notice


def notice_by_version(version: str) -> ConsentNotice | None:
    return NOTICES.get(version) if isinstance(version, str) else None


def has_saveable_content(profile: CandidateProfile) -> bool:
    """At least one meaningful stored value. Declined fields and an explicitly empty skill list do not count."""
    return any((
        profile.name is not None, profile.target_role is not None, profile.experience_years is not None,
        profile.experience_level is not None, profile.preferred_location is not None, profile.work_mode is not None,
        bool(profile.technical_skills),
    ))


def profile_digest(profile: CandidateProfile) -> str:
    """SHA-256 over the canonical stored form (`profile_values`: sorted declines, ordered skills), so the digest the client
    reviewed is exactly what would be persisted."""
    canonical = json.dumps(profile_values(profile), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
