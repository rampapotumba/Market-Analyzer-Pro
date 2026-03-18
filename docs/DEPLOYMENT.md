# Market Analyzer Pro v2 — Deployment Guide

## Prerequisites

| Component | Version | Notes |
|-----------|---------|-------|
| Docker | ≥ 24.0 | With compose plugin |
| docker-compose | ≥ 2.20 | |
| Python | 3.11+ | Local dev only |
| 4 GB RAM | — | For FinBERT model |

---

## Quick Start (Development)

```bash
# 1. Clone and set up environment
cp .env.example .env
# Fill in your API keys in .env

# 2. Start all services
docker compose up -d

# 3. Run migrations
docker compose exec app alembic upgrade head

# 4. Verify health
curl http://localhost:8000/api/v2/health
```

Services available at:
- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3001 (admin / value from GRAFANA_PASSWORD)

---

## Production Deployment

### 1. Environment Variables

Copy `.env.example` to `.env` and configure all required values:

```bash
# Required API keys (free tiers available):
FINNHUB_KEY=...        # Fundamental analysis, earnings
FRED_KEY=...           # Macro economic data

# Required infrastructure:
POSTGRES_PASSWORD=<strong-random-password>
REDIS_URL=redis://redis:6379/0

# Monitoring:
GRAFANA_PASSWORD=<strong-random-password>

# Optional but recommended:
ANTHROPIC_API_KEY=...  # LLM signal validation (Claude)
TELEGRAM_BOT_TOKEN=... # Signal alerts
TELEGRAM_CHAT_ID=...   # Your chat ID
```

### 2. Docker Secrets (Production)

For production, use Docker secrets instead of environment variables for sensitive values:

```bash
# Create secrets
echo "my-strong-db-password" | docker secret create postgres_password -
echo "my-grafana-password"   | docker secret create grafana_password -
echo "sk-ant-..."            | docker secret create anthropic_api_key -

# Use in docker-compose.prod.yml (example):
# services:
#   app:
#     secrets:
#       - anthropic_api_key
#     environment:
#       ANTHROPIC_API_KEY_FILE: /run/secrets/anthropic_api_key
```

### 3. Database Migrations

```bash
# Apply all migrations
docker compose exec app alembic upgrade head

# Check current revision
docker compose exec app alembic current

# Rollback last migration
docker compose exec app alembic downgrade -1
```

### 4. Nginx TLS (HTTPS)

For production HTTPS, use Certbot with the Nginx container:

```bash
# Install certbot in the nginx container or use a separate certbot container
# Example with Let's Encrypt:
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d certbot

# Then update nginx.conf to listen on 443 and reference /etc/letsencrypt
```

Minimal TLS addition to `nginx.conf`:
```nginx
server {
    listen 443 ssl http2;
    ssl_certificate     /etc/letsencrypt/live/yourdomain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    # ... rest of config
}

server {
    listen 80;
    return 301 https://$host$request_uri;
}
```

---

## Scaling

### Celery Workers

Scale the number of workers for higher throughput:

```bash
docker compose up -d --scale celery_worker=4
```

### FastAPI Workers

The app service uses uvicorn. To use gunicorn + uvicorn workers:

```bash
# In Dockerfile CMD:
gunicorn src.main:app -k uvicorn.workers.UvicornWorker --workers 4 --bind 0.0.0.0:8000
```

---

## Monitoring Setup

### Grafana Dashboards

1. Open Grafana at http://localhost:3001
2. Login with admin / GRAFANA_PASSWORD
3. Dashboards are auto-provisioned from `infra/grafana/provisioning/dashboards/`
4. The "Market Analyzer Pro" dashboard shows:
   - Active signals count
   - Portfolio heat %
   - Collectors health
   - HTTP request rate and latency (p50/p95/p99)
   - Signal generation rate
   - Circuit breaker states

### Key Metrics

| Metric | Description |
|--------|-------------|
| `signals_generated_total` | Signals emitted by market/direction |
| `signals_active` | Currently open signals |
| `portfolio_heat_percent` | Total portfolio risk % |
| `collector_up{collector}` | 1=healthy, 0=down |
| `collector_latency_seconds` | Per-collector run time |
| `circuit_breaker_state` | 0=CLOSED, 1=OPEN, 2=HALF_OPEN |
| `http_request_duration_seconds` | API latency histogram |

### Alerts (Prometheus)

Configured in `infra/prometheus/alerts.yml`:
- `CollectorDown` — any collector down > 5 min
- `AllCollectorsDown` — all down > 2 min (critical)
- `HighAPILatency` — p95 > 2s
- `PortfolioHeatHigh` — heat > 5.5%
- `CircuitBreakerOpen` — CB open > 2 min
- `AppHighErrorRate` — 5xx > 5%

To add AlertManager (email/Slack/PagerDuty):
```yaml
# prometheus.yml
alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]
```

---

## Backup and Recovery

```bash
# Backup PostgreSQL
docker compose exec postgres pg_dump -U analyzer market_analyzer | gzip > backup_$(date +%Y%m%d).sql.gz

# Restore
gunzip -c backup_20260317.sql.gz | docker compose exec -T postgres psql -U analyzer market_analyzer

# Backup Redis (optional — Redis is used as cache, not primary store)
docker compose exec redis redis-cli SAVE
docker cp market-analyzer-redis:/data/dump.rdb ./redis_backup.rdb
```

---

## Troubleshooting

### Signal generation stopped

```bash
# Check collector health
curl http://localhost:8000/api/v2/health/collectors

# Check Celery tasks
docker compose logs celery_worker --tail=50

# Check circuit breakers (Prometheus)
# Query: circuit_breaker_state{name="gdelt_cb"}
```

### Database connection errors

```bash
docker compose logs postgres --tail=20
docker compose exec app python3 -c "from src.database.engine import async_session_factory; print('OK')"
```

### FinBERT not responding

```bash
docker compose logs finbert --tail=20
curl http://localhost:8001/health
# If unhealthy, first startup downloads model (can take 10+ min)
```

### Prometheus not scraping

```bash
# Check targets in Prometheus UI: http://localhost:9090/targets
# App metrics endpoint:
curl http://localhost:8000/metrics | head -20
```
