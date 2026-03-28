"""
Frone - GPIO Handler for Buttons and LEDs

Handles:
- Button input with debouncing
- LED output for status indication
- Contact buttons (mapped to call targets)
- Hangup/answer button

Uses libgpiod (gpiod) for modern kernel compatibility.
"""

import logging
import threading
import time
from datetime import timedelta
from typing import Callable, Optional

import config

logger = logging.getLogger(__name__)

# Try to import gpiod, provide mock for development on non-Pi
try:
    import gpiod
    from gpiod.line import Direction, Value, Bias, Edge
    GPIOD_AVAILABLE = True
except ImportError:
    logger.warning("gpiod not available - running in mock mode")
    GPIOD_AVAILABLE = False
    gpiod = None


class GPIOHandler:
    def __init__(self):
        self._on_call_button: Optional[Callable[[str], None]] = None
        self._on_hangup_button: Optional[Callable[[], None]] = None
        self._button_pins: list = []
        self._led_ring_state = False
        self._led_call_state = False
        self._blink_thread = None
        self._blinking = False
        self._running = False
        self._event_thread = None
        self._chip = None
        self._button_request = None
        self._led_request = None

    def set_on_call_button(self, callback: Callable[[str], None]):
        """Set callback for call button press. Receives target phone_id."""
        self._on_call_button = callback

    def set_on_hangup_button(self, callback: Callable[[], None]):
        """Set callback for hangup/answer button press."""
        self._on_hangup_button = callback

    def setup(self):
        """Initialize GPIO pins."""
        if not GPIOD_AVAILABLE:
            logger.info("GPIO mock mode - no hardware")
            return

        # Collect button pins
        button_pins = list(config.CONTACTS.keys()) + [config.GPIO_BUTTON]
        self._button_pins = button_pins

        # LED pins
        led_pins = [config.GPIO_LED_RING, config.GPIO_LED_CALL]

        # Request button lines (input with pull-up, edge detection)
        button_config = {
            pin: gpiod.LineSettings(
                direction=Direction.INPUT,
                bias=Bias.PULL_UP,
                edge_detection=Edge.FALLING,
                debounce_period=timedelta(milliseconds=50)
            )
            for pin in button_pins
        }

        # Request LED lines (output, default low)
        led_config = {
            pin: gpiod.LineSettings(
                direction=Direction.OUTPUT,
                output_value=Value.INACTIVE
            )
            for pin in led_pins
        }

        try:
            self._button_request = gpiod.request_lines(
                "/dev/gpiochip0",
                consumer="frone-buttons",
                config=button_config
            )

            self._led_request = gpiod.request_lines(
                "/dev/gpiochip0",
                consumer="frone-leds",
                config=led_config
            )
        except Exception as e:
            logger.error(f"Failed to request GPIO lines: {e}")
            raise

        # Start event monitoring thread
        self._running = True
        self._event_thread = threading.Thread(target=self._event_loop, daemon=True)
        self._event_thread.start()

        logger.info(f"GPIO initialized: buttons={button_pins}, leds={led_pins}")

    def _event_loop(self):
        """Monitor button events."""
        while self._running:
            try:
                # Wait for events with timeout
                if self._button_request.wait_edge_events(timeout=timedelta(seconds=0.5)):
                    events = self._button_request.read_edge_events()
                    for event in events:
                        self._handle_button_event(event.line_offset)
            except Exception as e:
                if self._running:
                    logger.error(f"Event loop error: {e}")
                    time.sleep(0.1)

    def _handle_button_event(self, pin: int):
        """Handle a button press event."""
        if pin == config.GPIO_BUTTON:
            logger.info("Hangup/answer button pressed")
            if self._on_hangup_button:
                self._on_hangup_button()
        elif pin in config.CONTACTS:
            contact = config.CONTACTS[pin]
            target_id = contact["id"]
            logger.info(f"Call button pressed: {target_id}")
            if self._on_call_button:
                self._on_call_button(target_id)

    def cleanup(self):
        """Clean up GPIO resources."""
        self._running = False
        self.stop_blinking()

        if self._event_thread:
            self._event_thread.join(timeout=1.0)
            self._event_thread = None

        if self._button_request:
            self._button_request.release()
            self._button_request = None

        if self._led_request:
            self._led_request.release()
            self._led_request = None

        logger.info("GPIO cleaned up")

    def set_led_ring(self, state: bool):
        """Set ringing LED state."""
        self._led_ring_state = state
        if GPIOD_AVAILABLE and self._led_request:
            self._led_request.set_value(
                config.GPIO_LED_RING,
                Value.ACTIVE if state else Value.INACTIVE
            )
        logger.debug(f"Ring LED: {'ON' if state else 'OFF'}")

    def set_led_call(self, state: bool):
        """Set in-call LED state."""
        self._led_call_state = state
        if GPIOD_AVAILABLE and self._led_request:
            self._led_request.set_value(
                config.GPIO_LED_CALL,
                Value.ACTIVE if state else Value.INACTIVE
            )
        logger.debug(f"Call LED: {'ON' if state else 'OFF'}")

    def start_blinking(self, led: str = "ring", interval: float = 0.5):
        """Start blinking an LED."""
        self.stop_blinking()
        self._blinking = True

        def blink_loop():
            state = False
            while self._blinking:
                state = not state
                if led == "ring":
                    self.set_led_ring(state)
                else:
                    self.set_led_call(state)
                time.sleep(interval)
            # Turn off when done
            if led == "ring":
                self.set_led_ring(False)
            else:
                self.set_led_call(False)

        self._blink_thread = threading.Thread(target=blink_loop, daemon=True)
        self._blink_thread.start()

    def stop_blinking(self):
        """Stop LED blinking."""
        self._blinking = False
        if self._blink_thread:
            self._blink_thread.join(timeout=1.0)
            self._blink_thread = None

    def all_leds_off(self):
        """Turn off all LEDs."""
        self.stop_blinking()
        self.set_led_ring(False)
        self.set_led_call(False)


# Standalone test mode
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)

    def on_call(target):
        print(f"Call button pressed for: {target}")

    def on_hangup():
        print("Hangup button pressed")

    handler = GPIOHandler()
    handler.set_on_call_button(on_call)
    handler.set_on_hangup_button(on_hangup)
    handler.setup()

    print("GPIO test mode. Press Ctrl+C to exit.")
    print("Testing LED blink...")

    handler.start_blinking("ring")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nCleaning up...")
        handler.cleanup()
