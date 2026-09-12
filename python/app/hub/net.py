"""Network resilience primitives for the hub adapters.

Design reference: ``docs/DESIGN_V280_DUAL_SOURCE.md`` §2.5.

Everything here is injectable so tests stay **offline and fast**:

* ``sleep`` — defaults to :func:`asyncio.sleep`; tests pass a recorder so retry
  back-offs never actually wait (design §6.3 "three iron rules").
* ``clock`` — defaults to :func:`time.monotonic`; tests may freeze it.
* ``client`` — an ``httpx.AsyncClient`` supplied by the adapter.

A separate ``TokenBucket`` (rather than reusing ``main.py``'s private one) is a
deliberate trade-off: ``main`` imports ``hub``, so importing ``main`` from
``hub`` would be a circular dependency (design §5.3, debt D1).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import httpx

# ---------------------------------------------------------------------------
# Timeouts (§2.5) — split connect/read so a half-open socket cannot stall us
# ---------------------------------------------------------------------------


def timeout_for(kind: str = "search") -> httpx.Timeout:
    """Return the per-scenario timeout object.

    ===========  =======  =====  =====
    scenario     connect  read   total
    ===========  =======  =====  =====
    search       5 s      8 s    10 s
    detail       5 s      8 s    10 s
    files        5 s      12 s   15 s
    ===========  =======  =====  =====
    """
    table = {
        "search": (5.0, 8.0, 10.0),
        "detail": (5.0, 8.0, 10.0),
        "files": (5.0, 12.0, 15.0),
    }
    connect, read, total = table.get(kind, table["search"])
    return httpx.Timeout(total, connect=connect, read=read, write=5.0, pool=5.0)


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------


class TTLCache:
    """Bounded in-process cache with per-entry TTL and LRU eviction.

    Not thread-safe by design: it is only touched from the asyncio event loop.
    """

    def __init__(
        self,
        max_entries: int = 512,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max(1, int(max_entries))
        self._clock = clock
        self._data: dict[str, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any:
        """Return the cached value or ``None`` (expired entries are dropped)."""
        item = self._data.get(key)
        if item is None:
            self.misses += 1
            return None
        expires_at, value = item
        if expires_at <= self._clock():
            self._data.pop(key, None)
            self.misses += 1
            return None
        # refresh LRU position
        self._data.pop(key, None)
        self._data[key] = (expires_at, value)
        self.hits += 1
        return value

    def set(self, key: str, value: Any, ttl: float = 90.0) -> None:
        """Store ``value`` for ``ttl`` seconds, evicting LRU entries if full."""
        if ttl <= 0:
            return
        self._data.pop(key, None)
        self._data[key] = (self._clock() + float(ttl), value)
        while len(self._data) > self.max_entries:
            self._data.pop(next(iter(self._data)), None)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def purge_expired(self) -> int:
        """Drop every expired entry; return how many were removed."""
        now = self._clock()
        stale = [k for k, (exp, _v) in self._data.items() if exp <= now]
        for k in stale:
            self._data.pop(k, None)
        return len(stale)

    def __len__(self) -> int:
        return len(self._data)


# ---------------------------------------------------------------------------
# Token bucket (local self-protection, §2.5)
# ---------------------------------------------------------------------------


class TokenBucket:
    """Constant-rate token bucket. ``take()`` never blocks."""

    def __init__(
        self,
        rate: float = 5.0,
        capacity: float = 10.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = float(rate)
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self._clock = clock
        self._ts = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._ts)
        self._ts = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

    def take(self, n: float = 1.0) -> bool:
        """Consume ``n`` tokens; False when the bucket is empty."""
        self._refill()
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def available(self) -> float:
        self._refill()
        return self.tokens

    def retry_after(self, n: float = 1.0) -> float:
        """Seconds until ``n`` tokens are available again."""
        self._refill()
        if self.tokens >= n:
            return 0.0
        return (n - self.tokens) / self.rate if self.rate > 0 else 1.0


# ---------------------------------------------------------------------------
# Circuit breaker (§2.5)
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """closed → (N failures) → open → (cooldown) → half-open → closed."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"

    def __init__(
        self,
        fail_threshold: int = 5,
        open_seconds: float = 60.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.fail_threshold = max(1, int(fail_threshold))
        self.open_seconds = float(open_seconds)
        self._clock = clock
        self._fails = 0
        self._opened_at = -1.0
        self._state = self.CLOSED
        self._probe_in_flight = False

    @property
    def state(self) -> str:
        # NOTE: `_opened_at` may legitimately be 0.0 (frozen test clock), so we
        # test for a sentinel `-1.0` rather than truthiness — otherwise a breaker
        # opened at t=0.0 could never transition to half-open.
        if self._state == self.OPEN and self._opened_at >= 0.0:
            if self._clock() - self._opened_at >= self.open_seconds:
                return self.HALF_OPEN
        return self._state

    @property
    def reopen_in(self) -> float:
        """Seconds until the breaker moves to half-open (0 when not open)."""
        if self.state != self.OPEN:
            return 0.0
        remaining = self.open_seconds - (self._clock() - self._opened_at)
        return max(0.0, round(remaining, 2))

    def allow(self) -> bool:
        """Should we send a request right now?"""
        st = self.state
        if st == self.CLOSED:
            return True
        if st == self.HALF_OPEN:
            # Exactly one probe request is let through.
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True
        return False

    def record_ok(self) -> None:
        """A successful (or at least answered) request closes the breaker."""
        self._fails = 0
        self._state = self.CLOSED
        self._opened_at = -1.0
        self._probe_in_flight = False

    def record_fail(self) -> None:
        """Count a failure; trip to open once the threshold is reached."""
        self._fails += 1
        self._probe_in_flight = False
        if self._state == self.HALF_OPEN:
            # A failed probe re-opens immediately for a full cooldown.
            self._state = self.OPEN
            self._opened_at = self._clock()
            return
        if self._fails >= self.fail_threshold:
            self._state = self.OPEN
            self._opened_at = self._clock()

    def reset(self) -> None:
        self._fails = 0
        self._state = self.CLOSED
        self._opened_at = -1.0
        self._probe_in_flight = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "failures": self._fails,
            "reopen_in_s": self.reopen_in,
        }


# ---------------------------------------------------------------------------
# Retrying request helper
# ---------------------------------------------------------------------------

#: Statuses worth retrying (§2.5). 4xx other than 429 are never retried.
DEFAULT_RETRY_STATUSES: tuple[int, ...] = (429, 502, 503, 504)
#: Statuses that must never be retried (a retry is provably useless).
NO_RETRY_STATUSES: frozenset[int] = frozenset({400, 401, 403, 404, 405, 410, 422})


@dataclass
class FetchOutcome:
    """Result of :func:`request_with_retry` — never raises."""

    ok: bool
    url: str = ""
    status: int = 0
    error: str = ""
    #: ok | network | timeout | http_error | not_found | rate_limited |
    #: circuit_open | local_limited | bad_json
    code: str = "ok"
    attempts: int = 0
    sleeps: list[float] = field(default_factory=list)
    response: httpx.Response | None = None
    data: Any = None

    def json(self) -> Any:
        """Parsed JSON body (``None`` when absent or unparseable)."""
        return self.data


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    json_body: Any = None,
    content: bytes | None = None,
    timeout: httpx.Timeout | None = None,
    retries: int = 2,
    backoff: Sequence[float] = (0.4, 1.2),
    jitter: float = 0.3,
    sleep: Callable[[float], Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    breaker: CircuitBreaker | None = None,
    bucket: TokenBucket | None = None,
    retry_statuses: Sequence[int] = DEFAULT_RETRY_STATUSES,
    rand: Callable[[], float] | None = None,
) -> FetchOutcome:
    """Issue one HTTP request with rate-limit, circuit-breaker and retry.

    Returns a :class:`FetchOutcome` instead of raising — adapters turn any
    non-``ok`` outcome into a degraded :class:`~app.hub.base.PageResult` so the
    market page is never taken down by one flaky upstream (§2.6).

    ``429`` handling follows §2.5: honour ``Retry-After`` when it is ≤ 5 s,
    otherwise give up immediately and count a breaker failure (never let the
    sidecar hang on a hostile rate limiter).
    """
    sleeper = sleep if sleep is not None else asyncio.sleep
    rnd = rand if rand is not None else _default_rand
    outcome = FetchOutcome(ok=False, url=url)

    if bucket is not None and not bucket.take():
        outcome.code = "local_limited"
        outcome.error = "local rate limit"
        outcome.status = 429
        return outcome

    if breaker is not None and not breaker.allow():
        outcome.code = "circuit_open"
        outcome.error = "circuit open"
        return outcome

    max_attempts = max(1, int(retries) + 1)
    for attempt in range(1, max_attempts + 1):
        outcome.attempts = attempt
        try:
            resp = await client.request(
                method,
                url,
                params=dict(params) if params else None,
                headers=dict(headers) if headers else None,
                json=json_body,
                content=content,
                timeout=timeout,
            )
        except httpx.TimeoutException as e:
            outcome.code = "timeout"
            outcome.error = f"timeout: {e.__class__.__name__}"
        except httpx.HTTPError as e:
            outcome.code = "network"
            outcome.error = f"{e.__class__.__name__}: {str(e)[:200]}"
        except Exception as e:  # noqa: BLE001 — never let an adapter explode
            outcome.code = "network"
            outcome.error = f"{e.__class__.__name__}: {str(e)[:200]}"
        else:
            outcome.status = resp.status_code
            outcome.response = resp
            status = resp.status_code

            if status == 429:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                if retry_after is not None and 0 < retry_after <= 5.0 and attempt < max_attempts:
                    await _do_sleep(sleeper, retry_after, outcome)
                    continue
                if retry_after is None and attempt < max_attempts:
                    await _do_sleep(sleeper, _backoff_for(backoff, attempt, jitter, rnd), outcome)
                    continue
                if breaker is not None:
                    breaker.record_fail()
                outcome.code = "rate_limited"
                outcome.error = "429 rate limited"
                return outcome

            if status in retry_statuses and attempt < max_attempts:
                await _do_sleep(sleeper, _backoff_for(backoff, attempt, jitter, rnd), outcome)
                continue

            if status >= 400:
                # The upstream answered, so the transport is healthy: only a
                # 5xx counts as a breaker failure.
                if breaker is not None:
                    if status >= 500:
                        breaker.record_fail()
                    else:
                        breaker.record_ok()
                outcome.code = "not_found" if status == 404 else "http_error"
                outcome.error = f"http {status}"
                return outcome

            if breaker is not None:
                breaker.record_ok()
            try:
                outcome.data = resp.json()
            except Exception:
                outcome.code = "bad_json"
                outcome.error = "response body is not JSON"
                return outcome
            outcome.ok = True
            outcome.code = "ok"
            return outcome

        # transport failure path (timeout / network)
        if breaker is not None:
            breaker.record_fail()
        if attempt < max_attempts:
            await _do_sleep(sleeper, _backoff_for(backoff, attempt, jitter, rnd), outcome)
            continue
        return outcome

    return outcome


def _default_rand() -> float:
    import random

    return random.random()


async def _do_sleep(
    sleeper: Callable[[float], Any],
    seconds: float,
    outcome: FetchOutcome,
) -> None:
    outcome.sleeps.append(round(float(seconds), 4))
    result = sleeper(seconds)
    if hasattr(result, "__await__"):
        await result


def _backoff_for(
    backoff: Sequence[float],
    attempt: int,
    jitter: float,
    rnd: Callable[[], float],
) -> float:
    """Pick the base delay for ``attempt`` and apply ±``jitter`` (§2.5)."""
    if not backoff:
        return 0.4
    idx = min(attempt - 1, len(backoff) - 1)
    base = float(backoff[idx])
    if jitter <= 0:
        return base
    delta = base * jitter * (rnd() * 2 - 1)
    return max(0.0, round(base + delta, 4))


def _parse_retry_after(raw: str | None) -> float | None:
    """Parse ``Retry-After`` (delta-seconds or HTTP date — date unsupported)."""
    if not raw:
        return None
    s = str(raw).strip()
    try:
        return float(s)
    except ValueError:
        return None


__all__ = [
    "CircuitBreaker",
    "DEFAULT_RETRY_STATUSES",
    "FetchOutcome",
    "NO_RETRY_STATUSES",
    "TTLCache",
    "TokenBucket",
    "request_with_retry",
    "timeout_for",
]
