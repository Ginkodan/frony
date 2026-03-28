"""
Frone - UDP Audio Streaming

Handles full-duplex audio streaming between phones.
Uses raw PCM audio (16kHz, 16-bit, mono) over UDP.
"""

import socket
import threading
import logging
from collections import deque
from typing import Optional

import numpy as np
import sounddevice as sd

import config

logger = logging.getLogger(__name__)


class AudioStreamer:
    JITTER_PACKETS = 4  # Pre-buffer ~80ms before starting playback

    def __init__(self):
        self._running = False
        self._peer_ip: Optional[str] = None
        self._peer_port: Optional[int] = None

        self._send_thread: Optional[threading.Thread] = None
        self._recv_thread: Optional[threading.Thread] = None

        self._send_socket: Optional[socket.socket] = None
        self._recv_socket: Optional[socket.socket] = None

        # Audio stream references
        self._input_stream: Optional[sd.InputStream] = None
        self._output_stream: Optional[sd.OutputStream] = None

        # Ringtone
        self._ringtone_running = False
        self._ringtone_thread: Optional[threading.Thread] = None

    def start(self, peer_ip: str, peer_port: int):
        """Start bidirectional audio streaming to peer."""
        if self._running:
            logger.warning("Audio stream already running")
            return

        self._peer_ip = peer_ip
        self._peer_port = peer_port
        self._running = True

        # Create sockets
        self._send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._recv_socket.bind(("0.0.0.0", config.AUDIO_PORT))
        self._recv_socket.settimeout(0.1)  # 100ms timeout for clean shutdown

        # Start threads
        self._send_thread = threading.Thread(target=self._send_loop, daemon=True)
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)

        self._send_thread.start()
        self._recv_thread.start()

        logger.info(f"Audio streaming started to {peer_ip}:{peer_port}")

    def stop(self):
        """Stop audio streaming."""
        if not self._running:
            return

        self.stop_ringtone()
        self._running = False

        # Wait for threads to finish
        if self._send_thread:
            self._send_thread.join(timeout=1.0)
        if self._recv_thread:
            self._recv_thread.join(timeout=1.0)

        # Close sockets
        if self._send_socket:
            self._send_socket.close()
        if self._recv_socket:
            self._recv_socket.close()

        # Close audio streams
        if self._input_stream:
            self._input_stream.close()
        if self._output_stream:
            self._output_stream.close()

        self._send_socket = None
        self._recv_socket = None
        self._input_stream = None
        self._output_stream = None
        self._peer_ip = None
        self._peer_port = None

        logger.info("Audio streaming stopped")

    def _send_loop(self):
        """Capture audio from microphone and send via UDP."""
        try:
            self._input_stream = sd.InputStream(
                samplerate=config.SAMPLE_RATE,
                channels=config.CHANNELS,
                dtype=np.int16,
                blocksize=config.CHUNK_SAMPLES,
                device=config.AUDIO_DEVICE,
            )
            self._input_stream.start()

            logger.debug("Audio capture started")

            while self._running:
                try:
                    # Read audio chunk
                    data, overflowed = self._input_stream.read(config.CHUNK_SAMPLES)
                    if overflowed:
                        logger.debug("Audio input buffer overflowed")

                    # Send raw bytes
                    self._send_socket.sendto(
                        data.tobytes(),
                        (self._peer_ip, self._peer_port)
                    )

                except Exception as e:
                    if self._running:
                        logger.error(f"Send error: {e}")

        except Exception as e:
            logger.error(f"Failed to start audio capture: {e}")
        finally:
            if self._input_stream:
                self._input_stream.stop()

    def _recv_loop(self):
        """Receive audio via UDP, buffer for jitter smoothing, play through speaker."""
        expected_bytes = config.CHUNK_SAMPLES * 2  # 16-bit = 2 bytes per sample
        buf: deque = deque(maxlen=20)  # cap at ~400ms to bound latency
        buf_ready = threading.Event()

        def _socket_reader():
            while self._running:
                try:
                    data, _ = self._recv_socket.recvfrom(expected_bytes + 100)
                    buf.append(data)
                    if not buf_ready.is_set() and len(buf) >= self.JITTER_PACKETS:
                        buf_ready.set()
                except socket.timeout:
                    continue
                except Exception as e:
                    if self._running:
                        logger.error(f"Socket receive error: {e}")

        reader = threading.Thread(target=_socket_reader, daemon=True)
        reader.start()

        # Wait for jitter buffer to fill before opening audio output
        if not buf_ready.wait(timeout=5.0):
            logger.warning("Jitter buffer pre-fill timed out — starting anyway")

        try:
            self._output_stream = sd.OutputStream(
                samplerate=config.SAMPLE_RATE,
                channels=config.CHANNELS,
                dtype=np.int16,
                blocksize=config.CHUNK_SAMPLES,
                device=config.AUDIO_DEVICE,
            )
            self._output_stream.start()
            logger.debug("Audio playback started")

            silence = np.zeros(config.CHUNK_SAMPLES, dtype=np.int16)

            while self._running:
                if buf:
                    data = buf.popleft()
                    audio_data = np.frombuffer(data, dtype=np.int16)
                    if len(audio_data) < config.CHUNK_SAMPLES:
                        audio_data = np.pad(audio_data, (0, config.CHUNK_SAMPLES - len(audio_data)))
                    elif len(audio_data) > config.CHUNK_SAMPLES:
                        audio_data = audio_data[:config.CHUNK_SAMPLES]
                else:
                    # Buffer underrun — play silence rather than stalling
                    audio_data = silence

                self._output_stream.write(audio_data.reshape(-1, 1))

        except Exception as e:
            logger.error(f"Failed to start audio playback: {e}")
        finally:
            if self._output_stream:
                self._output_stream.stop()

    def start_ringtone(self):
        """Play a ringing tone (2s on / 4s off) until stopped."""
        if self._ringtone_running:
            return
        self._ringtone_running = True
        self._ringtone_thread = threading.Thread(target=self._ringtone_loop, daemon=True)
        self._ringtone_thread.start()

    def stop_ringtone(self):
        """Stop the ringtone."""
        self._ringtone_running = False
        if self._ringtone_thread:
            self._ringtone_thread.join(timeout=2.0)
            self._ringtone_thread = None

    def _ringtone_loop(self):
        """Generate and play a standard two-tone telephone ring (440 + 480 Hz)."""
        sr = config.SAMPLE_RATE
        chunk = config.CHUNK_SAMPLES

        t = np.linspace(0, 2.0, int(2.0 * sr), endpoint=False)
        tone = ((np.sin(2 * np.pi * 440 * t) + np.sin(2 * np.pi * 480 * t)) / 2 * 16000).astype(np.int16)
        silence_total = int(4.0 * sr)

        try:
            stream = sd.OutputStream(
                samplerate=sr, channels=1, dtype=np.int16,
                blocksize=chunk, device=config.AUDIO_DEVICE,
            )
            stream.start()
            try:
                while self._ringtone_running:
                    # 2s ring
                    for i in range(0, len(tone), chunk):
                        if not self._ringtone_running:
                            break
                        frame = tone[i:i + chunk]
                        if len(frame) < chunk:
                            frame = np.pad(frame, (0, chunk - len(frame)))
                        stream.write(frame.reshape(-1, 1))
                    # 4s silence
                    silence_frame = np.zeros((chunk, 1), dtype=np.int16)
                    for _ in range(silence_total // chunk):
                        if not self._ringtone_running:
                            break
                        stream.write(silence_frame)
            finally:
                stream.stop()
                stream.close()
        except Exception as e:
            logger.error(f"Ringtone error: {e}")


# Standalone test mode
if __name__ == "__main__":
    import sys
    import argparse

    logging.basicConfig(level=logging.DEBUG)

    parser = argparse.ArgumentParser(description="Audio stream test")
    parser.add_argument("--send", metavar="IP", help="Send audio to IP")
    parser.add_argument("--receive", action="store_true", help="Receive and play audio")
    parser.add_argument("--port", type=int, default=5000, help="UDP port")
    args = parser.parse_args()

    streamer = AudioStreamer()

    if args.send:
        print(f"Sending audio to {args.send}:{args.port}")
        print("Press Ctrl+C to stop")
        streamer.start(args.send, args.port)
        try:
            while True:
                pass
        except KeyboardInterrupt:
            streamer.stop()

    elif args.receive:
        print(f"Receiving audio on port {args.port}")
        print("Press Ctrl+C to stop")
        # For receive-only, we still need a peer to "send to" (even if not used)
        streamer.start("127.0.0.1", args.port)
        try:
            while True:
                pass
        except KeyboardInterrupt:
            streamer.stop()

    else:
        parser.print_help()
