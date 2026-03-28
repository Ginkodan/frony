# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Frone is a VoIP phone system for Raspberry Pi Zero W devices. Each Pi acts as a standalone phone that can call other phones on the same Tailscale network. Phones communicate via MQTT for signaling and UDP for audio.

## Architecture

**Main Components:**
- `main.py` - Entry point, orchestrates all components via `FronePhone` class
- `state_machine.py` - Call state management (IDLE → CALLING/RINGING → IN_CALL → IDLE)
- `mqtt_client.py` - MQTT signaling for call requests/responses and status
- `audio_stream.py` - Full-duplex UDP audio streaming (16kHz, 16-bit mono PCM)
- `gpio_handler.py` - Button input and LED output via libgpiod

**Data Flow:**
1. Button press → GPIO handler → State machine → MQTT (call request)
2. Incoming MQTT → State machine → GPIO (LED feedback) + Audio (start stream)
3. Audio captured from mic → UDP → Remote phone → Speaker playback

**MQTT Topics:**
- `phones/{id}/status` - Online/offline/busy status (retained)
- `phones/{id}/call/request` - Incoming call requests
- `phones/{id}/call/response` - Accept/reject/hangup responses

## Commands

**Installation (on Pi):**
```bash
sudo ./install.sh
```

**Run directly:**
```bash
source venv/bin/activate
python main.py
```

**Systemd service:**
```bash
sudo systemctl start frone
sudo systemctl status frone
journalctl -u frone -f
```

**Test audio subsystem:**
```bash
# Send audio to IP
python audio_stream.py --send <IP> --port 5000

# Receive audio
python audio_stream.py --receive --port 5000
```

**Test GPIO:**
```bash
python gpio_handler.py
```

## Configuration

Edit `config.py` for each device:
- `PHONE_ID` - Unique identifier (e.g., "phone-a", "phone-b")
- `MQTT_BROKER` - Tailscale IP of MQTT broker
- `CONTACTS` - GPIO pin to phone ID mapping for call buttons
- `GPIO_HANGUP`, `GPIO_LED_RING`, `GPIO_LED_CALL` - Pin assignments

## Hardware

- Raspberry Pi Zero W
- WM8960 audio HAT (driver enabled via dtoverlay in boot config)
- Push buttons with pull-up resistors (active low)
- LEDs for ring/call status
