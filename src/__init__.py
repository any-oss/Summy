"""
Summy v1.4.0 - Autonomous Edge AI Gateway
Local Coder Agent for resource-constrained ARMv7 hardware.
"""

from .warden import ResourceWarden
from .pipeline_optimizer import PipelineOptimizer
from .traffic_shaper import TrafficShaper, TokenBucket
from .memory_loader import run_memory_loader, create_memory_loader_server
from .gateway import MultiplexingGateway, run_gateway, create_gateway_app

__version__ = "1.4.0"
__all__ = [
    "ResourceWarden",
    "PipelineOptimizer",
    "TrafficShaper",
    "TokenBucket",
    "MultiplexingGateway",
    "run_memory_loader",
    "create_memory_loader_server",
    "run_gateway",
    "create_gateway_app",
]
