"""Canonical job-matching keys. Keys only: aliases (Hindi, Romanized Hindi, spelling variants) belong to the matching engine (T3).

Role and city keys are lowercase kebab-case. Skill keys are lowercase, NFKC-normalized, and may contain `+ # . /` and spaces
(`c++`, `node.js`, `ci/cd`, `spring boot`). The ordered tuples keep source order so tests can prove there are no duplicates.
"""

import re
import unicodedata

KEY_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

ROLE_KEY_ORDER: tuple[str, ...] = (
    "backend-developer", "frontend-developer", "full-stack-developer", "mobile-developer", "data-analyst",
    "data-engineer", "devops-engineer", "qa-engineer", "ui-ux-designer",
)
CITY_KEY_ORDER: tuple[str, ...] = (
    "delhi", "noida", "gurugram", "bengaluru", "hyderabad", "pune", "mumbai", "chennai", "kolkata", "jaipur",
)
SKILL_KEY_ORDER: tuple[str, ...] = (
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "kotlin", "swift", "sql",
    "html", "css", "react", "angular", "vue", "node.js", "django", "fastapi", "flask", "spring boot",
    ".net", "react native", "flutter", "android", "ios", "postgresql", "mysql", "mongodb", "redis", "docker",
    "kubernetes", "aws", "azure", "git", "linux", "ci/cd", "terraform", "rest apis", "graphql", "pandas",
    "numpy", "excel", "power bi", "tableau", "spark", "airflow", "selenium", "pytest", "jest", "figma",
    "manual testing",
)

ROLE_KEYS = frozenset(ROLE_KEY_ORDER)
CITY_KEYS = frozenset(CITY_KEY_ORDER)
SKILL_KEYS = frozenset(SKILL_KEY_ORDER)


def normalize_skill_text(text: str) -> str:
    """NFKC, casefold, trimmed, single-spaced. This is NOT alias resolution: it never maps one skill to another."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def is_role_key(value: object) -> bool:
    return isinstance(value, str) and value in ROLE_KEYS


def is_city_key(value: object) -> bool:
    return isinstance(value, str) and value in CITY_KEYS


def is_skill_key(value: object) -> bool:
    return isinstance(value, str) and value in SKILL_KEYS


def canonical_skill(text: str) -> str | None:
    """The skill key whose normalized spelling equals `text` exactly, else None (unknown text never matches anything)."""
    normalized = normalize_skill_text(text)
    return normalized if normalized in SKILL_KEYS else None


LEVEL_KEYS = frozenset({"fresher", "junior", "mid", "senior"})
