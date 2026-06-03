"""
Retry helpers with exponential backoff and jitter.

Usage::

    @retry(max_attempts=5, base_delay_s=2.0, transient_only=True)
    def flaky_thing(): ...
"""

import functools
import logging
import random
import time
from typing import Callable, TypeVar

from .exceptions import TransientError

log = logging.getLogger(__name__)
T = TypeVar("T")


def retry(
    max_attempts: int = 5,
    base_delay_s: float = 2.0,
    max_delay_s: float = 60.0,
    transient_only: bool = True,
):
    """
    Decorator: retry on TransientError with exponential backoff + jitter.

    If transient_only=False, retries on any Exception. Use with caution.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    is_transient = isinstance(exc, TransientError) or (
                        not transient_only and not isinstance(exc, KeyboardInterrupt)
                    )
                    if not is_transient or attempt == max_attempts:
                        log.error(
                            "%s failed after %d attempt(s): %s",
                            func.__name__, attempt, exc,
                        )
                        raise
                    delay = min(
                        max_delay_s,
                        base_delay_s * (2 ** (attempt - 1)) * (0.5 + random.random()),
                    )
                    log.warning(
                        "%s attempt %d/%d failed (%s). Retrying in %.1fs",
                        func.__name__, attempt, max_attempts, exc, delay,
                    )
                    last_exc = exc
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
