#!/bin/bash
# Production Deployment Script for Summy v1.4.0
# Usage: ./deploy.sh [production|staging]

set -e

ENV=${1:-production}
PROJECT_NAME="summy"
COMPOSE_FILE="docker-compose.yml"

echo "🚀 Starting $ENV deployment for $PROJECT_NAME..."

# 1. Pre-deployment checks
echo "🔍 Running pre-deployment checks..."
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

if ! command -v docker compose &> /dev/null && ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

# 2. Pull latest changes (if running on server with git)
if [ -d ".git" ]; then
    echo "📥 Pulling latest changes..."
    git pull origin main || echo "⚠️  Could not pull changes (expected if running from local build)"
fi

# 3. Build images
echo "🏗️  Building Docker images..."
if command -v docker compose &> /dev/null; then
    docker compose -f $COMPOSE_FILE build --no-cache
else
    docker-compose -f $COMPOSE_FILE build --no-cache
fi

# 4. Run database migrations (if any)
echo "🗄️  Running database migrations..."
# Add migration commands here if needed

# 5. Start services
echo "🔄 Starting services..."
if command -v docker compose &> /dev/null; then
    docker compose -f $COMPOSE_FILE up -d
else
    docker-compose -f $COMPOSE_FILE up -d
fi

# 6. Health check
echo "🏥 Performing health check..."
sleep 10  # Wait for services to start

MAX_RETRIES=5
RETRY_COUNT=0
HEALTHY=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
        HEALTHY=true
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    echo "⏳ Waiting for services... (attempt $RETRY_COUNT/$MAX_RETRIES)"
    sleep 5
done

if [ "$HEALTHY" = true ]; then
    echo "✅ Deployment successful! Services are running."
    echo "🌐 Access the dashboard at: http://localhost:8080"
    
    # Show running containers
    if command -v docker compose &> /dev/null; then
        docker compose -f $COMPOSE_FILE ps
    else
        docker-compose -f $COMPOSE_FILE ps
    fi
else
    echo "❌ Health check failed. Services may not be running correctly."
    echo "📋 Check logs with: docker compose logs -f"
    exit 1
fi

echo "✨ Deployment complete!"
