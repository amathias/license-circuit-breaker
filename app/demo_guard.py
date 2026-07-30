"""Bounded mutation controls for the anonymous judge demo.

The confirmation is intentionally not authentication. It is a short-lived,
single-use acknowledgement that binds one browser to one named operation. The
single-flight, cooldown, and sliding-window controls bound anonymous use of the
shared demo estate without pretending the public console identifies a person.
"""

from __future__ import annotations

import math
import secrets
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import monotonic


class DemoCapacityError(RuntimeError):
    """The public demo is already busy or has reached a request budget."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("public demo capacity is temporarily limited")
        self.retry_after_seconds = max(1, retry_after_seconds)


class DemoConfirmationError(RuntimeError):
    """A confirmation is missing, expired, reused, or bound elsewhere."""


@dataclass(frozen=True, slots=True)
class _Confirmation:
    client_key: str
    operation: str
    expires_at: float


class DemoMutationGuard:
    """One-time confirmations plus bounded, serialized public mutations."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        token_factory: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        self._clock = clock
        self._token_factory = token_factory
        self._lock = Lock()
        self._confirmations: dict[str, _Confirmation] = {}
        self._by_client: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()
        self._active = False
        self._last_finished_at: float | None = None

    @staticmethod
    def _prune(samples: deque[float], *, now: float, window_seconds: int) -> None:
        cutoff = now - window_seconds
        while samples and samples[0] <= cutoff:
            samples.popleft()

    @staticmethod
    def _retry_after(samples: deque[float], *, now: float, window_seconds: int) -> int:
        if not samples:
            return 1
        return max(1, math.ceil(window_seconds - (now - samples[0])))

    def _prune_confirmations(self, now: float) -> None:
        expired = [
            token
            for token, confirmation in self._confirmations.items()
            if confirmation.expires_at <= now
        ]
        for token in expired:
            del self._confirmations[token]

    def _prune_client_buckets(self, now: float, window_seconds: int) -> None:
        empty: list[str] = []
        for client_key, samples in self._by_client.items():
            self._prune(samples, now=now, window_seconds=window_seconds)
            if not samples:
                empty.append(client_key)
        for client_key in empty:
            del self._by_client[client_key]

    def issue_confirmation(
        self,
        client_key: str,
        operation: str,
        *,
        ttl_seconds: int = 120,
        pending_limit: int = 20,
    ) -> tuple[str, int]:
        """Issue one short-lived confirmation for one client and operation."""
        now = self._clock()
        with self._lock:
            self._prune_confirmations(now)
            pending = sum(
                confirmation.client_key == client_key
                for confirmation in self._confirmations.values()
            )
            if pending >= pending_limit:
                raise DemoCapacityError(ttl_seconds)
            token = self._token_factory(32)
            self._confirmations[token] = _Confirmation(
                client_key=client_key,
                operation=operation,
                expires_at=now + ttl_seconds,
            )
        return token, ttl_seconds

    def begin_public(
        self,
        client_key: str,
        operation: str,
        token: str,
        *,
        client_limit: int = 20,
        global_limit: int = 80,
        cooldown_seconds: int = 1,
        window_seconds: int = 600,
    ) -> None:
        """Consume a confirmation and reserve the single mutation slot."""
        now = self._clock()
        with self._lock:
            self._prune_confirmations(now)
            confirmation = self._confirmations.pop(token, None)
            if (
                confirmation is None
                or confirmation.client_key != client_key
                or confirmation.operation != operation
            ):
                raise DemoConfirmationError(
                    "a fresh, operation-bound demo confirmation is required"
                )
            if self._active:
                raise DemoCapacityError(1)
            if self._last_finished_at is not None:
                remaining = cooldown_seconds - (now - self._last_finished_at)
                if remaining > 0:
                    raise DemoCapacityError(math.ceil(remaining))

            self._prune_client_buckets(now, window_seconds)
            self._prune(self._global, now=now, window_seconds=window_seconds)
            client_samples = self._by_client[client_key]
            if len(client_samples) >= client_limit:
                raise DemoCapacityError(
                    self._retry_after(
                        client_samples,
                        now=now,
                        window_seconds=window_seconds,
                    )
                )
            if len(self._global) >= global_limit:
                raise DemoCapacityError(
                    self._retry_after(
                        self._global,
                        now=now,
                        window_seconds=window_seconds,
                    )
                )

            client_samples.append(now)
            self._global.append(now)
            self._active = True

    def begin_unrestricted(self) -> None:
        """Serialize trusted local mutations without public rate controls."""
        with self._lock:
            if self._active:
                raise DemoCapacityError(1)
            self._active = True

    def finish(self) -> None:
        """Release the single mutation slot."""
        with self._lock:
            if self._active:
                self._last_finished_at = self._clock()
                self._active = False

    def reset(self) -> None:
        """Clear process-local guard state. Intended for isolated tests only."""
        with self._lock:
            self._confirmations.clear()
            self._by_client.clear()
            self._global.clear()
            self._active = False
            self._last_finished_at = None
