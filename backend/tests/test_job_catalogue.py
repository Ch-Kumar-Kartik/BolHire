"""The shipped sample catalogue (one test per rule family) and the validator's rejections (one test per rule)."""

import math
import re
from collections import Counter
from dataclasses import replace

import pytest

from app.jobs.catalogue_data import CATALOGUE, LOCATION_LABELS, REMOTE_LABEL, SAMPLE_COUNT, slugify
from app.jobs.catalogue_validation import MAX_JOBS, MIN_JOBS, REAL_COMPANY_DENYLIST, validate_catalogue
from app.jobs.vocabulary import CITY_KEYS, ROLE_KEYS, SKILL_KEYS

JOBS = list(CATALOGUE)


# ---- the shipped catalogue --------------------------------------------------------------------------------------------------------------------
def test_the_shipped_catalogue_is_valid():
    assert validate_catalogue(CATALOGUE) == []


def test_there_are_exactly_24_jobs_with_unique_slugs_derived_from_title_and_company():
    assert len(CATALOGUE) == SAMPLE_COUNT == 24
    assert len({job.slug for job in CATALOGUE}) == 24
    for job in CATALOGUE:
        assert job.slug == slugify(job.title, job.company) and len(job.slug) <= 80 and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", job.slug)


def test_every_job_is_a_sample_and_active():
    assert all(job.is_sample is True and job.is_active is True for job in CATALOGUE)


def test_companies_are_unique_enough_and_none_is_on_the_real_company_denylist():
    per_company = Counter(job.company for job in CATALOGUE)
    assert max(per_company.values()) <= 2 and len(per_company) >= 12
    for job in CATALOGUE:
        tokens = re.findall(r"[a-z0-9]+", job.company.casefold())
        for entry in REAL_COMPANY_DENYLIST:
            assert not any(tokens[i:i + len(entry)] == list(entry) for i in range(len(tokens))), (job.company, entry)
    assert len(REAL_COMPANY_DENYLIST) >= 25


def test_every_role_has_at_least_two_jobs_and_only_known_roles_are_used():
    roles = Counter(job.role_key for job in CATALOGUE)
    assert set(roles) == ROLE_KEYS and min(roles.values()) >= 2


def test_work_mode_distribution():
    modes = Counter(job.work_mode for job in CATALOGUE)
    assert modes["remote"] >= 4 and modes["hybrid"] >= 6 and modes["onsite"] >= 6 and sum(modes.values()) == 24


def test_cities_and_location_labels_are_consistent_with_the_work_mode():
    used = {job.city_key for job in CATALOGUE if job.city_key}
    assert len(used) >= 7 and used <= CITY_KEYS
    for job in CATALOGUE:
        if job.work_mode == "remote":
            assert job.city_key is None and job.location_label == REMOTE_LABEL == "Remote (India)"
        else:
            assert job.city_key in CITY_KEYS and job.location_label == LOCATION_LABELS[job.city_key]
            assert re.fullmatch(r"[A-Za-z ]+, [A-Za-z ]+", job.location_label)  # "<City>, <State>"


def test_employment_type_distribution():
    types = Counter(job.employment_type for job in CATALOGUE)
    assert types["full_time"] >= 18 and types["internship"] >= 2 and types["contract"] >= 1 and types["part_time"] >= 1
    assert set(types) <= {"full_time", "part_time", "contract", "internship"}


def test_experience_variety():
    lows = [(job.min_experience_years, job.max_experience_years, job) for job in CATALOGUE]
    assert sum(1 for low, _, _ in lows if low is not None and low <= 1) >= 4
    assert sum(1 for low, _, job in lows if job.employment_type == "internship" and low == 0) >= 2
    assert sum(1 for low, _, _ in lows if low is not None and low >= 3) >= 5
    assert sum(1 for low, high, _ in lows if low is not None and low >= 5 and high is None) >= 3
    assert sum(1 for low, high, _ in lows if low is None and high is not None) >= 1
    assert sum(1 for low, high, _ in lows if low is None and high is None) <= 1
    for low, high, _ in lows:
        assert all(v is None or (not math.isnan(v) and 0 <= v <= 80) for v in (low, high))
        assert low is None or high is None or low <= high


def test_skills_are_three_to_eight_known_normalized_and_unique():
    for job in CATALOGUE:
        assert 3 <= len(job.required_skills) <= 8, job.slug
        assert all(skill in SKILL_KEYS for skill in job.required_skills), job.slug
        assert len({skill.casefold() for skill in job.required_skills}) == len(job.required_skills), job.slug


def test_skills_are_coherent_with_the_role():
    allowed = {
        "data-analyst": {"sql", "excel", "power bi", "tableau", "python", "pandas", "numpy"},
        "ui-ux-designer": {"figma", "html", "css", "javascript"},
        "qa-engineer": {"selenium", "pytest", "python", "sql", "git", "ci/cd", "javascript", "jest", "rest apis", "manual testing"},
    }
    for job in CATALOGUE:
        if job.role_key in allowed:
            assert set(job.required_skills) <= allowed[job.role_key], job.slug
    mobile = {"kotlin", "swift", "android", "ios", "flutter", "react native", "rest apis", "git", "sql", "javascript", "typescript"}
    assert all(set(j.required_skills) <= mobile for j in CATALOGUE if j.role_key == "mobile-developer")
    assert all("kotlin" not in j.required_skills for j in CATALOGUE if j.role_key == "data-analyst")


def test_descriptions_are_plain_english_of_the_right_length_with_no_contact_or_pay_content():
    for job in CATALOGUE:
        text = job.description
        assert 200 <= len(text) <= 800 and text.isascii() and text == text.strip(), job.slug
        lowered = text.casefold()
        assert not any(marker in lowered for marker in ("http", "www.", "@", ".com", ".in/", "apply", "resume", "contact", "email", "salary", "stipend"))
        assert not re.search(r"\d{5,}", text)
        assert not re.search(r"₹|\blpa\b|\bctc\b|\bper month\b", lowered)


def test_same_role_jobs_share_core_skills_but_are_not_interchangeable():
    by_role: dict[str, list] = {}
    for job in CATALOGUE:
        by_role.setdefault(job.role_key, []).append(job)
    for role, jobs in by_role.items():
        counts = Counter(skill for job in jobs for skill in set(job.required_skills))
        assert any(count >= 2 for count in counts.values()), f"{role}: no shared core skill"
        assert len({(frozenset(j.required_skills), j.min_experience_years, j.max_experience_years, j.work_mode) for j in jobs}) == len(jobs), role
        assert len({frozenset(j.required_skills) for j in jobs}) >= 2, f"{role}: identical skill lists"
    backend = [frozenset(j.required_skills) for j in by_role["backend-developer"]]
    assert len(set(backend)) == len(backend)  # no two backend jobs are identical


# ---- the validator rejects each rule -------------------------------------------------------------------------------------------------------------
def find(title: str) -> int:
    return next(i for i, job in enumerate(JOBS) if job.title == title)


def with_job(which: str, **changes):
    """The catalogue with the job titled `which` changed; the slug follows a title or company change unless given."""
    jobs = list(JOBS)
    index = find(which)
    job = jobs[index]
    if "slug" not in changes and ("title" in changes or "company" in changes):
        changes["slug"] = slugify(changes.get("title", job.title), changes.get("company", job.company))
    jobs[index] = replace(job, **changes)
    return jobs


BACKEND, REMOTE, DATA = "Backend Developer", "Backend Developer (Node.js)", "Data Analyst"
SKILLS = ("python", "django", "postgresql")


def case(rule, jobs, fragment):
    return pytest.param(jobs, fragment, id=rule)


def extra_jobs(total):
    extras = [replace(JOBS[0], title=f"Extra Developer {n}", slug=slugify(f"Extra Developer {n}", f"Fictional Co {n}"), company=f"Fictional Co {n}")
              for n in range(total - len(JOBS))]
    return JOBS + extras


REJECTED = [
    case("duplicate slug", with_job("Frontend Developer", slug=JOBS[0].slug), "duplicate slug"),
    case("case-insensitive duplicate skill", with_job(BACKEND, required_skills=("python", "PYTHON", "django")), "duplicate skill"),
    case("exact duplicate skill", with_job(BACKEND, required_skills=("python", "python", "django")), "duplicate skill"),
    case("unknown skill", with_job(BACKEND, required_skills=("python", "django", "cobol")), "unknown skill"),
    case("unnormalized skill (uppercase)", with_job(BACKEND, required_skills=("Python", "django", "postgresql")), "skill is not normalized"),
    case("unnormalized skill (padded)", with_job(BACKEND, required_skills=(" python", "django", "postgresql")), "skill is not normalized"),
    case("unnormalized skill (full-width)", with_job(BACKEND, required_skills=("ｐｙｔｈｏｎ", "django", "postgresql")), "skill is not normalized"),
    case("fewer than 3 skills", with_job(BACKEND, required_skills=("python", "django")), "needs 3 to 8 skills"),
    case("more than 8 skills", with_job(BACKEND, required_skills=("python", "django", "postgresql", "docker", "git", "linux", "aws", "redis", "sql")), "needs 3 to 8 skills"),
    case("unknown role", with_job(BACKEND, role_key="rocket-scientist"), "unknown role_key"),
    case("unknown city", with_job(BACKEND, city_key="atlantis"), "unknown city_key"),
    case("remote job with a city_key", with_job(REMOTE, city_key="pune"), "remote job must not have a city_key"),
    case("remote job with the wrong label", with_job(REMOTE, location_label="Pune, Maharashtra"), "location_label must be 'Remote (India)'"),
    case("hybrid job without a city_key", with_job(BACKEND, city_key=None), "non-remote job needs a city_key"),
    case("location label inconsistent with the city", with_job(BACKEND, location_label="Mumbai, Maharashtra"), "location_label must be"),
    case("unknown work_mode", with_job(BACKEND, work_mode="office"), "unknown work_mode"),
    case("unknown employment_type", with_job(BACKEND, employment_type="freelance"), "unknown employment_type"),
    case("URL in the description", with_job(BACKEND, description=JOBS[0].description + " Read more at https://example.com/jobs today."), "must not contain a URL"),
    case("bare domain in the description", with_job(BACKEND, description=JOBS[0].description + " Details are on kestrelbyte.com for everyone."), "must not contain a URL"),
    case("email in the description", with_job(BACKEND, description=JOBS[0].description + " Write to hiring@kestrelbyte.example for details."), "must not contain an email"),
    case("phone number in the description", with_job(BACKEND, description=JOBS[0].description + " Call +91 98765 43210 after reading this."), "must not contain a phone number"),
    case("apply call to action", with_job(BACKEND, description=JOBS[0].description + " Please apply now."), "call to action"),
    case("send-your-resume call to action", with_job(BACKEND, description=JOBS[0].description + " Send your resume to the team."), "call to action"),
    case("contact route", with_job(BACKEND, description=JOBS[0].description + " Contact the hiring manager directly."), "call to action"),
    case("salary figure", with_job(BACKEND, description=JOBS[0].description + " The salary is generous."), "salary or pay figures"),
    case("currency amount", with_job(BACKEND, description=JOBS[0].description + " Pay is ₹ 12 per hour."), "salary or pay figures"),
    case("non-English description", with_job(BACKEND, description=JOBS[0].description + " यह एक नमूना है"), "plain English"),
    case("description too short", with_job(BACKEND, description="Too short to be a real description."), "description must be 200 to 800"),
    case("description above the demo limit", with_job(BACKEND, description="x " * 401), "description must be 200 to 800"),
    case("description above the database limit", with_job(BACKEND, description="word " * 900), "description is too long (max 4000)"),
    case("title above 120 characters", with_job(BACKEND, title="T" * 121), "title is too long (max 120)"),
    case("company above 120 characters", with_job(BACKEND, company="C" * 121), "company is too long (max 120)"),
    case("location_label above 120 characters", with_job(BACKEND, location_label="L" * 121), "location_label is too long (max 120)"),
    case("slug above 80 characters", with_job(BACKEND, slug="s" * 81), "slug is too long (max 80)"),
    case("slug not kebab-case", with_job(BACKEND, slug="Backend_Dev"), "slug is not lowercase kebab-case"),
    case("slug not derived from title and company", with_job(BACKEND, slug="something-else"), "slug does not match the title and company"),
    case("blank title", with_job(BACKEND, title="   ", slug=JOBS[0].slug), "title is blank"),
    case("tab-only company", with_job(BACKEND, company="\t", slug=JOBS[0].slug), "company is blank"),
    case("newline in the title", with_job(BACKEND, title="Back\nend Developer", slug=JOBS[0].slug), "title contains a control character"),
    case("whitespace-padded title", with_job(BACKEND, title=" Backend Developer", slug=JOBS[0].slug), "title has leading or trailing whitespace"),
    case("minimum above maximum", with_job(BACKEND, min_experience_years=6, max_experience_years=3), "min_experience_years exceeds max_experience_years"),
    case("negative experience", with_job(BACKEND, min_experience_years=-1), "min_experience_years must be between 0 and 80"),
    case("experience above 80", with_job(BACKEND, max_experience_years=81), "max_experience_years must be between 0 and 80"),
    case("NaN experience", with_job(BACKEND, min_experience_years=float("nan")), "min_experience_years must be a finite number"),
    case("boolean experience", with_job(BACKEND, min_experience_years=True), "min_experience_years must be a finite number"),
    case("real company: Infosys", with_job(BACKEND, company="Infosys Labs"), "company is on the real-company denylist"),
    case("real company: multi-word entry", with_job(BACKEND, company="Tata Consultancy Services"), "company is on the real-company denylist"),
    case("real company: different case", with_job(BACKEND, company="FLIPKART India"), "company is on the real-company denylist"),
    case("non-sample job", with_job(BACKEND, is_sample=False), "must be a sample listing"),
    case("inactive job", with_job(BACKEND, is_active=False), "must be active"),
    case("19 jobs (below the minimum)", JOBS[:19], "between 20 and 30 jobs (has 19)"),
    case("31 jobs (above the maximum)", extra_jobs(31), "between 20 and 30 jobs (has 31)"),
]


@pytest.mark.parametrize("jobs, fragment", REJECTED)
def test_the_validator_rejects_each_rule(jobs, fragment):
    errors = validate_catalogue(jobs)
    assert any(fragment in error for error in errors), (fragment, errors)


COUNT_ERROR = "between 20 and 30 jobs"


@pytest.mark.parametrize("jobs", [JOBS[:20], extra_jobs(30)], ids=["20 jobs", "30 jobs"])
def test_the_boundary_counts_do_not_produce_the_count_error(jobs):
    assert not any(COUNT_ERROR in error for error in validate_catalogue(jobs))


def test_the_count_error_names_both_bounds_and_the_shipped_count_is_inside_them():
    assert MIN_JOBS == 20 and MAX_JOBS == 30 and MIN_JOBS <= len(CATALOGUE) <= MAX_JOBS
    assert not any(COUNT_ERROR in error for error in validate_catalogue(CATALOGUE))


def test_more_than_two_jobs_for_one_company_is_rejected():
    jobs = list(JOBS)
    company = jobs[0].company  # already used twice
    third = find("Frontend Developer")
    jobs[third] = replace(jobs[third], company=company, slug=slugify(jobs[third].title, company))
    assert any("more than 2 jobs for company" in error for error in validate_catalogue(jobs))


def test_distribution_rules_are_enforced():
    def convert(jobs, predicate, **changes):
        return [replace(j, **changes) if predicate(j) else j for j in jobs]

    no_remote = convert(JOBS, lambda j: j.work_mode == "remote", work_mode="hybrid", city_key="pune", location_label=LOCATION_LABELS["pune"])
    assert any("at least 4 remote" in error for error in validate_catalogue(no_remote))
    one_city = convert(JOBS, lambda j: j.city_key is not None, city_key="pune", location_label=LOCATION_LABELS["pune"])
    assert any("at least 7 distinct cities" in error for error in validate_catalogue(one_city))
    no_internships = convert(JOBS, lambda j: j.employment_type == "internship", employment_type="full_time")
    assert any("at least 2 internship" in error for error in validate_catalogue(no_internships))
    one_role_missing = convert(JOBS, lambda j: j.role_key == "ui-ux-designer", role_key="qa-engineer")
    assert any("role 'ui-ux-designer' needs at least 2 jobs" in error for error in validate_catalogue(one_role_missing))
    all_both_null = convert(JOBS, lambda j: j.role_key in {"qa-engineer", "devops-engineer"}, min_experience_years=None, max_experience_years=None)
    assert any("at most 1 job may leave both experience bounds empty" in error for error in validate_catalogue(all_both_null))


def test_a_non_job_entry_is_rejected():
    assert any("must be a CatalogueJob" in error for error in validate_catalogue(JOBS[:-1] + ["not a job"]))


def test_validation_is_pure_and_repeatable():
    assert validate_catalogue(CATALOGUE) == validate_catalogue(list(CATALOGUE)) == []
