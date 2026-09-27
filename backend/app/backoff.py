"""How long to wait before asking a provider again.

Both outbound providers rate-limit, and both are called from the same place: a
zone dispatch fanning out over hundreds of households. That shape is what this
module exists for. Doubling alone is not enough when the calls are concurrent —
ten generations that hit the same 429 in the same instant would all wait exactly
one second and then retry in the same instant, which is the burst that caused
the 429 arriving again on a schedule. So the delay is spread: half of it fixed,
half of it random, which keeps the schedule recognisably exponential while
pulling the retries apart from each other.

A delay here is always *reactive* — the provider has already said "not now". It
is never a pacing delay applied to sends that could have gone out, which is why
Domain Rule 5 has nothing to skip for ``evacuate_now``: waiting out a 429 is not
a batching delay, it is the only way the evacuation order gets through at all.
"""

import random

# Half the delay is fixed and half is jittered ("equal jitter"), so attempt N
# still waits on the order of 2**N seconds — a wait short enough to matter to a
# household, long enough to let a rate limit clear — without every concurrent
# caller waiting the identical amount.
JITTER_FRACTION = 0.5


def backoff_seconds(attempt: int, initial: float) -> float:
    """How long to wait before retry ``attempt`` (1-based), jittered.

    ``initial`` is the first delay's size, so attempt 1 waits around ``initial``
    seconds, attempt 2 around twice that, and so on. The return value lands in
    ``[delay/2, delay]`` for that attempt's nominal ``delay``.
    """
    delay = initial * 2 ** (attempt - 1)
    fixed = delay * (1 - JITTER_FRACTION)
    return fixed + random.uniform(0, delay * JITTER_FRACTION)
