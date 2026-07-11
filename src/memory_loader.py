"""
Memory Loader - Lightweight HTTP server serving static markdown configuration files.
Implements basic caching to minimize disk I/O.
"""

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Dict, Optional, Tuple
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading


class MemoryLoaderCache:
    """In-memory cache for served files."""

    def __init__(self):
        self._cache: Dict[str, Tuple[bytes, str, str]] = {}
        self._lock = threading.Lock()

    def get(self, path: str) -> Optional[Tuple[bytes, str, str]]:
        """Get cached file content. Returns (content, content_type, etag) or None."""
        with self._lock:
            return self._cache.get(path)

    def set(self, path: str, content: bytes, content_type: str, etag: str) -> None:
        """Cache file content."""
        with self._lock:
            self._cache[path] = (content, content_type, etag)

    def invalidate(self, path: str) -> None:
        """Remove a file from cache."""
        with self._lock:
            self._cache.pop(path, None)

    def clear(self) -> None:
        """Clear all cached content."""
        with self._lock:
            self._cache.clear()


class MemoryLoaderHandler(BaseHTTPRequestHandler):
    """HTTP request handler for serving static configuration files."""

    cache = MemoryLoaderCache()
    config_dir: str = "./config"

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def _get_content_type(self, path: str) -> str:
        """Determine content type based on file extension."""
        ext = Path(path).suffix.lower()
        content_types = {
            ".md": "text/markdown",
            ".markdown": "text/markdown",
            ".txt": "text/plain",
            ".yaml": "application/x-yaml",
            ".yml": "application/x-yaml",
            ".json": "application/json",
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
        }
        return content_types.get(ext, "application/octet-stream")

    def _compute_etag(self, content: bytes) -> str:
        """Compute ETag for content."""
        return f'"{hashlib.md5(content).hexdigest()}"'

    def _send_response(
        self, status_code: int, content: bytes, content_type: str, etag: Optional[str] = None
    ) -> None:
        """Send HTTP response with appropriate headers."""
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        """Handle GET requests."""
        # Parse path and remove leading slash
        request_path = self.path.lstrip("/")

        # Security: prevent directory traversal
        if ".." in request_path or request_path.startswith("/"):
            self._send_response(403, b"Forbidden", "text/plain")
            return

        # Default to config directory
        if not request_path:
            request_path = "config"

        # Build full file path
        full_path = os.path.join(self.config_dir, request_path)

        # Ensure the path is within config directory
        try:
            real_path = os.path.realpath(full_path)
            real_config = os.path.realpath(self.config_dir)
            if not real_path.startswith(real_config):
                self._send_response(403, b"Forbidden", "text/plain")
                return
        except Exception:
            self._send_response(403, b"Forbidden", "text/plain")
            return

        # Check if it's a directory - serve index if exists
        if os.path.isdir(full_path):
            index_path = os.path.join(full_path, "index.md")
            if os.path.isfile(index_path):
                full_path = index_path
                request_path = os.path.join(request_path, "index.md")
            else:
                self._send_response(404, b"Not Found", "text/plain")
                return

        # Check cache first
        cached = self.cache.get(request_path)
        if cached:
            content, content_type, etag = cached

            # Check If-None-Match header for conditional GET
            if_none_match = self.headers.get("If-None-Match")
            if if_none_match and if_none_match == etag:
                self.send_response(304)
                self.end_headers()
                return

            self._send_response(200, content, content_type, etag)
            return

        # Read file from disk
        try:
            with open(full_path, "rb") as f:
                content = f.read()
        except FileNotFoundError:
            self._send_response(404, b"Not Found", "text/plain")
            return
        except IOError:
            self._send_response(500, b"Internal Server Error", "text/plain")
            return

        content_type = self._get_content_type(full_path)
        etag = self._compute_etag(content)

        # Cache the content
        self.cache.set(request_path, content, content_type, etag)

        self._send_response(200, content, content_type, etag)

    def do_HEAD(self) -> None:
        """Handle HEAD requests (for checking if file exists)."""
        request_path = self.path.lstrip("/")

        if ".." in request_path:
            self.send_response(403)
            self.end_headers()
            return

        full_path = os.path.join(self.config_dir, request_path)

        try:
            if not os.path.isfile(full_path):
                self.send_response(404)
                self.end_headers()
                return

            cached = self.cache.get(request_path)
            if cached:
                content, content_type, etag = cached
            else:
                with open(full_path, "rb") as f:
                    content = f.read()
                content_type = self._get_content_type(full_path)
                etag = self._compute_etag(content)
                self.cache.set(request_path, content, content_type, etag)

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("ETag", etag)
            self.end_headers()
        except Exception:
            self.send_response(500)
            self.end_headers()


def create_memory_loader_server(
    host: str = "0.0.0.0",
    port: int = 9010,
    config_dir: str = "./config",
) -> HTTPServer:
    """Create and configure the memory loader HTTP server."""
    MemoryLoaderHandler.config_dir = config_dir
    server = HTTPServer((host, port), MemoryLoaderHandler)
    return server


async def run_memory_loader(
    host: str = "0.0.0.0",
    port: int = 9010,
    config_dir: str = "./config",
) -> None:
    """Run the memory loader server asynchronously."""
    server = create_memory_loader_server(host, port, config_dir)

    loop = asyncio.get_event_loop()
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        server.shutdown()
        server_thread.join()


if __name__ == "__main__":
    import sys

    config_directory = sys.argv[1] if len(sys.argv) > 1 else "./config"
    print(f"Starting Memory Loader on port 9010, serving: {config_directory}")

    try:
        asyncio.run(run_memory_loader(config_dir=config_directory))
    except KeyboardInterrupt:
        print("\nShutting down Memory Loader...")
