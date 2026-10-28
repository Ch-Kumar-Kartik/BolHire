"""Session-scoped candidate intake, separate from general conversation."""

from collections.abc import Sequence
import logging
import re
from time import perf_counter
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic_core import PydanticSerializationError

from app.models import ConversationMessage, TextChatResponse, TimingMS
from app.providers.llm import LLMProviderError
from app.services.candidate_profile import (
    CandidateProfile, CandidateProfileUpdate, ProfileField, apply_candidate_update,
)
from app.services.pipeline import LLMGenerationError
from app.services.sessions import InMemorySessionStore

logger = logging.getLogger(__name__)
IntakeFailureReason = Literal[
    "acknowledgement_invalid", "evidence_fields", "quote_not_literal",
    "value_not_grounded", "years_not_stated", "work_mode_not_grounded",
    "profile_apply", "unknown",
]


class IntakeValidationError(ValueError):
    def __init__(self, reason: IntakeFailureReason) -> None:
        super().__init__("Invalid candidate intake proposal.")
        self.reason = reason


def _invalid_response(reason: IntakeFailureReason) -> LLMProviderError:
    logger.warning("component=candidate_intake reason=%s", reason)
    return LLMProviderError(
        "CANDIDATE_INTAKE_INVALID_RESPONSE", "The conversation provider returned an invalid intake reply."
    )


class IntakeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: ProfileField
    quote: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=300)]


class CandidateIntakeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    update: CandidateProfileUpdate
    acknowledgement: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160)]
    evidence: list[IntakeEvidence] = Field(default_factory=list, max_length=6)


class CandidateIntakeGenerator(Protocol):
    async def generate_intake(
        self, messages: Sequence[ConversationMessage], profile: CandidateProfile, language: str,
    ) -> CandidateIntakeProposal: ...


_FIELD_ORDER: tuple[ProfileField, ...] = (
    "name", "target_role", "experience", "technical_skills", "preferred_location", "work_mode",
)
_YEAR_WORDS = {
    0: ("zero", "shunya", "शून्य"),
    1: ("one", "ek", "एक"),
    2: ("two", "do", "दो"),
    3: ("three", "teen", "तीन"),
    4: ("four", "chaar", "चार"),
    5: ("five", "paanch", "पांच", "पाँच"),
    6: ("six", "chhe", "छह"),
    7: ("seven", "saat", "सात"),
    8: ("eight", "aath", "आठ"),
    9: ("nine", "nau", "नौ"),
    10: ("ten", "das", "दस"),
}
_WORK_MODE_WORDS = {
    "remote": ("remote", "रिमोट", "घर से", "work from home", "wfh"),
    "hybrid": ("hybrid", "हाइब्रिड"),
    "onsite": ("onsite", "on-site", "office", "ऑफिस", "दफ्तर"),
    "no_preference": ("any", "no preference", "either", "कोई भी", "फर्क नहीं"),
}
_SAFE_ACKNOWLEDGEMENTS = {
    "en-IN": {"Thanks for sharing.", "Got it.", "Thank you.", "Understood."},
    "hi-IN": {"धन्यवाद।", "ठीक है।", "समझ गया।", "समझ गई।"},
}
_QUESTIONS = {
    "en-IN": {
        "name": "What should I call you?",
        "target_role": "What kind of role are you looking for?",
        "experience": "How much experience do you have, or are you a fresher?",
        "technical_skills": "Which technical skills would you like to mention?",
        "preferred_location": "Where would you prefer to work?",
        "work_mode": "Do you prefer remote, hybrid, or onsite work?",
    },
    "hi-IN": {
        "name": "आपको किस नाम से बुलाऊँ?",
        "target_role": "आप किस तरह की नौकरी चाहते हैं?",
        "experience": "आपके पास कितना अनुभव है, या आप फ्रेशर हैं?",
        "technical_skills": "आप कौन से तकनीकी कौशल बताना चाहेंगे?",
        "preferred_location": "आप किस जगह काम करना पसंद करेंगे?",
        "work_mode": "आप रिमोट, हाइब्रिड या ऑफिस से काम करना पसंद करेंगे?",
    },
}


def missing_fields(profile: CandidateProfile) -> tuple[ProfileField, ...]:
    supplied = {
        "name": profile.name is not None,
        "target_role": profile.target_role is not None,
        "experience": profile.experience_years is not None or profile.experience_level is not None,
        "technical_skills": profile.technical_skills is not None,
        "preferred_location": profile.preferred_location is not None,
        "work_mode": profile.work_mode is not None,
    }
    return tuple(
        field for field in _FIELD_ORDER if not supplied[field] and field not in profile.declined_fields
    )


def _changed_fields(update: CandidateProfileUpdate) -> set[ProfileField]:
    changed: set[ProfileField] = set(update.decline_fields)
    for field in update.clear_fields:
        changed.add("experience" if field.startswith("experience") else field)
    for field in ("name", "target_role", "preferred_location", "work_mode"):
        if field in update.model_fields_set:
            changed.add(field)
    if "experience_years" in update.model_fields_set or "experience_level" in update.model_fields_set:
        changed.add("experience")
    if update.skills_replace is not None or update.skills_add or update.skills_remove:
        changed.add("technical_skills")
    return changed


def _years_appear_in_quote(years: float, quote: str) -> bool:
    if not any(marker in quote.casefold() for marker in ("year", "yr", "saal", "sal ", "साल", "वर्ष")):
        return False
    words = _YEAR_WORDS.get(int(years), ()) if years.is_integer() else ()
    expressions = (f"{years:g}", *words)
    return any(
        re.search(rf"(?<!\w){re.escape(expression)}(?!\w)", quote, re.IGNORECASE)
        for expression in expressions
    )


def validate_proposal(proposal: CandidateIntakeProposal, utterance: str) -> CandidateIntakeProposal:
    """Require a quote from this turn for every proposed profile change."""
    if not isinstance(proposal, CandidateIntakeProposal):
        raise IntakeValidationError("unknown")
    try:
        proposal = CandidateIntakeProposal.model_validate(
            proposal.model_dump(exclude_unset=True, warnings="error")
        )
    except (ValueError, PydanticSerializationError) as error:
        raise IntakeValidationError("unknown") from error
    if any(mark in proposal.acknowledgement for mark in ("?", "؟", "？")):
        raise IntakeValidationError("acknowledgement_invalid")
    quotes = {item.field: item.quote for item in proposal.evidence}
    if len(quotes) != len(proposal.evidence) or set(quotes) != _changed_fields(proposal.update):
        raise IntakeValidationError("evidence_fields")
    text = utterance.casefold()
    if any(quote.casefold() not in text for quote in quotes.values()):
        raise IntakeValidationError("quote_not_literal")
    update = proposal.update
    for field in ("name", "target_role", "experience_level", "preferred_location"):
        evidence_field = "experience" if field == "experience_level" else field
        if field in update.model_fields_set and getattr(update, field).casefold() not in quotes[evidence_field].casefold():
            raise IntakeValidationError("value_not_grounded")
    for skill in (*update.skills_add, *update.skills_remove, *(update.skills_replace or ())):
        if skill.casefold() not in quotes["technical_skills"].casefold():
            raise IntakeValidationError("value_not_grounded")
    if "experience_years" in update.model_fields_set and not _years_appear_in_quote(
        update.experience_years, quotes["experience"]
    ):
        raise IntakeValidationError("years_not_stated")
    if "work_mode" in update.model_fields_set and not any(
        word in quotes["work_mode"].casefold() for word in _WORK_MODE_WORDS[update.work_mode]
    ):
        raise IntakeValidationError("work_mode_not_grounded")
    return proposal


def _summary(profile: CandidateProfile, language: str) -> str:
    hindi = language == "hi-IN"
    sentences: list[str] = []
    if profile.name is not None:
        sentences.append(f"आपका नाम {profile.name} है।" if hindi else f"Your name is {profile.name}.")
    if profile.target_role is not None:
        sentences.append(f"आप {profile.target_role} की नौकरी चाहते हैं।" if hindi else f"You're looking for a {profile.target_role} role.")
    if profile.experience_years is not None:
        years = f"{profile.experience_years:g}"
        unit = "year" if profile.experience_years == 1 else "years"
        sentences.append(f"आपके पास {years} साल का अनुभव है।" if hindi else f"You have {years} {unit} of experience.")
    if profile.experience_level is not None:
        sentences.append(f"आपने अपना अनुभव स्तर {profile.experience_level} बताया है।" if hindi else f"You described your experience level as {profile.experience_level}.")
    if profile.technical_skills is not None:
        skills = profile.technical_skills
        if skills:
            conjunction = " और " if hindi else " and "
            listed = skills[0] if len(skills) == 1 else ", ".join(skills[:-1]) + conjunction + skills[-1]
            sentences.append(f"आपके कौशल में {listed} शामिल हैं।" if hindi else f"Your skills include {listed}.")
        else:
            sentences.append("आपने कोई तकनीकी कौशल नहीं बताया है।" if hindi else "You haven't listed any technical skills.")
    if profile.preferred_location is not None:
        sentences.append(f"आप {profile.preferred_location} में काम करना चाहते हैं।" if hindi else f"You'd prefer to work in {profile.preferred_location}.")
    mode = {
        "remote": "आप रिमोट काम करना चाहते हैं।" if hindi else "You prefer remote work.",
        "hybrid": "आप हाइब्रिड काम करना चाहते हैं।" if hindi else "You prefer hybrid work.",
        "onsite": "आप ऑफिस से काम करना चाहते हैं।" if hindi else "You prefer onsite work.",
        "no_preference": "काम के तरीके को लेकर आपकी कोई प्राथमिकता नहीं है।" if hindi else "You have no work mode preference.",
    }.get(profile.work_mode)
    if mode:
        sentences.append(mode)
    sentences.append("कुछ बदलना चाहें तो बताइए।" if hindi else "Let me know if anything needs correcting.")
    return " ".join(sentences)


def _reply(proposal: CandidateIntakeProposal, profile: CandidateProfile, language: str) -> str:
    missing = missing_fields(profile)
    if not missing:
        return _summary(profile, language)
    acknowledgement = proposal.acknowledgement
    if acknowledgement not in _SAFE_ACKNOWLEDGEMENTS[language]:
        acknowledgement = "ठीक है।" if language == "hi-IN" else "Got it."
    return f"{acknowledgement} {_QUESTIONS[language][missing[0]]}"


class CandidateIntakePipeline:
    def __init__(self, *, generator: CandidateIntakeGenerator, sessions: InMemorySessionStore) -> None:
        self._generator = generator
        self._sessions = sessions

    @property
    def sessions(self) -> InMemorySessionStore:
        return self._sessions

    async def respond(self, *, session_id: str | None, text: str, language: str) -> TextChatResponse:
        if language not in _QUESTIONS or not text.strip():
            raise ValueError("Candidate intake needs non-empty text and a supported language.")
        started = perf_counter()
        user_message = ConversationMessage(role="user", content=text)
        if session_id is None:
            profile = CandidateProfile()
            proposal, llm_ms = await self._generate([user_message], profile, language)
            next_profile = self._validate_and_apply(proposal, profile, text)
            reply = _reply(proposal, next_profile, language)
            session = await self._sessions.create_candidate_exchange(
                proposal.update, [user_message, ConversationMessage(role="assistant", content=reply)]
            )
        else:
            session = await self._sessions.resolve(session_id, workflow="candidate_intake")
            async with session.lock:
                history = await self._sessions.history_for(session, workflow="candidate_intake")
                profile = await self._sessions.candidate_profile_for(session)
                proposal, llm_ms = await self._generate(history + [user_message], profile, language)
                next_profile = self._validate_and_apply(proposal, profile, text)
                reply = _reply(proposal, next_profile, language)
                await self._sessions.commit_candidate_exchange(
                    session, proposal.update, [user_message, ConversationMessage(role="assistant", content=reply)]
                )
        return TextChatResponse(
            session_id=session.session_id, transcript=text, reply=reply,
            timings_ms=TimingMS(llm=llm_ms, total=round((perf_counter() - started) * 1000, 2)),
        )

    async def _generate(
        self, messages: Sequence[ConversationMessage], profile: CandidateProfile, language: str,
    ) -> tuple[CandidateIntakeProposal, float]:
        started = perf_counter()
        try:
            proposal = await self._generator.generate_intake(messages, profile, language)
        except LLMProviderError:
            raise
        except Exception as error:
            raise LLMGenerationError from error
        return proposal, round((perf_counter() - started) * 1000, 2)

    @staticmethod
    def _validate_and_apply(
        proposal: CandidateIntakeProposal, profile: CandidateProfile, text: str,
    ) -> CandidateProfile:
        try:
            validated = validate_proposal(proposal, text)
        except IntakeValidationError as error:
            raise _invalid_response(error.reason) from None
        except (ValueError, PydanticSerializationError):
            raise _invalid_response("unknown") from None
        try:
            return apply_candidate_update(profile, validated.update)
        except (ValueError, PydanticSerializationError):
            raise _invalid_response("profile_apply") from None
