"""Pure LinkedIn export mapping. Synthetic records only: no network, no Apify, no real listings."""

from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from app.jobs import linkedin_import as li
from app.jobs.linkedin_import import MAX_DESCRIPTION, normalize_records
from app.jobs.vocabulary import KEY_PATTERN

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
FALLBACK = datetime(2026, 11, 8, 9, 30, tzinfo=UTC)
APPLY = "https://careers.example.com/apply/secret-route"
POSTER = "Synthetic Recruiter Person"


def rec(**fields):
    base = {
        "id": "4000000001", "title": "Backend Developer", "companyName": "Fictional Co", "location": "Pune, Maharashtra, India",
        "link": "https://www.linkedin.com/jobs/view/backend-developer-at-fictional-co-4000000001?refId=r&trackingId=t#frag",
        "descriptionText": "Build Django services.\nPostgreSQL\nDocker", "workplaceType": "Hybrid", "employmentType": "Full-time",
    }
    base.update(fields)
    return {key: value for key, value in base.items() if value is not ...}


def run(*records):
    return normalize_records(list(records), retrieved_fallback=FALLBACK, now=NOW)


def only(*records):
    report = run(*records)
    assert len(report.accepted) == 1, report.dropped
    return report.accepted[0]


# ---- the happy path ---------------------------------------------------------------------------------------------------------
def test_a_complete_record_maps_every_column():
    job = only(rec(postedAt="2026-11-01T10:00:00Z"))
    assert asdict(job) == dict(
        source="linkedin", external_id="4000000001", slug="linkedin-4000000001", title="Backend Developer", company="Fictional Co",
        location_label="Pune, Maharashtra, India", description="Build Django services.\nPostgreSQL\nDocker",
        employment_type="full_time", role_key="backend-developer", city_key="pune", work_mode="hybrid",
        min_experience_years=None, max_experience_years=None, required_skills=("django", "postgresql", "docker"),
        source_url="https://www.linkedin.com/jobs/view/backend-developer-at-fictional-co-4000000001",
        retrieved_at=FALLBACK, posted_at=datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
    )


def test_the_same_input_always_gives_the_same_report():
    records = [rec(), rec(id="2", title="Software Engineer", location="Delhi NCR"), "junk", rec(id="3", title="Chef")]
    assert run(*records) == run(*records)


# ---- key variants -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("fields", [
    pytest.param(dict(id=..., jobId=4000000001), id="integer jobId"),
    pytest.param(dict(id=..., job_id="4000000001"), id="job_id"),
    pytest.param(dict(id=..., jobPostingId="4000000001"), id="jobPostingId"),
    pytest.param(dict(id=...), id="id from the posting link"),
])
def test_external_id_key_variants(fields):
    assert only(rec(**fields)).external_id == "4000000001"


@pytest.mark.parametrize("fields, attribute, expected", [
    (dict(title=..., jobTitle="Backend Developer"), "title", "Backend Developer"),
    (dict(title=..., position="Backend Developer"), "title", "Backend Developer"),
    (dict(companyName=..., company={"name": "Nested Co"}), "company", "Nested Co"),
    (dict(companyName=..., company="Plain Co"), "company", "Plain Co"),
    (dict(location=..., formattedLocation="Pune"), "location_label", "Pune"),
    (dict(location=..., jobLocation="Pune"), "location_label", "Pune"),
    (dict(link=..., jobUrl="https://www.linkedin.com/jobs/view/4000000001/"), "source_url", "https://www.linkedin.com/jobs/view/4000000001/"),
    (dict(link=..., url="https://www.linkedin.com/jobs/view/4000000001"), "source_url", "https://www.linkedin.com/jobs/view/4000000001"),
    (dict(descriptionText=..., description="Python and SQL work."), "description", "Python and SQL work."),
    (dict(descriptionText=..., descriptionHtml="<p>Python and SQL work.</p>"), "description", "Python and SQL work."),
    (dict(descriptionText=..., jobDescription="Python and SQL work."), "description", "Python and SQL work."),
])
def test_field_key_variants(fields, attribute, expected):
    assert getattr(only(rec(**fields)), attribute) == expected


def test_key_names_are_reported_without_values():
    report = run(rec(applyUrl=APPLY, posterFullName=POSTER, company={"name": "Nested Co"}))
    assert {"applyUrl", "posterFullName", "company", "company.name", "link"} <= set(report.key_counts)
    flat = repr(report.key_counts)
    assert APPLY not in flat and POSTER not in flat and "Nested Co" not in flat


# ---- never read apply or people data ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("key", [
    "applyUrl", "applyLink", "applyMethod", "companyApplyUrl", "easyApply", "posterFullName", "posterProfileUrl", "jobPoster",
    "recruiterName", "hiringTeam", "applicantsCount", "contactEmail", "APPLYURL",
])
def test_denied_keys_are_unreachable(key):
    assert li.is_denied_key(key)
    assert li.lookup({key: "x"}, key) is None
    assert li.lookup({"company": {key: "x"}}, f"company.{key}") is None


def test_an_apply_link_is_never_used_even_when_it_is_the_only_link():
    report = run(rec(link=..., applyUrl=APPLY, applyLink=APPLY, companyApplyUrl=APPLY))
    assert report.accepted == () and report.dropped == {"missing_https_source_url": 1}


def test_an_accepted_job_holds_no_apply_or_poster_data():
    job = only(rec(applyUrl=APPLY, applyMethod={"companyApplyUrl": APPLY}, posterFullName=POSTER,
                   posterProfileUrl="https://www.linkedin.com/in/someone"))
    text = repr(asdict(job))
    assert APPLY not in text and POSTER not in text and "/in/someone" not in text


# ---- https source url ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("link", [
    "http://www.linkedin.com/jobs/view/4000000001", "javascript:alert(1)", "ftp://x/y", "https://", "https://user:pw@evil.example/jobs/view/1",
    "https:// space.example/jobs", 42, None,
])
def test_a_non_https_posting_link_drops_the_record(link):
    report = run(rec(link=link, id="4000000001"))
    assert report.dropped == {"missing_https_source_url": 1}


def test_query_and_fragment_are_stripped_and_the_host_lowercased():
    assert li.https_url("HTTPS://WWW.LinkedIn.com/jobs/view/1?trackingId=abc&refId=x#top") == "https://www.linkedin.com/jobs/view/1"


# ---- description --------------------------------------------------------------------------------------------------------------
def test_html_becomes_plain_text_without_scripts_styles_or_links():
    html = ('<p>Build&nbsp;APIs &amp; services.</p><ul><li>Python</li><li>SQL</li></ul>'
            '<script>alert(1)</script><style>p{}</style><a href="https://evil.example/x">read more</a>')
    text = li.clean_description(html)
    assert text == "Build APIs & services.\n\nPython\n\nSQL\n\nread more"
    assert "<" not in text and "alert" not in text and "evil" not in text


@pytest.mark.parametrize("raw", [
    "Write to hr@fictional.example today.", "See https://jobs.example.com/x?y=1 now.", "Visit www.example.org/careers.",
    "Visit fictional-co.com for more.", "Call +91 98765 43210 now.", "Call 080-1234-5678 today.",
])
def test_contact_routes_and_links_are_redacted(raw):
    text = li.clean_description(raw)
    assert "[removed]" in text
    assert not any(token in text for token in ("@", "http", "www.", ".com", ".org", "98765", "1234"))


def test_skill_names_and_year_ranges_survive_redaction():
    text = li.clean_description("ASP.NET, .NET Core, Node.js and Vue.js. Experience 2019-2024 preferred.")
    assert text == "ASP.NET, .NET Core, Node.js and Vue.js. Experience 2019-2024 preferred."


def test_long_descriptions_are_truncated_to_the_database_limit():
    exact = "a" * MAX_DESCRIPTION
    assert li.clean_description(exact) == exact
    long_text = li.clean_description("word " * 2000)
    assert len(long_text) <= MAX_DESCRIPTION and long_text.endswith("…")


def test_titles_are_truncated_to_the_column_size():
    job = only(rec(title="Backend Developer " + "x" * 300))
    assert len(job.title) == 120 and job.title.endswith("…")


def test_invisible_characters_are_removed_without_splitting_words():
    assert li.clean_line("Back​end Developer\t") == "Backend Developer"


# ---- role and city -------------------------------------------------------------------------------------------------------------
def test_an_ambiguous_role_takes_the_first_key_in_vocabulary_order_and_is_counted():
    report = run(rec(title="Software Engineer"))
    assert report.accepted[0].role_key == "backend-developer"
    assert report.ambiguous == {"role": 1, "city": 0}


def test_an_ambiguous_city_takes_the_first_key_in_vocabulary_order_and_is_counted():
    report = run(rec(location="Delhi NCR"))
    assert report.accepted[0].city_key == "delhi"
    assert report.ambiguous == {"role": 0, "city": 1}


def test_ambiguity_counts_are_split_by_role_and_city():
    report = run(rec(id="1", title="Software Engineer", location="Delhi NCR"), rec(id="2", title="Web Developer"), rec(id="3"))
    assert report.ambiguous == {"role": 2, "city": 1}


def test_an_unrecognized_role_is_dropped_never_invented():
    assert run(rec(title="Chef")).dropped == {"unrecognized_role": 1}


def test_an_unrecognized_city_is_dropped_never_invented():
    assert run(rec(location="Atlantis")).dropped == {"unrecognized_city": 1}
    assert run(rec(location=...)).dropped == {"unrecognized_city": 1}


@pytest.mark.parametrize("fields", [
    dict(workplaceType="Remote", location="Atlantis"), dict(workplaceType=..., workRemoteAllowed=True, location=...),
    dict(workplaceType=..., location="Remote, India"),
])
def test_a_remote_job_needs_no_city_and_stores_none(fields):
    job = only(rec(**fields))
    assert job.work_mode == "remote" and job.city_key is None and job.location_label


# ---- defaults and seniority -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value, expected", [("Hybrid", "hybrid"), ("On-site", "onsite"), ("onsite", "onsite"), ("Remote", "remote")])
def test_known_work_modes_map(value, expected):
    report = run(rec(workplaceType=value))
    assert report.accepted[0].work_mode == expected and report.defaults["work_mode"] == 0


@pytest.mark.parametrize("value", [..., "Flexible", False, ""])
def test_a_missing_or_unknown_work_mode_defaults_to_onsite_and_is_counted(value):
    report = run(rec(workplaceType=value))
    assert report.accepted[0].work_mode == "onsite" and report.defaults["work_mode"] == 1


@pytest.mark.parametrize("value, expected", [
    ("Full-time", "full_time"), ("Part-time", "part_time"), ("Contract", "contract"), ("Temporary", "contract"),
    ("Internship", "internship"), ("FULL_TIME", "full_time"),
])
def test_known_employment_types_map(value, expected):
    report = run(rec(employmentType=value))
    assert report.accepted[0].employment_type == expected and report.defaults["employment_type"] == 0


@pytest.mark.parametrize("value", [..., "Volunteer", "Other", 3])
def test_a_missing_or_unknown_employment_type_defaults_to_full_time_and_is_counted(value):
    report = run(rec(employmentType=value))
    assert report.accepted[0].employment_type == "full_time" and report.defaults["employment_type"] == 1


def test_only_the_internship_level_maps_to_experience_bounds():
    report = run(rec(seniorityLevel="Internship"))
    job = report.accepted[0]
    assert (job.min_experience_years, job.max_experience_years) == (0.0, 1.0) and report.seniority["mapped"] == 1


@pytest.mark.parametrize("level", ["Entry level", "Associate", "Mid-Senior level", "Director", "Executive", "Not Applicable"])
def test_other_levels_leave_experience_open_and_are_counted(level):
    report = run(rec(seniorityLevel=level))
    job = report.accepted[0]
    assert (job.min_experience_years, job.max_experience_years) == (None, None)
    assert report.seniority == {"mapped": 0, "left_null": 1, "absent": 0}


def test_a_missing_level_is_counted_as_absent():
    assert run(rec()).seniority == {"mapped": 0, "left_null": 0, "absent": 1}


# ---- skills -----------------------------------------------------------------------------------------------------------------------
def test_skills_keep_first_appearance_order_and_are_capped_at_eight():
    description = "\n".join(["Python", "SQL", "Docker", "Kubernetes", "AWS", "Git", "Linux", "Redis", "Terraform", "Airflow"])
    job = only(rec(title="Backend Developer", descriptionText=description))
    assert job.required_skills == ("python", "sql", "docker", "kubernetes", "aws", "git", "linux", "redis")


def test_explicit_skill_lists_are_used_first():
    job = only(rec(skills=[{"name": "Figma"}, "Tableau", 5], descriptionText="Python work."))
    assert job.required_skills[:2] == ("figma", "tableau") and "python" in job.required_skills


def test_whole_item_only_words_match_only_as_their_own_line():
    assert only(rec(descriptionText="You will excel at teamwork with Python.")).required_skills == ("python",)
    assert "excel" in only(rec(descriptionText="Tools:\nExcel\nPython")).required_skills


def test_a_record_without_resolvable_skills_is_dropped():
    assert run(rec(title="Backend Developer", descriptionText="Great culture and snacks.")).dropped == {"no_resolved_skills": 1}


# ---- times ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value, expected", [
    ("2026-11-01T10:00:00Z", datetime(2026, 11, 1, 10, tzinfo=UTC)),
    ("2026-11-01T15:30:00+05:30", datetime(2026, 11, 1, 10, tzinfo=UTC)),
    ("2026-11-01", datetime(2026, 11, 1, tzinfo=UTC)),
    (1_793_000_000_000, datetime.fromtimestamp(1_793_000_000, tz=UTC)),
    (1_793_000_000, datetime.fromtimestamp(1_793_000_000, tz=UTC)),
    ("2 days ago", None), ("2030-01-01T00:00:00Z", None), ("1970-01-01", None), (True, None), ("", None), (None, None),
])
def test_posted_at_parsing(value, expected):
    assert li.parse_time(value, NOW) == expected


def test_retrieved_at_uses_the_records_own_time_or_the_file_fallback():
    own = run(rec(scrapedAt="2026-11-08T20:00:00Z"))
    assert own.accepted[0].retrieved_at == datetime(2026, 11, 8, 20, tzinfo=UTC) and own.retrieved_at_fallbacks == 0
    fallback = run(rec())
    assert fallback.accepted[0].retrieved_at == FALLBACK and fallback.retrieved_at_fallbacks == 1


# ---- drops ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("record, reason", [
    ("not a record", "not_an_object"), (None, "not_an_object"), ([1], "not_an_object"),
    (rec(id=..., link="https://www.linkedin.com/jobs/search/?q=x"), "missing_external_id"),
    (rec(id="   ", link="https://www.linkedin.com/jobs/collections/recommended/"), "missing_external_id"),
    (rec(title="  "), "missing_title"), (rec(companyName=...), "missing_company"),
    (rec(descriptionText="<p> </p>"), "missing_description"), (rec(descriptionText=...), "missing_description"),
    (rec(link="http://www.linkedin.com/jobs/view/4000000001"), "missing_https_source_url"),
    (rec(title="Chef"), "unrecognized_role"), (rec(location="Atlantis"), "unrecognized_city"),
    (rec(descriptionText="Nice office."), "no_resolved_skills"),
])
def test_each_drop_reason_is_counted(record, reason):
    report = run(record)
    assert report.accepted == () and report.dropped == {reason: 1}


def test_a_duplicate_external_id_keeps_the_first_record():
    report = run(rec(title="Backend Developer"), rec(title="Data Analyst"))
    assert [job.title for job in report.accepted] == ["Backend Developer"] and report.dropped == {"duplicate_in_file": 1}


@pytest.mark.parametrize("external_id", ["4000000001", "ABC_123/xyz", "9" * 100])
def test_slugs_match_the_jobs_slug_format(external_id):
    slug = li.slug_for(external_id)
    assert slug is not None and KEY_PATTERN.fullmatch(slug) and len(slug) <= 80 and slug.startswith("linkedin-")
