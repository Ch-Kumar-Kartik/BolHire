import hashlib

import pytest

from app.services.candidate_auth import (
    SESSION_TTL, AuthSettings, InvalidSessionTokenError, MalformedRecoveryKeyError, RecoveryKey,
    SessionToken, normalize_origin, origin_allowed,
)


def test_generated_keys_are_unique_canonical_and_roundtrip():
    keys = [RecoveryKey.generate() for _ in range(500)]
    displays = {key.display() for key in keys}
    assert len(displays) == 500
    for key in keys[:50]:
        text = key.display()
        assert len(text) == 31 and text.count("-") == 5
        assert set(text.replace("-", "")) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
        assert RecoveryKey.parse(text).verifier() == key.verifier()


def test_parse_tolerates_case_spacing_and_crockford_aliases():
    key = RecoveryKey.generate()
    text = key.display()
    assert RecoveryKey.parse(text.lower()).verifier() == key.verifier()
    assert RecoveryKey.parse("  " + text.replace("-", " ") + "\n").verifier() == key.verifier()
    aliased = text.replace("0", "O").replace("1", "I")
    assert RecoveryKey.parse(aliased).verifier() == key.verifier()


@pytest.mark.parametrize("bad", [
    "", "short", "A" * 25, "A" * 27, "U" * 26, "!" * 26, "ı" * 26, "ſ" * 26, "A" * 200, " " * 30,
    "0" * 25 + "1",  # non-zero padding bits: a second spelling of an existing key
    None, 123, b"0" * 26,
])
def test_parse_rejects_malformed_without_echo(bad):
    with pytest.raises(MalformedRecoveryKeyError) as error:
        RecoveryKey.parse(bad)
    assert str(bad) not in str(error.value) or str(bad) == ""


def test_verifiers_are_32_bytes_and_domain_separated():
    key = RecoveryKey(b"\x01" * 16)
    assert len(key.verifier()) == 32
    assert key.verifier() != hashlib.sha256(b"\x01" * 16).digest()
    token = SessionToken.generate()
    assert len(token.verifier()) == 32
    assert token.verifier() != hashlib.sha256(token.reveal().encode()).digest()
    # Same bytes can never verify as both kinds of secret.
    raw = b"A" * 43
    assert RecoveryKey(raw[:16]).verifier() != SessionToken(raw.decode()).verifier()


def test_tokens_are_unique_urlsafe_and_validated():
    tokens = {SessionToken.generate().reveal() for _ in range(500)}
    assert len(tokens) == 500 and all(len(value) == 43 for value in tokens)
    for bad in ["", "a" * 42, "a" * 44, "a" * 42 + "=", "a" * 42 + "!", "é" * 43, None]:
        with pytest.raises(InvalidSessionTokenError):
            SessionToken(bad)


def test_secrets_are_redacted_in_repr_and_str():
    key, token = RecoveryKey.generate(), SessionToken.generate()
    for secret, text in [(key, key.display()), (token, token.reveal())]:
        for rendered in (repr(secret), str(secret), f"{secret}", f"{secret!r}", repr([secret]), repr({"k": secret})):
            assert text not in rendered and "redacted" in rendered
    assert not hasattr(key, "__dict__") and not hasattr(token, "__dict__")


@pytest.mark.parametrize("value, expected", [
    ("https://app.example.com", "https://app.example.com"),
    ("HTTPS://App.Example.com:8443", "https://app.example.com:8443"),
    (" http://localhost:5173 ", "http://localhost:5173"),
    ("https://app.example.com:443", "https://app.example.com"),  # a browser never sends a default port
    ("HTTPS://App.Example.com:443", "https://app.example.com"),
    ("http://localhost:80", "http://localhost"),
    ("https://app.example.com:80", "https://app.example.com:80"),  # 80 is not the default for https
    ("http://app.example.com:443", "http://app.example.com:443"),  # 443 is not the default for http
    ("http://[::1]:5173", "http://[::1]:5173"),
    ("https://[2001:DB8::1]:443", "https://[2001:db8::1]"),
])
def test_normalize_origin(value, expected):
    assert normalize_origin(value) == expected


@pytest.mark.parametrize("bad", [
    "app.example.com", "ftp://x.com", "https://x.com/", "https://x.com/path", "https://x.com?q=1",
    "https://user@x.com", "https://x.com#f", "https://x.com:99999", "https://", "https://x.com:0", "https://x.com:",
    "https://x.com:abc", "", "   ",
])
def test_normalize_origin_rejects_non_origins(bad):
    with pytest.raises(ValueError):
        normalize_origin(bad)


def test_a_browser_origin_header_matches_a_configured_origin_that_spelled_the_default_port():
    configured = normalize_origin("https://app.example.com:443")
    assert origin_allowed("https://app.example.com", configured)
    # Only a non-browser client would send the default port; the comparison stays exact and fails closed.
    assert not origin_allowed("https://app.example.com:443", configured)


def test_origin_allowed_is_exact_and_fails_closed():
    trusted = "https://app.example.com"
    assert origin_allowed(trusted, trusted)
    for header in [None, "", "null", "https://app.example.com/", "http://app.example.com",
                   "https://app.example.com:443", "https://evil.example.com", "https://app.example.com.evil.com",
                   "HTTPS://APP.EXAMPLE.COM"]:
        assert not origin_allowed(header, trusted)
    assert not origin_allowed(trusted, None)
    assert not origin_allowed(None, None)


def test_settings_defaults_and_disabled_database_ignores_environment():
    assert AuthSettings.from_env({}, database_enabled=True) == AuthSettings()
    assert AuthSettings.from_env({"AUTH_TRUSTED_ORIGIN": "garbage", "AUTH_COOKIE_SECURE": "x"},
                                 database_enabled=False) == AuthSettings()
    settings = AuthSettings.from_env({"AUTH_TRUSTED_ORIGIN": "https://App.example.com:443", "AUTH_TRUSTED_PROXY_HOPS": "2"}, database_enabled=True)
    assert (settings.trusted_origin, settings.cookie_secure, settings.trusted_proxy_hops) == ("https://app.example.com", True, 2)


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173", "http://[::1]:5173", "http://localhost"])
def test_insecure_cookies_are_allowed_only_for_local_http_development(origin):
    settings = AuthSettings.from_env({"AUTH_TRUSTED_ORIGIN": origin, "AUTH_COOKIE_SECURE": "0"}, database_enabled=True)
    assert settings.cookie_secure is False and settings.cookie_name == "bolhire_session"
    assert AuthSettings.from_env({"AUTH_TRUSTED_ORIGIN": origin}, database_enabled=True).cookie_secure is True  # secure by default, even locally


@pytest.mark.parametrize("env", [
    {"AUTH_TRUSTED_ORIGIN": "https://app.example.com", "AUTH_COOKIE_SECURE": "0"},  # a deployed origin never gets insecure cookies
    {"AUTH_TRUSTED_ORIGIN": "https://localhost:5173", "AUTH_COOKIE_SECURE": "0"},  # loopback alone is not enough: the scheme must be http
    {"AUTH_COOKIE_SECURE": "0"},  # no origin to justify it
    {"AUTH_TRUSTED_ORIGIN": "http://app.example.com"},  # cleartext, non-loopback
    {"AUTH_TRUSTED_ORIGIN": "http://localhost.evil.example.com", "AUTH_COOKIE_SECURE": "0"},
    {"AUTH_TRUSTED_ORIGIN": "http://192.168.1.20:5173", "AUTH_COOKIE_SECURE": "0"},
])
def test_insecure_cookie_and_cleartext_origin_configurations_are_rejected(env):
    with pytest.raises(ValueError):
        AuthSettings.from_env(env, database_enabled=True)


def test_a_rejected_configuration_is_ignored_when_the_database_is_disabled():
    assert AuthSettings.from_env({"AUTH_TRUSTED_ORIGIN": "https://x.com", "AUTH_COOKIE_SECURE": "0"}, database_enabled=False) == AuthSettings()


@pytest.mark.parametrize("env", [
    {"AUTH_TRUSTED_ORIGIN": "https://x.com/path"}, {"AUTH_COOKIE_SECURE": "yes"},
    {"AUTH_TRUSTED_PROXY_HOPS": "-1"}, {"AUTH_TRUSTED_PROXY_HOPS": "6"}, {"AUTH_TRUSTED_PROXY_HOPS": "x"},
    {"AUTH_TRUSTED_PROXY_HOPS": "١"},
])
def test_settings_reject_invalid_values(env):
    with pytest.raises(ValueError):
        AuthSettings.from_env(env, database_enabled=True)


def test_cookie_attributes():
    token = SessionToken.generate()
    secure = AuthSettings(trusted_origin="https://a.example", cookie_secure=True)
    args = secure.set_cookie_args(token)
    assert args == dict(key="__Host-bolhire_session", value=token.reveal(), max_age=7 * 24 * 3600, path="/",
                        secure=True, httponly=True, samesite="strict")
    assert "domain" not in args and SESSION_TTL.days == 7
    assert secure.clear_cookie_args()["key"] == "__Host-bolhire_session"
    local = AuthSettings(cookie_secure=False)
    assert local.cookie_name == "bolhire_session" and local.set_cookie_args(token)["secure"] is False
