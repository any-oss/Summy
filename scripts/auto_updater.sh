#!/bin/bash
# Auto-Updater - Autonomous self-healing script for Docker container updates.
# Compares local and remote image digests, pulls new images, and performs zero-downtime restarts.

set -e

# Configuration
CONTAINER_NAME="${CONTAINER_NAME:-summy-gateway}"
IMAGE_NAME="${IMAGE_NAME:-summy/gateway}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
DOCKER_REGISTRY="${DOCKER_REGISTRY:-registry.hub.docker.com}"
RESTART_DELAY="${RESTART_DELAY:-5}"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $1" >&2
}

# Check if running in a container
check_container_env() {
    if [ ! -f /.dockerenv ] && [ -z "$KUBERNETES_SERVICE_HOST" ]; then
        log "Warning: Not running in a container environment"
    fi
}

# Get local image digest
get_local_digest() {
    local full_image="$IMAGE_NAME:$IMAGE_TAG"
    local digest
    
    digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$full_image" 2>/dev/null | cut -d'@' -f2)
    
    if [ -z "$digest" ]; then
        # Fallback: get from Image ID
        digest=$(docker inspect --format='{{.Id}}' "$full_image" 2>/dev/null)
    fi
    
    echo "$digest"
}

# Get remote image digest from Docker Hub using Python for JSON parsing
get_remote_digest() {
    local namespace="${IMAGE_NAME%%/*}"
    local repo="${IMAGE_NAME#*/}"
    
    if [ "$namespace" = "$repo" ]; then
        # Official image
        namespace="library"
    fi
    
    # Use Python for reliable JSON parsing on minimal ARM environments
    python3 << EOF
import urllib.request
import json
import sys

try:
    url = "https://hub.docker.com/v2/repositories/$namespace/$repo/tags/$IMAGE_TAG"
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode())
        digest = data.get('images', [{}])[0].get('digest', '')
        print(digest)
except Exception as e:
    print("", file=sys.stderr)
    sys.exit(1)
EOF
}

# Pull new image
pull_image() {
    local full_image="$IMAGE_NAME:$IMAGE_TAG"
    log "Pulling new image: $full_image"
    
    if docker pull "$full_image"; then
        log "Image pulled successfully"
        return 0
    else
        log_error "Failed to pull image"
        return 1
    fi
}

# Zero-downtime container restart
restart_container() {
    log "Restarting container: $CONTAINER_NAME"
    
    # Get current container configuration
    local container_id
    container_id=$(docker ps -q -f name="$CONTAINER_NAME" 2>/dev/null | head -1)
    
    if [ -z "$container_id" ]; then
        log_error "Container not found: $CONTAINER_NAME"
        return 1
    fi
    
    # Get container labels and configuration
    local labels
    labels=$(docker inspect --format='{{json .Config.Labels}}' "$container_id" 2>/dev/null)
    
    # Get environment variables
    local env_vars
    env_vars=$(docker inspect --format='{{range .Config.Env}}{{.}}|{{end}}' "$container_id" 2>/dev/null)
    
    # Get volume mounts
    local volumes
    volumes=$(docker inspect --format='{{range .Mounts}}{{if eq .Type "volume"}}{{.Source}}:{{.Destination}}{{else}}{{.Source}}:{{.Destination}}:{{.Mode}}{{end}}|{{end}}' "$container_id" 2>/dev/null)
    
    # Get port mappings
    local ports
    ports=$(docker inspect --format='{{range \$p, \$c := .NetworkSettings.Ports}}{{\$p}}={{join \$c ","}}|{{end}}' "$container_id" 2>/dev/null)
    
    # Get network settings
    local networks
    networks=$(docker inspect --format='{{range \$n, \$c := .NetworkSettings.Networks}}{{\$n}},{{end}}' "$container_id" 2>/dev/null | sed 's/,$//')
    
    # Create new container
    local full_image="$IMAGE_NAME:$IMAGE_TAG"
    local new_container_id
    
    # Build docker run command dynamically
    local run_cmd="docker run -d --name ${CONTAINER_NAME}-new"
    
    # Add labels if present
    if [ -n "$labels" ] && [ "$labels" != "null" ]; then
        while IFS= read -r label; do
            if [ -n "$label" ]; then
                run_cmd="$run_cmd --label $label"
            fi
        done < <(python3 -c "import json; labels=$labels; [print(f'{k}={v}') for k,v in (labels or {}).items()]" 2>/dev/null)
    fi
    
    # Add environment variables
    if [ -n "$env_vars" ]; then
        IFS='|' read -ra ENV_ARRAY <<< "$env_vars"
        for env in "${ENV_ARRAY[@]}"; do
            if [ -n "$env" ]; then
                run_cmd="$run_cmd -e \"$env\""
            fi
        done
    fi
    
    # Add volume mounts
    if [ -n "$volumes" ]; then
        IFS='|' read -ra VOL_ARRAY <<< "$volumes"
        for vol in "${VOL_ARRAY[@]}"; do
            if [ -n "$vol" ]; then
                run_cmd="$run_cmd -v $vol"
            fi
        done
    fi
    
    # Add port mappings
    if [ -n "$ports" ]; then
        IFS='|' read -ra PORT_ARRAY <<< "$ports"
        for port in "${PORT_ARRAY[@]}"; do
            if [ -n "$port" ]; then
                host_port=$(echo "$port" | cut -d'=' -f1 | cut -d'/' -f1)
                run_cmd="$run_cmd -p $host_port:$host_port"
            fi
        done
    fi
    
    # Add network
    if [ -n "$networks" ]; then
        first_network=$(echo "$networks" | cut -d',' -f1)
        if [ -n "$first_network" ]; then
            run_cmd="$run_cmd --net $first_network"
        fi
    fi
    
    run_cmd="$run_cmd $full_image"
    
    # Execute the run command
    if eval "$run_cmd"; then
        log "New container started successfully"
        
        # Wait for health check or delay
        sleep "$RESTART_DELAY"
        
        # Stop old container
        log "Stopping old container"
        docker stop "$container_id" 2>/dev/null || true
        
        # Remove old container
        log "Removing old container"
        docker rm "$container_id" 2>/dev/null || true
        
        # Rename new container
        docker rename "${CONTAINER_NAME}-new" "$CONTAINER_NAME" 2>/dev/null || true
        
        log "Container restart completed"
        return 0
    else
        log_error "Failed to start new container"
        docker rm -f "${CONTAINER_NAME}-new" 2>/dev/null || true
        return 1
    fi
}

# Main update check and execution
main() {
    log "Starting auto-updater check"
    
    check_container_env
    
    # Get local digest
    local_digest=$(get_local_digest)
    if [ -z "$local_digest" ]; then
        log_error "Could not get local image digest"
        exit 1
    fi
    log "Local digest: $local_digest"
    
    # Get remote digest
    remote_digest=$(get_remote_digest)
    if [ -z "$remote_digest" ]; then
        log_error "Could not get remote image digest"
        exit 1
    fi
    log "Remote digest: $remote_digest"
    
    # Compare digests
    if [ "$local_digest" = "$remote_digest" ]; then
        log "Image is up to date"
        exit 0
    fi
    
    log "Update available - digests differ"
    
    # Pull new image
    if ! pull_image; then
        exit 1
    fi
    
    # Verify new digest
    new_local_digest=$(get_local_digest)
    if [ "$new_local_digest" != "$remote_digest" ]; then
        log_error "Pulled image digest does not match remote"
        exit 1
    fi
    
    # Restart container with zero downtime
    if restart_container; then
        log "Auto-update completed successfully"
        exit 0
    else
        log_error "Auto-update failed during restart"
        exit 1
    fi
}

# Run main function
main "$@"
