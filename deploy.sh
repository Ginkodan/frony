#!/bin/bash
#
# Deploy Frone to a Raspberry Pi over SSH.
#
# Usage:
#   ./deploy.sh pi@192.168.x.x          # deploy to specific host
#   ./deploy.sh pi@phone-a              # deploy using hostname
#   FRONE_HOST=pi@phone-a ./deploy.sh   # use env var
#
# First run: the Pi must already have been set up with install.sh.
#

set -e

REMOTE=${1:-$FRONE_HOST}
if [ -z "$REMOTE" ]; then
    echo "Usage: ./deploy.sh [user@host]"
    echo "   or: FRONE_HOST=user@host ./deploy.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_DIR="/home/$(echo "$REMOTE" | cut -d@ -f1)/frone"

echo "Deploying to $REMOTE:$REMOTE_DIR ..."

# Sync source files (exclude venv, cache, git)
rsync -avz --delete \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.git/' \
    --exclude='*.wav' \
    "$SCRIPT_DIR/" "$REMOTE:$REMOTE_DIR/"

# Install any new Python dependencies and restart
ssh "$REMOTE" "
    cd $REMOTE_DIR
    source venv/bin/activate
    pip install -q -r requirements.txt
    deactivate
    sudo systemctl restart frone
    echo 'Waiting for service...'
    sleep 2
    sudo systemctl status frone --no-pager -l
"

echo ""
echo "Done. Follow logs with:"
echo "  ssh $REMOTE 'journalctl -u frone -f'"
