"""Process-local, per-client abuse limiter. Best-effort load protection, not the security boundary:
recovery keys and session tokens are unguessable regardless of attempt counts.

Whether it limits individual clients depends entirely on the deployment, which is NOT established yet:
* With no trusted proxy hops the client is the socket peer. Behind any proxy that is the proxy itself, so every
  user would share one bucket and a single caller could lock everyone out of login.
* Trusting hops only helps if the real forwarding-header chain has been observed. It does not stop a caller who
  reaches the backend directly (for example a public Railway URL) from forging the entries to the left, and it is
  not a substitute for deciding whether direct backend access must be restricted.
* State is per process; several workers or instances multiply the limits.
"""

import time
from collections import deque
from collections.abc import Callable, Mapping


class SlidingWindowLimiter:
    def __init__(
        self, *, max_attempts: int, window_seconds: float, max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_attempts < 1 or window_seconds <= 0 or max_keys < 1:
            raise ValueError("Limiter limits must be positive.")
        self._max_attempts = max_attempts
        self._window = window_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._attempts: dict[str, deque[float]] = {}

    def hit(self, key: str) -> float | None:
        """Record an attempt. Returns seconds to wait when the limit is exceeded, else None.

        Blocked attempts are not recorded, so a client regains access once its oldest
        counted attempt leaves the window.
        """
        now = self._clock()
        attempts = self._attempts.get(key)
        if attempts is None:
            if len(self._attempts) >= self._max_keys:
                self._make_room(now)
            attempts = self._attempts[key] = deque()
        while attempts and now - attempts[0] >= self._window:
            attempts.popleft()
        if len(attempts) >= self._max_attempts:
            return max(self._window - (now - attempts[0]), 0.0)
        attempts.append(now)
        return None

    def _make_room(self, now: float) -> None:
        for key in [key for key, items in self._attempts.items() if not items or now - items[-1] >= self._window]:
            del self._attempts[key]
        if len(self._attempts) >= self._max_keys:
            # Flood of distinct keys: forget the oldest key. This can only weaken the per-key
            # limit for that key; the global limiter still bounds total throughput.
            del self._attempts[next(iter(self._attempts))]


def client_key(peer_host: str | None, headers: Mapping[str, str], trusted_proxy_hops: int) -> str:
    """Select the client address. Forwarded headers are ignored unless proxies are configured as trusted.

    With N trusted hops the client is the Nth entry from the right of X-Forwarded-For; entries
    further left are attacker-controlled. Missing or short headers fall back to the socket peer.
    """
    if trusted_proxy_hops > 0:
        entries = [part.strip() for part in headers.get("x-forwarded-for", "").split(",") if part.strip()]
        if len(entries) >= trusted_proxy_hops:
            return entries[-trusted_proxy_hops][:64]
    return peer_host or "unknown"
