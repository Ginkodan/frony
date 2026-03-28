#!/bin/bash
#
# Frone Installation Script
# Run this on each Raspberry Pi Zero W
#
# Usage: sudo ./install.sh
#

set -e

echo "==================================="
echo "  Frone Phone System Installation"
echo "==================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo ./install.sh"
    exit 1
fi

# Get the actual user (not root)
ACTUAL_USER=${SUDO_USER:-$USER}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Detect boot config path (Bookworm/Trixie use /boot/firmware/, older use /boot/)
if [ -f /boot/firmware/config.txt ]; then
    BOOT_CONFIG="/boot/firmware/config.txt"
else
    BOOT_CONFIG="/boot/config.txt"
fi
echo "Detected boot config: $BOOT_CONFIG"

echo "[1/6] Updating system packages..."
apt-get update
apt-get upgrade -y

echo ""
echo "[2/6] Installing system dependencies..."
apt-get install -y \
    python3-pip \
    python3-dev \
    python3-venv \
    portaudio19-dev \
    libffi-dev \
    libopenblas-dev \
    libgpiod-dev \
    i2c-tools \
    git

echo ""
echo "[3/6] Installing WM8960 audio driver..."

# Enable I2C using raspi-config (more reliable than just editing config.txt)
echo "Enabling I2C interface..."
raspi-config nonint do_i2c 0

# Ensure dtparam line is in config.txt (raspi-config should handle this, but be safe)
if ! grep -q "^dtparam=i2c_arm=on" "$BOOT_CONFIG"; then
    echo "dtparam=i2c_arm=on" >> "$BOOT_CONFIG"
fi

# On modern Raspberry Pi OS (Bookworm/Trixie), wm8960-soundcard overlay is included
# No need to install Waveshare driver - just enable the overlay

# Ensure dtoverlay is enabled in config.txt
if ! grep -q "dtoverlay=wm8960-soundcard" "$BOOT_CONFIG"; then
    echo "dtoverlay=wm8960-soundcard" >> "$BOOT_CONFIG"
    echo "WM8960 overlay enabled in $BOOT_CONFIG. Reboot required."
else
    echo "WM8960 overlay already enabled in config.txt."
fi

# Configure WM8960 mixer settings (runs after reboot when card is available)
echo "Creating WM8960 mixer configuration service..."
cat > /etc/systemd/system/wm8960-mixer.service << 'EOF'
[Unit]
Description=Configure WM8960 mixer settings
After=sound.target
ConditionPathExists=/proc/asound/card1

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/amixer -c 1 cset numid=52 on
ExecStart=/usr/bin/amixer -c 1 cset numid=55 on
ExecStart=/usr/bin/amixer -c 1 sset 'Speaker' 100%
ExecStart=/usr/bin/amixer -c 1 sset 'Playback' 100%
ExecStart=/usr/bin/amixer -c 1 sset 'Headphone' 100%

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable wm8960-mixer

# If sound card is already available, configure mixer now
if [ -e /proc/asound/card1 ]; then
    echo "Sound card detected, configuring mixer..."
    systemctl start wm8960-mixer || true
fi

echo ""
echo "[4/6] Installing Tailscale..."
if ! command -v tailscale &> /dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
    echo ""
    echo "Tailscale installed. Run 'sudo tailscale up' to authenticate."
else
    echo "Tailscale already installed."
fi

echo ""
echo "[5/6] Setting up Python environment..."
cd "$SCRIPT_DIR"

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python packages
pip install --upgrade pip
pip install -r requirements.txt

deactivate

echo ""
echo "[6/6] Installing systemd service..."

# Create dedicated service user with only the groups it needs
if ! id -u frone &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin \
        --groups audio,gpio frone
    echo "Created 'frone' system user"
fi

# Give frone user read access to the install directory
chown -R root:frone "$SCRIPT_DIR"
chmod -R g+rX "$SCRIPT_DIR"

sed "s|__INSTALL_DIR__|$SCRIPT_DIR|g" frone.service > /etc/systemd/system/frone.service
systemctl daemon-reload
systemctl enable frone

echo ""
echo "==================================="
echo "  Installation Complete!"
echo "==================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Edit config.py to set:"
echo "   - PHONE_ID (unique per phone, e.g., 'phone-a', 'phone-b')"
echo "   - MQTT_BROKER (your Unraid Tailscale IP)"
echo "   - CONTACTS (button-to-phone mappings)"
echo ""
echo "2. Configure Tailscale:"
echo "   sudo tailscale up"
echo ""
echo "3. Test audio:"
echo "   arecord -D plughw:1 -f S16_LE -r 16000 -d 5 test.wav"
echo "   aplay -D plughw:1 test.wav"
echo ""
echo "4. Start the service:"
echo "   sudo systemctl start frone"
echo ""
echo "5. Check logs:"
echo "   journalctl -u frone -f"
echo ""
echo "NOTE: Reboot required if WM8960 driver was just installed!"
