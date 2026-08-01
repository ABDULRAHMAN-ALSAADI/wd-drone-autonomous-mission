#!/usr/bin/env python3
"""Show the Pi-side annotated OpenCV stream on this laptop."""
from __future__ import annotations

import argparse
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


SOI = b"\xff\xd8"
EOI = b"\xff\xd9"
WINDOW_NAME = "WD Drone Pi OpenCV Monitor"
STATUS_FONT = cv2.FONT_HERSHEY_SIMPLEX


def start_tunnel(
    ssh_alias: str,
    local_port: int,
    remote_port: int,
    timeout_s: float,
) -> subprocess.Popen:
    command = [
        "ssh",
        "-T",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=3",
        "-N",
        "-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
        ssh_alias,
    ]
    print(f"[SSH TUNNEL] {ssh_alias}:127.0.0.1:{remote_port} -> 127.0.0.1:{local_port}")
    process = subprocess.Popen(command)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"SSH tunnel exited with code {process.returncode}. "
                f"First verify: ssh {ssh_alias} true"
            )
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=0.25):
                print("[SSH TUNNEL] ready")
                return process
        except OSError:
            time.sleep(0.1)

    stop_process(process)
    raise RuntimeError(
        f"SSH tunnel did not become ready within {timeout_s:.1f}s. "
        f"First verify: ssh {ssh_alias} true"
    )


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=2.0)
    except Exception:
        process.kill()
        process.wait(timeout=2.0)


def save_snapshot(frame: np.ndarray, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"mission-stream-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
    cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    return path


def pop_latest_jpeg(buffer: bytearray) -> Optional[bytes]:
    """Return the newest complete JPEG and discard older complete frames."""
    latest: Optional[bytes] = None
    consumed = 0
    partial_start: Optional[int] = None
    search_from = 0
    while True:
        start = buffer.find(SOI, search_from)
        if start < 0:
            break
        end = buffer.find(EOI, start + len(SOI))
        if end < 0:
            partial_start = start
            break
        frame_end = end + len(EOI)
        latest = bytes(buffer[start:frame_end])
        consumed = frame_end
        search_from = frame_end
    trim_to = partial_start if partial_start is not None else consumed
    if trim_to:
        del buffer[:trim_to]
    elif len(buffer) > 4_000_000:
        last_start = buffer.rfind(SOI)
        buffer[:] = buffer[last_start:] if last_start >= 0 else b""
    return latest


def recent_fps(frame_times: deque[float]) -> float:
    if len(frame_times) < 2:
        return 0.0
    return (len(frame_times) - 1) / max(1e-6, frame_times[-1] - frame_times[0])


def draw_receiver_status(
    frame: np.ndarray,
    fps: float,
    status: str,
    frame_age_s: Optional[float],
    reconnects: int,
) -> np.ndarray:
    output = frame.copy()
    age = "--" if frame_age_s is None else f"{frame_age_s:.1f}s"
    text = f"Laptop RX {fps:.1f} FPS | {status} | age {age} | reconnects {reconnects}"
    colour = (70, 230, 70) if status == "LIVE" else (40, 80, 255)
    (text_width, text_height), baseline = cv2.getTextSize(text, STATUS_FONT, 0.48, 1)
    y = output.shape[0] - 12
    cv2.rectangle(
        output,
        (8, y - text_height - baseline - 8),
        (min(output.shape[1] - 8, text_width + 18), y + baseline + 2),
        (20, 20, 20),
        -1,
    )
    cv2.putText(output, text, (13, y), STATUS_FONT, 0.48, colour, 1, cv2.LINE_AA)
    return output


def waiting_frame(width: int, height: int, message: str) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(
        frame,
        message,
        (30, max(60, height // 2)),
        STATUS_FONT,
        0.75,
        (230, 230, 230),
        2,
        cv2.LINE_AA,
    )
    return frame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-alias", default="pi5")
    parser.add_argument(
        "--direct-host",
        help="Connect directly to this Pi IP instead of creating an SSH tunnel",
    )
    parser.add_argument("--local-port", type=int, default=15602)
    parser.add_argument("--remote-port", type=int, default=5602)
    parser.add_argument("--tunnel-timeout", type=float, default=20.0)
    parser.add_argument("--read-timeout", type=float, default=2.0)
    parser.add_argument("--reconnect-delay", type=float, default=0.5)
    parser.add_argument("--display-width", type=int, default=1280)
    parser.add_argument("--display-height", type=int, default=720)
    parser.add_argument("--snapshot-dir", type=Path, default=Path("data/mission_snapshots"))
    args = parser.parse_args()

    tunnel: subprocess.Popen | None = None
    tunnel_required = not bool(args.direct_host)
    if args.direct_host:
        url = f"http://{args.direct_host}:{args.remote_port}/stream.mjpg"
        print(f"[DIRECT STREAM] Pi {args.direct_host}:{args.remote_port}")
    else:
        url = f"http://127.0.0.1:{args.local_port}/stream.mjpg"
    print(f"[MISSION VIDEO] {url}")
    print("[KEYS] q/esc quit | s snapshot")
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, args.display_width, args.display_height)
    response = None
    buffer = bytearray()
    last_frame: Optional[np.ndarray] = None
    last_frame_at: Optional[float] = None
    frame_times: deque[float] = deque(maxlen=120)
    reconnects = 0
    status = "CONNECTING"
    try:
        while True:
            if tunnel_required and (tunnel is None or tunnel.poll() is not None):
                if tunnel is not None:
                    print("[MISSION VIDEO WARNING] SSH tunnel stopped; reconnecting")
                    reconnects += 1
                if response is not None:
                    response.close()
                    response = None
                try:
                    tunnel = start_tunnel(
                        args.ssh_alias,
                        args.local_port,
                        args.remote_port,
                        args.tunnel_timeout,
                    )
                except RuntimeError as exc:
                    tunnel = None
                    status = "RECONNECTING"
                    print(f"[MISSION VIDEO WARNING] {exc}")

            stream_reachable = not tunnel_required or tunnel is not None
            if response is None and stream_reachable:
                try:
                    response = urllib.request.urlopen(url, timeout=args.read_timeout)
                    print("[MISSION VIDEO] stream connected")
                    status = "LIVE"
                    buffer.clear()
                except (OSError, TimeoutError, urllib.error.URLError) as exc:
                    status = "RECONNECTING"
                    print(f"[MISSION VIDEO WARNING] waiting for stream: {exc}")
                    time.sleep(max(0.05, args.reconnect_delay))

            if response is not None:
                try:
                    read_chunk = getattr(response, "read1", response.read)
                    chunk = read_chunk(65536)
                    if not chunk:
                        raise ConnectionError("stream ended")
                    buffer.extend(chunk)
                    jpeg = pop_latest_jpeg(buffer)
                    if jpeg is not None:
                        frame = cv2.imdecode(
                            np.frombuffer(jpeg, dtype=np.uint8),
                            cv2.IMREAD_COLOR,
                        )
                        if frame is not None:
                            last_frame = frame
                            last_frame_at = time.monotonic()
                            frame_times.append(last_frame_at)
                            status = "LIVE"
                except (OSError, TimeoutError, ConnectionError) as exc:
                    print(f"[MISSION VIDEO WARNING] stream paused: {exc}; reconnecting")
                    try:
                        response.close()
                    except Exception:
                        pass
                    response = None
                    buffer.clear()
                    frame_times.clear()
                    reconnects += 1
                    status = "RECONNECTING"

            now = time.monotonic()
            while frame_times and now - frame_times[0] > 3.0:
                frame_times.popleft()
            source = last_frame
            if source is None:
                source = waiting_frame(
                    args.display_width,
                    args.display_height,
                    "Waiting for Pi mission stream...",
                )
            display = cv2.resize(
                source,
                (args.display_width, args.display_height),
                interpolation=cv2.INTER_AREA,
            )
            frame_age_s = None if last_frame_at is None else now - last_frame_at
            display = draw_receiver_status(
                display,
                recent_fps(frame_times),
                status,
                frame_age_s,
                reconnects,
            )
            cv2.imshow(WINDOW_NAME, display)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("s") and last_frame is not None:
                print(f"[SNAPSHOT] {save_snapshot(last_frame, args.snapshot_dir)}")
    except KeyboardInterrupt:
        print("[MISSION VIDEO] stopped by operator")
    finally:
        if response is not None:
            response.close()
        if tunnel is not None:
            stop_process(tunnel)
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
