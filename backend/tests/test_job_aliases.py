import pytest

from app.jobs.aliases import CITY_ALIASES, LEVEL_ALIASES, ROLE_ALIASES, SKILL_ALIASES, WHOLE_ITEM_ONLY
from app.jobs.resolver import build_index
from app.jobs.text import normalize_text, tokenize
from app.jobs.vocabulary import CITY_KEYS, LEVEL_KEYS, ROLE_KEYS, SKILL_KEYS

TABLES = [
    pytest.param({alias: (target,) for alias, target in SKILL_ALIASES.items()}, SKILL_KEYS, id="skills"),
    pytest.param(ROLE_ALIASES, ROLE_KEYS, id="roles"),
    pytest.param(CITY_ALIASES, CITY_KEYS, id="cities"),
    pytest.param(LEVEL_ALIASES, LEVEL_KEYS, id="levels"),
]
ALL_TABLES = (SKILL_ALIASES, ROLE_ALIASES, CITY_ALIASES, LEVEL_ALIASES)


def devanagari(text):
    return any("ऀ" <= char <= "ॿ" for char in text)


def devanagari_targets(table):
    return {target for alias, targets in table.items() if devanagari(alias) for target in targets}


@pytest.mark.parametrize("table, vocabulary", TABLES)
def test_every_target_is_in_the_vocabulary(table, vocabulary):
    assert {target for targets in table.values() for target in targets} <= vocabulary


@pytest.mark.parametrize("table, vocabulary", TABLES)
def test_no_token_sequence_has_two_different_targets(table, vocabulary):
    seen = {tokenize(key): frozenset({key}) for key in vocabulary}
    assert len(seen) == len(vocabulary)  # no two keys collapse to one token sequence
    for alias, targets in table.items():
        assert seen.setdefault(tokenize(alias), frozenset(targets)) == frozenset(targets), alias


@pytest.mark.parametrize("table, vocabulary", TABLES)
def test_every_alias_is_non_empty_after_normalization(table, vocabulary):
    for alias in table:
        assert normalize_text(alias) and tokenize(alias), alias


def test_every_city_has_a_devanagari_alias():
    assert devanagari_targets(CITY_ALIASES) == CITY_KEYS


def test_every_role_has_a_devanagari_alias():
    assert devanagari_targets(ROLE_ALIASES) == ROLE_KEYS


def test_at_least_twelve_skills_have_a_devanagari_alias():
    assert len({target for alias, target in SKILL_ALIASES.items() if devanagari(alias)}) >= 12


def test_whole_item_only_entries_are_aliases():
    assert WHOLE_ITEM_ONLY <= set().union(*ALL_TABLES)


def test_the_dictionary_stays_small():
    assert sum(len(table) for table in ALL_TABLES) <= 400


@pytest.mark.parametrize("aliases, vocabulary, whole_item_only, message", [
    pytest.param({"!!!": "a"}, {"a"}, frozenset(), "no tokens", id="alias-with-no-tokens"),
    pytest.param({"x y": "a", "x-y": "b"}, {"a", "b"}, frozenset(), "different targets", id="same-tokens-different-targets"),
    pytest.param({"a": "b"}, {"a", "b"}, frozenset(), "different targets", id="alias-conflicts-with-a-key"),
    pytest.param({"x": "z"}, {"a"}, frozenset(), "outside the vocabulary", id="target-outside-the-vocabulary"),
    pytest.param({"x": ("a", "z")}, {"a"}, frozenset(), "outside the vocabulary", id="one-of-several-targets-outside"),
    pytest.param({"x": "a"}, {"a"}, frozenset({"missing"}), "not aliases", id="whole-item-entry-that-is-not-an-alias"),
])
def test_build_index_rejects_an_invalid_table(aliases, vocabulary, whole_item_only, message):
    with pytest.raises(ValueError, match=message):
        build_index(aliases, vocabulary, whole_item_only)


def test_build_index_accepts_two_spellings_of_one_target():
    index = build_index({"x y": "a", "x-y": "a"}, {"a"})
    assert index.entries[("x", "y")].targets == frozenset({"a"})
    assert index.max_length == 2


def test_every_level_has_a_devanagari_alias():
    assert devanagari_targets(LEVEL_ALIASES) == LEVEL_KEYS


def test_the_level_whole_item_only_entries_are_exactly_graduate_and_student():
    assert WHOLE_ITEM_ONLY & set(LEVEL_ALIASES) == {"graduate", "student"}


def test_whole_item_only_words_are_not_aliases_in_a_second_table():
    owners = [sum(word in table for table in ALL_TABLES) for word in WHOLE_ITEM_ONLY]
    assert owners == [1] * len(owners)


def test_the_level_table_has_the_specified_size():
    assert len(LEVEL_ALIASES) == 33
