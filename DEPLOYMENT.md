# Production Deployment Guide - Summy v1.4.0

## 🚀 Quick Start

### Prerequisites
- Docker (v20.10+)
- Docker Compose (v2.0+) or Docker Compose plugin
- 2.5GB+ available RAM
- ARMv7/x86_64 architecture support

### One-Command Deployment

```bash
# Clone the repository
git clone https://github.com/any-oss/Summy.git
cd Summy

# Run the deployment script
./scripts/deploy.sh production
```

### Manual Deployment

```bash
# Build and start all services
docker compose up -d --build

# Check service status
docker compose ps

# View logs
docker compose logs -f
```

## 🏗️ Architecture Overview

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Nginx Proxy   │────▶│    Gateway      │────▶│  Pipeline       │
│   (Rate Limit)  │     │  (Multiplexer)   │     │  Optimizer      │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │                        │
                               ▼                        ▼
                        ┌─────────────┐         ┌─────────────┐
                        │   Warden    │         │   Models    │
                        │ (OOM Guard) │         │  (Routing)  │
                        └─────────────┘         └─────────────┘
                               │
                               ▼
                        ┌─────────────┐
                        │   Memory    │
                        │   Loader    │
                        └─────────────┘
```

## 🔧 Configuration

### Environment Variables

Create a `.env` file in the root directory:

```bash
# Gateway Configuration
GATEWAY_PORT=8080
LOG_LEVEL=INFO

# Memory Loader
MEMORY_LOADER_PORT=8081
CACHE_TTL=3600

# Rate Limiting
RATE_LIMIT_BURST=10
RATE_LIMIT_RATE=5

# OOM Protection
OOM_THRESHOLD=0.85
MEMORY_LIMIT_MB=2048
```

### Custom Settings

Edit `config/summy.yaml` for application-specific settings:
- Model endpoints
- Routing weights
- Timeout configurations
- Feature flags

## 📊 Monitoring

### Health Checks

```bash
# Gateway health
curl http://localhost:8080/health

# Memory loader health
curl http://localhost:8081/health

# Detailed metrics
curl http://localhost:8080/metrics
```

### Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f gateway
docker compose logs -f memory-loader
```

### Dashboard

Access the PWA dashboard at: `http://localhost:8080`

Features:
- Real-time resource monitoring
- Request throughput graphs
- Error rate tracking
- Model routing visualization

## 🔄 Updates & Maintenance

### Zero-Downtime Updates

```bash
# Pull latest changes
git pull origin main

# Run auto-updater
./scripts/auto_updater.sh
```

### Manual Update

```bash
# Rebuild and restart
docker compose up -d --build --force-recreate
```

### Rollback

```bash
# Stop current version
docker compose down

# Checkout previous version
git checkout <previous-tag>

# Restart
docker compose up -d --build
```

## 🛡️ Security

### Recommended Practices

1. **Change Default Ports** in production
2. **Enable HTTPS** via Nginx SSL termination
3. **Restrict Network Access** using firewall rules
4. **Rotate Secrets** regularly
5. **Monitor Logs** for suspicious activity

### SSL Setup (Nginx)

Edit `config/nginx.conf`:

```nginx
server {
    listen 443 ssl;
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    # ... rest of config
}
```

## 🐛 Troubleshooting

### Common Issues

**1. Out of Memory**
```bash
# Check memory usage
docker stats

# Reduce model concurrency in config/summy.yaml
```

**2. High Latency**
```bash
# Check pipeline optimizer metrics
curl http://localhost:8080/metrics

# Review routing weights in config
```

**3. Rate Limiting Too Aggressive**
```bash
# Adjust in config/nginx.conf
# Or update .env variables
```

### Debug Mode

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG
docker compose up -d
```

## 📈 Performance Tuning

### For High Traffic

1. Increase worker threads in `src/gateway.py`
2. Adjust token bucket parameters in `src/traffic_shaper.py`
3. Scale horizontally with multiple gateway instances
4. Use Redis for distributed caching (future enhancement)

### For Low Memory Environments

1. Reduce `MEMORY_LIMIT_MB` in `.env`
2. Lower `OOM_THRESHOLD` for earlier intervention
3. Disable non-essential features in `config/summy.yaml`

## 🎯 Production Checklist

- [ ] Change default passwords/secrets
- [ ] Configure SSL/TLS
- [ ] Set up log rotation
- [ ] Configure backup strategy
- [ ] Set up monitoring alerts
- [ ] Test disaster recovery
- [ ] Document custom configurations
- [ ] Train operations team

## 📞 Support

For issues or questions:
- GitHub Issues: https://github.com/any-oss/Summy/issues
- Documentation: https://any-oss.github.io/Summy/docs

---

**Version:** 1.4.0  
**Last Updated:** 2024  
**License:** MIT
