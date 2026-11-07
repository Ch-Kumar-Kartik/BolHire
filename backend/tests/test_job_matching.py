import copy
import random
from dataclasses import replace
from fractions import Fraction

import pytest

from app.jobs.catalogue_data import CATALOGUE
from app.jobs.matching import WEIGHTS, JobMatch, MatchableJob, MatchOutcome, half_up_percent, match_jobs
from app.services.candidate_profile import CandidateProfile


def job(slug="job", **changes) -> MatchableJob:
    base = dict(
        id=slug, slug=slug, title="Backend Developer", company="Fictional Co", location_label="Pune, Maharashtra",
        description="d", employment_type="full_time", role_key="backend-developer", city_key="pune", work_mode="hybrid",
        min_experience_years=None, max_experience_years=None, required_skills=("python", "django", "sql"),
    )
    return MatchableJob(**{**base, "slug": slug, "id": slug, **changes})


def profile(declined=(), **fields) -> CandidateProfile:
    return CandidateProfile(declined_fields=frozenset(declined), **fields)


SKILLS = {"technical_skills": ("python", "django", "sql")}
ROLE = {"target_role": "backend developer"}
KEY = {**SKILLS, **ROLE}  # a profile that always evaluates both key criteria


def criteria(candidate, the_job=None):
    """The criteria of the single job's match, by name."""
    outcome = match_jobs(candidate, [the_job or job()])
    assert len(outcome.matches) == 1, outcome
    return {c.name: c for c in outcome.matches[0].criteria}


# ---- weights, order, shape -----------------------------------------------------------------------------------------------------------------------
def test_weights_sum_to_100():
    assert WEIGHTS == {"skills": 50, "role": 20, "experience": 15, "location": 10, "work_mode": 5}
    assert sum(WEIGHTS.values()) == 100


def test_criteria_come_in_the_fixed_order_with_their_weights():
    match = match_jobs(profile(**KEY), [job()]).matches[0]
    assert [(c.name, c.weight) for c in match.criteria] == [("skills", 50), ("role", 20), ("experience", 15), ("location", 10), ("work_mode", 5)]


def test_the_result_types_are_frozen_tuples():
    outcome = match_jobs(profile(**KEY), [job()])
    assert isinstance(outcome, MatchOutcome) and isinstance(outcome.matches, tuple) and isinstance(outcome.matches[0], JobMatch)
    with pytest.raises(AttributeError):
        outcome.status = "x"


# ---- skills --------------------------------------------------------------------------------------------------------------------------------------
def test_skills_all_required_skills_matched():
    skills = criteria(profile(**KEY))["skills"]
    assert (skills.state, skills.score_percent, skills.matched_skills, skills.missing_skills) == ("matched", 100, ("python", "django", "sql"), ())


def test_skills_partial_follows_the_job_order_and_uses_aliases():
    skills = criteria(profile(target_role="backend developer", technical_skills=("पाइथन", "SQL")))["skills"]
    assert (skills.state, skills.score_percent, skills.matched_skills, skills.missing_skills) == ("partial", 67, ("python", "sql"), ("django",))


def test_skills_unmatched_when_the_role_keeps_the_job_in():
    skills = criteria(profile(target_role="backend developer", technical_skills=("figma",)))["skills"]
    assert (skills.state, skills.score_percent, skills.matched_skills, skills.missing_skills) == ("unmatched", 0, (), ("python", "django", "sql"))


def test_skills_required_by_the_job_are_deduplicated_ignoring_case():
    skills = criteria(profile(**KEY), job(required_skills=("python", "Python", "sql")))["skills"]
    assert (skills.score_percent, skills.matched_skills, skills.missing_skills) == (100, ("python", "sql"), ())


def test_extra_candidate_skills_do_not_change_the_score():
    assert criteria(profile(target_role="backend developer", technical_skills=("python", "django", "sql", "figma", "docker")))["skills"].score_percent == 100


def test_a_job_with_no_required_skills_scores_zero_on_skills():
    skills = criteria(profile(**KEY), job(required_skills=()))["skills"]
    assert (skills.state, skills.score_percent) == ("unmatched", 0)


# ---- role ----------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, role_key, state", [
    ("backend developer", "backend-developer", "matched"),
    ("software engineer", "full-stack-developer", "matched"),
    ("software engineer", "data-analyst", "unmatched"),
    ("बैकएंड डेवलपर", "backend-developer", "matched"),
])
def test_role(text, role_key, state):
    result = criteria(profile(technical_skills=("python",), target_role=text), job(role_key=role_key))["role"]
    assert (result.state, result.score_percent) == (state, 100 if state == "matched" else 0)


# ---- experience ----------------------------------------------------------------------------------------------------------------------------------
def exp(years, low, high):
    return criteria(profile(**KEY, experience_years=years), job(min_experience_years=low, max_experience_years=high))["experience"]


@pytest.mark.parametrize("years, low, high, percent", [
    (3, 2, 5, 100), (2, 2, 5, 100), (5, 2, 5, 100), (4, None, None, 100), (1, None, 3, 100), (30, 5, None, 100),
    (2.0, 3, 5, 50),        # gap exactly 1 below the minimum
    (1.99, 3, 5, 0),        # gap 1.01
    (7.0, 2, 5, 50),        # excess exactly 2 above the maximum
    (7.01, 2, 5, 0),        # excess 2.01
    (0, 0, 2, 100),         # zero years is a value, not "missing"
    (0, 1, 3, 50),
    (0, 2, 3, 0),
    (1.1, 2.1, 4, 50),      # decimal gap of exactly 1 must not be lost to binary floating point
])
def test_years_against_the_job_range(years, low, high, percent):
    assert exp(years, low, high).score_percent == percent


def test_years_states():
    assert (exp(2.0, 3, 5).state, exp(3, 2, 5).state, exp(1.99, 3, 5).state) == ("partial", "matched", "unmatched")


def test_zero_years_is_evaluated():
    assert exp(0, 0, 2).state == "matched"


def test_years_override_the_level():
    both = criteria(profile(**KEY, experience_years=8, experience_level="fresher"), job(min_experience_years=5))["experience"]
    assert both.state == "matched"
    wrong = criteria(profile(**KEY, experience_years=0, experience_level="senior"), job(min_experience_years=5))["experience"]
    assert wrong.state == "unmatched"


def level(text, low, high):
    return criteria(profile(**KEY, experience_level=text), job(min_experience_years=low, max_experience_years=high))["experience"]


@pytest.mark.parametrize("text, low, high, percent", [
    ("fresher", None, None, 100), ("fresher", 0, 1, 100), ("fresher", 0, 2, 100),
    ("fresher", 1, 3, 0),       # band [0,1) does not reach a minimum of 1
    ("junior", 3, 5, 0),        # band [1,3) does not reach a minimum of 3
    ("junior", 1, 3, 100), ("junior", 2, None, 100),
    ("mid", 6, None, 0),        # band [3,6) does not reach a minimum of 6
    ("mid", 5, 8, 100), ("mid", 0, 2, 0),
    ("senior", None, 5, 0),     # band [6, inf) is beyond a maximum of 5
    ("senior", 5, None, 100), ("senior", 6, 6, 100),
    ("junior to mid", 3, 5, 100),   # several levels: any overlap counts
    ("fresher or senior", 6, None, 100),
    ("मैं फ्रेशर हूँ", 0, 1, 100),
])
def test_level_bands(text, low, high, percent):
    assert level(text, low, high).score_percent == percent


def test_levels_never_score_partially():
    assert {level("mid", low, None).state for low in (0, 1, 2, 3, 4, 5, 6, 7)} <= {"matched", "unmatched"}


# ---- location ------------------------------------------------------------------------------------------------------------------------------------
def loc(text, **changes):
    return criteria(profile(**KEY, preferred_location=text), job(**changes))["location"]


def test_location_matches_the_job_city():
    assert loc("Pune").state == "matched" and loc("पुणे").state == "matched"


def test_location_does_not_match_another_city():
    assert loc("Mumbai").state == "unmatched"


def test_a_remote_job_matches_any_recognized_location():
    assert loc("Mumbai", work_mode="remote", city_key=None, location_label="Remote (India)").state == "matched"


@pytest.mark.parametrize("city", ["delhi", "noida", "gurugram"])
def test_ncr_matches_each_of_its_three_cities(city):
    assert loc("NCR", city_key=city).state == "matched"


def test_a_list_of_cities_matches_either():
    assert loc("Delhi/Noida", city_key="noida").state == "matched" and loc("Delhi/Noida", city_key="pune").state == "unmatched"


# ---- work mode -----------------------------------------------------------------------------------------------------------------------------------
def mode(value, job_mode):
    return criteria(profile(**KEY, work_mode=value), job(work_mode=job_mode))["work_mode"]


@pytest.mark.parametrize("wanted, actual, state", [
    ("remote", "remote", "matched"), ("hybrid", "hybrid", "matched"), ("onsite", "onsite", "matched"),
    ("remote", "onsite", "unmatched"), ("hybrid", "remote", "unmatched"),
    ("no_preference", "remote", "matched"), ("no_preference", "hybrid", "matched"), ("no_preference", "onsite", "matched"),
])
def test_work_mode(wanted, actual, state):
    assert mode(wanted, actual).state == state


def test_no_preference_is_evaluated_and_counts_toward_coverage():
    assert match_jobs(profile(**KEY, work_mode="no_preference"), [job()]).coverage_percent == 75


# ---- declined, missing, unrecognized -----------------------------------------------------------------------------------------------------------------
NAMES = {"skills": "technical_skills", "role": "target_role", "experience": "experience", "location": "preferred_location", "work_mode": "work_mode"}


@pytest.mark.parametrize("name, base", [
    ("skills", ROLE), ("role", SKILLS), ("experience", KEY), ("location", KEY), ("work_mode", KEY),
])
def test_a_declined_criterion_is_not_evaluated_with_the_reason_declined(name, base):
    result = criteria(profile(declined=[NAMES[name]], **base))[name]
    assert (result.state, result.score_percent, result.reason) == ("not_evaluated", None, "declined")


@pytest.mark.parametrize("name, base", [
    ("skills", ROLE), ("role", SKILLS), ("experience", KEY), ("location", KEY), ("work_mode", KEY),
])
def test_a_missing_criterion_is_not_evaluated_with_the_reason_missing(name, base):
    result = criteria(profile(**base))[name]
    assert (result.state, result.score_percent, result.reason) == ("not_evaluated", None, "missing")


def test_an_empty_skill_tuple_is_missing():
    assert criteria(profile(technical_skills=(), **ROLE))["skills"].reason == "missing"


@pytest.mark.parametrize("name, base, fields", [
    ("skills", ROLE, {"technical_skills": ("cobol", "fortran")}),
    ("role", SKILLS, {"target_role": "astronaut"}),
    ("experience", KEY, {"experience_level": "wizard"}),
    ("location", KEY, {"preferred_location": "Atlantis"}),
])
def test_an_unrecognized_value_is_not_evaluated_with_the_reason_unrecognized(name, base, fields):
    result = criteria(profile(**base, **fields))[name]
    assert (result.state, result.score_percent, result.reason) == ("not_evaluated", None, "unrecognized")


def test_declined_experience_covers_both_years_and_level():
    result = criteria(profile(declined=["experience"], **KEY))["experience"]
    assert result.reason == "declined"


def test_an_evaluated_criterion_has_no_reason():
    assert all(c.reason is None for c in match_jobs(profile(**KEY, experience_years=3, preferred_location="Pune", work_mode="hybrid"), [job()]).matches[0].criteria)


# ---- coverage and statuses ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("fields, coverage", [
    (SKILLS, 50), (ROLE, 20), (KEY, 70),
    ({**KEY, "work_mode": "remote"}, 75),
    ({**KEY, "experience_years": 2, "preferred_location": "Pune", "work_mode": "hybrid"}, 100),
    ({**SKILLS, "preferred_location": "Pune"}, 60),
    ({**SKILLS, "preferred_location": "Atlantis"}, 50),
])
def test_coverage_is_the_evaluated_weight(fields, coverage):
    assert match_jobs(profile(**fields), [job()]).coverage_percent == coverage


def test_an_empty_profile_is_insufficient():
    assert match_jobs(profile(), [job()]) == MatchOutcome("insufficient_profile", 0, ())


def test_other_fields_do_not_make_an_insufficient_profile_sufficient():
    outcome = match_jobs(profile(experience_years=3, preferred_location="Pune", work_mode="hybrid"), [job()])
    assert outcome == MatchOutcome("insufficient_profile", 30, ())


def test_unrecognized_skills_and_role_are_insufficient():
    assert match_jobs(profile(technical_skills=("cobol",), target_role="astronaut"), [job()]).status == "insufficient_profile"


def test_insufficient_profile_is_checked_before_no_jobs():
    assert match_jobs(profile(), []).status == "insufficient_profile"


def test_no_jobs():
    assert match_jobs(profile(**KEY), []) == MatchOutcome("no_jobs", 70, ())


# ---- exclusion -----------------------------------------------------------------------------------------------------------------------------------
def test_a_job_scoring_zero_on_every_evaluated_key_criterion_is_excluded():
    other = job("other", role_key="qa-engineer", required_skills=("selenium", "pytest", "jest"))
    assert match_jobs(profile(**KEY, work_mode="hybrid"), [other]).matches == ()


def test_a_role_match_alone_keeps_a_job_in():
    assert len(match_jobs(profile(**KEY), [job(required_skills=("figma", "excel", "tableau"))]).matches) == 1


def test_a_skills_match_alone_keeps_a_job_in():
    assert len(match_jobs(profile(**KEY), [job(role_key="qa-engineer")]).matches) == 1


def test_with_only_skills_evaluated_a_zero_skill_score_excludes_the_job():
    assert match_jobs(profile(**SKILLS, work_mode="hybrid"), [job(required_skills=("figma", "excel", "tableau"))]).matches == ()


def test_when_everything_is_excluded_the_status_is_still_ok():
    outcome = match_jobs(profile(**KEY), [job(role_key="qa-engineer", required_skills=("figma", "excel", "tableau"))])
    assert (outcome.status, outcome.matches, outcome.coverage_percent) == ("ok", (), 70)


# ---- overall score and rounding ----------------------------------------------------------------------------------------------------------------
def test_the_overall_score_averages_over_evaluated_weight_only():
    # skills 2/3 (weight 50), role 1 (weight 20): (50*2/3 + 20) / 70 = 53.33/70 = 76.19 -> 76
    assert match_jobs(profile(technical_skills=("python", "sql"), **ROLE), [job()]).matches[0].score_percent == 76


def test_a_perfect_match_is_100():
    assert match_jobs(profile(**KEY, experience_years=3, preferred_location="Pune", work_mode="hybrid"), [job()]).matches[0].score_percent == 100


def test_not_evaluated_criteria_neither_help_nor_hurt():
    with_unrecognized = match_jobs(profile(**KEY, preferred_location="Atlantis"), [job()]).matches[0].score_percent
    assert with_unrecognized == match_jobs(profile(**KEY), [job()]).matches[0].score_percent == 100


def many_skills(total):
    return ("python", *(f"filler-{index}" for index in range(total - 1)))


def test_a_score_of_exactly_one_half_rounds_up():
    # 1 of 200 skills with only skills evaluated: exactly 0.5 percent, which half-up makes 1 (round() would give 0).
    match = match_jobs(profile(technical_skills=("python",)), [job(required_skills=many_skills(200))]).matches[0]
    assert match.score_percent == 1 and match.criteria[0].score_percent == 1


def test_a_score_just_below_one_half_rounds_down():
    match = match_jobs(profile(technical_skills=("python",)), [job(required_skills=many_skills(201))]).matches[0]
    assert match.score_percent == 0 and match.criteria[0].score_percent == 0


@pytest.mark.parametrize("score, percent", [
    (Fraction(1, 200), 1), (Fraction(3, 200), 2), (Fraction(5, 200), 3), (Fraction(199, 400), 50), (Fraction(1, 201), 0), (Fraction(1), 100), (Fraction(0), 0),
])
def test_half_up_percent(score, percent):
    assert half_up_percent(score) == percent


# ---- ordering ------------------------------------------------------------------------------------------------------------------------------------
def test_higher_overall_score_ranks_first_even_with_a_later_slug():
    best, worse = job("z-best"), job("a-worse", required_skills=("python", "figma", "excel"))
    assert [m.job.slug for m in match_jobs(profile(**KEY), [worse, best]).matches] == ["z-best", "a-worse"]


def test_equal_scores_rank_by_skills_score_before_slug():
    # Same overall score (40/70) by different routes; the higher skills score must win despite the later slug.
    candidate = profile(technical_skills=("python", "django", "sql", "docker"), **ROLE)
    role_heavy = job("a-job", required_skills=("python", "django", "git", "linux", "aws"))                # skills 2/5, role 1
    skills_heavy = job("z-job", role_key="frontend-developer", required_skills=("python", "django", "sql", "docker", "git"))  # skills 4/5, role 0
    outcome = match_jobs(candidate, [role_heavy, skills_heavy])
    assert outcome.matches[0].score_percent == outcome.matches[1].score_percent == 57
    assert [m.job.slug for m in outcome.matches] == ["z-job", "a-job"]


def test_equal_scores_and_skills_rank_by_slug():
    assert [m.job.slug for m in match_jobs(profile(**KEY), [job("b"), job("c"), job("a")]).matches] == ["a", "b", "c"]


def test_limit_defaults_to_ten():
    jobs = [job(f"job-{index:02d}") for index in range(15)]
    outcome = match_jobs(profile(**KEY), jobs)
    assert [m.job.slug for m in outcome.matches] == [f"job-{index:02d}" for index in range(10)]


def test_limit_takes_the_top_n_after_sorting():
    jobs = [job("c"), job("b", required_skills=("python", "figma", "excel")), job("a")]
    assert [m.job.slug for m in match_jobs(profile(**KEY), jobs, limit=2).matches] == ["a", "c"]


def test_limit_of_fifty_is_allowed():
    assert len(match_jobs(profile(**KEY), [job(f"j{index:02d}") for index in range(60)], limit=50).matches) == 50


@pytest.mark.parametrize("bad", [0, -1, 51, True, 3.0, "3", None])
def test_an_invalid_limit_raises(bad):
    with pytest.raises(ValueError):
        match_jobs(profile(**KEY), [job()], limit=bad)


def test_an_invalid_limit_raises_even_for_an_insufficient_profile():
    with pytest.raises(ValueError):
        match_jobs(profile(), [], limit=0)


# ---- determinism and purity ---------------------------------------------------------------------------------------------------------------------------
def catalogue_jobs() -> list[MatchableJob]:
    return [
        MatchableJob(job.slug, job.slug, job.title, job.company, job.location_label, job.description, job.employment_type, job.role_key,
                     job.city_key, job.work_mode, job.min_experience_years, job.max_experience_years, tuple(job.required_skills))
        for job in CATALOGUE
    ]


CANDIDATE = profile(
    technical_skills=("python", "django", "postgresql"), target_role="backend developer", experience_level="fresher",
    preferred_location="Pune", work_mode="hybrid",
)


def test_the_same_input_gives_an_equal_output():
    assert match_jobs(CANDIDATE, catalogue_jobs()) == match_jobs(CANDIDATE, catalogue_jobs())


def test_job_order_does_not_change_the_output():
    jobs = catalogue_jobs()
    expected = match_jobs(CANDIDATE, jobs, limit=50)
    for seed in range(5):
        shuffled = jobs[:]
        random.Random(seed).shuffle(shuffled)
        assert match_jobs(CANDIDATE, shuffled, limit=50) == expected


def test_inputs_are_not_mutated():
    jobs, snapshot = catalogue_jobs(), catalogue_jobs()
    candidate, candidate_snapshot = CANDIDATE, copy.deepcopy(CANDIDATE)
    match_jobs(candidate, jobs)
    assert jobs == snapshot and candidate == candidate_snapshot


def test_a_tuple_of_jobs_is_accepted():
    assert match_jobs(CANDIDATE, tuple(catalogue_jobs())).status == "ok"


# ---- end to end against the real sample catalogue -------------------------------------------------------------------------------------------------
def test_a_python_django_backend_fresher_in_pune_gets_the_kestrelbyte_backend_job_first():
    outcome = match_jobs(CANDIDATE, catalogue_jobs())
    assert outcome.status == "ok" and outcome.coverage_percent == 100
    assert outcome.matches[0].job.slug == "backend-developer-kestrelbyte-technologies"
    assert len(outcome.matches) == 10
    assert [m.score_percent for m in outcome.matches] == sorted((m.score_percent for m in outcome.matches), reverse=True)


def test_a_candidate_with_excel_and_sql_who_wants_to_be_a_data_analyst_gets_data_analyst_jobs_first():
    outcome = match_jobs(profile(technical_skills=("excel", "sql"), target_role="data analyst"), catalogue_jobs())
    assert outcome.coverage_percent == 70
    top = [m.job for m in outcome.matches[:3]]
    assert {j.role_key for j in top} == {"data-analyst"}
    assert all("excel" in j.required_skills or "sql" in j.required_skills for j in top)


def test_a_fully_empty_profile_gets_insufficient_profile_from_the_catalogue():
    assert match_jobs(profile(), catalogue_jobs()) == MatchOutcome("insufficient_profile", 0, ())


def test_a_job_replaced_in_the_catalogue_changes_the_ranking():
    jobs = catalogue_jobs()
    index = next(i for i, j in enumerate(jobs) if j.slug == "backend-developer-kestrelbyte-technologies")
    jobs[index] = replace(jobs[index], required_skills=("figma", "excel", "tableau"), city_key="chennai")
    assert match_jobs(CANDIDATE, jobs).matches[0].job.slug != "backend-developer-kestrelbyte-technologies"
