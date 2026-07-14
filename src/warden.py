"""
Resource Warden - Asynchronous resource monitor for OOM prediction and prevention.
Monitors physical memory usage, predicts OOM events using linear regression,
and enforces serialized model loading via asyncio.Lock.
"""

import asyncio
import os
from collections import deque
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp


class ResourceWarden:
    """Monitors system memory and prevents OOM events during model inference."""

    def __init__(
        self,
        meminfo_path: str = "/proc/meminfo",
        memory_limit_mb: float = 2200.0,
        window_size: int = 10,
        prediction_horizon_seconds: float = 5.0,
        ollama_host: str = "http://ollama:11434",
    ):
        self.meminfo_path = meminfo_path
        self.memory_limit_bytes = int(memory_limit_mb * 1024 * 1024)
        self.window_size = window_size
        self.prediction_horizon = prediction_horizon_seconds
        self.ollama_host = ollama_host

        # Rolling window for memory samples: (timestamp, used_bytes)
        self._memory_window: deque = deque(maxlen=window_size)
        self._lock = asyncio.Lock()
        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None

    @property
    def lock(self) -> asyncio.Lock:
        """Return the async lock for serialized model loading."""
        return self._lock

    def _read_meminfo(self) -> dict:
        """Parse /proc/meminfo and return memory values in bytes."""
        meminfo = {}
        try:
            with open(self.meminfo_path, "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) != 2:
                        continue
                    key = parts[0].strip()
                    value_part = parts[1].strip().split()
                    if len(value_part) < 1:
                        continue
                    value = int(value_part[0])
                    # Convert from kB to bytes
                    meminfo[key] = value * 1024
        except (IOError, OSError, ValueError):
            # Fallback for non-Linux systems or read errors
            meminfo = {
                "MemTotal": 4 * 1024 * 1024 * 1024,
                "MemAvailable": 2 * 1024 * 1024 * 1024,
            }
        return meminfo

    def _get_used_memory(self) -> int:
        """Calculate currently used memory in bytes."""
        meminfo = self._read_meminfo()
        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        return total - available

    def _linear_regression_slope(self) -> float:
        """
        Calculate the slope of memory consumption using simple linear regression.
        Returns bytes per second.
        """
        if len(self._memory_window) < 2:
            return 0.0

        n = len(self._memory_window)
        sum_x = 0.0
        sum_y = 0.0
        sum_xy = 0.0
        sum_x2 = 0.0

        first_timestamp = self._memory_window[0][0]
        for ts, mem in self._memory_window:
            x = ts - first_timestamp  # Relative time in seconds
            y = float(mem)
            sum_x += x
            sum_y += y
            sum_xy += x * y
            sum_x2 += x * x

        denominator = n * sum_x2 - sum_x * sum_x
        if abs(denominator) < 1e-9:
            return 0.0

        slope = (n * sum_xy - sum_x * sum_y) / denominator
        return slope

    def _predict_oom(self) -> Tuple[bool, float]:
        """
        Predict if OOM will occur within the prediction horizon.
        Returns (will_oom, time_to_oom_seconds).
        """
        current_used = self._get_used_memory()
        slope = self._linear_regression_slope()

        if slope <= 0:
            return False, float("inf")

        remaining_bytes = self.memory_limit_bytes - current_used
        if remaining_bytes <= 0:
            return True, 0.0

        time_to_oom = remaining_bytes / slope
        will_oom = time_to_oom < self.prediction_horizon

        return will_oom, time_to_oom

    async def _evict_model(self, session=None) -> bool:
        """Send request to Ollama to evict the current model."""
        import aiohttp

        url = f"{self.ollama_host}/api/generate"
        payload = {
            "model": "dummy",
            "prompt": "",
            "keep_alive": 0,
        }

        timeout = aiohttp.ClientTimeout(total=5.0)
        
        # Use provided session or create temporary one
        if session is not None:
            try:
                async with session.post(url, json=payload, timeout=timeout) as resp:
                    return resp.status == 200
            except Exception:
                return False
        else:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as temp_session:
                    async with temp_session.post(url, json=payload) as resp:
                        return resp.status == 200
            except Exception:
                return False

    async def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while self._monitoring:
            timestamp = asyncio.get_event_loop().time()
            used_memory = self._get_used_memory()
            self._memory_window.append((timestamp, used_memory))

            will_oom, time_to_oom = self._predict_oom()

            if will_oom:
                async with self._lock:
                    await self._evict_model()

            await asyncio.sleep(0.5)

    async def start_monitoring(self) -> None:
        """Start the background monitoring task."""
        if self._monitoring:
            return

        self._monitoring = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop_monitoring(self) -> None:
        """Stop the background monitoring task."""
        self._monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

    async def acquire_with_check(self, timeout: Optional[float] = None) -> bool:
        """
        Acquire the lock for model loading with OOM check.
        Returns True if acquired successfully, False if OOM risk is too high.
        """
        will_oom, _ = self._predict_oom()
        if will_oom:
            await self._evict_model()

        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def release(self) -> None:
        """Release the model loading lock."""
        if self._lock.locked():
            self._lock.release()

    def get_memory_status(self) -> dict:
        """Return current memory status information."""
        meminfo = self._read_meminfo()
        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        used = total - available
        slope = self._linear_regression_slope()
        will_oom, time_to_oom = self._predict_oom()

        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": used,
            "limit_bytes": self.memory_limit_bytes,
            "usage_percent": (used / total * 100) if total > 0 else 0,
            "slope_bytes_per_sec": slope,
            "oom_predicted": will_oom,
            "time_to_oom_seconds": time_to_oom if will_oom else None,
        }
