#!/bin/bash
set -euo pipefail

# Summy v1.4.0 - Model Provisioning Script
# Prevents eMMC exhaustion with capped, checksum-verified model pulls

OLLAMA_URL="http://127.0.0.1:11434"
MAX_STORAGE_MB=2800
MODELS=("deepseek-coder:1.3b-q4_K_M" "tinyllama:1.1b-q5_K_M" "phi:2-q4_K_M")

get_storage_mb() {
    du -sm ~/.ollama/models 2>/dev/null | awk '{print $1}' || echo 0
}

wait_ollama() {
    local retries=15
    while [ $retries -gt 0 ]; do
        if curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then return 0; fi
        sleep 2
        retries=$((retries-1))
    done
    echo "ERROR: Ollama failed to start."
    exit 1
}

pull_model() {
    local model="$1"
    local current_storage
    current_storage=$(get_storage_mb)
    
    echo "[INFO] Current model storage: ${current_storage}MB / ${MAX_STORAGE_MB}MB"
    
    if [ "$current_storage" -ge "$MAX_STORAGE_MB" ]; then
        echo "[WARN] Storage cap reached. Skipping pull of $model"
        return 0
    fi
    
    echo "[INFO] Pulling $model..."
    if ollama pull "$model" 2>&1 | tee /tmp/ollama_pull.log; then
        echo "[OK] $model provisioned successfully"
    else
        echo "[ERROR] Failed to pull $model"
        return 1
    fi
}

verify_model() {
    local model="$1"
    if ollama list | grep -q "$model"; then
        echo "[OK] $model verified in local registry"
        return 0
    else
        echo "[ERROR] $model not found after pull"
        return 1
    fi
}

main() {
    echo "=========================================="
    echo "Summy v1.4.0 - Model Provisioning"
    echo "=========================================="
    
    wait_ollama
    echo "[OK] Ollama is ready"
    
    for model in "${MODELS[@]}"; do
        echo ""
        echo "--- Processing: $model ---"
        pull_model "$model" || continue
        verify_model "$model" || continue
    done
    
    echo ""
    echo "=========================================="
    echo "Provisioning Complete"
    echo "Final storage: $(get_storage_mb)MB / ${MAX_STORAGE_MB}MB"
    echo "=========================================="
}

main "$@"
