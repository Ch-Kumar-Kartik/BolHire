import ast
from pathlib import Path

import pytest

from app.jobs.text import normalize_text, tokenize

JOBS_PACKAGE = Path(__file__).parents[1] / "app" / "jobs"


# ---- normalize_text -------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    pytest.param("ＰＹＴＨＯＮ", "python", id="full-width"),
    pytest.param("pyt​hon", "python", id="zero-width-space"),
    pytest.param("py‍th‌on", "python", id="zwj-zwnj-latin"),
    pytest.param("pyt⁠hon", "python", id="word-joiner"),
    pytest.param("﻿python", "python", id="bom"),
    pytest.param("py­thon", "python", id="soft-hyphen"),
    pytest.param("py‎thon‏", "python", id="bidi-marks"),
    pytest.param("क्‍ष", "क्ष", id="zwj-devanagari"),
    pytest.param("क्‌ष", "क्ष", id="zwnj-devanagari"),
    pytest.param("data analyst", "data analyst", id="nbsp"),
    pytest.param("data　analyst", "data analyst", id="ideographic-space"),
    pytest.param("  data \t\n  analyst  ", "data analyst", id="whitespace-runs"),
    pytest.param("STRASSE ß", "strasse ss", id="casefold"),
    pytest.param("गुड़गांव", "गुड़गांव", id="precomposed-nukta-decomposes"),
    pytest.param(None, "", id="none"),
    pytest.param("", "", id="empty"),
    pytest.param(" ​ ", "", id="only-invisible"),
])
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


@pytest.mark.parametrize("code", [*range(0x2010, 0x2016), 0x2212])
def test_every_dash_variant_becomes_a_hyphen(code):
    assert normalize_text(f"ci{chr(code)}cd") == "ci-cd"


@pytest.mark.parametrize("value", [
    "ＰＹＴＨＯＮ", "pyt​hon", "क्‍ष", "गुड़गांव", "Straße ẞ", "ﬁ ﬀ", "ΐ ΐ", "e​́", "ͅá",
    "c＋＋", "﹘ ‑", "㎒", "가", "İ", " ́", "\x00\x1f\x85", "\ud800", "😀",
])
def test_normalize_text_is_idempotent(value):
    assert normalize_text(normalize_text(value)) == normalize_text(value)


def test_normalize_text_accepts_a_lone_surrogate():
    assert normalize_text("\ud800x") == "\ud800x"


# ---- tokenize -------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("c++", ("c++",)),
    ("C#", ("c#",)),
    (".NET", (".net",)),
    ("Node.js", ("node.js",)),
    ("node.js,", ("node.js",)),
    ("asp.net", ("asp.net",)),
    ("python.", ("python",)),
    ("CI/CD", ("ci/cd",)),
    ("ui/ux", ("ui/ux",)),
    ("ci-cd", ("ci", "cd")),
    ("Java, Python; SQL", ("java", "python", "sql")),
])
def test_tokenize_keeps_technical_names_together(raw, expected):
    assert tokenize(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    pytest.param("c+++", ("c++",), id="at-most-two-suffix-characters"),
    pytest.param("c+#", ("c+#",), id="mixed-suffix"),
    pytest.param("+c", ("c",), id="plus-before-a-token"),
    pytest.param("c +", ("c",), id="detached-plus"),
    pytest.param("c++11", ("c++", "11"), id="suffix-ends-the-token"),
    pytest.param("3.10", ("3.10",), id="dot-between-digits"),
    pytest.param(".5", ("5",), id="leading-dot-before-a-digit"),
    pytest.param("(.net)", (".net",), id="leading-dot-after-punctuation"),
    pytest.param("java .net", ("java", ".net"), id="leading-dot-mid-input"),
    pytest.param("a..b", ("a", ".b"), id="second-dot-of-a-pair-starts-a-token"),
    pytest.param("24/7", ("24", "7"), id="slash-between-digits"),
    pytest.param("ci/", ("ci",), id="trailing-slash"),
    pytest.param("/ci", ("ci",), id="leading-slash"),
    pytest.param("", (), id="empty"),
    pytest.param(None, (), id="none"),
    pytest.param(" ,;-! ", (), id="separators-only"),
])
def test_tokenize_boundaries(raw, expected):
    assert tokenize(raw) == expected


@pytest.mark.parametrize("word", ["दिल्ली", "बेंगलुरु", "जावास्क्रिप्ट", "एंड्रॉइड", "क्यूए", "गुड़गांव"])
def test_a_devanagari_word_stays_one_token_with_its_signs_intact(word):
    assert tokenize(word) == (word,)


@pytest.mark.parametrize("raw", ["पाइथन।जावा", "पाइथन॥जावा", "पाइथन॰जावा", "पाइथन । जावा"])
def test_devanagari_punctuation_separates(raw):
    assert tokenize(raw) == ("पाइथन", "जावा")


def test_mixed_script_text_tokenizes():
    assert tokenize("Python और SQL आते हैं") == ("python", "और", "sql", "आते", "हैं")


def test_tokenize_normalizes_before_splitting():
    assert tokenize("ＣＩ​／ＣＤ") == ("ci/cd",)


@pytest.mark.parametrize("unit, count, tokens", [
    pytest.param("c++ ", 2_500, 2_500, id="repeated-c++"),
    pytest.param("a", 10_000, 1, id="one-long-word"),
    pytest.param("ि", 10_000, 1, id="combining-marks"),
    pytest.param(".", 10_000, 0, id="dots"),
    pytest.param("a.", 5_000, 1, id="alternating-dots"),
    pytest.param("a/", 5_000, 1, id="alternating-slashes"),
])
def test_long_inputs_complete(unit, count, tokens):
    assert len(tokenize(unit * count)) == tokens


@pytest.mark.parametrize("module", ["text.py", "resolver.py"])
def test_no_regular_expressions_in_the_matching_code(module):
    tree = ast.parse((JOBS_PACKAGE / module).read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "re" not in imported and "regex" not in imported
