"""Resolve free text to canonical skill, role and city keys by exact token-sequence matching. Pure; never substring matches."""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from app.jobs.aliases import CITY_ALIASES, LEVEL_ALIASES, ROLE_ALIASES, SKILL_ALIASES, WHOLE_ITEM_ONLY
from app.jobs.text import tokenize
from app.jobs.vocabulary import CITY_KEYS, LEVEL_KEYS, ROLE_KEYS, SKILL_KEYS


@dataclass(frozen=True)
class Entry:
    targets: frozenset[str]
    whole_item_only: bool


@dataclass(frozen=True)
class Index:
    entries: Mapping[tuple[str, ...], Entry]
    max_length: int
    slash_tokens: frozenset[str]  # tokens containing "/" that an alias needs intact


def build_index(
    aliases: Mapping[str, str | tuple[str, ...]],
    vocabulary: Collection[str],
    whole_item_only: Collection[str] = frozenset(),
) -> Index:
    """Index `aliases` plus each vocabulary key's own text. Raises ValueError on an invalid table."""
    if unknown := sorted(set(whole_item_only) - set(aliases)):
        raise ValueError(f"WHOLE_ITEM_ONLY entries are not aliases: {unknown}")
    entries: dict[tuple[str, ...], Entry] = {}

    def add(alias: str, targets: frozenset[str]) -> None:
        tokens = tokenize(alias)
        if not tokens:
            raise ValueError(f"Alias has no tokens: {alias!r}")
        if unknown := sorted(targets - set(vocabulary)):
            raise ValueError(f"Alias {alias!r} has targets outside the vocabulary: {unknown}")
        existing = entries.get(tokens)
        if existing is not None and existing.targets != targets:
            raise ValueError(f"Aliases with the tokens {tokens!r} have different targets.")
        entries[tokens] = Entry(targets, alias in whole_item_only or (existing is not None and existing.whole_item_only))

    for key in sorted(vocabulary):
        add(key, frozenset({key}))
    for alias, target in aliases.items():
        add(alias, frozenset({target}) if isinstance(target, str) else frozenset(target))
    return Index(
        entries=entries,
        max_length=max((len(tokens) for tokens in entries), default=0),
        slash_tokens=frozenset(token for tokens in entries for token in tokens if "/" in token),
    )


_SKILLS = build_index(SKILL_ALIASES, SKILL_KEYS, WHOLE_ITEM_ONLY & set(SKILL_ALIASES))
_ROLES = build_index(ROLE_ALIASES, ROLE_KEYS, WHOLE_ITEM_ONLY & set(ROLE_ALIASES))
_CITIES = build_index(CITY_ALIASES, CITY_KEYS, WHOLE_ITEM_ONLY & set(CITY_ALIASES))
_LEVELS = build_index(LEVEL_ALIASES, LEVEL_KEYS, WHOLE_ITEM_ONLY & set(LEVEL_ALIASES))


def _split_lists(tokens: tuple[str, ...], index: Index) -> tuple[str, ...]:
    """`delhi/noida` is one token; unless an alias needs that token, the slash is a list separator."""
    split: list[str] = []
    for token in tokens:
        split.extend(token.split("/") if "/" in token and token not in index.slash_tokens else (token,))
    return tuple(split)


def _longest(index: Index, tokens: tuple[str, ...], position: int, item_length: int) -> tuple[int, Entry] | None:
    """The longest alias starting at `position`; a WHOLE_ITEM_ONLY alias counts only if it spans all `item_length` tokens."""
    for length in range(min(index.max_length, len(tokens) - position), 0, -1):
        entry = index.entries.get(tokens[position:position + length])
        if entry is not None and not (entry.whole_item_only and length != item_length):
            return length, entry
    return None


def _match(index: Index, text: str | None) -> frozenset[str]:
    """Scan left to right, consuming the longest alias at each position. Only when nothing matches the token as written, and it
    ends in `+` or `#` after at least one other character, retry once with those trailing symbols removed (`python+sql`)."""
    tokens = _split_lists(tokenize(text), index)
    found: set[str] = set()
    position = 0
    while position < len(tokens):
        hit = _longest(index, tokens, position, len(tokens))
        if hit is None:
            stripped = tokens[position].rstrip("+#")
            if stripped and stripped != tokens[position]:
                hit = _longest(index, (stripped, *tokens[position + 1:]), 0, len(tokens))
        if hit is None:
            position += 1
            continue
        length, entry = hit
        found |= entry.targets
        position += length
    return frozenset(found)


def resolve_skills(items: Iterable[str | None]) -> frozenset[str]:
    """Each item is resolved separately, so WHOLE_ITEM_ONLY applies per item."""
    return frozenset().union(*(_match(_SKILLS, item) for item in items))


def resolve_roles(text: str | None) -> frozenset[str]:
    return _match(_ROLES, text)


def resolve_cities(text: str | None) -> frozenset[str]:
    return _match(_CITIES, text)


def resolve_experience_level(text: str | None) -> frozenset[str]:
    return _match(_LEVELS, text)
