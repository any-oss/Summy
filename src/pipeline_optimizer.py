"""
Refactored Pipeline Optimizer - Clean, modular dynamic routing with ML-based predictions.
"""

import asyncio
import sqlite3
import time
import math
import random
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from contextlib import contextmanager
from dataclasses import dataclass, field
from collections import deque


@dataclass
class ModelStats:
    """Statistics for a model with multiple tracking metrics."""
    # Kalman Filter state for latency estimation
    kalman_estimate: float = 0.0
    kalman_variance: float = 1000.0
    process_noise: float = 0.1
    measurement_noise: float = 10.0
    
    # EMA for smooth tracking
    ema_latency: float = 0.0
    ema_alpha: float = 0.3
    
    # Throughput tracking (requests per second)
    throughput_ema: float = 0.0
    
    # Error rate tracking
    error_count: int = 0
    total_count: int = 0
    error_rate_ema: float = 0.0
    
    # Quantile estimation for tail latency
    p50_latency: float = 0.0
    p95_latency: float = 0.0
    p99_latency: float = 0.0
    latency_samples: deque = field(default_factory=lambda: deque(maxlen=100))
    
    # Thompson Sampling parameters (Beta distribution)
    alpha_success: float = 1.0
    beta_failure: float = 1.0
    
    # Time decay for recency weighting
    last_updated: float = 0.0
    decay_factor: float = 0.99
    
    # Request count
    request_count: int = 0


class _KalmanFilter:
    """Kalman Filter for noise-resistant latency estimation."""
    
    def __init__(self, process_noise: float = 0.1, measurement_noise: float = 10.0):
        self.process_noise = process_noise
        self.measurement_noise = measurement_noise
    
    def update(self, estimate: float, variance: float, measurement: float) -> Tuple[float, float]:
        """Update Kalman estimate with new measurement."""
        # Prediction step
        variance += self.process_noise
        
        # Update step
        gain = variance / (variance + self.measurement_noise)
        new_estimate = estimate + gain * (measurement - estimate)
        new_variance = (1 - gain) * variance
        
        return new_estimate, new_variance


class _ThompsonSampler:
    """Thompson Sampling for exploration-exploitation balance."""
    
    @staticmethod
    def sample(alpha: float, beta: float) -> float:
        """Sample from Beta(alpha, beta) distribution."""
        if alpha < 1 or beta < 1:
            return alpha / (alpha + beta)
        
        # Ratio of Gamma variates approximation
        gamma_alpha = max(0.001, sum(-math.log(random.random()) for _ in range(max(1, int(alpha)))))
        gamma_beta = max(0.001, sum(-math.log(random.random()) for _ in range(max(1, int(beta)))))
        return gamma_alpha / (gamma_alpha + gamma_beta)


class PipelineOptimizer:
    """Dynamic model router with ML-based predictions."""

    def __init__(
        self,
        db_path: str = "/data/summy.db",
        ema_alpha: float = 0.3,
        default_model: str = "tinyllama:1.1b-q5_K_M",
        kalman_process_noise: float = 0.1,
        kalman_measurement_noise: float = 10.0,
        thompson_sampling: bool = True,
        tail_latency_percentile: float = 0.95,
    ):
        self.db_path = db_path
        self.ema_alpha = ema_alpha
        self.default_model = default_model
        self.kalman_filter = _KalmanFilter(kalman_process_noise, kalman_measurement_noise)
        self.thompson_sampling = thompson_sampling
        self.tail_latency_percentile = tail_latency_percentile
        
        # In-memory cache for ModelStats
        self._stats_cache: Dict[str, ModelStats] = {}
        
        # Single lock for both cache and DB operations
        self._lock = asyncio.Lock()
        
        # Initialize database
        self._db_initialized = False
        self._initialize_db()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections with proper cleanup."""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    def _initialize_db(self) -> None:
        """Initialize SQLite database schema."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS inference_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    timestamp REAL NOT NULL,
                    success INTEGER NOT NULL
                )
            """
        )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS model_stats (
                    model TEXT PRIMARY KEY,
                    ema_latency REAL NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    last_updated REAL NOT NULL
                )
            """
        )

            conn.commit()
            
        self._db_initialized = True
        self._load_cache_from_db()
    
    def _load_cache_from_db(self) -> None:
        """Load existing stats from database into memory cache with advanced metrics."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT model, ema_latency, request_count, last_updated 
                    FROM model_stats
                """)
                for row in cursor.fetchall():
                    stats = ModelStats(
                        ema_latency=row[1],
                        kalman_estimate=row[1],
                        request_count=row[2],
                        last_updated=row[3] if len(row) > 3 else time.time(),
                        ema_alpha=self.ema_alpha,
                        process_noise=self.kalman_process_noise,
                        measurement_noise=self.kalman_measurement_noise,
                    )
                    self._stats_cache[row[0]] = stats
        except Exception:
            pass

    def _update_kalman_filter(self, stats: ModelStats, measurement: float) -> None:
        """Update Kalman Filter estimate for noise-resistant latency tracking."""
        # Prediction step
        stats.kalman_variance += stats.process_noise
        
        # Update step
        kalman_gain = stats.kalman_variance / (stats.kalman_variance + stats.measurement_noise)
        stats.kalman_estimate = stats.kalman_estimate + kalman_gain * (measurement - stats.kalman_estimate)
        stats.kalman_variance = (1 - kalman_gain) * stats.kalman_variance
    
    def _update_quantiles(self, stats: ModelStats, latency: float) -> None:
        """Update quantile estimates using sorted sample approximation."""
        stats.latency_samples.append(latency)
        if len(stats.latency_samples) >= 10:
            sorted_samples = sorted(stats.latency_samples)
            n = len(sorted_samples)
            stats.p50_latency = sorted_samples[int(n * 0.50)]
            stats.p95_latency = sorted_samples[min(int(n * 0.95), n - 1)]
            stats.p99_latency = sorted_samples[min(int(n * 0.99), n - 1)]
    
    def _sample_thompson(self, stats: ModelStats) -> float:
        """Sample from Beta distribution for Thompson Sampling (lower is better)."""
        # Sample success probability from Beta distribution
        alpha = stats.alpha_success
        beta = stats.beta_failure
        
        # Use inverse transform sampling for Beta distribution approximation
        # For production, use scipy.stats.beta.rvs() but we avoid external deps
        u1 = random.random()
        u2 = random.random()
        
        # Approximate Beta sample using ratio of Gamma samples
        # Gamma(a, 1) ≈ -sum(log(U_i)) for i in 1..a (for integer a)
        # For non-integer, use more sophisticated method
        if alpha < 1 or beta < 1:
            # Simple approximation for small parameters
            sample = alpha / (alpha + beta)
        else:
            # Ratio of Gamma variates approximation
            gamma_alpha = max(0.001, sum(-math.log(random.random()) for _ in range(max(1, int(alpha)))))
            gamma_beta = max(0.001, sum(-math.log(random.random()) for _ in range(max(1, int(beta)))))
            sample = gamma_alpha / (gamma_alpha + gamma_beta)
        
        # Return expected latency adjusted by uncertainty (higher uncertainty = more exploration)
        uncertainty_bonus = 1.0 + (1.0 / (alpha + beta))
        return stats.kalman_estimate * uncertainty_bonus * (1.0 - sample + 0.01)

    async def record_inference(
        self, model: str, latency_ms: float, success: bool = True
    ) -> None:
        """Record an inference event with its latency using advanced tracking."""
        timestamp = time.time()

        # Update in-memory cache first (fast path)
        async with self._lock:
            if model not in self._stats_cache:
                self._stats_cache[model] = ModelStats(
                    ema_alpha=self.ema_alpha,
                    process_noise=self.kalman_process_noise,
                    measurement_noise=self.kalman_measurement_noise,
                )
            
            stats = self._stats_cache[model]
            
            # Apply time decay to old stats
            time_decay = stats.decay_factor ** max(0, (timestamp - stats.last_updated) / 60.0)
            stats.ema_latency = stats.ema_latency * time_decay + (1 - time_decay) * stats.ema_latency
            
            # Update EMA latency
            stats.ema_latency = (
                self.ema_alpha * latency_ms
                + (1 - self.ema_alpha) * stats.ema_latency
            )
            
            # Update Kalman Filter
            self._update_kalman_filter(stats, latency_ms)
            
            # Update quantiles
            self._update_quantiles(stats, latency_ms)
            
            # Update throughput (exponential moving average)
            if stats.request_count > 0 and stats.last_updated > 0:
                time_diff = max(0.001, timestamp - stats.last_updated)
                instant_throughput = 1.0 / (time_diff / 1000.0)  # requests per second
                stats.throughput_ema = 0.1 * instant_throughput + 0.9 * stats.throughput_ema
            
            # Update error tracking
            stats.total_count += 1
            if not success:
                stats.error_count += 1
            stats.error_rate_ema = (
                0.1 * (0 if success else 1)
                + 0.9 * stats.error_rate_ema
            )
            
            # Update Thompson Sampling parameters
            if success:
                stats.alpha_success += 1
            else:
                stats.beta_failure += 1
            
            # Update metadata
            stats.request_count += 1
            stats.last_updated = timestamp
        
        # Persist to database asynchronously (non-blocking)
        asyncio.create_task(self._persist_inference(model, latency_ms, success, timestamp))
    
    async def _persist_inference(self, model: str, latency_ms: float, success: bool, timestamp: float) -> None:
        """Persist inference data to database."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    INSERT INTO inference_log (model, latency_ms, timestamp, success)
                    VALUES (?, ?, ?, ?)
                """,
                    (model, latency_ms, timestamp, 1 if success else 0),
                )

                # Update or insert model stats with EMA
                cursor.execute(
                    "SELECT ema_latency, request_count FROM model_stats WHERE model = ?",
                    (model,),
                )
                row = cursor.fetchone()

                if row:
                    old_ema, old_count = row
                    new_ema = self.ema_alpha * latency_ms + (1 - self.ema_alpha) * old_ema
                    new_count = old_count + 1
                    cursor.execute(
                        """
                        UPDATE model_stats
                        SET ema_latency = ?, request_count = ?, last_updated = ?
                        WHERE model = ?
                    """,
                        (new_ema, new_count, timestamp, model),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO model_stats (model, ema_latency, request_count, last_updated)
                        VALUES (?, ?, ?, ?)
                    """,
                        (model, latency_ms, 1, timestamp),
                    )

                conn.commit()
        except Exception:
            pass  # Silently ignore DB errors, cache is source of truth

    async def get_best_model(
        self, 
        task_type: Optional[str] = None,
        use_thompson: bool = True,
        tail_latency_guarantee: bool = False,
    ) -> tuple[str, float]:
        """
        Select the best model using advanced ML-based criteria.
        
        Args:
            task_type: Optional task type for specialized routing (future use)
            use_thompson: Use Thompson Sampling for exploration-exploitation balance
            tail_latency_guarantee: Optimize for p95/p99 latency instead of mean
        
        Returns:
            (model_name, expected_latency_ms) with Kalman-filtered estimate
        """
        async with self._lock:
            if not self._stats_cache:
                # Fallback to DB if cache is empty
                try:
                    with self._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            """
                            SELECT model, ema_latency FROM model_stats
                            WHERE request_count > 0
                            ORDER BY ema_latency ASC
                            LIMIT 1
                        """
                        )
                        row = cursor.fetchone()
                        if row:
                            return row[0], row[1]
                except Exception:
                    pass
                return self.default_model, 0.0
            
            if tail_latency_guarantee:
                # Optimize for tail latency (p95 or p99)
                percentile_key = f'p{int(self.tail_latency_percentile * 100)}_latency'
                valid_models = [
                    (m, getattr(s, percentile_key, s.kalman_estimate))
                    for m, s in self._stats_cache.items()
                    if s.request_count >= 10 and getattr(s, percentile_key, 0) > 0
                ]
                if valid_models:
                    best_model, best_latency = min(valid_models, key=lambda x: x[1])
                    return best_model, best_latency
            
            if use_thompson and self.thompson_sampling:
                # Thompson Sampling for exploration-exploitation balance
                scores = {
                    m: self._sample_thompson(s)
                    for m, s in self._stats_cache.items()
                    if s.request_count > 0
                }
                if scores:
                    best_model = min(scores.keys(), key=lambda m: scores[m])
                    return best_model, self._stats_cache[best_model].kalman_estimate
            
            # Default: Use Kalman Filter estimate (noise-resistant)
            valid_models = [
                (m, s.kalman_estimate)
                for m, s in self._stats_cache.items()
                if s.request_count > 0
            ]
            if valid_models:
                best_model, best_latency = min(valid_models, key=lambda x: x[1])
                return best_model, best_latency
            
            return self.default_model, 0.0

    async def get_routing_weights(self) -> Dict[str, float]:
        """
        Calculate routing weights using multi-factor scoring:
        - Kalman-filtered latency (primary)
        - Error rate penalty
        - Throughput bonus
        - Uncertainty adjustment
        
        Higher weight = more likely to be selected.
        Uses in-memory cache for fast computation.
        """
        async with self._lock:
            if not self._stats_cache:
                return {self.default_model: 1.0}

            weights: Dict[str, float] = {}
            total_weight = 0.0

            for model, stats in self._stats_cache.items():
                if stats.request_count == 0:
                    continue
                
                # Base score from Kalman estimate (lower latency = higher score)
                kalman_score = 1.0 / max(0.001, stats.kalman_estimate)
                
                # Error rate penalty (exponential decay)
                error_penalty = math.exp(-3.0 * stats.error_rate_ema)
                
                # Throughput bonus (logarithmic scaling)
                throughput_bonus = 1.0 + 0.1 * math.log1p(max(0, stats.throughput_ema))
                
                # Uncertainty adjustment (less certain = less weight)
                uncertainty_factor = 1.0 / (1.0 + stats.kalman_variance / 100.0)
                
                # Combined score
                weight = kalman_score * error_penalty * throughput_bonus * uncertainty_factor
                weights[model] = weight
                total_weight += weight

            # Normalize weights
            if total_weight > 0:
                for model in weights:
                    weights[model] /= total_weight
            else:
                # Equal weights if no valid data
                equal_weight = 1.0 / len(weights) if weights else 1.0
                for model in weights:
                    weights[model] = equal_weight

            return weights

    async def get_model_latency(self, model: str) -> Optional[float]:
        """Get the current Kalman-filtered latency for a specific model."""
        async with self._lock:
            stats = self._stats_cache.get(model)
            return stats.kalman_estimate if stats else None

    async def get_model_stats_detailed(self, model: str) -> Optional[Dict]:
        """Get detailed statistics for a specific model including all metrics."""
        async with self._lock:
            stats = self._stats_cache.get(model)
            if not stats:
                return None
            
            return {
                "model": model,
                "kalman_estimate_ms": stats.kalman_estimate,
                "kalman_variance": stats.kalman_variance,
                "ema_latency_ms": stats.ema_latency,
                "p50_latency_ms": stats.p50_latency,
                "p95_latency_ms": stats.p95_latency,
                "p99_latency_ms": stats.p99_latency,
                "throughput_rps": stats.throughput_ema,
                "error_rate": stats.error_rate_ema,
                "request_count": stats.request_count,
                "success_count": int(stats.alpha_success - 1),  # Subtract prior
                "failure_count": int(stats.beta_failure - 1),   # Subtract prior
                "thompson_alpha": stats.alpha_success,
                "thompson_beta": stats.beta_failure,
                "last_updated": stats.last_updated,
            }

    async def get_all_model_stats(self) -> List[Dict]:
        """Get comprehensive statistics for all tracked models."""
        async with self._lock:
            return [
                {
                    "model": model,
                    "kalman_estimate_ms": stats.kalman_estimate,
                    "kalman_variance": stats.kalman_variance,
                    "ema_latency_ms": stats.ema_latency,
                    "p50_latency_ms": stats.p50_latency,
                    "p95_latency_ms": stats.p95_latency,
                    "p99_latency_ms": stats.p99_latency,
                    "throughput_rps": stats.throughput_ema,
                    "error_rate": stats.error_rate_ema,
                    "request_count": stats.request_count,
                    "thompson_alpha": stats.alpha_success,
                    "thompson_beta": stats.beta_failure,
                    "last_updated": stats.last_updated,
                }
                for model, stats in self._stats_cache.items()
            ]

    async def reset_stats(self, model: Optional[str] = None) -> None:
        """Reset statistics for a specific model or all models."""
        async with self._lock:
            if model:
                self._stats_cache.pop(model, None)
                try:
                    with self._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM model_stats WHERE model = ?", (model,))
                        conn.commit()
                except Exception:
                    pass
            else:
                self._stats_cache.clear()
                try:
                    with self._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM model_stats")
                        cursor.execute("DELETE FROM inference_log")
                        conn.commit()
                except Exception:
                    pass
