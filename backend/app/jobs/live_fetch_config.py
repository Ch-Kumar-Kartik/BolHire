"""Server-side Apify configuration for the live job fetch (local demo only).

The token is read from the process environment and held in `ApifyToken`, whose repr and str never show it. Only
`ApifyToken.reveal()` returns the value, and it is called in exactly one place: building the Authorization header.
Without a token the feature is off and the endpoint answers with a safe 503 code.
"""

from collections.abc import Mapping
from dataclasses import dataclass


class ApifyToken:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("An Apify token must be a non-empty string.")
        self._value = value.strip()

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "ApifyToken(<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True)
class ApifySettings:
    token: ApifyToken | None = None

    @property
    def enabled(self) -> bool:
        return self.token is not None

    @classmethod
    def from_env(cls, environment: Mapping[str, str]) -> "ApifySettings":
        raw = environment.get("APIFY_TOKEN", "")
        return cls(token=ApifyToken(raw) if raw.strip() else None)
