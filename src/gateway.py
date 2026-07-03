"""
Multiplexing Gateway - HTTP server for AI inference with composite prompt building.
Merges code generation, reasoning, and tool-use instructions into single prompts.
"""

import asyncio
import json
import os
import time
from typing import Dict, Optional, Any

import aiohttp
from aiohttp import web
import yaml

from warden import get_warden, ResourceWarden
from pipeline_optimizer import get_optimizer, PipelineOptimizer
from traffic_shaper import TrafficShaper


class MultiplexingGateway:
    """HTTP gateway for multiplexed AI inference requests."""

    def __init__(self, config_path: str = "/app/config/summy.yaml"):
        self.config = self._load_config(config_path)
        self.warden: ResourceWarden = get_warden()
        self.optimizer: PipelineOptimizer = get_optimizer(
            db_path=self.config.get('db_path', '/data/summy.db')
        )
        self.shaper = TrafficShaper(
            tokens_per_minute=self.config.get('rate_limit', {}).get('tokens_per_minute', 10),
            burst=self.config.get('rate_limit', {}).get('burst', 3)
        )
        self.ollama_endpoint = self.config.get('ollama', {}).get(
            'endpoint', 'http://ollama:11434/api/generate'
        )
        self.model_map = self.config.get('models', {}).get('map', {})
        self.default_model = self.config.get('models', {}).get(
            'default', 'tinyllama:1.1b-q5_K_M'
        )
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
        """
        Build a composite prompt merging multiple task types.
        Optimizes for single-pass inference to reduce latency.
        """
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

        # Add instruction for structured output
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
    ) -> Dict[str, Any]:
        """Send request to Ollama API and return response."""
        default_options = self.config.get('ollama', {}).get('options', {
            'num_predict': 512,
            'temperature': 0.3
        })

        if options:
            default_options.update(options)

        payload = {
            'model': model,
            'prompt': prompt,
            'stream': False,
            'options': default_options,
            'keep_alive': 300  # Keep model loaded for 5 minutes
        }

        start_time = time.time()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.ollama_endpoint,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    latency_ms = (time.time() - start_time) * 1000

                    if response.status != 200:
                        return {
                            'success': False,
                            'error': f"Ollama API returned status {response.status}",
                            'latency_ms': latency_ms
                        }

                    result = await response.json()

                    # Record latency for optimizer
                    await self.optimizer.record_latency(model, latency_ms)

                    return {
                        'success': True,
                        'response': result.get('response', ''),
                        'model': model,
                        'latency_ms': latency_ms,
                        'done': result.get('done', True)
                    }

        except asyncio.TimeoutError:
            return {
                'success': False,
                'error': 'Ollama API timeout',
                'latency_ms': (time.time() - start_time) * 1000
            }
        except aiohttp.ClientError as e:
            return {
                'success': False,
                'error': f'Ollama connection error: {str(e)}',
                'latency_ms': (time.time() - start_time) * 1000
            }

    async def handle_inference(self, request: web.Request) -> web.Response:
        """Handle multiplexed inference request."""
        # Rate limiting check
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

        # Extract task components
        code_instr = body.get('code')
        reasoning_instr = body.get('reasoning')
        tool_instr = body.get('tool')
        context = body.get('context')
        task_type = body.get('task_type', 'general')

        # Build composite prompt
        prompt = self._build_composite_prompt(
            code_instruction=code_instr,
            reasoning_instruction=reasoning_instr,
            tool_instruction=tool_instr,
            context=context
        )

        if not prompt.strip():
            return web.json_response(
                {'error': 'No valid task instructions provided'},
                status=400
            )

        # Acquire model lock through warden
        lock_acquired = await self.warden.acquire_model_lock()
        if not lock_acquired:
            return web.json_response(
                {'error': 'Service temporarily unavailable due to memory constraints'},
                status=503
            )

        try:
            # Select model
            if task_type in self.model_map:
                model = self.model_map[task_type]
            else:
                available_models = list(self.model_map.values())
                model = await self.optimizer.get_best_model(task_type, available_models)
                if not model:
                    model = self.default_model

            # Execute inference
            result = await self._call_ollama(model=model, prompt=prompt)

            if result['success']:
                return web.json_response({
                    'success': True,
                    'response': result['response'],
                    'model_used': result['model'],
                    'latency_ms': result['latency_ms']
                })
            else:
                return web.json_response({
                    'success': False,
                    'error': result['error'],
                    'latency_ms': result.get('latency_ms', 0)
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

        # Acquire model lock
        lock_acquired = await self.warden.acquire_model_lock()
        if not lock_acquired:
            return web.json_response(
                {'error': 'Service temporarily unavailable due to memory constraints'},
                status=503
            )

        try:
            model = self.default_model
            result = await self._call_ollama(model=model, prompt=message)

            if result['success']:
                return web.json_response({
                    'success': True,
                    'response': result['response'],
                    'model_used': result['model'],
                    'latency_ms': result['latency_ms']
                })
            else:
                return web.json_response({
                    'success': False,
                    'error': result['error']
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
