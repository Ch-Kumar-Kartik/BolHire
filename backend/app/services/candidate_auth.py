"""Candidate authentication primitives. No storage, routes, or profile behavior."""

import hashlib
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import ip_address
from urllib.parse import urlsplit

SESSION_TTL = timedelta(days=7)

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {char: index for index, char in enumerate(_ALPHABET)} | {"O": 0, "I": 1, "L": 1}
_KEY_BYTES = 16
_KEY_CHARS = 26
_MAX_KEY_INPUT = 64
_TOKEN_CHARS = 43
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_KEY_DOMAIN = b"bolhire/recovery-key/v1\0"
_TOKEN_DOMAIN = b"bolhire/session/v1\0"


class MalformedRecoveryKeyError(ValueError):
    def __init__(self) -> None:
        super().__init__("The recovery key is malformed.")


class InvalidSessionTokenError(ValueError):
    def __init__(self) -> None:
        super().__init__("The session token is invalid.")


class RecoveryKey:
    """128 random bits. Only the domain-separated verifier may be stored."""

    __slots__ = ("_raw",)

    def __init__(self, raw: bytes) -> None:
        if not isinstance(raw, bytes) or len(raw) != _KEY_BYTES:
            raise MalformedRecoveryKeyError
        self._raw = raw

    @classmethod
    def generate(cls) -> "RecoveryKey":
        return cls(secrets.token_bytes(_KEY_BYTES))

    @classmethod
    def parse(cls, text: str) -> "RecoveryKey":
        """Accept case, spaces, hyphens, and Crockford aliases; reject everything else."""
        if not isinstance(text, str) or len(text) > _MAX_KEY_INPUT or not text.isascii():
            raise MalformedRecoveryKeyError
        compact = text.strip().replace(" ", "").replace("-", "").upper()
        if len(compact) != _KEY_CHARS:
            raise MalformedRecoveryKeyError
        value = 0
        for char in compact:
            digit = _DECODE.get(char)
            if digit is None:
                raise MalformedRecoveryKeyError
            value = (value << 5) | digit
        if value & 3:  # 130 encoded bits carry 128: padding must be zero so each key has one spelling
            raise MalformedRecoveryKeyError
        return cls((value >> 2).to_bytes(_KEY_BYTES, "big"))

    def display(self) -> str:
        value = int.from_bytes(self._raw, "big") << 2
        chars = "".join(_ALPHABET[(value >> (5 * (_KEY_CHARS - 1 - index))) & 31] for index in range(_KEY_CHARS))
        return "-".join(chars[start:start + 5] for start in range(0, _KEY_CHARS, 5))

    def verifier(self) -> bytes:
        return hashlib.sha256(_KEY_DOMAIN + self._raw).digest()

    def __repr__(self) -> str:
        return "RecoveryKey(<redacted>)"

    __str__ = __repr__


class SessionToken:
    """256 random bits carried only in the cookie. Only the verifier may be stored."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not _TOKEN_PATTERN.fullmatch(value):
            raise InvalidSessionTokenError
        self._value = value

    @classmethod
    def generate(cls) -> "SessionToken":
        return cls(secrets.token_urlsafe(32))

    def reveal(self) -> str:
        return self._value

    def verifier(self) -> bytes:
        return hashlib.sha256(_TOKEN_DOMAIN + self._value.encode("ascii")).digest()

    def __repr__(self) -> str:
        return "SessionToken(<redacted>)"

    __str__ = __repr__


_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_origin(value: str) -> str:
    """Canonical form of a browser `Origin`: lower-case scheme and host, default port omitted.

    Browsers never send a default port, so `https://app.example.com:443` must compare equal to
    `https://app.example.com`. Paths, queries, fragments, credentials, port 0, and an empty port
    are rejected.
    """
    try:
        parts = urlsplit(value.strip())
        port = parts.port
        valid = (parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.path
                 and not parts.query and not parts.fragment and parts.username is None
                 and parts.password is None and port != 0 and not parts.netloc.endswith(":"))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("AUTH_TRUSTED_ORIGIN must be an origin such as https://app.example.com, with no path.") from None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}" + (f":{port}" if port is not None and port != _DEFAULT_PORTS[parts.scheme] else "")


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def origin_allowed(header: str | None, trusted_origin: str | None) -> bool:
    """Exact comparison only. Missing configuration, a missing header, and "null" all fail closed."""
    return trusted_origin is not None and header is not None and header == trusted_origin


@dataclass(frozen=True)
class AuthSettings:
    trusted_origin: str | None = None
    cookie_secure: bool = True
    trusted_proxy_hops: int = 0

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, database_enabled: bool) -> "AuthSettings":
        if not database_enabled:
            return cls()
        raw_origin = env.get("AUTH_TRUSTED_ORIGIN", "").strip()
        secure = env.get("AUTH_COOKIE_SECURE", "1")
        if secure not in {"0", "1"}:
            raise ValueError("AUTH_COOKIE_SECURE must be 0 or 1.")
        hops = env.get("AUTH_TRUSTED_PROXY_HOPS", "0")
        if not hops.isascii() or not hops.isdigit() or int(hops) > 5:
            raise ValueError("AUTH_TRUSTED_PROXY_HOPS must be an integer from 0 to 5.")
        origin = normalize_origin(raw_origin) if raw_origin else None
        parts = urlsplit(origin) if origin else None
        local_http = parts is not None and parts.scheme == "http" and _is_loopback_host(parts.hostname)
        if parts is not None and parts.scheme == "http" and not local_http:
            raise ValueError("AUTH_TRUSTED_ORIGIN must use https unless it is a loopback host such as http://localhost:5173.")
        if secure == "0" and not local_http:
            # Insecure cookies are a local-development convenience only; a deployed (https) origin must never get them.
            raise ValueError("AUTH_COOKIE_SECURE=0 is allowed only with an http AUTH_TRUSTED_ORIGIN on a loopback host.")
        return cls(trusted_origin=origin, cookie_secure=secure == "1", trusted_proxy_hops=int(hops))

    @property
    def cookie_name(self) -> str:
        # Browsers reject the __Host- prefix without Secure.
        return "__Host-bolhire_session" if self.cookie_secure else "bolhire_session"

    def set_cookie_args(self, token: SessionToken) -> dict[str, object]:
        return dict(key=self.cookie_name, value=token.reveal(), max_age=int(SESSION_TTL.total_seconds()),
                    path="/", secure=self.cookie_secure, httponly=True, samesite="strict")

    def clear_cookie_args(self) -> dict[str, object]:
        return dict(key=self.cookie_name, path="/", secure=self.cookie_secure, httponly=True, samesite="strict")
