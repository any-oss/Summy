#!/bin/bash
# Auto-Updater - Autonomous self-healing script for Docker container updates.
# Compares local and remote image digests and performs zero-downtime restarts.

set -euo pipefail

# Configuration from environment variables
DOCKERHUB_USERNAME="${DOCKERHUB_USERNAME:-}"
DOCKERHUB_TOKEN="${DOCKERHUB_TOKEN:-}"
IMAGE_NAME="${IMAGE_NAME:-any-oss/Summy}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
CONTAINER_NAME="${CONTAINER_NAME:-summy-gateway}"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Error handling
error_exit() {
    log "ERROR: $1"
    exit 1
}

# Authenticate with Docker Hub
authenticate() {
    if [ -z "$DOCKERHUB_USERNAME" ] || [ -z "$DOCKERHUB_TOKEN" ]; then
        log "WARNING: Docker Hub credentials not provided, skipping authentication"
        return 0
    fi

    log "Authenticating with Docker Hub..."
    if ! echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USERNAME" --password-stdin 2>/dev/null; then
        log "WARNING: Docker Hub authentication failed"
        return 1
    fi

    log "Docker Hub authentication successful"
    return 0
}

# Get local image digest
get_local_digest() {
    local full_image_name="$1"
    local digest

    digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$full_image_name" 2>/dev/null | cut -d'@' -f2)

    if [ -z "$digest" ]; then
        # Fallback: try to get from ID if no digest available
        digest=$(docker images --format '{{.ID}}' "$full_image_name" 2>/dev/null | head -n1)
    fi

    echo "$digest"
}

# Get remote image digest from Docker Hub API
get_remote_digest() {
    local image_name="$1"
    local image_tag="$2"
    local digest=""

    # Parse organization and repo from image name
    local org repo
    if [[ "$image_name" == *"/"* ]]; then
        org=$(echo "$image_name" | cut -d'/' -f1)
        repo=$(echo "$image_name" | cut -d'/' -f2)
    else
        org="library"
        repo="$image_name"
    fi

    log "Fetching remote digest for $org/$repo:$image_tag..."

    # Use Python for JSON parsing (more portable than jq on minimal ARM systems)
    digest=$(python3 -c "
import urllib.request
import json
import sys

try:
    url = 'https://hub.docker.com/v2/repositories/$org/$repo/tags/$image_tag'
    req = urllib.request.Request(url)
    req.add_header('Accept', 'application/json')
    
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode())
        # Get the digest from the images array
        images = data.get('images', [])
        if images:
            # Prefer amd64/arm architecture
            for img in images:
                arch = img.get('architecture', '')
                if 'arm' in arch or 'amd64' in arch:
                    print(img.get('digest', ''))
                    sys.exit(0)
            # Fallback to first image
            print(images[0].get('digest', ''))
        else:
            sys.exit(1)
except Exception as e:
    print('', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null)

    echo "$digest"
}

# Pull new image
pull_image() {
    local full_image_name="$1"

    log "Pulling new image: $full_image_name..."
    if ! docker pull "$full_image_name"; then
        error_exit "Failed to pull image $full_image_name"
    fi

    log "Image pulled successfully"
}

# Perform zero-downtime container restart
restart_container() {
    local container_name="$1"
    local image_name="$2"

    log "Restarting container $container_name with new image..."

    # Check if container exists
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
        log "Container $container_name does not exist, creating new one..."
        docker run -d --name "$container_name" "$image_name"
        return 0
    fi

    # Get container configuration
    local container_config
    container_config=$(docker inspect "$container_name" 2>/dev/null)

    if [ -z "$container_config" ]; then
        error_exit "Failed to inspect container $container_name"
    fi

    # Extract relevant configuration using Python
    local restart_cmd
    restart_cmd=$(python3 -c "
import json
import sys

config = json.loads('''$container_config''')[0]

# Build docker run command from container config
cmd = ['docker', 'run', '-d']

# Add name
cmd.extend(['--name', '$container_name'])

# Add network mode
network_mode = config.get('HostConfig', {}).get('NetworkMode')
if network_mode and network_mode != 'default':
    cmd.extend(['--network', network_mode])

# Add environment variables
env_vars = config.get('Config', {}).get('Env', [])
for env in env_vars:
    cmd.extend(['-e', env])

# Add volume mounts
mounts = config.get('HostConfig', {}).get('Binds', [])
for mount in mounts:
    cmd.extend(['-v', mount])

# Add port mappings
ports = config.get('HostConfig', {}).get('PortBindings', {})
for host_port, container_ports in ports.items():
    if container_ports:
        for cp in container_ports:
            cmd.extend(['-p', f\"{cp['HostIp']}:{host_port}:{cp['HostPort']}\"])

# Add restart policy
restart_policy = config.get('HostConfig', {}).get('RestartPolicy', {}).get('Name')
if restart_policy:
    cmd.extend(['--restart', restart_policy])

# Add image
cmd.append('$image_name')

print(' '.join(cmd))
" 2>/dev/null)

    # Stop old container (graceful shutdown)
    log "Stopping old container..."
    docker stop -t 30 "$container_name" 2>/dev/null || true

    # Remove old container
    log "Removing old container..."
    docker rm "$container_name" 2>/dev/null || true

    # Start new container
    log "Starting new container..."
    eval "$restart_cmd"

    # Verify container is running
    sleep 2
    if docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        log "Container restarted successfully"
        return 0
    else
        error_exit "Container failed to start after update"
    fi
}

# Main update check function
check_and_update() {
    local full_image_name="${IMAGE_NAME}:${IMAGE_TAG}"

    log "Starting update check for $full_image_name..."

    # Get local digest
    local local_digest
    local_digest=$(get_local_digest "$full_image_name")

    if [ -z "$local_digest" ]; then
        log "Local image not found, pulling initial image..."
        pull_image "$full_image_name"
        return 0
    fi

    log "Local digest: $local_digest"

    # Get remote digest
    local remote_digest
    remote_digest=$(get_remote_digest "$IMAGE_NAME" "$IMAGE_TAG")

    if [ -z "$remote_digest" ]; then
        log "WARNING: Could not fetch remote digest, skipping update check"
        return 0
    fi

    log "Remote digest: $remote_digest"

    # Compare digests
    if [ "$local_digest" = "$remote_digest" ]; then
        log "Image is up-to-date (digests match)"
        return 0
    fi

    log "Update detected! Local and remote digests differ."
    log "Proceeding with update..."

    # Pull new image
    pull_image "$full_image_name"

    # Restart container with new image
    restart_container "$CONTAINER_NAME" "$full_image_name"

    log "Update completed successfully!"
}

# Cleanup old images
cleanup_old_images() {
    log "Cleaning up dangling images..."
    docker image prune -f 2>/dev/null || true
}

# Main execution
main() {
    log "=== Summy Auto-Updater Starting ==="

    # Authenticate (optional)
    authenticate || true

    # Check and update
    check_and_update

    # Cleanup
    cleanup_old_images

    log "=== Auto-Updater Complete ==="
}

# Run main function
main "$@"
