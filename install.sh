#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print status messages
print_status() {
    echo -e "${GREEN}[+]${NC} $1"
}

print_error() {
    echo -e "${RED}[!]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[*]${NC} $1"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    print_error "Please run as root (use sudo)"
    exit 1
fi

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Check if main.py exists
if [ ! -f "$SCRIPT_DIR/main.py" ]; then
    print_error "main.py not found in $SCRIPT_DIR"
    exit 1
fi

# Check if beep.py exists
if [ ! -f "$SCRIPT_DIR/beep.py" ]; then
    print_error "beep.py not found in $SCRIPT_DIR"
    exit 1
fi

# Detect Python path
PYTHON_PATH=$(which python3)
if [ -z "$PYTHON_PATH" ]; then
    print_error "Python 3 not found in PATH"
    exit 1
fi

# Detect current user
CURRENT_USER=$(logname || echo $SUDO_USER)
if [ -z "$CURRENT_USER" ]; then
    print_error "Could not determine user"
    exit 1
fi

# Get user's home directory
USER_HOME=$(eval echo ~$CURRENT_USER)

print_status "Detected Python path: $PYTHON_PATH"
print_status "Detected user: $CURRENT_USER"
print_status "Detected home directory: $USER_HOME"

# Create systemd service directly
print_status "Creating systemd service..."

# Create the service file in the systemd directory
cat > /etc/systemd/system/bikeos.service << EOF
[Unit]
Description=BikeOS - Smart Bike Monitoring System
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_PATH $SCRIPT_DIR/main.py
Restart=always
RestartSec=10
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=bikeos

# Give the service time to start up
TimeoutStartSec=30

# Give the service time to stop gracefully
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

# Check if service was created successfully
if [ $? -ne 0 ]; then
    print_error "Failed to create systemd service"
    exit 1
fi

print_status "Installing BikeOS service..."

# Reload systemd
print_status "Reloading systemd..."
systemctl daemon-reload

# Enable service
print_status "Enabling service..."
systemctl enable bikeos

# Check if service is already running
if systemctl is-active --quiet bikeos; then
    print_warning "Service is already running. Restarting..."
    systemctl restart bikeos
else
    print_status "Starting service..."
    systemctl start bikeos
fi

# Check if service started successfully
if systemctl is-active --quiet bikeos; then
    print_status "Service installed and started successfully!"
    print_status "You can check the status with: sudo systemctl status bikeos"
    print_status "View logs with: sudo journalctl -u bikeos -f"
else
    print_error "Service failed to start. Check logs with: sudo journalctl -u bikeos -e"
    exit 1
fi

# Print some useful commands
echo
print_status "Useful commands:"
echo "  sudo systemctl status bikeos  # Check service status"
echo "  sudo systemctl stop bikeos    # Stop the service"
echo "  sudo systemctl start bikeos   # Start the service"
echo "  sudo systemctl restart bikeos # Restart the service"
echo "  sudo journalctl -u bikeos -f  # View live logs" 