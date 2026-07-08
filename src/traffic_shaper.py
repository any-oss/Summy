"""
Traffic Shaper - Token bucket rate limiter.
Reads limits from configuration and rejects requests with HTTP 429 when exceeded.
"""

import asyncio
import time
from typing import Dict, Optional


class TokenBucket:
    """Token bucket implementation for rate limiting."""

    def __init__(self, tokens_per_minute: float, burst: int):
        self.tokens_per_minute = tokens_per_minute
        self.burst = burst
        self.tokens_per_second = tokens_per_minute / 60.0
        self._tokens = float(burst)
        self._last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> bool:
        """
        Try to acquire tokens from the bucket.
        Returns True if successful, False if rate limited.
        """
        async with self._lock:
            now = time.time()
            elapsed = now - self._last_update
            self._last_update = now

            # Add tokens based on elapsed time
            self._tokens = min(self.burst, self._tokens + elapsed * self.tokens_per_second)

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    async def wait_for_token(self, timeout: Optional[float] = None) -> bool:
        """
        Wait until a token is available or timeout expires.
        Returns True if token acquired, False if timed out.
        """
        start_time = time.time()
        while True:
            if await self.acquire():
                return True

            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    return False

            # Calculate time to next token
            async with self._lock:
                tokens_needed = 1 - self._tokens
                wait_time = tokens_needed / self.tokens_per_second if self.tokens_per_second > 0 else 0.1

            remaining = None
            if timeout is not None:
                remaining = timeout - (time.time() - start_time)
                if remaining <= 0:
                    return False
                wait_time = min(wait_time, remaining)

            await asyncio.sleep(max(0.01, wait_time))

    @property
    def available_tokens(self) -> float:
        """Return current number of available tokens."""
        return self._tokens


class TrafficShaper:
    """Rate limiter using token buckets per client/API key."""

    def __init__(
        self,
        tokens_per_minute: float = 10.0,
        burst: int = 3,
    ):
        self.default_tokens_per_minute = tokens_per_minute
        self.default_burst = burst
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()

    def _get_bucket(self, client_id: str) -> TokenBucket:
        """Get or create a token bucket for a client."""
        if client_id not in self._buckets:
            self._buckets[client_id] = TokenBucket(
                self.default_tokens_per_minute, self.default_burst
            )
        return self._buckets[client_id]

    async def check_rate_limit(self, client_id: str = "default") -> bool:
        """
        Check if request is within rate limit.
        Returns True if allowed, False if rate limited (429).
        """
        bucket = self._get_bucket(client_id)
        return await bucket.acquire()

    async def get_remaining_tokens(self, client_id: str = "default") -> float:
        """Get remaining tokens for a client."""
        bucket = self._get_bucket(client_id)
        return bucket.available_tokens

    async def reset_client(self, client_id: str) -> None:
        """Reset rate limit for a specific client."""
        async with self._lock:
            if client_id in self._buckets:
                del self._buckets[client_id]

    async def reset_all(self) -> None:
        """Reset all rate limit buckets."""
        async with self._lock:
            self._buckets.clear()

    def update_limits(self, tokens_per_minute: float, burst: int) -> None:
        """Update default rate limits (affects new clients only)."""
        self.default_tokens_per_minute = tokens_per_minute
        self.default_burst = burst
