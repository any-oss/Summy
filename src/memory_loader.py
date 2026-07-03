"""
Memory Loader - Lightweight HTTP server for serving static configuration files.
Serves markdown files from the config directory with basic caching.
"""

import asyncio
import os
import time
from typing import Dict, Optional
from aiohttp import web


class MemoryLoader:
    """HTTP server for serving static configuration files with caching."""

    def __init__(self, config_dir: str = "/app/config", port: int = 9010):
        self.config_dir = config_dir
        self.port = port
        self._cache: Dict[str, dict] = {}
        self._cache_ttl = 60  # Cache TTL in seconds
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        """Configure HTTP routes."""
        self.app.router.add_get('/config/{filename}', self.handle_get_config)
        self.app.router.add_get('/list', self.handle_list_configs)
        self.app.router.add_get('/health', self.handle_health)

    def _get_cache_key(self, filename: str) -> str:
        """Generate cache key for a file."""
        return f"{filename}:{os.path.getmtime(os.path.join(self.config_dir, filename))}"

    def _is_cache_valid(self, filename: str) -> bool:
        """Check if cached content is still valid."""
        if filename not in self._cache:
            return False

        cache_entry = self._cache[filename]
        current_key = self._get_cache_key(filename)

        return cache_entry.get('key') == current_key

    def _read_file(self, filename: str) -> Optional[str]:
        """Read file content from disk."""
        filepath = os.path.join(self.config_dir, filename)

        # Security check: prevent directory traversal
        if not os.path.abspath(filepath).startswith(os.path.abspath(self.config_dir)):
            return None

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()
        except (FileNotFoundError, PermissionError, IOError):
            return None

    async def handle_get_config(self, request: web.Request) -> web.Response:
        """Handle request for a specific configuration file."""
        filename = request.match_info.get('filename', '')

        if not filename:
            return web.json_response(
                {'error': 'Filename required'},
                status=400
            )

        # Check cache first
        if self._is_cache_valid(filename):
            cache_entry = self._cache[filename]
            return web.Response(
                text=cache_entry['content'],
                content_type=cache_entry.get('content_type', 'text/plain'),
                headers={'X-Cache': 'HIT'}
            )

        # Read from disk
        content = self._read_file(filename)

        if content is None:
            return web.json_response(
                {'error': f'File not found: {filename}'},
                status=404
            )

        # Determine content type
        content_type = 'text/plain'
        if filename.endswith('.md'):
            content_type = 'text/markdown'
        elif filename.endswith('.yaml') or filename.endswith('.yml'):
            content_type = 'application/x-yaml'
        elif filename.endswith('.json'):
            content_type = 'application/json'

        # Update cache
        self._cache[filename] = {
            'key': self._get_cache_key(filename),
            'content': content,
            'content_type': content_type,
            'cached_at': time.time()
        }

        return web.Response(
            text=content,
            content_type=content_type,
            headers={'X-Cache': 'MISS'}
        )

    async def handle_list_configs(self, request: web.Request) -> web.Response:
        """List all available configuration files."""
        try:
            files = os.listdir(self.config_dir)
            configs = []

            for filename in files:
                filepath = os.path.join(self.config_dir, filename)
                if os.path.isfile(filepath):
                    file_stat = os.stat(filepath)
                    configs.append({
                        'name': filename,
                        'size': file_stat.st_size,
                        'modified': time.strftime(
                            '%Y-%m-%d %H:%M:%S',
                            time.localtime(file_stat.st_mtime)
                        ),
                        'cached': filename in self._cache
                    })

            return web.json_response({'configs': configs})

        except (OSError, IOError) as e:
            return web.json_response(
                {'error': f'Failed to list configs: {str(e)}'},
                status=500
            )

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            'status': 'healthy',
            'config_dir': self.config_dir,
            'port': self.port,
            'cache_entries': len(self._cache)
        })

    def run(self, host: str = '0.0.0.0'):
        """Start the memory loader server."""
        print(f"[MEMORY_LOADER] Starting on {host}:{self.port}")
        print(f"[MEMORY_LOADER] Serving configs from: {self.config_dir}")
        web.run_app(
            self.app,
            host=host,
            port=self.port,
            print=lambda x: print(f"[MEMORY_LOADER] {x}")
        )


def create_app(config_dir: str = "/app/config") -> web.Application:
    """Create and return the memory loader application."""
    loader = MemoryLoader(config_dir=config_dir)
    return loader.app


if __name__ == '__main__':
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 9010))
    config_dir = os.environ.get('CONFIG_DIR', '/app/config')

    loader = MemoryLoader(config_dir=config_dir, port=port)
    loader.run(host=host)
