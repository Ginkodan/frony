"""
Frone - MQTT Client for Call Signaling

Handles:
- Publishing phone status (online/offline/busy)
- Sending/receiving call requests
- Sending/receiving call responses (accept/reject/hangup)
"""

import json
import time
import socket
import logging
import threading
from typing import Callable, Optional

import paho.mqtt.client as mqtt

import config

logger = logging.getLogger(__name__)


def get_tailscale_ip() -> str:
    """Get this device's Tailscale IP address."""
    try:
        # Connect to a Tailscale IP to find our own Tailscale interface
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((config.MQTT_BROKER, 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        logger.error(f"Failed to get Tailscale IP: {e}")
        # Fallback to hostname resolution
        return socket.gethostbyname(socket.gethostname())


class MQTTClient:
    def __init__(self, phone_id: str):
        self.phone_id = phone_id
        self.my_ip = get_tailscale_ip()

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"frone-{phone_id}"
        )

        # Callbacks
        self._on_call_request: Optional[Callable] = None
        self._on_call_response: Optional[Callable] = None
        self._on_status_update: Optional[Callable] = None

        # Set up MQTT callbacks
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_message = self._handle_message

        # Last Will and Testament - mark offline if we disconnect
        lwt_topic = f"phones/{phone_id}/status"
        lwt_payload = json.dumps({"status": "offline", "timestamp": int(time.time())})
        self._client.will_set(lwt_topic, lwt_payload, qos=1, retain=True)

    def set_on_call_request(self, callback: Callable[[str, str, int], None]):
        """Set callback for incoming call requests: (from_id, from_ip, from_port)"""
        self._on_call_request = callback

    def set_on_call_response(self, callback: Callable[[str, str, Optional[int], Optional[str]], None]):
        """Set callback for call responses: (from_id, response_type, port, from_ip)"""
        self._on_call_response = callback

    def set_on_status_update(self, callback: Callable[[str, str], None]):
        """Set callback for peer status updates: (phone_id, status)"""
        self._on_status_update = callback

    def connect(self):
        """Connect to MQTT broker."""
        logger.info(f"Connecting to MQTT broker at {config.MQTT_BROKER}:{config.MQTT_PORT}")

        if config.MQTT_USER and config.MQTT_PASS:
            self._client.username_pw_set(config.MQTT_USER, config.MQTT_PASS)

        self._client.connect(config.MQTT_BROKER, config.MQTT_PORT, keepalive=60)
        self._client.loop_start()

    def disconnect(self):
        """Disconnect from MQTT broker."""
        self.publish_status("offline")
        self._client.loop_stop()
        self._client.disconnect()

    def _handle_connect(self, client, userdata, flags, reason_code, properties):
        """Handle MQTT connection."""
        if reason_code == 0:
            logger.info("Connected to MQTT broker")

            # Subscribe to our topics
            topics = [
                (f"phones/{self.phone_id}/call/request", 1),
                (f"phones/{self.phone_id}/call/response", 1),
                ("phones/+/status", 0),  # Subscribe to all status updates
            ]
            self._client.subscribe(topics)

            # Publish online status
            self.publish_status("online")
        else:
            logger.error(f"Failed to connect: {reason_code}")

    def _handle_disconnect(self, client, userdata, flags, reason_code, properties):
        """Handle MQTT disconnection."""
        logger.warning(f"Disconnected from MQTT broker: {reason_code}")
        if reason_code != 0:
            logger.info("Unexpected disconnect, reconnecting in 5s...")
            threading.Timer(5.0, self._reconnect).start()

    def _reconnect(self):
        """Attempt to reconnect to MQTT broker."""
        try:
            self._client.reconnect()
            logger.info("Reconnected to MQTT broker")
        except Exception as e:
            logger.error(f"Reconnect failed: {e}, retrying in 5s...")
            threading.Timer(5.0, self._reconnect).start()

    def _handle_message(self, client, userdata, msg):
        """Handle incoming MQTT messages."""
        try:
            payload = json.loads(msg.payload.decode())
            topic_parts = msg.topic.split("/")

            if msg.topic == f"phones/{self.phone_id}/call/request":
                self._handle_call_request(payload)
            elif msg.topic == f"phones/{self.phone_id}/call/response":
                self._handle_call_response(payload)
            elif topic_parts[0] == "phones" and topic_parts[2] == "status":
                peer_id = topic_parts[1]
                if peer_id != self.phone_id:  # Ignore our own status
                    self._handle_status_update(peer_id, payload)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in message: {e}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    def _handle_call_request(self, payload: dict):
        """Handle incoming call request."""
        from_id = payload.get("from")
        from_ip = payload.get("from_ip")
        from_port = payload.get("audio_port", config.AUDIO_PORT)

        logger.info(f"Incoming call from {from_id} ({from_ip}:{from_port})")

        if self._on_call_request:
            self._on_call_request(from_id, from_ip, from_port)

    def _handle_call_response(self, payload: dict):
        """Handle call response (accept/reject/hangup/busy)."""
        from_id = payload.get("from")
        response_type = payload.get("type")
        port = payload.get("audio_port")
        from_ip = payload.get("from_ip")

        logger.info(f"Call response from {from_id}: {response_type}")

        if self._on_call_response:
            self._on_call_response(from_id, response_type, port, from_ip)

    def _handle_status_update(self, peer_id: str, payload: dict):
        """Handle peer status update."""
        status = payload.get("status")
        logger.debug(f"Status update: {peer_id} is {status}")

        if self._on_status_update:
            self._on_status_update(peer_id, status)

    def publish_status(self, status: str):
        """Publish our status (online/offline/busy)."""
        topic = f"phones/{self.phone_id}/status"
        payload = json.dumps({
            "status": status,
            "timestamp": int(time.time())
        })
        self._client.publish(topic, payload, qos=1, retain=True)
        logger.info(f"Published status: {status}")

    def send_call_request(self, target_id: str):
        """Send a call request to another phone."""
        topic = f"phones/{target_id}/call/request"
        payload = json.dumps({
            "from": self.phone_id,
            "from_ip": self.my_ip,
            "audio_port": config.AUDIO_PORT,
            "timestamp": int(time.time())
        })
        self._client.publish(topic, payload, qos=1)
        logger.info(f"Sent call request to {target_id}")

    def send_call_response(self, target_id: str, response_type: str, include_port: bool = False):
        """Send call response (accept/reject/hangup/busy)."""
        topic = f"phones/{target_id}/call/response"
        payload = {
            "from": self.phone_id,
            "type": response_type,
            "timestamp": int(time.time())
        }
        if include_port:
            payload["audio_port"] = config.AUDIO_PORT
            payload["from_ip"] = self.my_ip

        self._client.publish(topic, json.dumps(payload), qos=1)
        logger.info(f"Sent {response_type} to {target_id}")
