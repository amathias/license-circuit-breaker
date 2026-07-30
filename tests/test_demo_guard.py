"""Unit tests for anonymous public-demo mutation controls."""

from __future__ import annotations

import pytest

from app.demo_guard import (
    DemoCapacityError,
    DemoConfirmationError,
    DemoMutationGuard,
)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def guard(clock: Clock) -> DemoMutationGuard:
    counter = iter(range(100))
    return DemoMutationGuard(
        clock=clock,
        token_factory=lambda _: f"token-{next(counter)}",
    )


def test_confirmation_expires_and_cannot_be_reused():
    clock = Clock()
    control = guard(clock)
    token, _ = control.issue_confirmation("judge", "approve", ttl_seconds=5)
    clock.advance(5)

    with pytest.raises(DemoConfirmationError):
        control.begin_public("judge", "approve", token)
    with pytest.raises(DemoConfirmationError):
        control.begin_public("judge", "approve", token)


def test_confirmation_is_bound_to_client_and_operation():
    clock = Clock()
    control = guard(clock)
    token, _ = control.issue_confirmation("judge-a", "approve")

    with pytest.raises(DemoConfirmationError):
        control.begin_public("judge-b", "approve", token)

    token, _ = control.issue_confirmation("judge-a", "approve")
    with pytest.raises(DemoConfirmationError):
        control.begin_public("judge-a", "execute", token)


def test_only_one_mutation_runs_at_a_time():
    clock = Clock()
    control = guard(clock)
    first, _ = control.issue_confirmation("judge-a", "approve")
    second, _ = control.issue_confirmation("judge-b", "execute")
    control.begin_public("judge-a", "approve", first)

    with pytest.raises(DemoCapacityError):
        control.begin_public("judge-b", "execute", second)


def test_cooldown_and_client_sliding_window_are_enforced():
    clock = Clock()
    control = guard(clock)
    first, _ = control.issue_confirmation("judge", "approve")
    control.begin_public("judge", "approve", first, client_limit=1)
    control.finish()

    immediate, _ = control.issue_confirmation("judge", "execute")
    with pytest.raises(DemoCapacityError) as cooldown:
        control.begin_public("judge", "execute", immediate, client_limit=1)
    assert cooldown.value.retry_after_seconds == 1

    clock.advance(1)
    limited, _ = control.issue_confirmation("judge", "execute")
    with pytest.raises(DemoCapacityError) as window:
        control.begin_public("judge", "execute", limited, client_limit=1)
    assert window.value.retry_after_seconds == 599

    clock.advance(599)
    allowed, _ = control.issue_confirmation("judge", "execute")
    control.begin_public("judge", "execute", allowed, client_limit=1)


def test_pending_confirmation_budget_is_bounded():
    clock = Clock()
    control = guard(clock)
    control.issue_confirmation("judge", "approve", pending_limit=1)

    with pytest.raises(DemoCapacityError):
        control.issue_confirmation("judge", "execute", pending_limit=1)
