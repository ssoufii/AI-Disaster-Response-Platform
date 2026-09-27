"""How long a throttled caller waits before asking again.

Shared by both providers, because both are called the same way: a zone dispatch
fanning out over hundreds of households at once. That is what the jitter is for —
the callers that trip a rate limit trip it together, and an exact schedule would
have all of them retry in the same instant and recreate the burst.
"""

import pytest

from app.backoff import backoff_seconds
from app.services import content_generator, delivery_service

INITIAL = 1.0


@pytest.mark.parametrize(
    ("attempt", "nominal"),
    [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0)],
)
def test_each_wait_lands_in_its_own_doubling_window(attempt: int, nominal: float) -> None:
    """Half the nominal delay is fixed, half is jittered — so never outside either.

    Asserted over many draws rather than one, because a single sample from a
    random range says very little about the range.
    """
    draws = [backoff_seconds(attempt, INITIAL) for _ in range(200)]

    assert min(draws) >= nominal / 2
    assert max(draws) <= nominal


def test_the_waits_are_not_all_the_same() -> None:
    """The whole point: two callers throttled together do not retry in unison."""
    draws = {backoff_seconds(1, INITIAL) for _ in range(50)}

    assert len(draws) > 1


def test_a_later_attempt_always_waits_longer_than_an_earlier_one() -> None:
    """Jitter spreads the retries; it does not flatten the backoff.

    The windows do not overlap — attempt N's longest wait is attempt N+1's
    shortest — so the schedule stays recognisably exponential however the draws
    fall.
    """
    for attempt in range(1, 5):
        assert max(backoff_seconds(attempt, INITIAL) for _ in range(200)) <= min(
            backoff_seconds(attempt + 1, INITIAL) for _ in range(200)
        )


def test_both_providers_ride_out_a_rate_limit_for_about_the_same_time() -> None:
    """One dispatch, two providers, one reason to wait — so one shape of waiting.

    Not an accident worth locking down for its own sake: it is that the fan-out
    that throttles Claude is the fan-out that throttles Twilio moments later, and
    a household should not wait an order of magnitude longer on one than the other.
    """
    assert delivery_service.TWILIO_MAX_ATTEMPTS == content_generator.MAX_RATE_LIMIT_ATTEMPTS
    assert (
        delivery_service.TWILIO_INITIAL_BACKOFF_SECONDS == content_generator.INITIAL_BACKOFF_SECONDS
    )
