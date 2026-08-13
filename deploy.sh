#!/usr/bin/env bash
set -e

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
  echo "❌ Error: Docker is not running or not accessible."
  exit 1
fi

COMMAND=$1

case "$COMMAND" in
  build)
    echo "📦 Building containers..."
    docker compose build
    ;;
  
  up)
    echo "🚀 Starting deployment for janus-print..."
    docker compose up -d --remove-orphans
    
    echo "⏳ Waiting for services to be healthy..."
    sleep 5
    
    echo "✅ Deployment complete!"
    echo ""
    echo "You can access the services at:"
    echo " - Admin Console: http://localhost:8088 (admin / janus-print)"
    echo " - CUPS Admin:    http://localhost:6631"
    echo ""
    echo "To view SIEM logs, run: docker compose logs -f siem"
    echo "To run a print sample, run: docker compose exec client print-samples office-laser"
    ;;
    
  down)
    echo "🛑 Stopping and removing containers..."
    docker compose down
    echo "✅ Teardown complete!"
    ;;
    
  *)
    echo "Usage: $0 {build|up|down}"
    echo "  build - Build the docker images"
    echo "  up    - Start the deployment in the background"
    echo "  down  - Stop and remove the containers"
    exit 1
    ;;
esac
