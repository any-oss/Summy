# Summy AI Gateway - Production-Ready Multiplexing Gateway

A high-performance, production-ready HTTP gateway for multiplexed AI inference requests with dynamic model routing and resource management.

## Features

### Performance Optimizations
- **Connection Pooling**: Reuses HTTP connections to Ollama API with configurable pool size
- **Retry Logic**: Automatic retry with exponential backoff for failed requests
- **Timeout Configuration**: Configurable request and connection timeouts
- **Async I/O**: Non-blocking operations using aiohttp

### Resource Management
- **OOM Prevention**: Predicts out-of-memory events using linear regression
- **Memory Monitoring**: Real-time memory usage tracking with proactive model eviction
- **Serialized Loading**: Async locks prevent concurrent model loading

### Intelligent Routing
- **ML-Based Selection**: Kalman filter for noise-resistant latency estimation
- **Thompson Sampling**: Balances exploration-exploitation for optimal model selection
- **Tail Latency Tracking**: P95/P99 latency guarantees
- **Time-Decay Weighting**: Emphasizes recent performance metrics

### Rate Limiting
- **Token Bucket Algorithm**: Per-client rate limiting with burst support
- **Configurable Limits**: Tokens per minute and burst size from config

### Production Features
- **Health Checks**: `/health` endpoint for basic availability
- **Readiness Probes**: `/ready` endpoint for dependency verification
- **Metrics Endpoint**: `/metrics` for monitoring and alerting
- **Request Logging**: Structured logging with timing information
- **Graceful Shutdown**: Proper cleanup of resources on termination
- **Error Handling**: Comprehensive error handling with detailed responses

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Edit `config/summy.yaml`:

```yaml
version: "1.4.0"
host: "0.0.0.0"
port: 8000
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
  max_retries: 3
  retry_delay: 1.0
  timeout: 120
  connect_timeout: 10
rate_limit:
  tokens_per_minute: 10
  burst: 3
```

## Environment Variables

- `OLLAMA_HOST`: Ollama API host (default: `http://ollama:11434`)
- `DB_PATH`: SQLite database path (default: `/data/summy.db`)

## Usage

### As a Library

```python
from src import create_gateway_app

app = create_gateway_app(config_path="./config/summy.yaml")
```

### Command Line

```bash
python -m src.gateway ./config/summy.yaml
```

### Docker

```bash
docker-compose up
```

## API Endpoints

### POST /api/generate
Generate text with composite prompts:

```json
{
  "code_instruction": "Write a function to sort a list",
  "reasoning_instruction": "Explain the algorithm",
  "prompt": "User input here"
}
```

### POST /api/chat
Conversational requests:

```json
{
  "messages": [
    {"role": "user", "content": "Hello!"}
  ],
  "model": "tinyllama:1.1b-q5_K_M"
}
```

### GET /health
Health check endpoint:

```json
{
  "status": "healthy",
  "uptime_seconds": 3600,
  "memory": {...},
  "request_count": 100,
  "error_count": 2
}
```

### GET /ready
Readiness probe:

```json
{
  "status": "ready"
}
```

### GET /metrics
Monitoring metrics:

```json
{
  "model_stats": [...],
  "routing_weights": {...},
  "memory": {...},
  "uptime_seconds": 3600,
  "request_count": 100,
  "error_count": 2,
  "error_rate": 0.02
}
```

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Client    │────▶│   Gateway    │────▶│   Ollama    │
│             │◀────│              │◀────│   API       │
└─────────────┘     └──────────────┘     └─────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │  Warden  │    │ Optimizer│    │  Shaper  │
    │ (Memory) │    │ (Routing)│    │(Rate Lim)│
    └──────────┘    └──────────┘    └──────────┘
```

## License

MIT License
