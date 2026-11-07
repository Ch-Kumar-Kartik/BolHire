"""Deterministic text normalization and tokenization for job matching. Pure; no regular expressions.

Devanagari vowel signs and viramas are category M*, so token characters are decided by Unicode category with a hand-written loop.
"""

import unicodedata

_DASHES = str.maketrans({chr(code): "-" for code in (*range(0x2010, 0x2016), 0x2212)})
_MAX_SUFFIX = 2


def _strip_format_characters(text: str) -> str:
    return "".join(char for char in text if unicodedata.category(char) != "Cf")


def normalize_text(value: str | None) -> str:
    """NFKC, format characters removed, casefolded, NFKC again, dashes unified, whitespace collapsed. Idempotent."""
    if value is None:
        return ""
    text = _strip_format_characters(unicodedata.normalize("NFKC", value))
    text = unicodedata.normalize("NFKC", text.casefold())
    return " ".join(text.translate(_DASHES).split())


def _is_token_character(char: str) -> bool:
    return unicodedata.category(char)[0] in "LMN"


def _is_letter(char: str) -> bool:
    return unicodedata.category(char)[0] == "L"


def tokenize(value: str | None) -> tuple[str, ...]:
    """Split normalized text into tokens in linear time.

    Token characters are L*, M* and N*. `+` and `#` attach after a token character (at most two) and end the token. `.` joins two
    token characters, or starts a token before a letter (`.net`). `/` joins two letters. Everything else separates.
    """
    text = normalize_text(value)
    length = len(text)
    tokens: list[str] = []
    start = -1  # index where the current token began; -1 when outside a token
    index = 0
    while index < length:
        char = text[index]
        following = text[index + 1] if index + 1 < length else ""
        if _is_token_character(char):
            start = index if start < 0 else start
            index += 1
        elif char in "+#" and start >= 0:
            end = index
            while end < length and end - index < _MAX_SUFFIX and text[end] in "+#":
                end += 1
            tokens.append(text[start:end])
            start, index = -1, end
        elif char == "." and start >= 0 and following and _is_token_character(following):
            index += 1
        elif char == "." and start < 0 and following and _is_letter(following):
            start = index
            index += 1
        elif char == "/" and start >= 0 and _is_letter(text[index - 1]) and following and _is_letter(following):
            index += 1
        else:
            if start >= 0:
                tokens.append(text[start:index])
                start = -1
            index += 1
    if start >= 0:
        tokens.append(text[start:])
    return tuple(tokens)
