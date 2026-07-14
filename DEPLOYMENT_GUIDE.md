# Production Deployment Guide

## Overview

This guide covers the production deployment of the Multiplexing Gateway with optimized performance and security configurations.

## Prerequisites

- Docker 20.10+ and Docker Compose 2.0+
- At least 8GB RAM available
- Linux or macOS environment (Windows requires WSL2)

## Quick Start

### Deploy to Production

```bash
# Build and deploy
./deploy.sh deploy

# Or manually
docker compose -f docker-compose.prod.yml up -d --build
```

### Verify Deployment

```bash
# Check status
./deploy.sh status

# View health
curl http://localhost:8000/health

# View logs
./deploy.sh logs
```

## Architecture

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│   Client        │────▶│   Gateway    │────▶│   Ollama    │
│   (HTTP/REST)   │     │   (Port 8000)│     │   (Port     │
│                 │     │              │     │   11434)    │
└─────────────────┘     └──────────────┘     └─────────────┘
                              │
                         ┌────▼────┐
                         │ SQLite  │
                         │ DB      │
                         └─────────┘
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GATEWAY_HOST` | `0.0.0.0` | Host to bind the gateway |
| `GATEWAY_PORT` | `8000` | Port for the gateway service |
| `CONFIG_PATH` | `/app/config/summy.yaml` | Path to configuration file |
| `OLLAMA_HOST` | `http://ollama:11434` | Ollama API endpoint |
| `DB_PATH` | `/data/summy.db` | SQLite database path |

### Resource Limits

The production configuration includes:
- **Gateway**: 512MB memory limit, 256MB reservation
- **Ollama**: 4GB memory limit, 2GB reservation

Adjust these in `docker-compose.prod.yml` based on your workload.

## API Endpoints

### Health & Monitoring

- `GET /health` - Liveness probe
- `GET /ready` - Readiness probe
- `GET /metrics` - Prometheus-compatible metrics

### Inference

- `POST /api/v1/generate` - Main inference endpoint

Example:
```bash
curl -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "code",
    "prompt": "Write a Python function to sort a list",
    "model_hint": "fast"
  }'
```

## Management Commands

```bash
# Build images
./deploy.sh build

# Deploy application
./deploy.sh deploy

# Start (if already built)
./deploy.sh start

# Stop application
./deploy.sh stop

# Restart application
./deploy.sh restart

# View logs
./deploy.sh logs [service_name]

# Check status
./deploy.sh status

# Health check
./deploy.sh health
```

## Scaling

For horizontal scaling, modify `docker-compose.prod.yml`:

```yaml
services:
  gateway:
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: '0.5'
          memory: 512M
```

Then deploy with Docker Swarm:
```bash
docker stack deploy -c docker-compose.prod.yml multiplexing
```

## Security Considerations

1. **Non-root user**: Application runs as `appuser` inside container
2. **Read-only config**: Configuration mounted as read-only
3. **Network isolation**: Services communicate over isolated network
4. **Resource limits**: Prevents resource exhaustion attacks

## Monitoring

### Metrics Endpoint

Access `/metrics` for JSON-formatted metrics:

```json
{
  "model_stats": {...},
  "routing_weights": {...},
  "memory": {...},
  "uptime_seconds": 3600,
  "request_count": 150,
  "error_count": 2,
  "error_rate": 0.013
}
```

### Logging

Logs are written to stdout/stderr and can be accessed via:
```bash
docker logs multiplexing-gateway
# or
./deploy.sh logs gateway
```

## Troubleshooting

### Gateway won't start

1. Check logs: `./deploy.sh logs gateway`
2. Verify config: `cat config/summy.yaml`
3. Check port conflicts: `netstat -tlnp | grep 8000`

### High memory usage

1. Monitor metrics: `curl http://localhost:8000/metrics`
2. Adjust resource limits in `docker-compose.prod.yml`
3. Consider scaling horizontally

### Ollama connection issues

1. Verify Ollama is running: `curl http://localhost:11434/api/tags`
2. Check network connectivity between containers
3. Review Ollama logs: `./deploy.sh logs ollama`

## Backup & Recovery

### Backup Database

```bash
docker cp multiplexing-gateway:/data/summy.db ./backup-$(date +%Y%m%d).db
```

### Restore Database

```bash
docker cp ./backup.db multiplexing-gateway:/data/summy.db
docker restart multiplexing-gateway
```

## Performance Tuning

### Connection Pool Settings

Edit `config/summy.yaml`:

```yaml
ollama:
  max_retries: 3
  retry_delay: 1.0
  timeout: 120.0
  connect_timeout: 10.0
  
rate_limit:
  tokens_per_minute: 100
  burst: 10
```

### Model Optimization

Use the Pipeline Optimizer to select optimal models based on latency and accuracy requirements.

## Updates

To update the application:

```bash
# Pull latest changes
git pull

# Rebuild and redeploy
./deploy.sh deploy
```

For zero-downtime updates, use Docker Swarm or Kubernetes.
