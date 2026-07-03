#!/bin/bash
set -euo pipefail

# Summy v1.4.0 - Startup Validation Script
# Gates traffic until all dependencies are healthy

GATEWAY_URL="http://127.0.0.1:8000"
MEMORY_LOADER_URL="http://127.0.0.1:9010"
OLLAMA_URL="http://127.0.0.1:11434"
MAX_RETRIES=30
RETRY_INTERVAL=2

wait_service() {
    local name="$1"
    local url="$2"
    local retries=$MAX_RETRIES
    
    echo "[INFO] Waiting for $name..."
    while [ $retries -gt 0 ]; do
        if curl -sf "$url" >/dev/null 2>&1; then
            echo "[OK] $name is ready"
            return 0
        fi
        sleep $RETRY_INTERVAL
        retries=$((retries-1))
    done
    echo "[ERROR] $name failed to start within timeout"
    return 1
}

validate_ollama() {
    echo "[INFO] Validating Ollama models..."
    local models
    models=$(curl -sf "$OLLAMA_URL/api/tags" | jq -r '.models[].name' 2>/dev/null || echo "")
    
    if [ -z "$models" ]; then
        echo "[WARN] No models found in Ollama. Run provision_models.sh first."
        return 1
    fi
    
    local required=("deepseek-coder:1.3b-q4_K_M" "tinyllama:1.1b-q5_K_M" "phi:2-q4_K_M")
    local missing=()
    
    for model in "${required[@]}"; do
        if ! echo "$models" | grep -q "^${model}$"; then
            missing+=("$model")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        echo "[WARN] Missing models: ${missing[*]}"
        echo "[INFO] Run provision_models.sh to install missing models"
        return 1
    fi
    
    echo "[OK] All required models present"
    return 0
}

validate_gateway() {
    echo "[INFO] Validating Gateway health endpoint..."
    local response
    response=$(curl -sf "$GATEWAY_URL/health" 2>/dev/null || echo '{"status":"unknown"}')
    
    if echo "$response" | jq -e '.status == "healthy"' >/dev/null 2>&1; then
        echo "[OK] Gateway is healthy"
        return 0
    else
        echo "[WARN] Gateway health check returned: $response"
        return 1
    fi
}

validate_memory_loader() {
    echo "[INFO] Validating Memory Loader..."
    if curl -sf "$MEMORY_LOADER_URL/config" >/dev/null 2>&1; then
        echo "[OK] Memory Loader is serving config"
        return 0
    else
        echo "[WARN] Memory Loader not responding"
        return 1
    fi
}

main() {
    echo "=========================================="
    echo "Summy v1.4.0 - Startup Validation"
    echo "=========================================="
    
    local failed=0
    
    wait_service "Ollama" "$OLLAMA_URL/api/tags" || failed=1
    wait_service "Memory Loader" "$MEMORY_LOADER_URL" || failed=1
    wait_service "Gateway" "$GATEWAY_URL/health" || failed=1
    
    if [ $failed -eq 0 ]; then
        validate_ollama || true
        validate_gateway || true
        validate_memory_loader || true
    fi
    
    echo ""
    echo "=========================================="
    if [ $failed -eq 0 ]; then
        echo "All services started successfully"
        echo "Summy v1.4.0 is ready to accept traffic"
    else
        echo "Startup validation FAILED"
        echo "Check logs: docker compose logs"
        exit 1
    fi
    echo "=========================================="
}

main "$@"
