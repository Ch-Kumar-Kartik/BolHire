import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.jobs.aliases import CITY_ALIASES, LEVEL_ALIASES, ROLE_ALIASES, SKILL_ALIASES
from app.jobs.resolver import resolve_cities, resolve_experience_level, resolve_roles, resolve_skills
from app.jobs.vocabulary import CITY_KEY_ORDER, LEVEL_KEYS, ROLE_KEY_ORDER, SKILL_KEY_ORDER

NCR = {"delhi", "noida", "gurugram"}


def skills(*items):
    return resolve_skills(items)


# ---- canonical keys and declared aliases ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("key", SKILL_KEY_ORDER)
def test_every_skill_key_resolves_to_itself(key):
    assert skills(key) == {key}


@pytest.mark.parametrize("key", ROLE_KEY_ORDER)
def test_every_role_key_resolves_to_itself(key):
    assert resolve_roles(key) == {key}
    assert resolve_roles(key.replace("-", " ")) == {key}


@pytest.mark.parametrize("key", CITY_KEY_ORDER)
def test_every_city_key_resolves_to_itself(key):
    assert resolve_cities(key) == {key}


@pytest.mark.parametrize("alias, target", SKILL_ALIASES.items())
def test_every_skill_alias_resolves_to_its_target(alias, target):
    assert skills(alias) == {target}


@pytest.mark.parametrize("alias, targets", ROLE_ALIASES.items())
def test_every_role_alias_resolves_to_its_targets(alias, targets):
    assert resolve_roles(alias) == set(targets)


@pytest.mark.parametrize("alias, targets", CITY_ALIASES.items())
def test_every_city_alias_resolves_to_its_targets(alias, targets):
    assert resolve_cities(alias) == set(targets)


# ---- behaviour --------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    pytest.param("NCR", NCR, id="ncr"),
    pytest.param("Delhi NCR", NCR, id="delhi-ncr"),
    pytest.param("दिल्ली एनसीआर", NCR, id="ncr-devanagari"),
    pytest.param("Delhi/Noida", {"delhi", "noida"}, id="slash-list"),
    pytest.param("Delhi / Noida", {"delhi", "noida"}, id="spaced-slash-list"),
    pytest.param("Pune or Mumbai", {"pune", "mumbai"}, id="two-cities"),
    pytest.param("पुणे या मुंबई में", {"pune", "mumbai"}, id="two-cities-devanagari"),
    pytest.param("Bangalore, Karnataka", {"bengaluru"}, id="old-name-in-a-longer-label"),
    pytest.param("Nagpur", set(), id="unknown-city"),
])
def test_resolve_cities(text, expected):
    assert resolve_cities(text) == expected


@pytest.mark.parametrize("spelling", [
    pytest.param("गुड़गांव", id="precomposed-nukta"),
    pytest.param("गुड़गांव", id="decomposed-nukta"),
    pytest.param("गुडगांव", id="no-nukta"),
    pytest.param("गुड़गाँव", id="precomposed-nukta-chandrabindu"),
    pytest.param("गुरगाँव", id="gurgaon-chandrabindu"),
])
def test_every_spelling_of_gurgaon_resolves(spelling):
    assert resolve_cities(spelling) == {"gurugram"}


@pytest.mark.parametrize("text, expected", [
    pytest.param("frontend or backend developer", {"frontend-developer", "backend-developer"}, id="two-roles"),
    pytest.param("frontend/backend developer", {"frontend-developer", "backend-developer"}, id="slash-list"),
    pytest.param("software engineer", {"backend-developer", "full-stack-developer"}, id="software-engineer"),
    pytest.param("Senior Backend Engineer", {"backend-developer"}, id="alias-inside-a-title"),
    pytest.param("full stack web developer", {"full-stack-developer"}, id="longest-over-web-developer"),
    pytest.param("UI/UX Designer", {"ui-ux-designer"}, id="slash-token-the-alias-needs"),
    pytest.param("यूआई/यूएक्स डिज़ाइनर", {"ui-ux-designer"}, id="slash-token-devanagari"),
    pytest.param("मुझे डेटा एनालिस्ट बनना है", {"data-analyst"}, id="devanagari-sentence"),
    pytest.param("chef", set(), id="unknown-role"),
])
def test_resolve_roles(text, expected):
    assert resolve_roles(text) == expected


@pytest.mark.parametrize("items, expected", [
    pytest.param(["Python और SQL आते हैं"], {"python", "sql"}, id="mixed-script"),
    pytest.param(["python", "Docker", "डॉकर"], {"python", "docker"}, id="union-of-items"),
    pytest.param(["ＰＹＴＨＯＮ"], {"python"}, id="full-width"),
    pytest.param(["जा‍वा"], {"java"}, id="zero-width-inside-devanagari"),
    pytest.param(["python/sql"], {"python", "sql"}, id="slash-list"),
    pytest.param(["c/c++"], {"c++"}, id="slash-list-with-a-suffix"),
    pytest.param(["CI/CD"], {"ci/cd"}, id="slash-token-the-alias-needs"),
    pytest.param(["CI-CD pipelines"], {"ci/cd"}, id="hyphenated-alias-in-a-phrase"),
    pytest.param(["Node.js,"], {"node.js"}, id="trailing-comma"),
    pytest.param([".NET Core"], {".net"}, id="longest-dotnet"),
    pytest.param(["ms excel"], {"excel"}, id="unambiguous-excel-in-a-phrase"),
    pytest.param(["python 3"], {"python"}, id="version-suffix"),
])
def test_resolve_skills(items, expected):
    assert resolve_skills(items) == expected


@pytest.mark.parametrize("text", ["java", "Java"])
def test_java_is_not_javascript(text):
    assert skills(text) == {"java"}


def test_javascript_is_only_javascript():
    assert skills("JavaScript") == {"javascript"}


@pytest.mark.parametrize("text", [
    "javascripting", "reactive", "gopher", "excellent", "I excel at teamwork", "go getter", "swift response team",
    "c", "cobol", "pythonic", "dockerized",
])
def test_ordinary_words_resolve_to_no_skill(text):
    assert skills(text) == frozenset()


@pytest.mark.parametrize("text", ["I go to work", "excel at teamwork", "take a rest", "node of a tree", "spring season", "taylor swift"])
def test_whole_item_only_aliases_never_match_inside_a_longer_item(text):
    assert skills(text) == frozenset()


def test_a_whole_item_only_alias_matches_alone_in_its_own_item():
    assert skills("Go", "Excel", "REST", "Node", "Spring", "Swift", "एक्सेल") == {
        "go", "excel", "rest apis", "node.js", "spring boot", "swift",
    }


def test_a_whole_item_only_alias_with_surrounding_punctuation_is_still_the_whole_item():
    assert skills("  Excel. ") == {"excel"}


def test_slash_splitting_does_not_defeat_whole_item_only():
    assert skills("go/getter") == frozenset()


@pytest.mark.parametrize("text, expected", [("mysql", "mysql"), ("postgresql", "postgresql")])
def test_one_word_skills_do_not_match_sql(text, expected):
    assert skills(text) == {expected}


def test_longest_match_wins_for_react_native():
    assert skills("react native") == {"react native"}


def test_longest_match_consumes_its_tokens():
    assert skills("react native and react") == {"react native", "react"}


@pytest.mark.parametrize("empty", [None, "", "   ", "​"])
def test_empty_input_resolves_to_nothing(empty):
    assert resolve_roles(empty) == resolve_cities(empty) == skills(empty) == resolve_experience_level(empty) == frozenset()


def test_no_items_resolve_to_nothing():
    assert resolve_skills([]) == frozenset()


# ---- purity and determinism ---------------------------------------------------------------------------------------------------------------------------
def test_resolvers_return_frozensets_for_levels_too():
    assert type(resolve_experience_level("junior")) is frozenset
    assert type(resolve_experience_level(None)) is frozenset


def test_resolvers_return_frozensets():
    assert type(resolve_skills(["python"])) is frozenset
    assert type(resolve_skills([])) is frozenset
    assert type(resolve_roles("qa")) is frozenset
    assert type(resolve_cities("pune")) is frozenset


def test_resolve_skills_does_not_mutate_a_list():
    items = ["Python", None, "ms excel"]
    resolve_skills(items)
    assert items == ["Python", None, "ms excel"]


def test_resolve_skills_accepts_a_generator():
    assert resolve_skills(item for item in ("Python", None, "SQL")) == {"python", "sql"}


def test_repeated_calls_agree():
    assert resolve_cities("NCR") == resolve_cities("NCR") == NCR


DETERMINISM_SCRIPT = """
import json
from app.jobs.resolver import resolve_cities, resolve_experience_level, resolve_roles, resolve_skills
corpus = ["Python और SQL आते हैं", "react native", "CI/CD", "software engineer", "web developer", "NCR", "Delhi/Noida", "frontend or backend developer",
          "Python+SQL", "Java+", "node.js+react", "c++11", "python++sql", "go+", "Junior to Mid", "mid-senior", "graduate", "I am a graduate student", "जूनियर"]
print(json.dumps({text: [sorted(resolve_skills([text])), sorted(resolve_roles(text)), sorted(resolve_cities(text)), sorted(resolve_experience_level(text))]
                  for text in corpus}, sort_keys=True))
"""


def test_results_do_not_depend_on_the_hash_seed():
    def run(seed):
        return subprocess.run(
            [sys.executable, "-c", DETERMINISM_SCRIPT], capture_output=True, text=True, check=True,
            cwd=Path(__file__).parents[1], env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout

    first, second = run("1"), run("2")
    assert first == second
    decoded = json.loads(first)
    assert decoded["NCR"][2] == sorted(NCR)
    assert decoded["Python+SQL"][0] == ["python", "sql"] and decoded["Junior to Mid"][3] == ["junior", "mid"]


# ---- trailing + / # fallback ------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    pytest.param("Python+SQL", {"python", "sql"}, id="python-plus-sql"),
    pytest.param("Java+", {"java"}, id="java-plus"),
    pytest.param("node.js+react", {"node.js", "react"}, id="nodejs-plus-react"),
    pytest.param("Python#SQL", {"python", "sql"}, id="hash-separator"),
    pytest.param("C++", {"c++"}, id="c++-exact-unchanged"),
    pytest.param("C#", {"c#"}, id="c#-exact-unchanged"),
    pytest.param("c++11", {"c++"}, id="c++11-unchanged"),
    pytest.param("c++ and python", {"c++", "python"}, id="c++-in-a-phrase"),
    pytest.param("C+", set(), id="c-plus-has-no-alias-once-stripped"),
    pytest.param("+", set(), id="symbol-alone"),
    pytest.param("++", set(), id="symbols-alone"),
    pytest.param("go+", {"go"}, id="whole-item-go-plus"),
    pytest.param("excel+", {"excel"}, id="whole-item-excel-plus"),
    pytest.param("I go+ to work", set(), id="whole-item-only-still-applies-to-the-stripped-token"),
    pytest.param("python++sql", {"python", "sql"}, id="two-symbols-then-a-word"),
    pytest.param("react+ native", {"react native"}, id="stripped-token-starts-a-multi-token-alias"),
    pytest.param("sql+", {"sql"}, id="sql-plus"),
])
def test_trailing_symbols_are_stripped_only_when_nothing_matches_as_written(text, expected):
    assert skills(text) == expected


def test_python_plus_plus_sql_is_python_and_sql_because_the_tokenizer_ends_a_token_after_two_symbols():
    # tokenize("python++sql") == ("python++", "sql"); "python++" has no alias, so the fallback strips it to "python".
    from app.jobs.text import tokenize
    assert tokenize("python++sql") == ("python++", "sql")
    assert skills("python++sql") == {"python", "sql"}


def test_the_fallback_does_not_change_the_tokenizer():
    from app.jobs.text import tokenize
    assert tokenize("Python+SQL") == ("python+", "sql")


# ---- experience level -------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("key", sorted(LEVEL_KEYS))
def test_every_level_key_resolves_to_itself(key):
    assert resolve_experience_level(key) == {key}


@pytest.mark.parametrize("alias, targets", LEVEL_ALIASES.items())
def test_every_level_alias_resolves_to_its_targets(alias, targets):
    assert resolve_experience_level(alias) == set(targets)


@pytest.mark.parametrize("text, expected", [
    pytest.param("Junior to Mid", {"junior", "mid"}, id="range"),
    pytest.param("mid-senior", {"mid", "senior"}, id="hyphenated-range"),
    pytest.param("Mid-Level", {"mid"}, id="mid-level-hyphen"),
    pytest.param("entry-level developer", {"fresher"}, id="alias-in-a-phrase"),
    pytest.param("Entry Level", {"fresher"}, id="entry-level-spaced"),
    pytest.param("Sr. Engineer", {"senior"}, id="abbreviation-with-a-dot"),
    pytest.param("fresher or junior", {"fresher", "junior"}, id="two-levels"),
    pytest.param("recent graduate", {"fresher"}, id="recent-graduate-beats-graduate"),
    pytest.param("fresh graduate", {"fresher"}, id="fresh-graduate"),
    pytest.param("no experience", {"fresher"}, id="no-experience"),
    pytest.param("summer internship", {"fresher"}, id="internship-in-a-phrase"),
    pytest.param("मैं फ्रेशर हूँ", {"fresher"}, id="devanagari-sentence"),
    pytest.param("जूनियर से मिड लेवल", {"junior", "mid"}, id="devanagari-range"),
    pytest.param("सीनियर", {"senior"}, id="devanagari-senior"),
    pytest.param("ＳＥＮＩＯＲ", {"senior"}, id="full-width"),
    pytest.param("sen\u200bior", {"senior"}, id="zero-width"),
])
def test_resolve_experience_level(text, expected):
    assert resolve_experience_level(text) == expected


@pytest.mark.parametrize("text", ["midnight", "seniority", "internal", "interned", "juniper", "srinivas", "midway", "international"])
def test_level_words_do_not_match_inside_longer_words(text):
    assert resolve_experience_level(text) == frozenset()


@pytest.mark.parametrize("text", ["I am a graduate student", "graduate student", "graduate school", "student council", "a student", "graduate degree"])
def test_graduate_and_student_never_match_inside_a_longer_item(text):
    # Both words are WHOLE_ITEM_ONLY: "graduate student" has two tokens, so neither alias spans the whole item.
    assert resolve_experience_level(text) == frozenset()


@pytest.mark.parametrize("text", ["graduate", "Graduate", "student", " Student. "])
def test_graduate_and_student_match_as_the_whole_item(text):
    assert resolve_experience_level(text) == {"fresher"}


def test_whole_item_only_for_levels_does_not_leak_into_the_other_resolvers():
    assert skills("graduate") == frozenset() and resolve_roles("student") == frozenset() and resolve_cities("graduate") == frozenset()


@pytest.mark.parametrize("text, expected", [
    pytest.param("c++", {"c++"}, id="c++"),
    pytest.param("c#", {"c#"}, id="c#"),
    pytest.param("c++ c#", {"c++", "c#"}, id="both"),
    pytest.param("c+", {"c"}, id="no-exact-alias-so-it-falls-back-to-c"),
])
def test_the_exact_token_is_tried_before_the_trailing_symbols_are_stripped(text, expected):
    # In the shipped vocabulary "c" is not a key, so precedence cannot be seen there. With "c" a key it can: "c++" must stay "c++".
    from app.jobs.resolver import _match, build_index
    index = build_index({}, {"c", "c++", "c#"})
    assert _match(index, text) == expected
