"""Live LinkedIn listings through Apify (local demo only). Pure helpers plus a small HTTP client.

Endpoint (Apify API docs, "Run Actor synchronously and get dataset items"):
    POST https://api.apify.com/v2/actors/{actorId}/run-sync-get-dataset-items
with the token in `Authorization: Bearer ...` (the docs' recommended form; never in the URL), the run `timeout` in seconds and
`maxTotalChargeUsd` as the per-run spending cap. The body is the Actor input. Success is a JSON array of dataset items.

Every failure becomes `LiveFetchUnavailable` with a fixed reason code. Response bodies, the token, the request URL and the
LinkedIn search URL never go into an exception, a log line or a response.
"""

import asyncio
import copy
import re
from urllib.parse import quote

import httpx

from app.jobs.live_fetch_config import ApifySettings

ACTOR_ID = "curious_coder~linkedin-jobs-scraper"
API_URL = f"https://api.apify.com/v2/actors/{ACTOR_ID}/run-sync-get-dataset-items"
ITEMS_PER_FETCH = 25
MAX_ITEMS_SINCE_START = 125          # 5 fetches; a backend restart resets the in-memory counter
FETCHES_PER_CANDIDATE_PER_HOUR = 3
MAX_CHARGE_USD = "0.25"
RUN_TIMEOUT_SECONDS = 110            # the Actor run itself (Apify `timeout`)
TOTAL_TIMEOUT_SECONDS = 120          # the whole HTTP exchange, enforced locally
SEARCH_BASE = "https://www.linkedin.com/jobs/search/"

# The user's Actor input, verbatim. Only `urls` and `limitPerSource` are ever changed (see `actor_input`).
ACTOR_INPUT_TEMPLATE: dict[str, object] = {
    "autoConvertToAiSearch": True,
    "limitPerSource": 25,
    "scrapeCompany": False,
    "splitByLocation": False,
    "under10Applicants": False,
    "urls": [
        "https://www.linkedin.com/jobs/search/?keywords=Data%20Analyst&location=Bengaluru%2C%20Karnataka%2C%20India",
    ],
}

_ENDS_WITH_INDIA = re.compile(r"(?:^|[\s,])india$", re.IGNORECASE)


class LiveFetchUnavailable(Exception):
    """Apify could not be used. `reason` is a fixed code; no upstream text is ever attached."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def search_location(location: str | None) -> str | None:
    """The trimmed location with ', India' appended unless it already ends with the word India (case-insensitive)."""
    if not isinstance(location, str) or not location.strip():
        return None
    text = location.strip()
    return text if _ENDS_WITH_INDIA.search(text) else f"{text}, India"


def search_url(role: str | None, location: str | None) -> str | None:
    """A LinkedIn job-search URL for the saved role and location, both percent-encoded. None when either is blank."""
    place = search_location(location)
    if not isinstance(role, str) or not role.strip() or place is None:
        return None
    return f"{SEARCH_BASE}?keywords={quote(role.strip(), safe='')}&location={quote(place, safe='')}"


def actor_input(url: str) -> dict[str, object]:
    """The template with only the search URL and the item limit replaced. The template itself is never mutated."""
    payload = copy.deepcopy(ACTOR_INPUT_TEMPLATE)
    payload["urls"] = [url]
    payload["limitPerSource"] = ITEMS_PER_FETCH
    return payload


class ApifyClient:
    def __init__(self, settings: ApifySettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    async def fetch(self, url: str) -> list[dict]:
        if self._settings.token is None:
            raise LiveFetchUnavailable("not_configured")
        headers = {"Authorization": f"Bearer {self._settings.token.reveal()}", "Content-Type": "application/json"}
        params = {"timeout": RUN_TIMEOUT_SECONDS, "maxTotalChargeUsd": MAX_CHARGE_USD}
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(transport=self._transport, timeout=TOTAL_TIMEOUT_SECONDS) as client:
                    response = await client.post(API_URL, params=params, headers=headers, json=actor_input(url))
        except (TimeoutError, httpx.TimeoutException):
            raise LiveFetchUnavailable("timeout") from None
        except httpx.HTTPError:
            raise LiveFetchUnavailable("network") from None
        if response.status_code not in (200, 201):
            raise LiveFetchUnavailable("upstream_status")
        try:
            payload = response.json()
        except ValueError:
            raise LiveFetchUnavailable("bad_payload") from None
        if not isinstance(payload, list):
            raise LiveFetchUnavailable("bad_payload")
        return [item for item in payload if isinstance(item, dict)][:ITEMS_PER_FETCH]
