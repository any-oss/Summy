"""
Multiplexing Gateway - HTTP server for AI inference with composite prompt building.
Merges code generation, reasoning, and tool-use instructions into single prompts.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import web
import yaml

from warden import ResourceWarden, get_warden
from pipeline_optimizer import PipelineOptimizer, get_optimizer
from traffic_shaper import TrafficShaper


# Configuration constants
DEFAULT_MODEL = "tinyllama:1.1b-q5_K_M"
DEFAULT_DB_PATH = "/data/summy.db"
DEFAULT_OLLAMA_ENDPOINT = "http://ollama:11434/api/generate"
DEFAULT_NUM_PREDICT = 512
DEFAULT_TEMPERATURE = 0.3
MODEL_KEEP_ALIVE_SEC = 300
REQUEST_TIMEOUT_SEC = 120


class TaskType(str, Enum):
    """Supported task types for model routing."""
    CODE = "code"
    REASONING = "reasoning"
    TOOL = "tool"
    GENERAL = "general"


@dataclass
class InferenceResult:
    """Result of an inference operation."""
    success: bool
    response: Optional[str] = None
    error: Optional[str] = None
    model: Optional[str] = None
    latency_ms: float = 0.0


class MultiplexingGateway:
    """HTTP gateway for multiplexed AI inference requests."""

    def __init__(self, config_path: str = "/app/config/summy.yaml"):
        self.config = self._load_config(config_path)
        self.warden: ResourceWarden = get_warden()
        self.optimizer: PipelineOptimizer = get_optimizer(
            db_path=self.config.get('db_path', DEFAULT_DB_PATH)
        )
        rate_limit = self.config.get('rate_limit', {})
        self.shaper = TrafficShaper(
            tokens_per_minute=rate_limit.get('tokens_per_minute', 10),
            burst=rate_limit.get('burst', 3)
        )
        ollama_config = self.config.get('ollama', {})
        self.ollama_endpoint = ollama_config.get('endpoint', DEFAULT_OLLAMA_ENDPOINT)
        self.model_map = self.config.get('models', {}).get('map', {})
        self.default_model = self.config.get('models', {}).get('default', DEFAULT_MODEL)
        self.app = web.Application()
        self._setup_routes()

    def _load_config(self, config_path: str) -> dict:
        """Load configuration from YAML file."""
        try:
            with open(config_path, 'r') as f:
                return yaml.safe_load(f)
        except (FileNotFoundError, yaml.YAMLError) as e:
            print(f"[GATEWAY] Config load error: {e}, using defaults")
            return {}

    def _setup_routes(self):
        """Configure HTTP routes."""
        self.app.router.add_post('/api/v1/infer', self.handle_inference)
        self.app.router.add_post('/api/v1/chat', self.handle_chat)
        self.app.router.add_get('/health', self.handle_health)
        self.app.router.add_get('/metrics', self.handle_metrics)

    def _build_composite_prompt(
        self,
        code_instruction: Optional[str] = None,
        reasoning_instruction: Optional[str] = None,
        tool_instruction: Optional[str] = None,
        context: Optional[str] = None
    ) -> str:
        """Build a composite prompt merging multiple task types."""
        sections = []

        if context:
            sections.append(f"CONTEXT:\n{context}\n")

        if code_instruction:
            sections.append(f"CODE GENERATION TASK:\n{code_instruction}\n")

        if reasoning_instruction:
            sections.append(f"REASONING TASK:\n{reasoning_instruction}\n")

        if tool_instruction:
            sections.append(f"TOOL USAGE TASK:\n{tool_instruction}\n")

        if not sections:
            return ""

        sections.append(
            "INSTRUCTIONS:\n"
            "- Address all tasks in your response\n"
            "- Use clear section headers for each task type\n"
            "- For code: provide complete, executable code blocks\n"
            "- For reasoning: show step-by-step logic\n"
            "- For tools: specify function calls with parameters\n"
        )

        return "\n".join(sections)

    def _select_model_for_task(self, task_type: str) -> str:
        """Select the appropriate model based on task type."""
        model_name = self.model_map.get(task_type, self.default_model)
        return model_name

    async def _call_ollama(
        self,
        model: str,
        prompt: str,
        options: Optional[dict] = None
    ) -> InferenceResult:
        """Send request to Ollama API and return response."""
        default_options = self.config.get('ollama', {}).get('options', {
            'num_predict': DEFAULT_NUM_PREDICT,
            'temperature': DEFAULT_TEMPERATURE
        })

        if options:
            default_options.update(options)

        payload = {
            'model': model,
            'prompt': prompt,
            'stream': False,
            'options': default_options,
            'keep_alive': MODEL_KEEP_ALIVE_SEC
        }

        start_time = time.time()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.ollama_endpoint,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC)
                ) as response:
                    latency_ms = (time.time() - start_time) * 1000

                    if response.status != 200:
                        return InferenceResult(
                            success=False,
                            error=f"Ollama API returned status {response.status}",
                            latency_ms=latency_ms
                        )

                    result = await response.json()
                    await self.optimizer.record_latency(model, latency_ms)

                    return InferenceResult(
                        success=True,
                        response=result.get('response', ''),
                        model=model,
                        latency_ms=latency_ms
                    )

        except asyncio.TimeoutError:
            return InferenceResult(
                success=False,
                error='Ollama API timeout',
                latency_ms=(time.time() - start_time) * 1000
            )
        except aiohttp.ClientError as e:
            return InferenceResult(
                success=False,
                error=f'Ollama connection error: {str(e)}',
                latency_ms=(time.time() - start_time) * 1000
            )

    async def handle_inference(self, request: web.Request) -> web.Response:
        """Handle multiplexed inference request."""
        client_ip = request.remote or 'unknown'
        if not self.shaper.allow_request(client_ip):
            return web.json_response(
                {'error': 'Rate limit exceeded'},
                status=429
            )

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {'error': 'Invalid JSON body'},
                status=400
            )

        prompt = self._build_composite_prompt(
            code_instruction=body.get('code'),
            reasoning_instruction=body.get('reasoning'),
            tool_instruction=body.get('tool'),
            context=body.get('context')
        )

        if not prompt.strip():
            return web.json_response(
                {'error': 'No valid task instructions provided'},
                status=400
            )

        lock_acquired = await self.warden.acquire_model_lock()
        if not lock_acquired:
            return web.json_response(
                {'error': 'Service temporarily unavailable due to memory constraints'},
                status=503
            )

        try:
            task_type = body.get('task_type', TaskType.GENERAL.value)
            model = self.model_map.get(task_type) or await self.optimizer.get_best_model(
                task_type, list(self.model_map.values())
            ) or self.default_model

            result = await self._call_ollama(model=model, prompt=prompt)

            if result.success:
                return web.json_response({
                    'success': True,
                    'response': result.response,
                    'model_used': result.model,
                    'latency_ms': result.latency_ms
                })
            else:
                return web.json_response({
                    'success': False,
                    'error': result.error,
                    'latency_ms': result.latency_ms
                }, status=502)

        finally:
            self.warden.release_model_lock()

    async def handle_chat(self, request: web.Request) -> web.Response:
        """Handle simple chat request."""
        client_ip = request.remote or 'unknown'
        if not self.shaper.allow_request(client_ip):
            return web.json_response(
                {'error': 'Rate limit exceeded'},
                status=429
            )

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {'error': 'Invalid JSON body'},
                status=400
            )

        message = body.get('message', '')
        if not message.strip():
            return web.json_response(
                {'error': 'Empty message'},
                status=400
            )

        lock_acquired = await self.warden.acquire_model_lock()
        if not lock_acquired:
            return web.json_response(
                {'error': 'Service temporarily unavailable due to memory constraints'},
                status=503
            )

        try:
            result = await self._call_ollama(model=self.default_model, prompt=message)

            if result.success:
                return web.json_response({
                    'success': True,
                    'response': result.response,
                    'model_used': result.model,
                    'latency_ms': result.latency_ms
                })
            else:
                return web.json_response({
                    'success': False,
                    'error': result.error
                }, status=502)

        finally:
            self.warden.release_model_lock()

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            'status': 'healthy',
            'version': self.config.get('version', '1.4.0'),
            'memory_limit_mb': self.warden.memory_limit_mb
        })

    async def handle_metrics(self, request: web.Request) -> web.Response:
        """Return service metrics."""
        weights = await self.optimizer.get_routing_weights(list(self.model_map.values()))

        return web.json_response({
            'model_weights': weights,
            'rate_limit': {
                'tokens_per_minute': self.shaper.tokens_per_minute,
                'burst': self.shaper.burst
            },
            'memory_samples': len(self.warden.memory_samples),
            'current_memory_mb': self.warden.memory_samples[-1] if self.warden.memory_samples else 0
        })

    def run(self, host: str = '0.0.0.0', port: int = 8000):
        """Start the gateway server."""
        self.warden.start_monitoring()
        print(f"[GATEWAY] Starting on {host}:{port}")
        web.run_app(self.app, host=host, port=port, print=lambda x: print(f"[GATEWAY] {x}"))


def create_app() -> web.Application:
    """Create and return the gateway application."""
    gateway = MultiplexingGateway()
    return gateway.app


if __name__ == '__main__':
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 8000))

    gateway = MultiplexingGateway()
    gateway.run(host=host, port=port)
