#!/bin/bash
set -euo pipefail

# Production deployment script for Multiplexing Gateway

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "Docker Compose is not installed. Please install Docker Compose first."
        exit 1
    fi
    
    # Check if config file exists
    if [ ! -f "config/summy.yaml" ]; then
        log_error "Configuration file config/summy.yaml not found."
        exit 1
    fi
    
    log_info "All prerequisites met."
}

# Build the application
build() {
    log_info "Building Docker images..."
    
    if docker compose version &> /dev/null; then
        docker compose -f docker-compose.prod.yml build --no-cache
    else
        docker-compose -f docker-compose.prod.yml build --no-cache
    fi
    
    log_info "Build completed successfully."
}

# Deploy the application
deploy() {
    log_info "Deploying application..."
    
    # Create necessary directories
    mkdir -p data
    
    if docker compose version &> /dev/null; then
        docker compose -f docker-compose.prod.yml up -d
    else
        docker-compose -f docker-compose.prod.yml up -d
    fi
    
    log_info "Deployment initiated. Waiting for services to be healthy..."
    sleep 5
    
    # Check service health
    check_health
}

# Check service health
check_health() {
    log_info "Checking service health..."
    
    max_attempts=30
    attempt=0
    
    while [ $attempt -lt $max_attempts ]; do
        if curl -s -f http://localhost:8000/health > /dev/null 2>&1; then
            log_info "Gateway is healthy and running!"
            
            # Show status
            if docker compose version &> /dev/null; then
                docker compose -f docker-compose.prod.yml ps
            else
                docker-compose -f docker-compose.prod.yml ps
            fi
            
            return 0
        fi
        
        attempt=$((attempt + 1))
        log_warn "Waiting for gateway to be ready (attempt $attempt/$max_attempts)..."
        sleep 2
    done
    
    log_error "Gateway failed to become healthy within timeout."
    
    # Show logs for debugging
    if docker compose version &> /dev/null; then
        docker compose -f docker-compose.prod.yml logs gateway
    else
        docker-compose -f docker-compose.prod.yml logs gateway
    fi
    
    return 1
}

# Show application logs
logs() {
    if docker compose version &> /dev/null; then
        docker compose -f docker-compose.prod.yml logs -f "$@"
    else
        docker-compose -f docker-compose.prod.yml logs -f "$@"
    fi
}

# Stop the application
stop() {
    log_info "Stopping application..."
    
    if docker compose version &> /dev/null; then
        docker compose -f docker-compose.prod.yml down
    else
        docker-compose -f docker-compose.prod.yml down
    fi
    
    log_info "Application stopped."
}

# Restart the application
restart() {
    stop
    sleep 2
    deploy
}

# Show application status
status() {
    if docker compose version &> /dev/null; then
        docker compose -f docker-compose.prod.yml ps
    else
        docker-compose -f docker-compose.prod.yml ps
    fi
    
    echo ""
    log_info "Health check:"
    if curl -s -f http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Gateway is healthy${NC}"
    else
        echo -e "${RED}✗ Gateway is not responding${NC}"
    fi
}

# Print usage
usage() {
    echo "Usage: $0 {build|deploy|start|stop|restart|logs|status|health}"
    echo ""
    echo "Commands:"
    echo "  build    - Build Docker images"
    echo "  deploy   - Build and deploy the application"
    echo "  start    - Start the application (assumes already built)"
    echo "  stop     - Stop the application"
    echo "  restart  - Restart the application"
    echo "  logs     - Show application logs"
    echo "  status   - Show application status"
    echo "  health   - Check application health"
    echo ""
}

# Main entry point
main() {
    if [ $# -eq 0 ]; then
        usage
        exit 1
    fi
    
    case "$1" in
        build)
            check_prerequisites
            build
            ;;
        deploy)
            check_prerequisites
            build
            deploy
            ;;
        start)
            check_prerequisites
            deploy
            ;;
        stop)
            stop
            ;;
        restart)
            restart
            ;;
        logs)
            logs "${@:2}"
            ;;
        status)
            status
            ;;
        health)
            check_health
            ;;
        *)
            log_error "Unknown command: $1"
            usage
            exit 1
            ;;
    esac
}

main "$@"
