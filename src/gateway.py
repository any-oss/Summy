"""
Multiplexing Gateway - HTTP server combining Code, Reasoning, and Tool tasks.
Routes composite prompts to models selected by Pipeline Optimizer.
Uses aiohttp for non-blocking I/O.
"""

import asyncio
import json
import os
import time
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import web
import yaml


class MultiplexingGateway:
    """HTTP gateway for multiplexed AI inference requests."""

    def __init__(
        self,
        config_path: str = "./config/summy.yaml",
        ollama_host: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self.config = self._load_config(config_path)
        self.ollama_host = ollama_host or os.environ.get(
            "OLLAMA_HOST", "http://ollama:11434"
        )
        self.db_path = db_path or os.environ.get(
            "DB_PATH", "/data/summy.db"
        )

        # Initialize components
        from .warden import ResourceWarden
        from .pipeline_optimizer import PipelineOptimizer
        from .traffic_shaper import TrafficShaper

        self.warden = ResourceWarden(ollama_host=self.ollama_host)
        self.optimizer = PipelineOptimizer(db_path=self.db_path)
        
        rate_config = self.config.get("rate_limit", {})
        self.traffic_shaper = TrafficShaper(
            tokens_per_minute=rate_config.get("tokens_per_minute", 10),
            burst=rate_config.get("burst", 3),
        )

        self.app = web.Application()
        self._setup_routes()

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        try:
            with open(config_path, "r") as f:
                return yaml.safe_load(f) or {}
        except (IOError, yaml.YAMLError):
            return {}

    def _setup_routes(self) -> None:
        """Configure HTTP routes."""
        self.app.router.add_post("/api/generate", self.handle_generate)
        self.app.router.add_post("/api/chat", self.handle_chat)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/metrics", self.handle_metrics)

    def _build_composite_prompt(
        self,
        code_instruction: Optional[str] = None,
        reasoning_instruction: Optional[str] = None,
        tool_instruction: Optional[str] = None,
        user_prompt: Optional[str] = None,
    ) -> str:
        """Build a composite prompt merging multiple task types."""
        parts = []

        if code_instruction:
            parts.append(f"[CODE GENERATION]\n{code_instruction}")

        if reasoning_instruction:
            parts.append(f"[REASONING]\n{reasoning_instruction}")

        if tool_instruction:
            parts.append(f"[TOOL USE]\n{tool_instruction}")

        if user_prompt:
            parts.append(f"[USER INPUT]\n{user_prompt}")

        return "\n\n".join(parts) if parts else ""

    def _get_model_for_task(self, task_type: str) -> str:
        """Get the configured model for a specific task type."""
        models_map = self.config.get("models", {}).get("map", {})
        return models_map.get(task_type, self.config.get("models", {}).get("default", "tinyllama:1.1b-q5_K_M"))

    async def _call_ollama(
        self,
        model: str,
        prompt: str,
        options: Optional[Dict] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Send request to Ollama API."""
        url = f"{self.ollama_host}/api/generate"

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "options": options or self.config.get("ollama", {}).get("options", {}),
        }

        timeout = aiohttp.ClientTimeout(total=120)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    return {
                        "error": f"Ollama API error: {resp.status}",
                        "response": await resp.text(),
                    }

                response_data = await resp.json()
                return response_data

    async def handle_generate(self, request: web.Request) -> web.Response:
        """Handle /api/generate endpoint."""
        client_id = request.remote or "default"

        # Check rate limit
        if not await self.traffic_shaper.check_rate_limit(client_id):
            return web.json_response(
                {"error": "Rate limit exceeded"}, status=429
            )

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"}, status=400
            )

        # Extract task components
        code_instr = body.get("code_instruction")
        reasoning_instr = body.get("reasoning_instruction")
        tool_instr = body.get("tool_instruction")
        user_prompt = body.get("prompt", "")
        model_override = body.get("model")

        # Build composite prompt
        composite_prompt = self._build_composite_prompt(
            code_instruction=code_instr,
            reasoning_instruction=reasoning_instr,
            tool_instruction=tool_instr,
            user_prompt=user_prompt,
        )

        if not composite_prompt:
            return web.json_response(
                {"error": "No prompt provided"}, status=400
            )

        # Acquire warden lock for serialized model loading
        acquired = await self.warden.acquire_with_check(timeout=30.0)
        if not acquired:
            return web.json_response(
                {"error": "Resource unavailable - OOM risk"}, status=503
            )

        start_time = time.time()
        success = False

        try:
            # Determine model to use
            if model_override:
                model = model_override
            else:
                # Get best model from optimizer
                model, _ = await self.optimizer.get_best_model()

            # Call Ollama
            response = await self._call_ollama(model, composite_prompt)

            if "error" in response:
                return web.json_response(response, status=500)

            success = True
            latency_ms = (time.time() - start_time) * 1000

            # Record inference for optimizer
            await self.optimizer.record_inference(model, latency_ms, success=True)

            return web.json_response({
                "response": response.get("response", ""),
                "model": model,
                "latency_ms": latency_ms,
            })

        finally:
            self.warden.release()

    async def handle_chat(self, request: web.Request) -> web.Response:
        """Handle /api/chat endpoint for conversational requests."""
        client_id = request.remote or "default"

        if not await self.traffic_shaper.check_rate_limit(client_id):
            return web.json_response(
                {"error": "Rate limit exceeded"}, status=429
            )

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"}, status=400
            )

        messages = body.get("messages", [])
        if not messages:
            return web.json_response(
                {"error": "No messages provided"}, status=400
            )

        # Convert chat format to prompt
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt_parts.append(f"[{role.upper()}]: {content}")

        user_prompt = "\n".join(prompt_parts)

        # Use reasoning model by default for chat
        model = body.get("model", self._get_model_for_task("reasoning"))

        acquired = await self.warden.acquire_with_check(timeout=30.0)
        if not acquired:
            return web.json_response(
                {"error": "Resource unavailable - OOM risk"}, status=503
            )

        start_time = time.time()
        success = False

        try:
            response = await self._call_ollama(model, user_prompt)

            if "error" in response:
                return web.json_response(response, status=500)

            success = True
            latency_ms = (time.time() - start_time) * 1000

            await self.optimizer.record_inference(model, latency_ms, success=True)

            return web.json_response({
                "message": {
                    "role": "assistant",
                    "content": response.get("response", ""),
                },
                "model": model,
                "latency_ms": latency_ms,
            })

        finally:
            self.warden.release()

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        memory_status = self.warden.get_memory_status()
        return web.json_response({
            "status": "healthy",
            "memory": memory_status,
        })

    async def handle_metrics(self, request: web.Request) -> web.Response:
        """Metrics endpoint for monitoring."""
        model_stats = await self.optimizer.get_all_model_stats()
        routing_weights = await self.optimizer.get_routing_weights()
        memory_status = self.warden.get_memory_status()

        return web.json_response({
            "model_stats": model_stats,
            "routing_weights": routing_weights,
            "memory": memory_status,
        })

    async def start(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        """Start the gateway server."""
        await self.warden.start_monitoring()

        runner = web.AppRunner(self.app)
        await runner.setup()

        site = web.TCPSite(runner, host, port)
        await site.start()

        print(f"Gateway started on http://{host}:{port}")

        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await runner.cleanup()
            await self.warden.stop_monitoring()


def create_gateway_app(
    config_path: str = "./config/summy.yaml",
) -> web.Application:
    """Create and return the gateway application."""
    gateway = MultiplexingGateway(config_path=config_path)
    return gateway.app


async def run_gateway(
    host: str = "0.0.0.0",
    port: int = 8000,
    config_path: str = "./config/summy.yaml",
) -> None:
    """Run the gateway server."""
    gateway = MultiplexingGateway(config_path=config_path)
    await gateway.start(host, port)


if __name__ == "__main__":
    import sys

    config_file = sys.argv[1] if len(sys.argv) > 1 else "./config/summy.yaml"
    
    try:
        asyncio.run(run_gateway(config_path=config_file))
    except KeyboardInterrupt:
        print("\nShutting down Gateway...")
