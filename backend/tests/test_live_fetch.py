"""Live-fetch helpers and the Apify client with mocked HTTP only. No network, no real token."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.jobs import live_fetch as lf
from app.jobs.live_fetch import ACTOR_INPUT_TEMPLATE, ApifyClient, LiveFetchUnavailable, actor_input, search_location, search_url
from app.jobs.live_fetch_config import ApifySettings, ApifyToken

TOKEN = "apify_api_TEST-TOKEN-never-real-0123456789"
SEARCH = "https://www.linkedin.com/jobs/search/?keywords=Data%20Analyst&location=Pune%2C%20India"


@pytest.fixture(autouse=True)
def no_real_token(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)


def client(handler):
    return ApifyClient(ApifySettings(ApifyToken(TOKEN)), transport=httpx.MockTransport(handler))


def run(coroutine):
    return asyncio.run(coroutine)


# ---- configuration ----------------------------------------------------------------------------------------------------------
def test_the_token_never_appears_in_repr_or_str():
    settings = ApifySettings.from_env({"APIFY_TOKEN": TOKEN})
    for text in (repr(settings), str(settings), repr(settings.token), str(settings.token), f"{settings}", f"{settings.token!r}"):
        assert TOKEN not in text and "redacted" in text
    assert settings.enabled and settings.token.reveal() == TOKEN


@pytest.mark.parametrize("environment", [{}, {"APIFY_TOKEN": ""}, {"APIFY_TOKEN": "   "}])
def test_without_a_token_the_feature_is_off(environment):
    settings = ApifySettings.from_env(environment)
    assert not settings.enabled and settings.token is None
    assert not ApifyClient(settings).enabled


def test_a_disabled_client_never_makes_a_request():
    calls = []
    disabled = ApifyClient(ApifySettings(), transport=httpx.MockTransport(lambda request: calls.append(request)))
    with pytest.raises(LiveFetchUnavailable) as error:
        run(disabled.fetch(SEARCH))
    assert error.value.reason == "not_configured" and calls == []


def test_the_limits_are_the_agreed_constants():
    assert (lf.ITEMS_PER_FETCH, lf.MAX_ITEMS_SINCE_START, lf.FETCHES_PER_CANDIDATE_PER_HOUR) == (25, 125, 3)
    assert lf.MAX_ITEMS_SINCE_START // lf.ITEMS_PER_FETCH == 5
    assert (lf.MAX_CHARGE_USD, lf.RUN_TIMEOUT_SECONDS, lf.TOTAL_TIMEOUT_SECONDS) == ("0.25", 110, 120)
    assert lf.API_URL == "https://api.apify.com/v2/actors/curious_coder~linkedin-jobs-scraper/run-sync-get-dataset-items"


# ---- search URL ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("location, expected", [
    ("Pune, India", "Pune, India"),
    ("pune", "pune, India"),
    ("India", "India"),
    ("  Pune, INDIA  ", "Pune, INDIA"),
    ("Bengaluru, Karnataka", "Bengaluru, Karnataka, India"),
    ("Indiana", "Indiana, India"),
    ("pune india", "pune india"),
    ("", None), ("   ", None), (None, None),
])
def test_the_india_suffix_is_added_only_when_missing(location, expected):
    assert search_location(location) == expected


def test_the_search_url_percent_encodes_role_and_location():
    assert search_url("Data Analyst", "Bengaluru, Karnataka") == (
        "https://www.linkedin.com/jobs/search/?keywords=Data%20Analyst&location=Bengaluru%2C%20Karnataka%2C%20India")
    assert search_url(" C++ & C# dev/ops ", "pune") == (
        "https://www.linkedin.com/jobs/search/?keywords=C%2B%2B%20%26%20C%23%20dev%2Fops&location=pune%2C%20India")
    assert search_url("डेटा एनालिस्ट", "दिल्ली").startswith("https://www.linkedin.com/jobs/search/?keywords=%E0%A4%A1")


@pytest.mark.parametrize("role, location", [("", "Pune"), ("   ", "Pune"), (None, "Pune"), ("Data Analyst", ""), ("Data Analyst", None)])
def test_a_blank_role_or_location_gives_no_url(role, location):
    assert search_url(role, location) is None


# ---- actor input --------------------------------------------------------------------------------------------------------------
def test_the_actor_input_changes_only_the_url_and_the_item_limit():
    template = json.loads(json.dumps(ACTOR_INPUT_TEMPLATE))
    payload = actor_input(SEARCH)
    assert payload == {**template, "urls": [SEARCH], "limitPerSource": 25}
    assert set(payload) == {"autoConvertToAiSearch", "limitPerSource", "scrapeCompany", "splitByLocation", "under10Applicants", "urls"}
    assert (payload["autoConvertToAiSearch"], payload["scrapeCompany"], payload["splitByLocation"], payload["under10Applicants"]) == (True, False, False, False)
    assert ACTOR_INPUT_TEMPLATE == template  # never mutated
    payload["urls"].append("x")
    assert ACTOR_INPUT_TEMPLATE == template


# ---- the HTTP client ----------------------------------------------------------------------------------------------------------
def test_the_request_matches_the_documented_endpoint_with_the_token_only_in_the_header():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201, json=[{"id": "1"}])

    assert run(client(handler).fetch(SEARCH)) == [{"id": "1"}]
    (request,) = seen
    parts = urlsplit(str(request.url))
    assert request.method == "POST"
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == lf.API_URL
    assert parse_qs(parts.query) == {"timeout": ["110"], "maxTotalChargeUsd": ["0.25"]}
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["content-type"] == "application/json"
    assert TOKEN not in str(request.url)
    assert json.loads(request.content) == actor_input(SEARCH)


def test_items_are_capped_at_25_and_non_objects_are_skipped():
    items = [{"id": str(n)} for n in range(40)]
    result = run(client(lambda request: httpx.Response(201, json=[1, "x", None, *items])).fetch(SEARCH))
    assert len(result) == 25 and result[0] == {"id": "0"}


def test_a_200_is_accepted_like_a_201():
    assert run(client(lambda request: httpx.Response(200, json=[])).fetch(SEARCH)) == []


UPSTREAM_SECRET = "UPSTREAM-DETAIL-must-not-leak"


@pytest.mark.parametrize("response, reason", [
    (httpx.Response(408, json={"error": {"type": "run-timeout-exceeded", "message": UPSTREAM_SECRET}}), "upstream_status"),
    (httpx.Response(402, json={"error": {"type": "x402-payment-required", "message": UPSTREAM_SECRET}}), "upstream_status"),
    (httpx.Response(401, json={"error": {"type": "invalid-token", "message": UPSTREAM_SECRET}}), "upstream_status"),
    (httpx.Response(500, text=UPSTREAM_SECRET), "upstream_status"),
    (httpx.Response(201, text=f"<html>{UPSTREAM_SECRET}</html>"), "bad_payload"),
    (httpx.Response(201, json={"items": [], "note": UPSTREAM_SECRET}), "bad_payload"),
])
def test_upstream_failures_become_a_fixed_reason_without_upstream_text(response, reason):
    with pytest.raises(LiveFetchUnavailable) as error:
        run(client(lambda request: response).fetch(SEARCH))
    assert error.value.reason == reason
    text = repr(error.value) + str(error.value) + repr(error.value.__cause__) + repr(error.value.__context__)
    assert UPSTREAM_SECRET not in text and TOKEN not in text and SEARCH not in text


@pytest.mark.parametrize("exception, reason", [
    (httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectTimeout("slow"), "timeout"),
    (httpx.ConnectError("refused"), "network"), (httpx.RemoteProtocolError("broken"), "network"),
])
def test_transport_failures_become_a_fixed_reason(exception, reason):
    def handler(request):
        raise exception

    with pytest.raises(LiveFetchUnavailable) as error:
        run(client(handler).fetch(SEARCH))
    assert error.value.reason == reason and error.value.__cause__ is None


def test_the_total_timeout_is_enforced_locally(monkeypatch):
    monkeypatch.setattr(lf, "TOTAL_TIMEOUT_SECONDS", 0.05)

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(201, json=[])

    with pytest.raises(LiveFetchUnavailable) as error:
        run(client(slow).fetch(SEARCH))
    assert error.value.reason == "timeout"
