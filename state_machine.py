"""
Frone - Phone State Machine

Manages call states: IDLE -> CALLING/RINGING -> IN_CALL -> IDLE
"""

from enum import Enum, auto
from typing import Callable, Optional
import logging
import threading
import time

logger = logging.getLogger(__name__)


class CallState(Enum):
    IDLE = auto()
    CALLING = auto()  # Outgoing call, waiting for answer
    RINGING = auto()  # Incoming call, waiting for user to answer
    IN_CALL = auto()  # Active call


class PhoneStateMachine:
    def __init__(self):
        self._state = CallState.IDLE
        self._peer_id: Optional[str] = None
        self._peer_ip: Optional[str] = None
        self._peer_port: Optional[int] = None
        self._state_changed_callback: Optional[Callable] = None
        self._call_start_time: Optional[float] = None
        self._lock = threading.RLock()

    @property
    def state(self) -> CallState:
        return self._state

    @property
    def peer_id(self) -> Optional[str]:
        return self._peer_id

    @property
    def peer_ip(self) -> Optional[str]:
        return self._peer_ip

    @property
    def peer_port(self) -> Optional[int]:
        return self._peer_port

    def set_state_changed_callback(self, callback: Callable[[CallState, CallState], None]):
        """Set callback for state changes. Called with (old_state, new_state)."""
        self._state_changed_callback = callback

    def _set_state(self, new_state: CallState):
        """Internal state setter with callback notification."""
        if new_state != self._state:
            old_state = self._state
            self._state = new_state
            logger.info(f"State: {old_state.name} -> {new_state.name}")
            if self._state_changed_callback:
                self._state_changed_callback(old_state, new_state)

    def start_call(self, peer_id: str) -> bool:
        """Initiate an outgoing call. Returns True if state transition was valid."""
        with self._lock:
            if self._state != CallState.IDLE:
                logger.warning(f"Cannot start call: current state is {self._state.name}")
                return False

            self._peer_id = peer_id
            self._peer_ip = None
            self._peer_port = None
            self._call_start_time = time.time()
            self._set_state(CallState.CALLING)
            return True

    def incoming_call(self, peer_id: str, peer_ip: str, peer_port: int) -> bool:
        """Handle incoming call request. Returns True if we can receive the call."""
        with self._lock:
            if self._state != CallState.IDLE:
                logger.warning(f"Cannot receive call: current state is {self._state.name}")
                return False

            self._peer_id = peer_id
            self._peer_ip = peer_ip
            self._peer_port = peer_port
            self._call_start_time = time.time()
            self._set_state(CallState.RINGING)
            return True

    def call_answered(self, peer_ip: str = None, peer_port: int = None) -> bool:
        """
        Call was answered.
        - If CALLING: remote answered us, peer_ip/port provided
        - If RINGING: we answered the call, peer info already set
        """
        with self._lock:
            if self._state == CallState.CALLING:
                if peer_ip and peer_port:
                    self._peer_ip = peer_ip
                    self._peer_port = peer_port
                self._set_state(CallState.IN_CALL)
                return True
            elif self._state == CallState.RINGING:
                self._set_state(CallState.IN_CALL)
                return True
            else:
                logger.warning(f"Cannot answer: current state is {self._state.name}")
                return False

    def call_rejected(self) -> bool:
        """Call was rejected by remote or we rejected incoming call."""
        with self._lock:
            if self._state in (CallState.CALLING, CallState.RINGING):
                self._reset()
                return True
            logger.warning(f"Cannot reject: current state is {self._state.name}")
            return False

    def hangup(self) -> bool:
        """End the current call or cancel outgoing/reject incoming."""
        with self._lock:
            if self._state == CallState.IDLE:
                logger.warning("Cannot hangup: already idle")
                return False

            self._reset()
            return True

    def call_timeout(self) -> bool:
        """Handle call timeout (ringing too long, etc.)."""
        with self._lock:
            if self._state in (CallState.CALLING, CallState.RINGING):
                logger.info("Call timed out")
                self._reset()
                return True
            return False

    def _reset(self):
        """Reset to idle state."""
        self._peer_id = None
        self._peer_ip = None
        self._peer_port = None
        self._call_start_time = None
        self._set_state(CallState.IDLE)

    def get_call_duration(self) -> Optional[float]:
        """Get duration of current call/ringing in seconds."""
        if self._call_start_time:
            return time.time() - self._call_start_time
        return None
