"""
Summy AI Gateway - Production-ready multiplexing gateway for AI inference.

This package provides:
- MultiplexingGateway: HTTP gateway combining Code, Reasoning, and Tool tasks
- ResourceWarden: OOM prediction and prevention system
- PipelineOptimizer: ML-based dynamic model routing
- TrafficShaper: Token bucket rate limiter
- MemoryLoader: Static file server for configuration
"""

from .gateway import MultiplexingGateway, create_gateway_app, run_gateway
from .warden import ResourceWarden
from .pipeline_optimizer import PipelineOptimizer
from .traffic_shaper import TrafficShaper, TokenBucket
from .memory_loader import MemoryLoaderCache, create_memory_loader_server, run_memory_loader

__version__ = "1.4.0"
__all__ = [
    "MultiplexingGateway",
    "create_gateway_app",
    "run_gateway",
    "ResourceWarden",
    "PipelineOptimizer",
    "TrafficShaper",
    "TokenBucket",
    "MemoryLoaderCache",
    "create_memory_loader_server",
    "run_memory_loader",
]
