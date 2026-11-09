"""Pure mapping of an Apify LinkedIn jobs dataset export into real job rows. No I/O, no network, no model calls.

The export's exact key names are not known in advance, so each field is looked up through an ordered list of likely keys
(`KEY_CANDIDATES`). Apply links, apply methods and any job-poster, recruiter or applicant data are NEVER read: lookups refuse
every key in `DENIED_KEYS` or starting with a denied prefix, even as a fallback. The posting link is kept only as an https
`source_url` without query or fragment.

Normalization reuses the existing deterministic resolvers. A record becomes a job only with a resolved role, a resolved city
unless remote, at least one resolved skill, a title, a company, a description, an external id and an https posting link. Every
other record is dropped and counted by reason. Ambiguous resolutions take the first key in vocabulary order and are counted;
missing work mode or employment type falls back to a neutral value and is counted; seniority maps to experience bounds only
where the sample catalogue convention is unambiguous (Internship -> 0-1 years).
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

from app.jobs.resolver import resolve_cities, resolve_roles, resolve_skills
from app.jobs.vocabulary import CITY_KEY_ORDER, ROLE_KEY_ORDER, SKILL_KEY_ORDER

SOURCE = "linkedin"
MAX_DESCRIPTION = 4000  # the jobs.description CHECK limit
MAX_TEXT = 120          # title, company and location_label column sizes
MAX_SLUG = 80
MAX_EXTERNAL_ID = 100
MAX_URL = 2048
MAX_SKILLS = 8          # the sample catalogue's upper bound; more skills would dilute every skills score
REMOTE_LABEL = "Remote"
DEFAULT_WORK_MODE = "onsite"
DEFAULT_EMPLOYMENT_TYPE = "full_time"

KEY_CANDIDATES: dict[str, tuple[str, ...]] = {
    "external_id": ("id", "jobId", "job_id", "jobPostingId"),
    "title": ("title", "jobTitle", "position"),
    "company": ("companyName", "company.name", "company"),
    "location": ("location", "jobLocation", "formattedLocation", "place"),
    "link": ("link", "jobUrl", "url", "jobPostingUrl"),
    "description": ("descriptionText", "description", "descriptionHtml", "jobDescription"),
    "work_mode": ("workplaceType", "workType", "workRemoteAllowed", "remote"),
    "employment_type": ("employmentType", "contractType", "jobType"),
    "seniority": ("seniorityLevel", "experienceLevel"),
    "skills": ("skills", "jobSkills"),
    "posted_at": ("postedAt", "publishedAt", "postedDate", "listedAt", "postedTime"),
    "retrieved_at": ("scrapedAt", "retrievedAt"),
}

# Never read, under any path segment: apply routes and people.
DENIED_KEYS = frozenset({
    "applyurl", "applylink", "applymethod", "companyapplyurl", "easyapply", "applytype", "hiringteam", "jobposter",
})
DENIED_PREFIXES = ("apply", "poster", "recruiter", "hiring", "applicant", "jobposter", "contact")

DROP_REASONS = (
    "not_an_object", "missing_external_id", "duplicate_in_file", "missing_title", "missing_company", "missing_description",
    "missing_https_source_url", "unrecognized_role", "unrecognized_city", "no_resolved_skills",
)

_ROLE_RANK = {key: index for index, key in enumerate(ROLE_KEY_ORDER)}
_CITY_RANK = {key: index for index, key in enumerate(CITY_KEY_ORDER)}
_SKILL_RANK = {key: index for index, key in enumerate(SKILL_KEY_ORDER)}

_JOB_VIEW_ID = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d+)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Bare domains: only TLDs that do not collide with skill names (".net", "node.js", "vue.js" must survive).
_URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|in|io|org|co)\b(?:/\S*)?", re.I)
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_BLOCK_TAGS = frozenset({"p", "br", "li", "div", "ul", "ol", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section"})


@dataclass(frozen=True)
class ImportedJob:
    source: str
    external_id: str
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
    source_url: str
    retrieved_at: datetime
    posted_at: datetime | None


@dataclass(frozen=True)
class ImportReport:
    records_seen: int
    key_counts: dict[str, int]
    accepted: tuple[ImportedJob, ...]
    dropped: dict[str, int]
    defaults: dict[str, int]
    ambiguous: dict[str, int]
    seniority: dict[str, int]
    retrieved_at_fallbacks: int
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---- key lookup -----------------------------------------------------------------------------------------------------------
def is_denied_key(key: str) -> bool:
    folded = key.casefold()
    return folded in DENIED_KEYS or folded.startswith(DENIED_PREFIXES)


def lookup(record: Mapping[str, object], path: str) -> object:
    """The value at a dotted path, or None. Refuses any denied segment, so apply and people data are unreachable."""
    current: object = record
    for segment in path.split("."):
        if is_denied_key(segment) or not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def first(record: Mapping[str, object], name: str) -> object:
    """The first usable value among the field's candidate keys. Mappings are skipped (use a dotted path); empty values too."""
    for path in KEY_CANDIDATES[name]:
        value = lookup(record, path)
        if value is None or isinstance(value, Mapping):
            continue
        if isinstance(value, (str, list)) and not value:
            continue
        return value
    return None


def key_names(record: Mapping[str, object]) -> list[str]:
    """Top-level key names plus one nested level, for the --check report. Names only, never values."""
    names = []
    for key, value in record.items():
        names.append(str(key))
        if isinstance(value, Mapping):
            names.extend(f"{key}.{inner}" for inner in value)
    return names


# ---- text normalization ---------------------------------------------------------------------------------------------------
def _visible(text: str) -> str:
    """Control characters become spaces; format/invisible characters (zero-width joiners, bidi marks) are removed."""
    return "".join(" " if unicodedata.category(ch) == "Cc" else "" if unicodedata.category(ch)[0] == "C" else ch for ch in text)


def clean_line(value: object, limit: int = MAX_TEXT) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(_visible(unescape(value)).split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: ARG002 - attributes (including hrefs) are deliberately ignored
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def _redact_phone(match: re.Match[str]) -> str:
    return "[removed]" if sum(ch.isdigit() for ch in match.group()) >= 10 else match.group()


def clean_description(value: object) -> str | None:
    """Plain text: HTML stripped, contact routes and links redacted, whitespace normalized, truncated to the DB limit."""
    if not isinstance(value, str):
        return None
    raw = html_to_text(value) if "<" in value and ">" in value else unescape(value)
    lines = []
    for line in raw.replace("\r", "\n").split("\n"):
        text = " ".join(_visible(line).split())
        if text or (lines and lines[-1]):
            lines.append(text)
    text = "\n".join(lines).strip()
    text = _EMAIL.sub("[removed]", text)
    text = _URL.sub("[removed]", text)
    text = _PHONE.sub(_redact_phone, text)
    if not text:
        return None
    if len(text) > MAX_DESCRIPTION:
        cut = text[: MAX_DESCRIPTION - 1]
        space = cut.rfind(" ")
        text = (cut[:space] if space > MAX_DESCRIPTION // 2 else cut).rstrip() + "…"
    return text


def https_url(value: object) -> str | None:
    """The posting link only if it is https with a host and no credentials; query and fragment are dropped."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname or "@" in parts.netloc or any(ch.isspace() for ch in value.strip()):
        return None
    url = urlunsplit(("https", parts.netloc.lower(), parts.path or "/", "", ""))
    return url if len(url) <= MAX_URL else None


def external_id_of(value: object, link: object) -> str | None:
    if isinstance(value, bool):
        value = None
    if isinstance(value, int):
        value = str(value)
    if isinstance(value, str) and value.strip() and len(value.strip()) <= MAX_EXTERNAL_ID and value.strip().isprintable():
        return value.strip()
    if isinstance(link, str) and (found := _JOB_VIEW_ID.search(link)):
        return found.group(1)
    return None


def slug_for(external_id: str) -> str | None:
    body = "-".join(part for part in re.split(r"[^a-z0-9]+", external_id.casefold()) if part)
    slug = f"{SOURCE}-{body}"[:MAX_SLUG].rstrip("-")
    return slug if body else None


def parse_time(value: object, now: datetime) -> datetime | None:
    """ISO 8601 or epoch seconds/milliseconds -> aware UTC. Relative text ('2 days ago'), nonsense and the future -> None."""
    moment: datetime | None = None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 100_000_000_000 else value
        try:
            moment = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
    if moment is None or moment > now + timedelta(days=1) or moment.year < 2000:
        return None
    return moment.astimezone(UTC)


# ---- vocabulary mapping ----------------------------------------------------------------------------------------------------
def pick(resolved: frozenset[str], rank: Mapping[str, int]) -> tuple[str | None, bool]:
    """(first key in vocabulary order or None, whether more than one key resolved)."""
    if not resolved:
        return None, False
    return min(resolved, key=lambda key: rank[key]), len(resolved) > 1


def work_mode_of(value: object, location: str | None) -> str | None:
    if isinstance(value, bool):
        if value:
            return "remote"
        value = None
    if isinstance(value, str):
        folded = value.casefold()
        if "remote" in folded:
            return "remote"
        if "hybrid" in folded:
            return "hybrid"
        if any(word in folded for word in ("on-site", "onsite", "on site", "office")):
            return "onsite"
    if location and "remote" in location.casefold().replace(",", " ").split():
        return "remote"
    return None


def employment_type_of(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    folded = " ".join(value.casefold().replace("-", " ").replace("_", " ").split())
    table = {
        "full time": "full_time", "fulltime": "full_time", "part time": "part_time", "parttime": "part_time",
        "contract": "contract", "contractor": "contract", "temporary": "contract", "internship": "internship", "intern": "internship",
    }
    return table.get(folded)


def experience_of(value: object) -> tuple[float | None, float | None, str]:
    """Only LinkedIn's 'Internship' level maps unambiguously (all sample internships are 0-1 years). Others stay open."""
    if not isinstance(value, str) or not value.strip():
        return None, None, "absent"
    if value.strip().casefold() == "internship":
        return 0.0, 1.0, "mapped"
    return None, None, "left_null"


def skills_of(explicit: object, title: str, description: str) -> tuple[str, ...]:
    items: list[str] = []
    if isinstance(explicit, str):
        items.extend(part for part in explicit.split(","))
    elif isinstance(explicit, list):
        for entry in explicit:
            if isinstance(entry, str):
                items.append(entry)
            elif isinstance(entry, Mapping) and isinstance(entry.get("name"), str):
                items.append(entry["name"])
    items.append(title)
    items.extend(description.split("\n"))
    ordered: list[str] = []
    for item in items:
        for skill in sorted(resolve_skills([item]), key=lambda key: _SKILL_RANK[key]):
            if skill not in ordered:
                ordered.append(skill)
    return tuple(ordered[:MAX_SKILLS])


# ---- records ---------------------------------------------------------------------------------------------------------------
def normalize_records(records: Iterable[object], *, retrieved_fallback: datetime, now: datetime) -> ImportReport:
    """Deterministic: the same records, fallback and `now` always give the same report."""
    keys: Counter[str] = Counter()
    dropped: Counter[str] = Counter()
    defaults: Counter[str] = Counter()
    ambiguous: Counter[str] = Counter()
    seniority: Counter[str] = Counter()
    fallbacks = 0
    seen_ids: set[str] = set()
    accepted: list[ImportedJob] = []
    count = 0
    for record in records:
        count += 1
        if not isinstance(record, Mapping):
            dropped["not_an_object"] += 1
            continue
        keys.update(key_names(record))
        link = first(record, "link")
        external_id = external_id_of(first(record, "external_id"), link)
        slug = slug_for(external_id) if external_id else None
        if external_id is None or slug is None:
            dropped["missing_external_id"] += 1
            continue
        if external_id in seen_ids:
            dropped["duplicate_in_file"] += 1
            continue
        seen_ids.add(external_id)
        title = clean_line(first(record, "title"))
        if title is None:
            dropped["missing_title"] += 1
            continue
        company = clean_line(first(record, "company"))
        if company is None:
            dropped["missing_company"] += 1
            continue
        description = clean_description(first(record, "description"))
        if description is None:
            dropped["missing_description"] += 1
            continue
        source_url = https_url(link)
        if source_url is None:
            dropped["missing_https_source_url"] += 1
            continue
        role_key, role_ambiguous = pick(resolve_roles(title), _ROLE_RANK)
        if role_key is None:
            dropped["unrecognized_role"] += 1
            continue
        location = clean_line(first(record, "location"))
        work_mode = work_mode_of(first(record, "work_mode"), location)
        if work_mode is None:
            work_mode = DEFAULT_WORK_MODE
            defaults["work_mode"] += 1
        city_key, city_ambiguous = (None, False) if work_mode == "remote" else pick(resolve_cities(location), _CITY_RANK)
        if work_mode != "remote" and city_key is None:
            dropped["unrecognized_city"] += 1
            continue
        skills = skills_of(first(record, "skills"), title, description)
        if not skills:
            dropped["no_resolved_skills"] += 1
            continue
        employment_type = employment_type_of(first(record, "employment_type"))
        if employment_type is None:
            employment_type = DEFAULT_EMPLOYMENT_TYPE
            defaults["employment_type"] += 1
        low, high, seniority_state = experience_of(first(record, "seniority"))
        seniority[seniority_state] += 1
        ambiguous["role"] += role_ambiguous
        ambiguous["city"] += city_ambiguous
        retrieved = parse_time(first(record, "retrieved_at"), now)
        if retrieved is None:
            retrieved = retrieved_fallback.astimezone(UTC)
            fallbacks += 1
        accepted.append(ImportedJob(
            source=SOURCE, external_id=external_id, slug=slug, title=title, company=company,
            location_label=location or REMOTE_LABEL,  # a non-remote row always has a location (its city resolved from it)
            description=description, employment_type=employment_type, role_key=role_key, city_key=city_key,
            work_mode=work_mode, min_experience_years=low, max_experience_years=high, required_skills=skills,
            source_url=source_url, retrieved_at=retrieved, posted_at=parse_time(first(record, "posted_at"), now),
        ))
    return ImportReport(
        records_seen=count, key_counts=dict(sorted(keys.items())), accepted=tuple(accepted),
        dropped={reason: dropped[reason] for reason in DROP_REASONS if dropped[reason]},
        defaults={name: defaults[name] for name in ("work_mode", "employment_type")},
        ambiguous={name: ambiguous[name] for name in ("role", "city")},
        seniority={name: seniority[name] for name in ("mapped", "left_null", "absent")},
        retrieved_at_fallbacks=fallbacks,
    )
