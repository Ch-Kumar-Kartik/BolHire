"""Deterministic scoring of jobs against a candidate profile. Pure: no database, no I/O, no input mutation.

Every score is an exact `Fraction`; floats are never used for ranking. The profile is resolved once, so which criteria are
evaluated is the same for every job. A criterion that cannot be evaluated is `not_evaluated` and is left out of the average
rather than counted as a miss.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import floor

from app.jobs.resolver import resolve_cities, resolve_experience_level, resolve_roles, resolve_skills
from app.services.candidate_profile import CandidateProfile

WEIGHTS: dict[str, int] = {"skills": 50, "role": 20, "experience": 15, "location": 10, "work_mode": 5}
CRITERIA_ORDER = tuple(WEIGHTS)
MAX_LIMIT = 50
_HALF = Fraction(1, 2)
_NEAR_MISS_BELOW_MIN, _NEAR_MISS_ABOVE_MAX = Fraction(1), Fraction(2)
# [start, end) in years; senior is open-ended.
LEVEL_BANDS: dict[str, tuple[Fraction, Fraction | None]] = {
    "fresher": (Fraction(0), Fraction(1)), "junior": (Fraction(1), Fraction(3)),
    "mid": (Fraction(3), Fraction(6)), "senior": (Fraction(6), None),
}


@dataclass(frozen=True)
class MatchableJob:
    id: str
    slug: str
    title: str
    company: str
    location_label: str
    description: str
    employment_type: str
    role_key: str
    city_key: str | None
    work_mode: str
    min_experience_years: float | None
    max_experience_years: float | None
    required_skills: tuple[str, ...]


@dataclass(frozen=True)
class CriterionResult:
    name: str
    weight: int
    state: str  # matched | partial | unmatched | not_evaluated
    score_percent: int | None
    reason: str | None = None  # missing | declined | unrecognized, only when not_evaluated
    matched_skills: tuple[str, ...] = ()
    missing_skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class JobMatch:
    job: MatchableJob
    score_percent: int
    criteria: tuple[CriterionResult, ...]


@dataclass(frozen=True)
class MatchOutcome:
    status: str  # ok | insufficient_profile | no_jobs
    coverage_percent: int
    matches: tuple[JobMatch, ...] = ()


def half_up_percent(score: Fraction) -> int:
    """Exact half-up rounding of score * 100."""
    return floor(score * 100 + _HALF)


def _exact(value: float | int) -> Fraction:
    return Fraction(str(float(value)))  # the decimal the person wrote, not its binary approximation


def _state(score: Fraction) -> str:
    return "matched" if score == 1 else "unmatched" if score == 0 else "partial"


@dataclass(frozen=True)
class _Evaluator:
    """One criterion for one profile: either a reason it is not evaluated, or a scoring function over a job."""

    name: str
    reason: str | None = None
    score: Callable[[MatchableJob], Fraction] | None = None

    @property
    def evaluated(self) -> bool:
        return self.score is not None


def _not_evaluated(name: str, reason: str) -> _Evaluator:
    return _Evaluator(name, reason=reason)


def _skill_evaluator(profile: CandidateProfile) -> _Evaluator:
    if "technical_skills" in profile.declined_fields:
        return _not_evaluated("skills", "declined")
    if not profile.technical_skills:
        return _not_evaluated("skills", "missing")
    resolved = resolve_skills(profile.technical_skills)
    if not resolved:
        return _not_evaluated("skills", "unrecognized")
    return _Evaluator("skills", score=lambda job: _skill_fraction(job, resolved))


def _required(job: MatchableJob) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered = []
    for skill in job.required_skills:
        if skill.casefold() not in seen:
            seen.add(skill.casefold())
            ordered.append(skill)
    return tuple(ordered)


def _skill_fraction(job: MatchableJob, resolved: frozenset[str]) -> Fraction:
    required = _required(job)
    if not required:
        return Fraction(0)  # a job that lists no skills gives nothing to match on
    return Fraction(sum(1 for skill in required if skill in resolved), len(required))


def _resolved_evaluator(
    name: str, field: str, value: str | None, profile: CandidateProfile, resolve: Callable[[str], frozenset[str]],
    score: Callable[[MatchableJob, frozenset[str]], Fraction],
) -> _Evaluator:
    if field in profile.declined_fields:
        return _not_evaluated(name, "declined")
    if value is None:
        return _not_evaluated(name, "missing")
    resolved = resolve(value)
    if not resolved:
        return _not_evaluated(name, "unrecognized")
    return _Evaluator(name, score=lambda job: score(job, resolved))


def _years_fraction(job: MatchableJob, years: Fraction) -> Fraction:
    low, high = job.min_experience_years, job.max_experience_years
    if low is not None and years < _exact(low):
        return _HALF if _exact(low) - years <= _NEAR_MISS_BELOW_MIN else Fraction(0)
    if high is not None and years > _exact(high):
        return _HALF if years - _exact(high) <= _NEAR_MISS_ABOVE_MAX else Fraction(0)
    return Fraction(1)


def _band_overlaps(job: MatchableJob, level: str) -> bool:
    start, end = LEVEL_BANDS[level]
    low, high = job.min_experience_years, job.max_experience_years
    return (high is None or start <= _exact(high)) and (low is None or end is None or _exact(low) < end)


def _experience_evaluator(profile: CandidateProfile) -> _Evaluator:
    if "experience" in profile.declined_fields:
        return _not_evaluated("experience", "declined")
    if profile.experience_years is not None:
        years = _exact(profile.experience_years)
        return _Evaluator("experience", score=lambda job: _years_fraction(job, years))
    if profile.experience_level is None:
        return _not_evaluated("experience", "missing")
    levels = resolve_experience_level(profile.experience_level)
    if not levels:
        return _not_evaluated("experience", "unrecognized")
    return _Evaluator("experience", score=lambda job: Fraction(int(any(_band_overlaps(job, level) for level in levels))))


def _work_mode_evaluator(profile: CandidateProfile) -> _Evaluator:
    if "work_mode" in profile.declined_fields:
        return _not_evaluated("work_mode", "declined")
    mode = profile.work_mode
    if mode is None:
        return _not_evaluated("work_mode", "missing")
    return _Evaluator("work_mode", score=lambda job: Fraction(int(mode == "no_preference" or mode == job.work_mode)))


def _evaluators(profile: CandidateProfile) -> tuple[_Evaluator, ...]:
    return (
        _skill_evaluator(profile),
        _resolved_evaluator("role", "target_role", profile.target_role, profile, resolve_roles,
                            lambda job, roles: Fraction(int(job.role_key in roles))),
        _experience_evaluator(profile),
        _resolved_evaluator("location", "preferred_location", profile.preferred_location, profile, resolve_cities,
                            lambda job, cities: Fraction(int(job.work_mode == "remote" or job.city_key in cities))),
        _work_mode_evaluator(profile),
    )


def _criterion(evaluator: _Evaluator, score: Fraction | None, job: MatchableJob, resolved_skills: frozenset[str]) -> CriterionResult:
    weight = WEIGHTS[evaluator.name]
    if score is None:
        return CriterionResult(evaluator.name, weight, "not_evaluated", None, evaluator.reason)
    matched = missing = ()
    if evaluator.name == "skills":
        required = _required(job)
        matched = tuple(skill for skill in required if skill in resolved_skills)
        missing = tuple(skill for skill in required if skill not in resolved_skills)
    return CriterionResult(evaluator.name, weight, _state(score), half_up_percent(score), None, matched, missing)


def match_jobs(profile: CandidateProfile, jobs: Sequence[MatchableJob], limit: int = 10) -> MatchOutcome:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {MAX_LIMIT}.")
    evaluators = _evaluators(profile)
    coverage = sum(WEIGHTS[e.name] for e in evaluators if e.evaluated)
    by_name = {e.name: e for e in evaluators}
    if not (by_name["skills"].evaluated or by_name["role"].evaluated):
        return MatchOutcome("insufficient_profile", coverage, ())
    if not jobs:
        return MatchOutcome("no_jobs", coverage, ())
    resolved_skills = resolve_skills(profile.technical_skills or ()) if by_name["skills"].evaluated else frozenset()

    ranked: list[tuple[Fraction, Fraction, str, JobMatch]] = []
    for job in jobs:
        scores = {e.name: (e.score(job) if e.score is not None else None) for e in evaluators}
        key_scores = [scores[name] for name in ("skills", "role") if scores[name] is not None]
        if all(score == 0 for score in key_scores):
            continue
        overall = sum(WEIGHTS[name] * score for name, score in scores.items() if score is not None) / coverage
        criteria = tuple(_criterion(by_name[name], scores[name], job, resolved_skills) for name in CRITERIA_ORDER)
        ranked.append((overall, scores["skills"] or Fraction(0), job.slug, JobMatch(job, half_up_percent(overall), criteria)))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return MatchOutcome("ok", coverage, tuple(item[3] for item in ranked[:limit]))
