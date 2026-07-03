"""
Traffic Shaper - Token bucket rate limiter for request throttling.
Implements per-client rate limiting based on configuration.
"""

import time
from typing import Dict, Optional
from threading import Lock


class TokenBucket:
    """Token bucket implementation for rate limiting."""

    def __init__(self, capacity: int, refill_rate: float):
        """
        Initialize token bucket.

        Args:
            capacity: Maximum number of tokens (burst capacity)
            refill_rate: Tokens added per second
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.time()
        self._lock = Lock()

    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        tokens_to_add = elapsed * self.refill_rate

        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill = now

    def consume(self, tokens: int = 1) -> bool:
        """
        Attempt to consume tokens from the bucket.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if tokens were consumed, False if insufficient tokens
        """
        with self._lock:
            self._refill()

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def get_tokens(self) -> float:
        """Get current number of available tokens."""
        with self._lock:
            self._refill()
            return self.tokens


class TrafficShaper:
    """Per-client traffic shaping using token buckets."""

    def __init__(
        self,
        tokens_per_minute: int = 10,
        burst: int = 3,
        cleanup_interval: int = 300
    ):
        """
        Initialize traffic shaper.

        Args:
            tokens_per_minute: Rate limit in tokens per minute
            burst: Burst capacity (max tokens at once)
            cleanup_interval: Seconds between client cleanup runs
        """
        self.tokens_per_minute = tokens_per_minute
        self.burst = burst
        self.cleanup_interval = cleanup_interval

        # Calculate refill rate (tokens per second)
        self.refill_rate = tokens_per_minute / 60.0

        # Per-client buckets
        self._buckets: Dict[str, TokenBucket] = {}
        self._last_access: Dict[str, float] = {}
        self._lock = Lock()

        self._last_cleanup = time.time()

    def _get_bucket(self, client_id: str) -> TokenBucket:
        """Get or create a token bucket for a client."""
        with self._lock:
            if client_id not in self._buckets:
                self._buckets[client_id] = TokenBucket(
                    capacity=self.burst,
                    refill_rate=self.refill_rate
                )
                self._last_access[client_id] = time.time()

            self._last_access[client_id] = time.time()
            return self._buckets[client_id]

    def allow_request(self, client_id: str, tokens: int = 1) -> bool:
        """
        Check if a request should be allowed for a client.

        Args:
            client_id: Unique identifier for the client (e.g., IP address)
            tokens: Number of tokens this request costs

        Returns:
            True if request is allowed, False if rate limited
        """
        # Periodic cleanup of stale clients
        self._maybe_cleanup()

        bucket = self._get_bucket(client_id)
        return bucket.consume(tokens)

    def get_client_tokens(self, client_id: str) -> float:
        """Get remaining tokens for a client."""
        bucket = self._get_bucket(client_id)
        return bucket.get_tokens()

    def _maybe_cleanup(self):
        """Clean up stale client entries if needed."""
        now = time.time()
        if now - self._last_cleanup < self.cleanup_interval:
            return

        with self._lock:
            stale_threshold = now - self.cleanup_interval
            stale_clients = [
                cid for cid, last_access in self._last_access.items()
                if last_access < stale_threshold
            ]

            for client_id in stale_clients:
                del self._buckets[client_id]
                del self._last_access[client_id]

            self._last_cleanup = now

    def reset_client(self, client_id: str):
        """Reset rate limit for a specific client."""
        with self._lock:
            if client_id in self._buckets:
                del self._buckets[client_id]
            if client_id in self._last_access:
                del self._last_access[client_id]

    def get_stats(self) -> Dict:
        """Get traffic shaper statistics."""
        with self._lock:
            return {
                'active_clients': len(self._buckets),
                'tokens_per_minute': self.tokens_per_minute,
                'burst_capacity': self.burst,
                'refill_rate': self.refill_rate,
                'clients': {
                    cid: {
                        'tokens': bucket.get_tokens(),
                        'last_access': self._last_access.get(cid, 0)
                    }
                    for cid, bucket in self._buckets.items()
                }
            }


# Singleton instance
_shaper_instance: Optional[TrafficShaper] = None


def get_shaper(
    tokens_per_minute: int = 10,
    burst: int = 3
) -> TrafficShaper:
    """Get or create the singleton TrafficShaper instance."""
    global _shaper_instance
    if _shaper_instance is None:
        _shaper_instance = TrafficShaper(
            tokens_per_minute=tokens_per_minute,
            burst=burst
        )
    return _shaper_instance
