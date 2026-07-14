#!/usr/bin/env python3
"""Show the Pi mission controller's exact annotated frame on this laptop."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np


SOI = b"\xff\xd8"
EOI = b"\xff\xd9"
WINDOW_NAME = "WD Drone Live Mission"


def start_tunnel(ssh_alias: str, local_port: int, remote_port: int) -> subprocess.Popen:
    command = [
        "ssh",
        "-T",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=5",
        "-N",
        "-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
        ssh_alias,
    ]
    process = subprocess.Popen(command, start_new_session=True)
    time.sleep(0.4)
    if process.poll() is not None:
        raise RuntimeError(f"SSH tunnel exited with code {process.returncode}")
    return process


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2.0)
    except Exception:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2.0)


def save_snapshot(frame: np.ndarray, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"mission-stream-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
    cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-alias", default="pi5")
    parser.add_argument("--local-port", type=int, default=15602)
    parser.add_argument("--remote-port", type=int, default=5602)
    parser.add_argument("--display-width", type=int, default=1280)
    parser.add_argument("--display-height", type=int, default=720)
    parser.add_argument("--snapshot-dir", type=Path, default=Path("data/mission_snapshots"))
    args = parser.parse_args()

    tunnel = start_tunnel(args.ssh_alias, args.local_port, args.remote_port)
    url = f"http://127.0.0.1:{args.local_port}/stream.mjpg"
    print(f"[MISSION VIDEO] {url}")
    print("[KEYS] q/esc quit | s snapshot")
    try:
        try:
            response = urllib.request.urlopen(url, timeout=5.0)
        except Exception as exc:
            print("[MISSION VIDEO ERROR] The Pi mission controller stream is not available.")
            print("Start Mission 2 on the Pi first, then run this viewer again.")
            print(f"Details: {exc}")
            return 2

        buffer = bytearray()
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_NAME, args.display_width, args.display_height)
        read_chunk = getattr(response, "read1", response.read)
        while True:
            chunk = read_chunk(65536)
            if not chunk:
                print("[MISSION VIDEO] stream ended")
                return 2
            buffer.extend(chunk)
            start = buffer.find(SOI)
            end = buffer.find(EOI, start + 2)
            if start < 0 or end < 0:
                if len(buffer) > 4_000_000:
                    buffer.clear()
                continue
            jpeg = bytes(buffer[start : end + 2])
            del buffer[: end + 2]
            frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            display = cv2.resize(
                frame,
                (args.display_width, args.display_height),
                interpolation=cv2.INTER_LINEAR,
            )
            cv2.imshow(WINDOW_NAME, display)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("s"):
                print(f"[SNAPSHOT] {save_snapshot(frame, args.snapshot_dir)}")
    finally:
        stop_process(tunnel)
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
