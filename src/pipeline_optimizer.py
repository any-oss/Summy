"""
Pipeline Optimizer - Dynamic routing using SQLite with EMA-based latency tracking.
Tracks inference latency for each model and routes requests to the fastest model.
"""

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Optional, Dict, List


class PipelineOptimizer:
    """Dynamic model router based on exponential moving average of latency."""

    def __init__(
        self,
        db_path: str = "/data/summy.db",
        ema_alpha: float = 0.3,
        default_model: str = "tinyllama:1.1b-q5_K_M",
    ):
        self.db_path = db_path
        self.ema_alpha = ema_alpha
        self.default_model = default_model
        self._lock = asyncio.Lock()

        # In-memory cache for EMA values
        self._ema_cache: Dict[str, float] = {}
        self._request_counts: Dict[str, int] = {}

        self._initialize_db()

    def _initialize_db(self) -> None:
        """Initialize SQLite database schema."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
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
        conn.close()

    async def record_inference(
        self, model: str, latency_ms: float, success: bool = True
    ) -> None:
        """Record an inference event with its latency."""
        async with self._lock:
            timestamp = time.time()

            conn = sqlite3.connect(self.db_path)
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
            conn.close()

            # Update in-memory cache
            self._ema_cache[model] = (
                self.ema_alpha * latency_ms
                + (1 - self.ema_alpha) * self._ema_cache.get(model, latency_ms)
            )
            self._request_counts[model] = self._request_counts.get(model, 0) + 1

    async def get_best_model(
        self, task_type: Optional[str] = None
    ) -> tuple[str, float]:
        """
        Select the best model based on lowest EMA latency.
        Returns (model_name, expected_latency_ms).
        """
        async with self._lock:
            conn = sqlite3.connect(self.db_path)
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
            conn.close()

            if row:
                return row[0], row[1]

            # Fallback to default model
            return self.default_model, 0.0

    async def get_routing_weights(self) -> Dict[str, float]:
        """
        Calculate routing weights inversely proportional to EMA latency.
        Higher weight = more likely to be selected.
        """
        async with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT model, ema_latency, request_count FROM model_stats"
            )
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return {self.default_model: 1.0}

            weights: Dict[str, float] = {}
            total_inverse = 0.0

            for model, ema_latency, count in rows:
                if count > 0 and ema_latency > 0:
                    inverse = 1.0 / ema_latency
                    weights[model] = inverse
                    total_inverse += inverse

            # Normalize weights
            if total_inverse > 0:
                for model in weights:
                    weights[model] /= total_inverse
            else:
                # Equal weights if no valid data
                equal_weight = 1.0 / len(weights) if weights else 1.0
                for model in weights:
                    weights[model] = equal_weight

            return weights

    async def get_model_latency(self, model: str) -> Optional[float]:
        """Get the current EMA latency for a specific model."""
        async with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT ema_latency FROM model_stats WHERE model = ?", (model,)
            )
            row = cursor.fetchone()
            conn.close()

            return row[0] if row else None

    async def get_all_model_stats(self) -> List[Dict]:
        """Get statistics for all tracked models."""
        async with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT model, ema_latency, request_count, last_updated
                FROM model_stats
                ORDER BY ema_latency ASC
            """
            )
            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    "model": row[0],
                    "ema_latency_ms": row[1],
                    "request_count": row[2],
                    "last_updated": row[3],
                }
                for row in rows
            ]

    async def reset_stats(self, model: Optional[str] = None) -> None:
        """Reset statistics for a specific model or all models."""
        async with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            if model:
                cursor.execute("DELETE FROM model_stats WHERE model = ?", (model,))
                self._ema_cache.pop(model, None)
                self._request_counts.pop(model, None)
            else:
                cursor.execute("DELETE FROM model_stats")
                cursor.execute("DELETE FROM inference_log")
                self._ema_cache.clear()
                self._request_counts.clear()

            conn.commit()
            conn.close()
