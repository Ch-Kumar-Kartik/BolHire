import pytest

from app.services.rate_limit import SlidingWindowLimiter, client_key


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_blocks_after_limit_and_recovers_as_window_slides():
    clock = Clock()
    limiter = SlidingWindowLimiter(max_attempts=3, window_seconds=60, clock=clock)
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    assert limiter.hit("a") == pytest.approx(60)
    clock.now += 20
    assert limiter.hit("a") == pytest.approx(40)  # blocked attempts are not counted
    clock.now += 40
    assert limiter.hit("a") is None


def test_keys_are_independent():
    limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60, clock=Clock())
    assert limiter.hit("a") is None and limiter.hit("b") is None
    assert limiter.hit("a") is not None and limiter.hit("b") is not None


def test_key_table_is_bounded_under_a_flood_of_distinct_keys():
    clock = Clock()
    limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60, max_keys=100, clock=clock)
    for index in range(10_000):
        limiter.hit(f"k{index}")
        assert len(limiter._attempts) <= 100
    clock.now += 61  # expired keys are reclaimed before any live key is evicted
    limiter.hit("fresh")
    assert len(limiter._attempts) == 1


def test_invalid_limits_rejected():
    for kwargs in [dict(max_attempts=0, window_seconds=1), dict(max_attempts=1, window_seconds=0),
                   dict(max_attempts=1, window_seconds=1, max_keys=0)]:
        with pytest.raises(ValueError):
            SlidingWindowLimiter(**kwargs)


def test_client_key_ignores_forwarded_headers_without_trusted_hops():
    headers = {"x-forwarded-for": "6.6.6.6"}
    assert client_key("10.0.0.1", headers, 0) == "10.0.0.1"
    assert client_key(None, {}, 0) == "unknown"


def test_client_key_counts_trusted_hops_from_the_right():
    spoofed = {"x-forwarded-for": "6.6.6.6, 203.0.113.9, 10.1.1.1"}
    assert client_key("10.0.0.1", spoofed, 1) == "10.1.1.1"
    assert client_key("10.0.0.1", spoofed, 2) == "203.0.113.9"
    assert client_key("10.0.0.1", spoofed, 3) == "6.6.6.6"
    assert client_key("10.0.0.1", spoofed, 4) == "10.0.0.1"  # fewer entries than trusted hops
    assert client_key("10.0.0.1", {}, 1) == "10.0.0.1"
    assert len(client_key("p", {"x-forwarded-for": "x" * 500}, 1)) == 64
