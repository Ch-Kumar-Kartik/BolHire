import unicodedata

import pytest

from app.jobs import vocabulary as vocab

# The exact lists the specification fixes. Written out independently of the module so a typo in either is caught.
SPEC_ROLES = {"backend-developer", "frontend-developer", "full-stack-developer", "mobile-developer", "data-analyst", "data-engineer",
              "devops-engineer", "qa-engineer", "ui-ux-designer"}
SPEC_CITIES = {"delhi", "noida", "gurugram", "bengaluru", "hyderabad", "pune", "mumbai", "chennai", "kolkata", "jaipur"}
SPEC_SKILLS = {"python", "java", "javascript", "typescript", "c++", "c#", "go", "kotlin", "swift", "sql", "html", "css", "react",
               "angular", "vue", "node.js", "django", "fastapi", "flask", "spring boot", ".net", "react native", "flutter", "android",
               "ios", "postgresql", "mysql", "mongodb", "redis", "docker", "kubernetes", "aws", "azure", "git", "linux", "ci/cd",
               "terraform", "rest apis", "graphql", "pandas", "numpy", "excel", "power bi", "tableau", "spark", "airflow", "selenium",
               "pytest", "jest", "figma", "manual testing"}


def test_the_vocabularies_are_exactly_the_specified_keys():
    assert vocab.ROLE_KEYS == SPEC_ROLES and vocab.CITY_KEYS == SPEC_CITIES and vocab.SKILL_KEYS == SPEC_SKILLS
    assert (len(vocab.ROLE_KEYS), len(vocab.CITY_KEYS), len(vocab.SKILL_KEYS)) == (9, 10, 51)


@pytest.mark.parametrize("order, keys", [(vocab.ROLE_KEY_ORDER, vocab.ROLE_KEYS), (vocab.CITY_KEY_ORDER, vocab.CITY_KEYS),
                                         (vocab.SKILL_KEY_ORDER, vocab.SKILL_KEYS)], ids=["roles", "cities", "skills"])
def test_there_are_no_duplicate_keys(order, keys):
    assert len(order) == len(set(order)) == len(keys)
    assert len({key.casefold() for key in order}) == len(order)


@pytest.mark.parametrize("key", sorted(SPEC_ROLES | SPEC_CITIES))
def test_role_and_city_keys_are_lowercase_kebab_case(key):
    assert vocab.KEY_PATTERN.fullmatch(key)


@pytest.mark.parametrize("key", sorted(SPEC_SKILLS))
def test_skill_keys_are_lowercase_nfkc_normalized_and_trimmed(key):
    assert key == key.strip() and key == key.casefold() and key == unicodedata.normalize("NFKC", key)
    assert "  " not in key and not any(unicodedata.category(char) == "Cc" for char in key)


def test_predicates_accept_only_exact_keys():
    assert vocab.is_role_key("data-analyst") and vocab.is_city_key("pune") and vocab.is_skill_key("c++")
    for value in ("Data-Analyst", "data analyst", "", None, 3, ["pune"]):
        assert not vocab.is_role_key(value) and not vocab.is_city_key(value)
    for value in ("Python", "reactjs", "", None, 3):
        assert not vocab.is_skill_key(value)


@pytest.mark.parametrize("text, expected", [
    ("python", "python"), ("Python", "python"), ("  PYTHON  ", "python"), ("ｐｙｔｈｏｎ", "python"),  # NFKC folds full-width letters
    ("C++", "c++"), ("Node.JS", "node.js"), ("spring   boot", "spring boot"), ("CI/CD", "ci/cd"),
])
def test_canonical_skill_normalizes_spelling_only(text, expected):
    assert vocab.canonical_skill(text) == expected


@pytest.mark.parametrize("text", ["पायथन", "reactjs", "node js", "nodejs", "golang", "k8s", "postgres", "ml", "", "   ", "python3"])
def test_unknown_or_alias_text_never_resolves_here(text):
    """Aliases (Hindi, Romanized Hindi, spelling variants) are T3's job; this module must not guess."""
    assert vocab.canonical_skill(text) is None


def test_the_vocabulary_module_defines_no_alias_table():
    assert not [name for name in dir(vocab) if "alias" in name.casefold() or "synonym" in name.casefold()]
