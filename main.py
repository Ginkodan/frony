#!/usr/bin/env python3
"""
Frone - Pi Zero W Phone System

Main entry point that orchestrates all components:
- State machine for call flow
- MQTT for signaling
- Audio streaming for voice
- GPIO for buttons and LEDs
"""

import signal
import sys
import time
import logging
import threading
from typing import Optional

import config
from state_machine import PhoneStateMachine, CallState
from mqtt_client import MQTTClient
from audio_stream import AudioStreamer
from gpio_handler import GPIOHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("frone")


class FronePhone:
    def __init__(self):
        self.state_machine = PhoneStateMachine()
        self.mqtt = MQTTClient(config.PHONE_ID)
        self.audio = AudioStreamer()
        self.gpio = GPIOHandler()

        self._running = False
        self._timeout_thread: Optional[threading.Thread] = None
        self._peer_status: dict = {}

        # Wire up callbacks
        self._setup_callbacks()

    def _setup_callbacks(self):
        """Connect all component callbacks."""
        # State machine callbacks
        self.state_machine.set_state_changed_callback(self._on_state_changed)

        # MQTT callbacks
        self.mqtt.set_on_call_request(self._on_call_request)
        self.mqtt.set_on_call_response(self._on_call_response)
        self.mqtt.set_on_status_update(self._on_peer_status)

        # GPIO callbacks
        self.gpio.set_on_call_button(self._on_call_button)
        self.gpio.set_on_hangup_button(self._on_hangup_button)

    def start(self):
        """Start the phone system."""
        logger.info(f"Starting Frone phone: {config.PHONE_ID}")

        self._running = True

        # Initialize components
        self.gpio.setup()
        self.mqtt.connect()

        # Start timeout checker
        self._timeout_thread = threading.Thread(target=self._timeout_loop, daemon=True)
        self._timeout_thread.start()

        logger.info("Phone ready")

    def stop(self):
        """Stop the phone system."""
        logger.info("Stopping phone...")

        self._running = False

        # Stop audio if active
        self.audio.stop()

        # Disconnect MQTT (publishes offline status)
        self.mqtt.disconnect()

        # Clean up GPIO
        self.gpio.cleanup()

        logger.info("Phone stopped")

    def _on_state_changed(self, old_state: CallState, new_state: CallState):
        """Handle state machine transitions."""
        logger.debug(f"State changed: {old_state.name} -> {new_state.name}")

        # Update LEDs based on state
        if new_state == CallState.IDLE:
            self.gpio.all_leds_off()
            self.audio.stop_ringtone()
            self.audio.stop()
            self.mqtt.publish_status("online")

        elif new_state == CallState.CALLING:
            self.gpio.start_blinking("call", 0.3)  # Fast blink while calling
            self.mqtt.publish_status("busy")
            self.audio.start_ringback()

        elif new_state == CallState.RINGING:
            self.gpio.start_blinking("ring", 0.5)  # Blink ring LED
            self.mqtt.publish_status("busy")
            self.audio.start_ringtone()

        elif new_state == CallState.IN_CALL:
            self.gpio.stop_blinking()
            self.gpio.set_led_call(True)
            self.audio.stop_ringtone()
            # Audio is started by the response handler

    def _on_call_request(self, from_id: str, from_ip: str, from_port: int):
        """Handle incoming call request from MQTT."""
        if self.state_machine.incoming_call(from_id, from_ip, from_port):
            logger.info(f"Incoming call from {from_id}")
            # State change callback handles LED/status
        else:
            # Already busy, send busy response
            self.mqtt.send_call_response(from_id, "busy")

    def _on_peer_status(self, peer_id: str, status: str):
        """Track peer online/offline/busy status from MQTT retained messages."""
        self._peer_status[peer_id] = status
        logger.debug(f"Peer status: {peer_id} = {status}")

    def _can_call(self, target_id: str) -> bool:
        """Check peer status before dialing. Plays busy tone and returns False if unreachable."""
        status = self._peer_status.get(target_id)
        if status in ("offline", None) and status is not None:
            logger.info(f"Cannot call {target_id}: {status}")
            self.audio.start_busy_tone()
            return False
        if status == "busy":
            logger.info(f"Cannot call {target_id}: busy")
            self.audio.start_busy_tone()
            return False
        return True

    def _on_call_response(self, from_id: str, response_type: str, port: Optional[int], from_ip: Optional[str] = None):
        """Handle call response from MQTT."""
        if response_type == "accept":
            if self.state_machine.state == CallState.CALLING:
                if not from_ip:
                    logger.warning("Accept response missing peer IP — cannot start audio")
                    return
                self.state_machine.call_answered(from_ip, port or config.AUDIO_PORT)
                self.audio.start(from_ip, port or config.AUDIO_PORT)

        elif response_type == "reject":
            logger.info(f"Call rejected by {from_id}")
            self.state_machine.call_rejected()
            self.audio.start_busy_tone()

        elif response_type == "hangup":
            was_in_call = self.state_machine.state == CallState.IN_CALL
            logger.info(f"Remote hangup from {from_id}")
            self.state_machine.hangup()
            if was_in_call:
                self.audio.play_disconnect_tone()

        elif response_type == "busy":
            logger.info(f"{from_id} is busy")
            self.state_machine.call_rejected()
            self.audio.start_busy_tone()

    def _on_call_button(self, target_id: str):
        """Handle call button press from GPIO."""
        if self.state_machine.state == CallState.IDLE:
            if not self._can_call(target_id):
                return
            if self.state_machine.start_call(target_id):
                self.mqtt.send_call_request(target_id)
        else:
            logger.debug("Ignoring call button - not idle")

    def _on_hangup_button(self):
        """Handle main button press - call/answer/hangup depending on state."""
        state = self.state_machine.state

        if state == CallState.IDLE:
            # Initiate call to default contact
            target_id = config.DEFAULT_CONTACT
            if not self._can_call(target_id):
                return
            if self.state_machine.start_call(target_id):
                self.mqtt.send_call_request(target_id)

        elif state == CallState.RINGING:
            # Answer the call
            peer_id = self.state_machine.peer_id
            peer_ip = self.state_machine.peer_ip
            peer_port = self.state_machine.peer_port

            if self.state_machine.call_answered():
                # Send accept response with our audio port
                self.mqtt.send_call_response(peer_id, "accept", include_port=True)
                # Start audio
                self.audio.start(peer_ip, peer_port)

        elif state == CallState.CALLING:
            # Cancel outgoing call
            peer_id = self.state_machine.peer_id
            self.mqtt.send_call_response(peer_id, "hangup")
            self.state_machine.hangup()

        elif state == CallState.IN_CALL:
            # Hang up active call
            peer_id = self.state_machine.peer_id
            self.mqtt.send_call_response(peer_id, "hangup")
            self.state_machine.hangup()

    def _timeout_loop(self):
        """Check for call timeouts."""
        while self._running:
            time.sleep(1)

            state = self.state_machine.state
            duration = self.state_machine.get_call_duration()

            if duration is None:
                continue

            if state == CallState.CALLING and duration > config.CALL_TIMEOUT:
                logger.info("Outgoing call timed out")
                peer_id = self.state_machine.peer_id
                if peer_id:
                    self.mqtt.send_call_response(peer_id, "hangup")
                self.state_machine.call_timeout()
                self.audio.start_busy_tone()  # No answer

            elif state == CallState.RINGING and duration > config.RING_TIMEOUT:
                logger.info("Incoming call timed out")
                peer_id = self.state_machine.peer_id
                if peer_id:
                    self.mqtt.send_call_response(peer_id, "reject")
                self.state_machine.call_timeout()


def main():
    phone = FronePhone()

    # Handle graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        phone.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start the phone
    phone.start()

    # Keep running
    logger.info("Phone running. Press Ctrl+C to stop.")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
