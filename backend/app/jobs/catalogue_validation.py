"""Pure validation of the sample job catalogue. No database, no I/O. It runs before ANY database access.

`validate_catalogue(jobs)` returns a list of human-readable errors; an empty list means valid.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence

from app.jobs.catalogue_data import (
    LOCATION_LABELS, REMOTE_LABEL, CatalogueJob, slugify,
)
from app.jobs.vocabulary import CITY_KEYS, KEY_PATTERN, ROLE_KEYS, SKILL_KEYS

WORK_MODES = frozenset({"remote", "hybrid", "onsite"})
EMPLOYMENT_TYPES = frozenset({"full_time", "part_time", "contract", "internship"})
MIN_JOBS, MAX_JOBS = 20, 30
MAX_JOBS_PER_COMPANY = 2
MIN_SKILLS, MAX_SKILLS = 3, 8
MIN_DESCRIPTION, MAX_DESCRIPTION = 200, 800  # the database allows 4000; the demo content is deliberately short and concrete

# GUARDRAIL, not proof: a small list of well-known Indian and global employers and consumer brands. It catches an accidental
# real name in the company field; it cannot know every real company and cannot detect a close variant of one. The real protection
# is that the company names in catalogue_data.py were invented, and reviewers should keep it that way.
REAL_COMPANY_DENYLIST: tuple[tuple[str, ...], ...] = tuple(tuple(entry.split()) for entry in (
    "infosys", "tcs", "tata consultancy", "wipro", "hcl", "tech mahindra", "cognizant", "accenture", "capgemini", "flipkart",
    "amazon", "google", "microsoft", "apple", "meta", "zomato", "swiggy", "paytm", "ola", "razorpay", "freshworks", "zoho",
    "phonepe", "myntra", "meesho", "cred", "byju", "nykaa", "dream11", "reliance", "jio", "airtel", "mindtree", "persistent",
    "zerodha", "groww", "unacademy", "upgrad", "oracle", "ibm", "sap", "adobe", "uber", "netflix",
))

_URL = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|in|io|org|net|co|ai|dev|app|tech)\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_CALL_TO_ACTION = re.compile(
    r"\b(?:apply|resume|cv|get in touch|reach out|contact|call us|whatsapp|e-?mail)\b|\bsend\s+(?:us\s+)?your\b", re.I)
_SALARY = re.compile(
    r"₹|\brs\.?\s*\d|\binr\b|\busd\b|\$\s*\d|\blpa\b|\bctc\b|\blakhs?\b|\bsalary\b|\bstipend\b|\bcompensation\b|\bwages?\b"
    r"|\bper\s+(?:month|annum|year)\b", re.I)


def _single_line(value: str) -> bool:
    return not any(unicodedata.category(char) == "Cc" for char in value)


def _normalized(skill: str) -> bool:
    return unicodedata.normalize("NFKC", skill).casefold() == skill and skill == skill.strip() and "  " not in skill


def _duplicate_skills(skills: Sequence[str]) -> list[str]:
    """Skills repeated ignoring case (Python and python are one skill)."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for skill in skills:
        key = skill.casefold()
        if key in seen:
            duplicates.append(skill)
        seen.add(key)
    return duplicates


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _company_tokens(company: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", company).casefold())


def _denylisted(company: str) -> bool:
    tokens = _company_tokens(company)
    return any(tokens[i:i + len(entry)] == list(entry) for entry in REAL_COMPANY_DENYLIST for i in range(len(tokens)))


def _text_field(errors: list[str], label: str, name: str, value: object, *, limit: int) -> bool:
    """Common string rules. Returns True when the value is a usable string."""
    if not isinstance(value, str):
        errors.append(f"{label}: {name} must be text")
        return False
    if not value.strip():
        errors.append(f"{label}: {name} is blank")
        return False
    if value != value.strip():
        errors.append(f"{label}: {name} has leading or trailing whitespace")
    if not _single_line(value):
        errors.append(f"{label}: {name} contains a control character (tab, newline, ...)")
    if len(value) > limit:
        errors.append(f"{label}: {name} is too long (max {limit})")
    return True


def _validate_job(job: CatalogueJob, label: str) -> list[str]:
    errors: list[str] = []
    for name, limit in (("title", 120), ("company", 120), ("location_label", 120), ("slug", 80)):
        _text_field(errors, label, name, getattr(job, name), limit=limit)
    description_ok = _text_field(errors, label, "description", job.description, limit=4000)

    if isinstance(job.slug, str):
        if not KEY_PATTERN.fullmatch(job.slug):
            errors.append(f"{label}: slug is not lowercase kebab-case")
        if isinstance(job.title, str) and isinstance(job.company, str) and job.slug != slugify(job.title, job.company):
            errors.append(f"{label}: slug does not match the title and company")
    if not isinstance(job.is_sample, bool) or not job.is_sample:
        errors.append(f"{label}: job must be a sample listing (is_sample=True)")
    if job.is_active is not True:
        errors.append(f"{label}: catalogue jobs must be active (is_active=True)")
    if isinstance(job.company, str) and _denylisted(job.company):
        errors.append(f"{label}: company is on the real-company denylist")

    if job.role_key not in ROLE_KEYS:
        errors.append(f"{label}: unknown role_key {job.role_key!r}")
    if job.city_key is not None and job.city_key not in CITY_KEYS:
        errors.append(f"{label}: unknown city_key {job.city_key!r}")
    if job.work_mode not in WORK_MODES:
        errors.append(f"{label}: unknown work_mode {job.work_mode!r}")
    if job.employment_type not in EMPLOYMENT_TYPES:
        errors.append(f"{label}: unknown employment_type {job.employment_type!r}")

    if job.work_mode == "remote":
        if job.city_key is not None:
            errors.append(f"{label}: remote job must not have a city_key")
        if job.location_label != REMOTE_LABEL:
            errors.append(f"{label}: remote job location_label must be {REMOTE_LABEL!r}")
    elif job.work_mode in WORK_MODES:
        if job.city_key is None:
            errors.append(f"{label}: non-remote job needs a city_key")
        elif job.city_key in CITY_KEYS and job.location_label != LOCATION_LABELS[job.city_key]:
            errors.append(f"{label}: location_label must be {LOCATION_LABELS[job.city_key]!r} for city {job.city_key!r}")

    low, high = job.min_experience_years, job.max_experience_years
    for name, value in (("min_experience_years", low), ("max_experience_years", high)):
        if value is not None and not _is_number(value):
            errors.append(f"{label}: {name} must be a finite number or None")
        elif value is not None and not 0 <= value <= 80:
            errors.append(f"{label}: {name} must be between 0 and 80")
    if _is_number(low) and _is_number(high) and low > high:
        errors.append(f"{label}: min_experience_years exceeds max_experience_years")

    skills = job.required_skills
    if not isinstance(skills, (tuple, list)) or not all(isinstance(skill, str) for skill in skills):
        errors.append(f"{label}: required_skills must be a sequence of text")
    else:
        if not MIN_SKILLS <= len(skills) <= MAX_SKILLS:
            errors.append(f"{label}: needs {MIN_SKILLS} to {MAX_SKILLS} skills (has {len(skills)})")
        for skill in skills:
            if not _normalized(skill):
                errors.append(f"{label}: skill is not normalized: {skill!r}")
            elif skill not in SKILL_KEYS:
                errors.append(f"{label}: unknown skill {skill!r}")
        for skill in _duplicate_skills(skills):
            errors.append(f"{label}: duplicate skill {skill!r}")

    if description_ok:
        text = job.description
        if not MIN_DESCRIPTION <= len(text) <= MAX_DESCRIPTION:
            errors.append(f"{label}: description must be {MIN_DESCRIPTION} to {MAX_DESCRIPTION} characters (has {len(text)})")
        if not text.isascii():
            errors.append(f"{label}: description must be plain English text")
        if _URL.search(text):
            errors.append(f"{label}: description must not contain a URL")
        if _EMAIL.search(text):
            errors.append(f"{label}: description must not contain an email address")
        if _PHONE.search(text):
            errors.append(f"{label}: description must not contain a phone number")
        if _CALL_TO_ACTION.search(text):
            errors.append(f"{label}: description must not contain a call to action or contact route")
        if _SALARY.search(text):
            errors.append(f"{label}: description must not mention salary or pay figures")
    return errors


def _distribution_errors(jobs: Sequence[CatalogueJob]) -> list[str]:
    errors: list[str] = []
    roles = Counter(job.role_key for job in jobs)
    for role in sorted(ROLE_KEYS):
        if roles[role] < 2:
            errors.append(f"role {role!r} needs at least 2 jobs (has {roles[role]})")
    modes = Counter(job.work_mode for job in jobs)
    for mode, minimum in (("remote", 4), ("hybrid", 6), ("onsite", 6)):
        if modes[mode] < minimum:
            errors.append(f"needs at least {minimum} {mode} jobs (has {modes[mode]})")
    cities = {job.city_key for job in jobs if job.city_key is not None}
    if len(cities) < 7:
        errors.append(f"needs at least 7 distinct cities (has {len(cities)})")
    types = Counter(job.employment_type for job in jobs)
    for kind, minimum in (("full_time", 18), ("internship", 2), ("contract", 1), ("part_time", 1)):
        if types[kind] < minimum:
            errors.append(f"needs at least {minimum} {kind} jobs (has {types[kind]})")

    def number(value):
        return value if _is_number(value) else None

    lows = [(number(job.min_experience_years), number(job.max_experience_years), job) for job in jobs]
    if sum(1 for low, _, _ in lows if low is not None and low <= 1) < 4:
        errors.append("needs at least 4 fresher-friendly jobs (min experience 1 or less)")
    if sum(1 for low, _, job in lows if job.employment_type == "internship" and low == 0) < 2:
        errors.append("needs at least 2 internships with min experience 0")
    if sum(1 for low, _, _ in lows if low is not None and low >= 3) < 5:
        errors.append("needs at least 5 jobs with min experience 3 or more")
    if sum(1 for low, high, _ in lows if low is not None and low >= 5 and high is None) < 3:
        errors.append("needs at least 3 senior jobs (min experience 5 or more, no maximum)")
    if sum(1 for low, high, _ in lows if low is None and high is not None) < 1:
        errors.append("needs at least 1 job with no minimum and a maximum experience")
    if sum(1 for low, high, _ in lows if low is None and high is None) > 1:
        errors.append("at most 1 job may leave both experience bounds empty")
    return errors


def validate_catalogue(jobs: Iterable[CatalogueJob]) -> list[str]:
    """All errors for a catalogue; [] when valid."""
    jobs = list(jobs)
    errors: list[str] = []
    if not MIN_JOBS <= len(jobs) <= MAX_JOBS:
        errors.append(f"catalogue must contain between {MIN_JOBS} and {MAX_JOBS} jobs (has {len(jobs)})")
    if not all(isinstance(job, CatalogueJob) for job in jobs):
        return errors + ["every catalogue entry must be a CatalogueJob"]
    slugs = Counter(job.slug for job in jobs)
    for slug, count in sorted(slugs.items()):
        if count > 1:
            errors.append(f"duplicate slug {slug!r} ({count} jobs)")
    companies = Counter(job.company.casefold() for job in jobs if isinstance(job.company, str))
    for company, count in sorted(companies.items()):
        if count > MAX_JOBS_PER_COMPANY:
            errors.append(f"more than {MAX_JOBS_PER_COMPANY} jobs for company {company!r} ({count} jobs)")
    for index, job in enumerate(jobs, 1):
        errors.extend(_validate_job(job, job.slug if isinstance(job.slug, str) and job.slug else f"job #{index}"))
    errors.extend(_distribution_errors(jobs))
    return errors
