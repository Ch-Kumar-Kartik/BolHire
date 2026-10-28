"""Validated, session-scoped candidate profile updates."""

from math import isfinite
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


ProfileField = Literal[
    "name", "target_role", "experience", "technical_skills",
    "preferred_location", "work_mode",
]
ClearableField = Literal[
    "name", "target_role", "experience", "experience_years",
    "experience_level", "technical_skills", "preferred_location", "work_mode",
]
WorkMode = Literal["remote", "hybrid", "onsite", "no_preference"]

Name = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=80)]
Role = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)]
Level = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=60)]
Skill = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=60)]
Location = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)]
MAX_SKILLS = 20


def _check_unique(values: list[str] | tuple[str, ...], label: str) -> None:
    if len({value.casefold() for value in values}) != len(values):
        raise ValueError(f"{label} contains duplicate values.")


def _check_years(value: object) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float) or not 0 <= value <= 80 or not isfinite(value):
        raise ValueError("experience_years must be a finite number between 0 and 80.")
    return float(value)


class CandidateProfile(BaseModel):
    """None means unknown; declined_fields records explicit non-disclosure."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: Name | None = None
    target_role: Role | None = None
    experience_years: float | None = None
    experience_level: Level | None = None
    technical_skills: tuple[Skill, ...] | None = Field(default=None, max_length=MAX_SKILLS)
    preferred_location: Location | None = None
    work_mode: WorkMode | None = None
    declined_fields: frozenset[ProfileField] = frozenset()

    @field_validator("experience_years", mode="before")
    @classmethod
    def check_years(cls, value: object) -> float | None:
        return _check_years(value)

    @model_validator(mode="after")
    def check_consistency(self) -> "CandidateProfile":
        if self.technical_skills is not None:
            _check_unique(self.technical_skills, "technical_skills")
        values = {
            "name": self.name is not None,
            "target_role": self.target_role is not None,
            "experience": self.experience_years is not None or self.experience_level is not None,
            "technical_skills": self.technical_skills is not None,
            "preferred_location": self.preferred_location is not None,
            "work_mode": self.work_mode is not None,
        }
        if any(values[field] for field in self.declined_fields):
            raise ValueError("A declined field cannot also have a value.")
        return self


class CandidateProfileUpdate(BaseModel):
    """Only explicitly supplied fields change; clears and declines are separate."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: Name | None = None
    target_role: Role | None = None
    experience_years: float | None = None
    experience_level: Level | None = None
    preferred_location: Location | None = None
    work_mode: WorkMode | None = None
    skills_replace: list[Skill] | None = Field(default=None, max_length=MAX_SKILLS)
    skills_add: list[Skill] = Field(default_factory=list, max_length=MAX_SKILLS)
    skills_remove: list[Skill] = Field(default_factory=list, max_length=MAX_SKILLS)
    clear_fields: list[ClearableField] = Field(default_factory=list, max_length=8)
    decline_fields: list[ProfileField] = Field(default_factory=list, max_length=6)

    @field_validator("experience_years", mode="before")
    @classmethod
    def check_years(cls, value: object) -> float | None:
        return _check_years(value)

    @model_validator(mode="after")
    def check_operations(self) -> "CandidateProfileUpdate":
        scalar_fields = {
            "name", "target_role", "experience_years", "experience_level",
            "preferred_location", "work_mode",
        }
        for field in scalar_fields | {"skills_replace"}:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"Use clear_fields to clear {field}.")
        _check_unique(self.skills_add, "skills_add")
        _check_unique(self.skills_remove, "skills_remove")
        if self.skills_replace is not None:
            _check_unique(self.skills_replace, "skills_replace")
        if len(set(self.clear_fields)) != len(self.clear_fields):
            raise ValueError("clear_fields contains duplicates.")
        if len(set(self.decline_fields)) != len(self.decline_fields):
            raise ValueError("decline_fields contains duplicates.")
        if {skill.casefold() for skill in self.skills_add} & {skill.casefold() for skill in self.skills_remove}:
            raise ValueError("A skill cannot be added and removed in the same update.")
        if self.skills_replace is not None and (self.skills_add or self.skills_remove):
            raise ValueError("skills_replace cannot be combined with skill additions or removals.")
        if "experience" in self.clear_fields and (
            "experience_years" in self.clear_fields or "experience_level" in self.clear_fields
        ):
            raise ValueError("Clear experience or its parts, not both.")
        changed = set(self.model_fields_set) & scalar_fields
        if self.skills_replace is not None or self.skills_add or self.skills_remove:
            changed.add("technical_skills")
        for field in self.clear_fields:
            if field in changed or (field == "experience" and changed & {"experience_years", "experience_level"}):
                raise ValueError("A field cannot be set and cleared in the same update.")
        for field in self.decline_fields:
            if field in self.clear_fields or field in changed or (
                field == "experience" and changed & {"experience_years", "experience_level"}
            ) or (field == "experience" and set(self.clear_fields) & {"experience_years", "experience_level"}):
                raise ValueError("A field cannot be changed and declined in the same update.")
        return self


def apply_candidate_update(profile: CandidateProfile, update: CandidateProfileUpdate) -> CandidateProfile:
    """Build a fully validated replacement without mutating the current profile."""

    values = profile.model_dump()
    declined = set(profile.declined_fields)
    scalar_fields = (
        "name", "target_role", "experience_years", "experience_level",
        "preferred_location", "work_mode",
    )
    for field in scalar_fields:
        if field in update.model_fields_set:
            values[field] = getattr(update, field)
            declined.discard("experience" if field.startswith("experience_") else field)
    for field in update.clear_fields:
        fields = ("experience_years", "experience_level") if field == "experience" else (field,)
        for item in fields:
            values[item] = None
        declined.discard("experience" if field.startswith("experience_") else field)
    for field in update.decline_fields:
        fields = ("experience_years", "experience_level") if field == "experience" else (field,)
        for item in fields:
            values[item] = None
        declined.add(field)
    if update.skills_replace is not None:
        values["technical_skills"] = tuple(update.skills_replace)
        declined.discard("technical_skills")
    elif update.skills_add:
        current = values["technical_skills"] or ()
        existing = {skill.casefold() for skill in current}
        additions = [skill for skill in update.skills_add if skill.casefold() not in existing]
        values["technical_skills"] = (*current, *additions)
        declined.discard("technical_skills")
    if update.skills_remove and values["technical_skills"] is not None:
        removed = {skill.casefold() for skill in update.skills_remove}
        values["technical_skills"] = tuple(
            skill for skill in values["technical_skills"] if skill.casefold() not in removed
        )
    values["declined_fields"] = frozenset(declined)
    return CandidateProfile.model_validate(values)
