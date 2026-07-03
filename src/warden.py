"""
Resource Warden - Asynchronous resource monitor for OOM prediction and prevention.
Monitors physical memory usage and enforces serialized model loading.
"""

import asyncio
import os
from collections import deque
from typing import Optional, Tuple


class ResourceWarden:
    """Monitors system memory and predicts OOM events using linear regression."""

    def __init__(self, memory_limit_mb: float = 2200.0, prediction_window_sec: int = 5):
        self.memory_limit_mb = memory_limit_mb
        self.prediction_window_sec = prediction_window_sec
        self.memory_samples: deque = deque(maxlen=10)
        self.sample_interval_sec = 0.5
        self._lock: Optional[asyncio.Lock] = None
        self._monitoring: bool = False
        self._monitor_task: Optional[asyncio.Task] = None

    @property
    def lock(self) -> asyncio.Lock:
        """Get or create the async lock for serialized model loading."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _read_meminfo(self) -> dict:
        """Read physical memory information from /proc/meminfo."""
        meminfo = {}
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    parts = line.split(':')
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value_parts = parts[1].strip().split()
                        value_kb = int(value_parts[0])
                        meminfo[key] = value_kb
        except (FileNotFoundError, ValueError, PermissionError):
            # Fallback for non-Linux systems or testing
            import psutil
            mem = psutil.virtual_memory()
            meminfo = {
                'MemTotal': mem.total // 1024,
                'MemAvailable': mem.available // 1024,
                'MemFree': mem.free // 1024,
                'Buffers': 0,
                'Cached': 0,
            }
        return meminfo

    def _get_used_memory_mb(self) -> float:
        """Calculate used memory in MB from /proc/meminfo."""
        meminfo = self._read_meminfo()
        total_kb = meminfo.get('MemTotal', 0)
        available_kb = meminfo.get('MemAvailable', meminfo.get('MemFree', 0))
        used_kb = total_kb - available_kb
        return used_kb / 1024.0

    def _linear_regression_slope(self) -> float:
        """
        Calculate the slope of memory consumption using linear regression.
        Returns slope in MB/sec.
        """
        if len(self.memory_samples) < 2:
            return 0.0

        n = len(self.memory_samples)
        x_values = list(range(n))
        y_values = list(self.memory_samples)

        x_mean = sum(x_values) / n
        y_mean = sum(y_values) / n

        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
        denominator = sum((x - x_mean) ** 2 for x in x_values)

        if denominator == 0:
            return 0.0

        slope_per_sample = numerator / denominator
        slope_per_sec = slope_per_sample / self.sample_interval_sec

        return slope_per_sec

    def _predict_oom(self) -> Tuple[bool, float]:
        """
        Predict if OOM will occur within the prediction window.
        Returns (will_oom, time_to_oom_seconds).
        """
        current_memory = self.memory_samples[-1] if self.memory_samples else 0
        slope = self._linear_regression_slope()

        if slope <= 0:
            return False, float('inf')

        remaining_memory = self.memory_limit_mb - current_memory
        if remaining_memory <= 0:
            return True, 0.0

        time_to_oom = remaining_memory / slope

        return time_to_oom <= self.prediction_window_sec, time_to_oom

    async def _evict_model(self):
        """Send HTTP POST to Ollama API to evict current model."""
        import aiohttp

        ollama_host = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
        url = f"{ollama_host}/api/generate"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={
                    'model': 'dummy',
                    'prompt': '',
                    'keep_alive': 0
                }, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        print(f"[WARDEN] Model eviction signal sent")
        except Exception as e:
            print(f"[WARDEN] Model eviction failed: {e}")

    async def _monitor_loop(self):
        """Continuous monitoring loop for memory sampling and OOM prediction."""
        while self._monitoring:
            current_memory = self._get_used_memory_mb()
            self.memory_samples.append(current_memory)

            if len(self.memory_samples) >= 3:
                will_oom, time_to_oom = self._predict_oom()

                if will_oom:
                    print(f"[WARDEN] OOM PREDICTED: {time_to_oom:.2f}s until limit breach")
                    print(f"[WARDEN] Current memory: {current_memory:.2f}MB / {self.memory_limit_mb:.2f}MB")

                    if self.lock.locked():
                        print("[WARDEN] Model loading in progress, waiting...")
                    else:
                        await self._evict_model()

            await asyncio.sleep(self.sample_interval_sec)

    def start_monitoring(self):
        """Start the background monitoring task."""
        if not self._monitoring:
            self._monitoring = True
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            print(f"[WARDEN] Monitoring started (limit: {self.memory_limit_mb}MB)")

    def stop_monitoring(self):
        """Stop the background monitoring task."""
        self._monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None
        print("[WARDEN] Monitoring stopped")

    async def acquire_model_lock(self) -> bool:
        """
        Attempt to acquire the model loading lock.
        Returns True if lock acquired, False if memory conditions are critical.
        """
        if len(self.memory_samples) >= 3:
            will_oom, _ = self._predict_oom()
            if will_oom:
                print("[WARDEN] Lock acquisition denied - critical memory state")
                return False

        await self.lock.acquire()
        print("[WARDEN] Model lock acquired")
        return True

    def release_model_lock(self):
        """Release the model loading lock."""
        if self.lock.locked():
            self.lock.release()
            print("[WARDEN] Model lock released")


# Singleton instance
_warden_instance: Optional[ResourceWarden] = None


def get_warden() -> ResourceWarden:
    """Get or create the singleton ResourceWarden instance."""
    global _warden_instance
    if _warden_instance is None:
        _warden_instance = ResourceWarden()
    return _warden_instance
