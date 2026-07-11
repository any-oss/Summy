# Summy v1.4.0

Autonomous Edge AI Gateway for resource-constrained ARM deployments.

## Overview

Summy is an edge AI API gateway engineered for ARMv7 devices with strict memory constraints (~2.2 GB). It provides intelligent model multiplexing, serialized model loading via async mutex, real-time OOM prediction, and dynamic routing based on latency-weighted EMA scoring.

## Core Capabilities

- **Multiplexing Gateway** (`src/gateway.py`): Merges code generation, reasoning, and tool-use tasks into composite prompts; routes to optimal model via Pipeline Optimizer
- **Resource Warden** (`src/warden.py`): Monitors `/proc/meminfo`, predicts OOM via linear regression slope analysis, enforces serialized model loading with `asyncio.Lock`, triggers model eviction via Ollama API (`keep_alive: 0`)
- **Pipeline Optimizer** (`src/pipeline_optimizer.py`): SQLite-backed latency tracking with Exponential Moving Average; computes inverse-latency routing weights
- **Traffic Shaper** (`src/traffic_shaper.py`): Token-bucket rate limiter with per-client IP tracking; returns HTTP 429 on burst exhaustion
- **Memory Loader** (`src/memory_loader.py`): Static file HTTP server on port 9010 with in-memory caching
- **Auto-Updater** (`scripts/auto_updater.sh`): Compares local vs remote Docker Hub image digests; executes zero-downtime container replacement

## System Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Nginx      │────▶│   Gateway    │────▶│   Ollama     │
│  :8080       │     │   :8000      │     │   :11434     │
└──────────────┘     └──────┬───────┘     └──────────────┘
                            │
                     ┌──────┴───────┐
                     │              │
              ┌──────▼──────┐ ┌─────▼──────┐
              │   Warden    │ │  Optimizer │
              │  (memory)   │ │  (SQLite)  │
              └─────────────┘ └────────────┘
                            │
                     ┌──────▼──────┐
                     │   Memory    │
                     │   Loader    │
                     │   :9010     │
                     └─────────────┘
```

## Hardware Specifications

| Component | Requirement |
|-----------|-------------|
| Architecture | ARMv7 or ARM64 |
| Available RAM | 2.2 GB minimum during inference |
| Storage | 4 GB+ for model weights and Docker layers |
| OS | Linux with Docker Engine 20.10+ |

## Deployment

### Prerequisites

- Docker Engine 20.10+
- Docker Compose v2.0+
- Docker Hub credentials (required for auto-updater)

### Quick Start

```bash
# Clone repository
git clone <repository-url>
cd summy

# Set Docker Hub credentials (optional, required for auto-updater)
export DOCKERHUB_USERNAME="your-username"
export DOCKERHUB_TOKEN="your-access-token"

# Deploy all services
docker-compose up -d

# Verify deployment
docker-compose ps
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| nginx | 8080 | Reverse proxy, static asset serving |
| gateway | 8000 | Multiplexing inference API |
| memory-loader | 9010 | Configuration file server |
| ollama | 11434 | Local LLM inference engine |

## Configuration Reference

### `config/summy.yaml`

```yaml
version: "1.4.0"
host: "0.0.0.0"
port: 8000

auth:
  enabled: false
  token: ""

models:
  map:
    code_generation: "deepseek-coder:1.3b-q4_K_M"
    reasoning: "tinyllama:1.1b-q5_K_M"
    tool: "phi:2-q4_K_M"
  default: "tinyllama:1.1b-q5_K_M"

ollama:
  endpoint: "http://ollama:11434/api/generate"
  options:
    num_predict: 512
    temperature: 0.3

memory_loader:
  port: 9010

db_path: "/data/summy.db"

rate_limit:
  tokens_per_minute: 10
  burst: 3
```

### Environment Variables

| Variable | Service | Description |
|----------|---------|-------------|
| `DOCKERHUB_USERNAME` | auto_updater.sh | Docker Hub username |
| `DOCKERHUB_TOKEN` | auto_updater.sh | Docker Hub access token |
| `OLLAMA_HOST` | gateway | Ollama endpoint override |
| `MEMORY_LOADER_HOST` | gateway | Memory loader endpoint |
| `DB_PATH` | gateway | SQLite database path |

## API Reference

### Inference Endpoint

**POST** `http://localhost:8080/api/v1/infer`

#### Request Body

```json
{
  "code": "Implement quicksort in Python",
  "reasoning": "Explain time complexity",
  "tool": "Call math.sqrt(144)",
  "context": "Educational programming tutorial",
  "task_type": "code_generation"
}
```

#### Response

```json
{
  "success": true,
  "response": "def quicksort(arr):\n    if len(arr) <= 1:\n        return arr\n    ...",
  "model_used": "deepseek-coder:1.3b-q4_K_M",
  "latency_ms": 245.3
}
```

**Status Codes:**
- `200`: Success
- `400`: Invalid request body
- `429`: Rate limit exceeded
- `502`: Ollama backend error
- `503`: Service unavailable (memory lock held)

### Chat Endpoint

**POST** `http://localhost:8080/api/v1/chat`

#### Request Body

```json
{
  "message": "What is the capital of France?"
}
```

#### Response

```json
{
  "success": true,
  "response": "The capital of France is Paris.",
  "model_used": "tinyllama:1.1b-q5_K_M",
  "latency_ms": 128.7
}
```

### Health Check

**GET** `http://localhost:8080/health`

```json
{
  "status": "healthy",
  "version": "1.4.0",
  "memory_limit_mb": 2200
}
```

### Metrics

**GET** `http://localhost:8080/metrics`

```json
{
  "model_weights": {
    "deepseek-coder:1.3b-q4_K_M": 0.42,
    "tinyllama:1.1b-q5_K_M": 0.35,
    "phi:2-q4_K_M": 0.23
  },
  "rate_limit": {
    "tokens_per_minute": 10,
    "burst": 3
  },
  "memory_samples": 50,
  "current_memory_mb": 1847.2
}
```

## Project Structure

```
summy/
├── src/
│   ├── gateway.py            # Multiplexing HTTP gateway (aiohttp)
│   ├── warden.py             # Memory monitoring, OOM prediction, async mutex
│   ├── pipeline_optimizer.py # SQLite EMA latency tracker, routing weights
│   ├── memory_loader.py      # Static file HTTP server (port 9010)
│   └── traffic_shaper.py     # Token-bucket rate limiter
├── config/
│   ├── nginx.conf            # Nginx reverse proxy configuration
│   └── summy.yaml            # Application YAML configuration
├── scripts/
│   ├── auto_updater.sh       # Docker Hub digest comparison, zero-downtime update
│   ├── provision_models.sh   # Pre-load models into Ollama
│   └── validate_startup.sh   # Service health validation
├── web/
│   └── index.html            # Progressive Web App dashboard
├── etc/
│   ├── summy/env             # Environment variable template
│   └── systemd/system/summy.service  # Systemd unit file
├── docker-compose.yml        # Multi-service orchestration
├── Dockerfile.gateway        # Gateway container build
├── requirements.txt          # Python dependencies (aiohttp, pyyaml)
└── README.md                 # This documentation
```

## Operational Scripts

### Auto-Updater

Performs autonomous self-healing by comparing local and remote Docker image digests.

```bash
# Execute manual update check
./scripts/auto_updater.sh

# Or schedule via cron
echo "0 * * * * /path/to/auto_updater.sh" | crontab -
```

**Requirements:** `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` environment variables.

### Model Provisioning

Pre-loads configured models into Ollama runtime.

```bash
./scripts/provision_models.sh
```

### Startup Validation

Verifies all services are operational before accepting traffic.

```bash
./scripts/validate_startup.sh
```

## Monitoring & Diagnostics

### Health Endpoints

```bash
# Gateway health
curl http://localhost:8080/health

# Memory loader status
curl http://localhost:9010/status

# Ollama model list
curl http://localhost:11434/api/tags
```

### Log Access

```bash
# Follow gateway logs
docker-compose logs -f gateway

# Follow all services
docker-compose logs -f

# Last 100 lines from warden
docker-compose logs --tail=100 gateway
```

### SQLite Metrics Database

Query latency history directly:

```bash
docker-compose exec gateway sqlite3 /data/summy.db \
  "SELECT model, AVG(latency_ms) as avg_latency FROM latency_log GROUP BY model;"
```

## Security Hardening

### Authentication

Enable API token authentication in `config/summy.yaml`:

```yaml
auth:
  enabled: true
  token: "${API_TOKEN}"  # Inject via environment variable
```

### Network Isolation

- All inter-service communication occurs over Docker bridge network
- No external ports exposed except Nginx (8080)
- Ollama and Memory Loader inaccessible from outside Docker network

### Secret Management

- Never commit `.env` files or hardcoded credentials
- Use Docker secrets or environment variable injection
- Rotate Docker Hub tokens quarterly

### Production Checklist

- [ ] Enable `auth.enabled: true`
- [ ] Configure TLS termination at Nginx layer
- [ ] Set restrictive firewall rules (ufw/iptables)
- [ ] Enable Docker log rotation
- [ ] Configure systemd service with restart policies

## Troubleshooting

### OOM Killer Activation

**Symptoms:** Gateway process terminated, `dmesg` shows OOM kill

**Resolution:**
1. Reduce `rate_limit.tokens_per_minute` in `summy.yaml`
2. Switch to lower-quantization models (Q4_K_S or Q3_K_M)
3. Close memory-intensive background processes
4. Increase swap space: `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`

### Model Loading Timeout

**Symptoms:** HTTP 503 responses, warden lock not released

**Resolution:**
```bash
# Check warden logs
docker-compose logs gateway | grep "warden"

# Force model eviction
curl -X POST http://localhost:11434/api/generate \
  -d '{"model": "dummy", "keep_alive": 0}'

# Restart gateway container
docker-compose restart gateway
```

### Rate Limit Exhaustion

**Symptoms:** HTTP 429 responses

**Resolution:**
1. Increase `rate_limit.burst` for temporary spikes
2. Implement client-side request queuing
3. Scale horizontally with additional gateway instances

### Docker Hub Authentication Failure

**Symptoms:** Auto-updater returns 401 Unauthorized

**Resolution:**
```bash
# Verify credentials
echo $DOCKERHUB_TOKEN | docker login -u $DOCKERHUB_USERNAME --password-stdin

# Regenerate token at https://hub.docker.com/settings/security
```

## Performance Tuning

### Model Selection Guidelines

| Task Type | Recommended Model | Quantization | VRAM Usage |
|-----------|------------------|--------------|------------|
| Code Generation | deepseek-coder:1.3b | Q4_K_M | ~800 MB |
| Reasoning | tinyllama:1.1b | Q5_K_M | ~700 MB |
| Tool Use | phi:2 | Q4_K_M | ~1.1 GB |

### Latency Optimization

1. Enable CUDA/MPS acceleration if GPU available
2. Pre-warm models during low-traffic periods
3. Tune `num_predict` based on typical response lengths
4. Adjust EMA alpha parameter in `pipeline_optimizer.py` (default: 0.3)

### Memory Optimization

1. Set `memory_limit_mb` in warden to 90% of available RAM
2. Use aggressive model eviction (`keep_alive: 60` seconds)
3. Disable unused task types in `summy.yaml`

## Contributing

1. Fork repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

**Code Standards:**
- Type hints required for all function signatures
- Async/await pattern for all I/O operations
- Unit test coverage >80% for new modules
- No external AI library dependencies (route to Ollama only)

## License

MIT License — See LICENSE file for terms.

## Version History

| Version | Release Date | Changes |
|---------|--------------|---------|
| 1.4.0 | 2024-Q3 | Multiplexing gateway, Resource Warden with OOM prediction, Pipeline Optimizer with SQLite EMA, Traffic Shaper, Memory Loader, Auto-Updater |
| 1.3.0 | 2024-Q2 | Basic gateway with single-model routing |
| 1.2.0 | 2024-Q1 | Initial Ollama integration |

---

**Support:** For issues and feature requests, use the GitHub Issues tracker.
