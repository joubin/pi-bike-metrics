#!/bin/bash

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Change to the script directory
cd "$SCRIPT_DIR"

# Pull latest changes
echo "Pulling latest changes..."
git pull

# Start the service
echo "Starting BikeOS..."
exec python3 main.py 