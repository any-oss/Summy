#!/usr/bin/env python3
"""
Main application entry point for the Multiplexing Gateway.
Production-ready setup with proper configuration and error handling.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.gateway import MultiplexingGateway, run_gateway

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point with configuration from environment variables."""
    # Get configuration from environment or use defaults
    host = os.environ.get("GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("GATEWAY_PORT", "8000"))
    config_path = os.environ.get("CONFIG_PATH", "./config/summy.yaml")
    
    logger.info(f"Starting Gateway on {host}:{port}")
    logger.info(f"Using configuration: {config_path}")
    
    # Validate config file exists
    if not Path(config_path).exists():
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)
    
    try:
        asyncio.run(run_gateway(host=host, port=port, config_path=config_path))
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Gateway failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
