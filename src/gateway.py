"""
Multiplexing Gateway - HTTP server combining Code, Reasoning, and Tool tasks.
Routes composite prompts to models selected by Pipeline Optimizer.
Uses aiohttp for non-blocking I/O with production-ready optimizations.
"""

import asyncio
import json
import logging
import os
import signal
import time
from typing import Any, Dict, Optional
from dataclasses import dataclass

import aiohttp
from aiohttp import web
import yaml

# Configure logging for production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class GatewayConfig:
    """Configuration container for gateway settings."""
    host: str = "0.0.0.0"
    port: int = 8000
    ollama_host: str = "http://ollama:11434"
    db_path: str = "/data/summy.db"
    connection_pool_size: int = 10
    request_timeout: float = 120.0
    connect_timeout: float = 10.0
    max_retries: int = 3
    retry_delay: float = 1.0


class MultiplexingGateway:
    """HTTP gateway for multiplexed AI inference requests with production optimizations."""

    def __init__(
        self,
        config_path: str = "./config/summy.yaml",
        ollama_host: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self.config = self._load_config(config_path)
        self.gateway_config = GatewayConfig(
            ollama_host=ollama_host or os.environ.get("OLLAMA_HOST", "http://ollama:11434"),
            db_path=db_path or os.environ.get("DB_PATH", "/data/summy.db"),
            host=self.config.get("host", "0.0.0.0"),
            port=self.config.get("port", 8000),
        )
        
        # Production-grade HTTP session with connection pooling
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        
        # Retry configuration
        self._max_retries = self.config.get("ollama", {}).get("max_retries", 3)
        self._retry_delay = self.config.get("ollama", {}).get("retry_delay", 1.0)
        self._request_timeout = self.config.get("ollama", {}).get("timeout", 120.0)
        self._connect_timeout = self.config.get("ollama", {}).get("connect_timeout", 10.0)

        # Initialize components
        from .warden import ResourceWarden
        from .pipeline_optimizer import PipelineOptimizer
        from .traffic_shaper import TrafficShaper

        self.warden = ResourceWarden(ollama_host=self.gateway_config.ollama_host)
        self.optimizer = PipelineOptimizer(db_path=self.gateway_config.db_path)
        
        rate_config = self.config.get("rate_limit", {})
        self.traffic_shaper = TrafficShaper(
            tokens_per_minute=rate_config.get("tokens_per_minute", 10),
            burst=rate_config.get("burst", 3),
        )

        # Metrics tracking
        self._request_count = 0
        self._error_count = 0
        self._start_time = time.time()

        self.app = web.Application()
        self.app.on_startup.append(self._on_startup)
        self.app.on_cleanup.append(self._on_cleanup)
        self._setup_routes()
        self._setup_middleware()

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from YAML file with validation."""
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}
                logger.info(f"Loaded configuration from {config_path}")
                return config
        except (IOError, yaml.YAMLError) as e:
            logger.warning(f"Failed to load config from {config_path}: {e}, using defaults")
            return {}

    def _setup_middleware(self) -> None:
        """Setup request middleware for logging and error handling."""
        @web.middleware
        async def error_handler(request: web.Request, handler):
            try:
                response = await handler(request)
                return response
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._error_count += 1
                logger.exception(f"Request failed: {request.method} {request.path} - {e}")
                return web.json_response(
                    {"error": "Internal server error", "type": type(e).__name__},
                    status=500
                )
        
        @web.middleware
        async def request_logger(request: web.Request, handler):
            start_time = time.time()
            self._request_count += 1
            try:
                response = await handler(request)
                duration_ms = (time.time() - start_time) * 1000
                logger.info(
                    f"{request.method} {request.path} - {response.status} - {duration_ms:.2f}ms"
                )
                return response
            except Exception:
                raise
        
        self.app.middlewares.append(error_handler)
        self.app.middlewares.append(request_logger)

    def _setup_routes(self) -> None:
        """Configure HTTP routes."""
        self.app.router.add_post("/api/generate", self.handle_generate)
        self.app.router.add_post("/api/chat", self.handle_chat)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/metrics", self.handle_metrics)
        self.app.router.add_get("/ready", self.handle_ready)

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

    async def _on_startup(self, app: web.Application) -> None:
        """Initialize resources on application startup."""
        logger.info("Starting gateway initialization...")
        
        # Create persistent HTTP session with connection pooling
        timeout = aiohttp.ClientTimeout(
            total=self._request_timeout,
            connect=self._connect_timeout,
            sock_read=self._request_timeout,
            sock_connect=self._connect_timeout,
        )
        
        connector = aiohttp.TCPConnector(
            limit=10,  # Connection pool size
            limit_per_host=5,
            ttl_dns_cache=300,
            use_dns_cache=True,
            enable_cleanup_closed=True,
        )
        
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={"Content-Type": "application/json"},
        )
        
        await self.warden.start_monitoring()
        logger.info("Gateway initialization complete")

    async def _on_cleanup(self, app: web.Application) -> None:
        """Cleanup resources on application shutdown."""
        logger.info("Shutting down gateway...")
        
        if self._session:
            await self._session.close()
            self._session = None
        
        await self.warden.stop_monitoring()
        logger.info("Gateway shutdown complete")

    async def _call_ollama(
        self,
        model: str,
        prompt: str,
        options: Optional[Dict] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Send request to Ollama API with retry logic and connection reuse."""
        url = f"{self.gateway_config.ollama_host}/api/generate"

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "options": options or self.config.get("ollama", {}).get("options", {}),
        }

        # Validate input
        if not model or not isinstance(model, str):
            return {"error": "Invalid model parameter"}
        if not prompt or not isinstance(prompt, str):
            return {"error": "Invalid or empty prompt"}

        last_error = None
        for attempt in range(self._max_retries):
            try:
                if not self._session:
                    return {"error": "HTTP session not initialized"}
                
                async with self._session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.warning(f"Ollama API error (attempt {attempt + 1}): {resp.status} - {error_text}")
                        last_error = {"error": f"Ollama API error: {resp.status}", "response": error_text}
                        
                        # Retry on server errors (5xx)
                        if 500 <= resp.status < 600 and attempt < self._max_retries - 1:
                            await asyncio.sleep(self._retry_delay * (attempt + 1))
                            continue
                        
                        return last_error

                    response_data = await resp.json()
                    logger.debug(f"Ollama request successful for model {model}")
                    return response_data
                    
            except aiohttp.ClientError as e:
                last_error = {"error": f"Connection error: {str(e)}"}
                logger.warning(f"Ollama connection error (attempt {attempt + 1}): {e}")
                
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._retry_delay * (attempt + 1))
                else:
                    logger.error(f"Ollama request failed after {self._max_retries} attempts: {e}")
                    
            except asyncio.TimeoutError:
                last_error = {"error": "Request timeout"}
                logger.warning(f"Ollama request timeout (attempt {attempt + 1})")
                
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._retry_delay * (attempt + 1))
                else:
                    logger.error(f"Ollama request timed out after {self._max_retries} attempts")

        return last_error or {"error": "Unknown error occurred"}

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
        """Health check endpoint - checks basic service availability."""
        memory_status = self.warden.get_memory_status()
        uptime_seconds = time.time() - self._start_time
        
        return web.json_response({
            "status": "healthy",
            "uptime_seconds": uptime_seconds,
            "memory": memory_status,
            "request_count": self._request_count,
            "error_count": self._error_count,
        })

    async def handle_ready(self, request: web.Request) -> web.Response:
        """Readiness check endpoint - verifies all dependencies are available."""
        try:
            # Check memory status
            memory_status = self.warden.get_memory_status()
            if memory_status["usage_percent"] > 95:
                return web.json_response(
                    {"status": "not_ready", "reason": "Memory usage too high"},
                    status=503
                )
            
            # Check optimizer is functional
            await self.optimizer.get_best_model()
            
            return web.json_response({"status": "ready"})
            
        except Exception as e:
            logger.error(f"Readiness check failed: {e}")
            return web.json_response(
                {"status": "not_ready", "reason": str(e)},
                status=503
            )

    async def handle_metrics(self, request: web.Request) -> web.Response:
        """Metrics endpoint for monitoring with Prometheus-compatible format."""
        model_stats = await self.optimizer.get_all_model_stats()
        routing_weights = await self.optimizer.get_routing_weights()
        memory_status = self.warden.get_memory_status()
        uptime_seconds = time.time() - self._start_time

        return web.json_response({
            "model_stats": model_stats,
            "routing_weights": routing_weights,
            "memory": memory_status,
            "uptime_seconds": uptime_seconds,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "error_rate": self._error_count / max(1, self._request_count),
        })

    async def start(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        """Start the gateway server with proper lifecycle management."""
        runner = web.AppRunner(self.app)
        await runner.setup()

        site = web.TCPSite(runner, host, port)
        await site.start()

        logger.info(f"Gateway started on http://{host}:{port}")

        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            logger.info("Received shutdown signal")
        finally:
            await runner.cleanup()


def create_gateway_app(
    config_path: str = "./config/summy.yaml",
) -> web.Application:
    """Create and return the gateway application for production deployment."""
    gateway = MultiplexingGateway(config_path=config_path)
    return gateway.app


async def run_gateway(
    host: str = "0.0.0.0",
    port: int = 8000,
    config_path: str = "./config/summy.yaml",
) -> None:
    """Run the gateway server with graceful shutdown handling."""
    gateway = MultiplexingGateway(config_path=config_path)
    await gateway.start(host, port)


if __name__ == "__main__":
    import sys

    config_file = sys.argv[1] if len(sys.argv) > 1 else "./config/summy.yaml"
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(run_gateway(config_path=config_file))
    except KeyboardInterrupt:
        logger.info("Shutting down Gateway...")
    finally:
        # Cleanup pending tasks
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
