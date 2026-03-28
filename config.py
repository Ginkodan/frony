"""
Frone - Pi Zero W Phone Configuration

Copy this file to each Pi and customize PHONE_ID and MQTT_BROKER.
"""

# Unique identifier for this phone (change per device)
PHONE_ID = "phone-a"

# MQTT Broker settings (Unraid server Tailscale IP)
MQTT_BROKER = "100.x.x.x"  # TODO: Set your Unraid Tailscale IP
MQTT_PORT = 1883
MQTT_USER = None  # Set if using authentication
MQTT_PASS = None

# Audio settings
AUDIO_PORT = 5000  # UDP port for audio streaming
SAMPLE_RATE = 16000  # 16kHz wideband voice
CHANNELS = 1  # Mono
CHUNK_MS = 20  # 20ms chunks
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_MS / 1000)  # 320 samples

# GPIO pins for buttons (directly mapped to contacts)
# For multi-button setup, map GPIO pins to phone IDs:
# CONTACTS = {
#     27: {"id": "phone-c", "name": "Phone C"},
# }
CONTACTS = {}

# GPIO pins
GPIO_BUTTON = 17  # Main button (WM8960 HAT button) - call/answer/hangup
DEFAULT_CONTACT = "phone-b"  # Who to call when button pressed in idle
GPIO_LED_RING = 23  # Yellow LED - ringing
GPIO_LED_CALL = 24  # Red LED - in call

# Timeouts (seconds)
RING_TIMEOUT = 30  # How long to ring before giving up
CALL_TIMEOUT = 30  # How long to wait for answer when calling

# Audio device (usually 'default' works with WM8960)
AUDIO_DEVICE = None  # None = system default
