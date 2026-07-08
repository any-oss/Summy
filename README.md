# Summy v1.4.0 - Autonomous Edge AI Gateway

Production-grade API gateway for resource-constrained ARMv7 edge devices with local LLM inference.

## Features

- **Multiplexing Gateway**: Combines Code, Reasoning, and Tool tasks into single prompts
- **Resource Warden**: OOM prediction via linear regression with automatic model eviction
- **Pipeline Optimizer**: Dynamic routing using SQLite with EMA-based latency tracking
- **Traffic Shaper**: Token bucket rate limiter per client
- **Memory Loader**: HTTP server for configuration files with caching
- **Auto-Updater**: Zero-downtime container updates via Docker Hub digest comparison

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Nginx     │────▶│   Gateway    │────▶│   Ollama    │
│  (8080)     │     │   (8000)     │     │  (11434)    │
└─────────────┘     └──────────────┘     └─────────────┘
       │                    │
       ▼                    ▼
┌─────────────┐     ┌──────────────┐
│   Web UI    │     │Memory Loader │
│  (PWA)      │     │   (9010)     │
└─────────────┘     └──────────────┘
```

## Hardware Requirements

- Architecture: ARMv7 (optimized for Huawei Y6P)
- Memory: Maximum 2.2 GB during inference
- Models loaded sequentially via async mutex

## Quick Start

### Docker Compose

```bash
docker-compose up -d
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://ollama:11434` | Ollama API endpoint |
| `MEMORY_LOADER_HOST` | `http://memory-loader:9010` | Memory loader endpoint |
| `DB_PATH` | `/data/summy.db` | SQLite database path |

## API Endpoints

### Generate (POST /api/generate)

```json
{
  "code_instruction": "Write a Python function",
  "reasoning_instruction": "Explain the logic",
  "tool_instruction": "Use file operations",
  "prompt": "Additional context"
}
```

### Chat (POST /api/chat)

```json
{
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}
```

### Health (GET /health)

Returns system status and memory metrics.

### Metrics (GET /metrics)

Returns model statistics, routing weights, and memory status.

## Configuration

Edit `config/summy.yaml`:

```yaml
models:
  map:
    code_generation: "deepseek-coder:1.3b-q4_K_M"
    reasoning: "tinyllama:1.1b-q5_K_M"
    tool: "phi:2-q4_K_M"
  default: "tinyllama:1.1b-q5_K_M"

rate_limit:
  tokens_per_minute: 10
  burst: 3
```

## Rate Limiting

- Default: 10 requests/minute with burst of 3
- Returns HTTP 429 when exceeded
- Per-client token buckets

## Security

- No hardcoded secrets (use environment variables)
- Directory traversal prevention
- Security headers via Nginx
- Optional authentication via config

## Monitoring

Dashboard available at `http://localhost:8080`:
- Real-time memory usage
- Model latency statistics
- Routing weight visualization
- OOM risk alerts

## Auto-Update

The `scripts/auto_updater.sh` script:
1. Compares local and remote Docker image digests
2. Pulls new image if different
3. Performs zero-downtime container restart

Run manually or schedule via cron:
```bash
0 * * * * /app/scripts/auto_updater.sh
```

## Project Structure

```
summy/
├── src/
│   ├── gateway.py           # Multiplexing HTTP gateway
│   ├── warden.py            # Resource monitoring & OOM prevention
│   ├── pipeline_optimizer.py # Dynamic model routing
│   ├── traffic_shaper.py    # Rate limiting
│   └── memory_loader.py     # Config file server
├── config/
│   ├── nginx.conf           # Reverse proxy configuration
│   └── summy.yaml           # Application settings
├── web/
│   └── index.html           # PWA dashboard
├── scripts/
│   └── auto_updater.sh      # Container updater
├── docker-compose.yml
├── Dockerfile.gateway
├── Dockerfile.memory
└── requirements.txt
```

## License

MIT
