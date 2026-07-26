"""MJPEG server for the annotated laptop mission view."""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

import cv2


class MjpegFrameServer:
    """Serve the mission overlay without encoding in the mission loop."""

    def __init__(self, display_config: dict[str, Any]) -> None:
        self.enabled = bool(display_config.get("mjpeg_stream_enabled", False))
        self.bind = str(display_config.get("mjpeg_stream_bind", "127.0.0.1"))
        self.port = int(display_config.get("mjpeg_stream_port", 5602))
        self.max_fps = float(display_config.get("mjpeg_stream_fps", 10.0))
        self.quality = int(display_config.get("mjpeg_stream_quality", 65))
        self.width = int(display_config.get("mjpeg_stream_width", 960))
        self._condition = threading.Condition()
        self._raw_frame: Optional[Any] = None
        self._raw_sequence = 0
        self._raw_consumed_sequence = 0
        self._jpeg: Optional[bytes] = None
        self._sequence = 0
        self._stopping = False
        self._last_encode_at = 0.0
        self._submitted_count = 0
        self._encoded_count = 0
        self._overwritten_count = 0
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._encoder_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.enabled:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path not in {"/", "/stream.mjpg"}:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                sequence = -1
                try:
                    while True:
                        with owner._condition:
                            owner._condition.wait_for(
                                lambda: owner._stopping or owner._sequence != sequence,
                                timeout=1.0,
                            )
                            if owner._stopping:
                                return
                            if owner._jpeg is None or owner._sequence == sequence:
                                continue
                            jpeg = owner._jpeg
                            sequence = owner._sequence
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    return

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        try:
            self._server = ThreadingHTTPServer((self.bind, self.port), Handler)
            self._server.daemon_threads = True
        except OSError as exc:
            self.enabled = False
            print(f"[VIDEO STREAM WARNING] disabled: {exc}")
            return
        self._encoder_thread = threading.Thread(
            target=self._encode_loop,
            name="mission-video-encoder",
            daemon=True,
        )
        self._encoder_thread.start()
        self._thread = threading.Thread(target=self._server.serve_forever, name="mission-video", daemon=True)
        self._thread.start()
        print(f"[VIDEO STREAM] http://{self.bind}:{self.port}/stream.mjpg via SSH tunnel")

    def publish(self, frame: Any) -> None:
        if not self.enabled:
            return
        with self._condition:
            if self._raw_sequence != self._raw_consumed_sequence:
                self._overwritten_count += 1
            self._raw_frame = frame
            self._raw_sequence += 1
            self._submitted_count += 1
            self._condition.notify_all()

    def _encode_loop(self) -> None:
        minimum_interval = 1.0 / max(0.1, self.max_fps)
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._stopping or self._raw_sequence != self._raw_consumed_sequence,
                    timeout=1.0,
                )
                if self._stopping:
                    return
                if self._raw_frame is None:
                    continue
                frame = self._raw_frame
                self._raw_consumed_sequence = self._raw_sequence

            delay = minimum_interval - (time.monotonic() - self._last_encode_at)
            if delay > 0:
                time.sleep(delay)
            output = frame
            if output.shape[1] > self.width:
                scale = self.width / output.shape[1]
                output = cv2.resize(
                    output,
                    (self.width, max(1, round(output.shape[0] * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            ok, encoded = cv2.imencode(
                ".jpg",
                output,
                [int(cv2.IMWRITE_JPEG_QUALITY), self.quality],
            )
            self._last_encode_at = time.monotonic()
            if not ok:
                continue
            with self._condition:
                self._jpeg = encoded.tobytes()
                self._sequence += 1
                self._encoded_count += 1
                self._condition.notify_all()

    def metrics(self) -> dict[str, int]:
        with self._condition:
            return {
                "submitted": self._submitted_count,
                "encoded": self._encoded_count,
                "overwritten": self._overwritten_count,
            }

    def stop(self) -> None:
        if not self.enabled and self._server is None:
            return
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._encoder_thread is not None:
            self._encoder_thread.join(timeout=2.0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
