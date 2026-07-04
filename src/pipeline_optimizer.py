"""
Pipeline Optimizer - Dynamic routing using SQLite and Exponential Moving Average.
Tracks inference latency and calculates routing weights for model selection.
"""

import asyncio
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Dict, List, Optional


class PipelineOptimizer:
    """Dynamic model router using EMA-based latency tracking."""

    def __init__(self, db_path: str = "/data/summy.db", ema_alpha: float = 0.3):
        self.db_path = db_path
        self.ema_alpha = ema_alpha
        self._latency_cache: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Get a database connection with proper cleanup."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Initialize the SQLite database schema."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_latency (
                    model_name TEXT PRIMARY KEY,
                    ema_latency REAL NOT NULL,
                    request_count INTEGER DEFAULT 0,
                    last_updated REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inference_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_name TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_timestamp ON inference_history(timestamp)"
            )
            conn.commit()

    async def record_latency(self, model_name: str, latency_ms: float):
        """Record a new latency measurement and update EMA."""
        async with self._lock:
            current_time = time.time()

            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT ema_latency, request_count FROM model_latency WHERE model_name = ?",
                    (model_name,)
                )
                row = cursor.fetchone()

                if row is None:
                    # First measurement - initialize EMA
                    new_ema = latency_ms
                    new_count = 1
                else:
                    # Update EMA: EMA_new = alpha * new_value + (1 - alpha) * EMA_old
                    old_ema = row['ema_latency']
                    old_count = row['request_count']
                    new_ema = self.ema_alpha * latency_ms + (1 - self.ema_alpha) * old_ema
                    new_count = old_count + 1

                conn.execute("""
                    INSERT OR REPLACE INTO model_latency 
                    (model_name, ema_latency, request_count, last_updated)
                    VALUES (?, ?, ?, ?)
                """, (model_name, new_ema, new_count, current_time))

                conn.execute("""
                    INSERT INTO inference_history (model_name, latency_ms, timestamp)
                    VALUES (?, ?, ?)
                """, (model_name, latency_ms, current_time))

                conn.commit()

            # Update cache
            self._latency_cache[model_name] = new_ema

    async def get_best_model(self, task_type: str, available_models: List[str]) -> Optional[str]:
        """
        Select the best model for a task based on lowest EMA latency.
        Returns model with highest weight (inverse of latency).
        """
        async with self._lock:
            if not available_models:
                return None

            # Fetch current EMAs from database
            with self._get_connection() as conn:
                placeholders = ','.join('?' * len(available_models))
                cursor = conn.execute(
                    f"SELECT model_name, ema_latency FROM model_latency WHERE model_name IN ({placeholders})",
                    available_models
                )
                rows = {row['model_name']: row['ema_latency'] for row in cursor.fetchall()}

            # Calculate weights (inverse of latency)
            weights: Dict[str, float] = {}
            for model in available_models:
                ema = rows.get(model)
                if ema is None:
                    # No history - use default weight
                    weights[model] = 1.0
                elif ema <= 0:
                    weights[model] = float('inf')
                else:
                    weights[model] = 1.0 / ema

            if not weights:
                return available_models[0] if available_models else None

            # Select model with highest weight (lowest latency)
            best_model = max(weights.keys(), key=lambda m: weights[m])
            return best_model

    async def get_routing_weights(self, available_models: List[str]) -> Dict[str, float]:
        """Get normalized routing weights for all available models."""
        async with self._lock:
            with self._get_connection() as conn:
                placeholders = ','.join('?' * len(available_models))
                cursor = conn.execute(
                    f"SELECT model_name, ema_latency FROM model_latency WHERE model_name IN ({placeholders})",
                    available_models
                )
                rows = {row['model_name']: row['ema_latency'] for row in cursor.fetchall()}

            weights: Dict[str, float] = {}
            for model in available_models:
                ema = rows.get(model, 100.0)  # Default EMA if no history
                weights[model] = 1.0 / max(ema, 0.001)

            # Normalize weights
            total = sum(weights.values())
            if total > 0:
                weights = {k: v / total for k, v in weights.items()}

            return weights

    async def get_model_stats(self, model_name: str) -> Dict:
        """Get statistics for a specific model."""
        async with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM model_latency WHERE model_name = ?",
                    (model_name,)
                )
                row = cursor.fetchone()

                if row is None:
                    return {"exists": False}

                # Get recent latency samples
                cursor = conn.execute(
                    """
                    SELECT latency_ms FROM inference_history 
                    WHERE model_name = ? 
                    ORDER BY timestamp DESC 
                    LIMIT 10
                    """,
                    (model_name,)
                )
                recent_latencies = [r['latency_ms'] for r in cursor.fetchall()]

                return {
                    "exists": True,
                    "ema_latency": row['ema_latency'],
                    "request_count": row['request_count'],
                    "recent_latencies": recent_latencies,
                    "avg_recent": sum(recent_latencies) / len(recent_latencies) if recent_latencies else 0
                }

    async def cleanup_old_history(self, max_age_hours: int = 24):
        """Remove inference history older than specified age."""
        cutoff_time = time.time() - (max_age_hours * 3600)

        async with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "DELETE FROM inference_history WHERE timestamp < ?",
                    (cutoff_time,)
                )
                conn.commit()


# Singleton instance
_optimizer_instance: Optional[PipelineOptimizer] = None


def get_optimizer(db_path: str = "/data/summy.db") -> PipelineOptimizer:
    """Get or create the singleton PipelineOptimizer instance."""
    global _optimizer_instance
    if _optimizer_instance is None:
        _optimizer_instance = PipelineOptimizer(db_path=db_path)
    return _optimizer_instance
