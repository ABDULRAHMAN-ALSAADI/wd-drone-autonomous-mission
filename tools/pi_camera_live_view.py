#!/usr/bin/env python3
"""Live Raspberry Pi Camera Module 3 viewer for the Ubuntu laptop.

Run this on the laptop. It starts ``rpicam-vid`` on the Pi through SSH, decodes
the MJPEG stream locally, and opens an OpenCV window with the same detector used
by the mission. This keeps the Pi light: it only captures camera frames.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import select
import shlex
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
MISSION_DIR = ROOT / "target_mission_v2"
sys.path.insert(0, str(MISSION_DIR))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from camera_sources import build_rpicam_mjpeg_command  # noqa: E402
from vision import Detection, HitTracker, create_detector  # noqa: E402


class RemoteMjpegCamera:
    SOI = b"\xff\xd8"
    EOI = b"\xff\xd9"

    def __init__(self, ssh_alias: str, camera_config: dict, remote_dir: str) -> None:
        command = build_rpicam_mjpeg_command(camera_config)
        remote_command = f"cd {quote_remote_path(remote_dir)} && exec {shlex.join(command)}"
        self.cmd = ["ssh", "-T", ssh_alias, remote_command]
        self.buffer = bytearray()
        self.chunk_size = int(camera_config.get("read_chunk_bytes", 65536))
        self.process = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=None,
            start_new_session=True,
        )
        if self.process.stdout is None:
            raise RuntimeError("Could not open SSH camera stream stdout")

    def release(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=2.0)
        except Exception:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except Exception:
                pass
            self.process.wait(timeout=2.0)

    def returncode(self) -> Optional[int]:
        return self.process.poll()

    def _pop_latest_jpeg(self) -> Optional[bytes]:
        latest = None
        while True:
            start = self.buffer.find(self.SOI)
            if start < 0:
                if len(self.buffer) > self.chunk_size:
                    self.buffer.clear()
                return latest
            if start > 0:
                del self.buffer[:start]
            end = self.buffer.find(self.EOI, 2)
            if end < 0:
                return latest
            frame_end = end + len(self.EOI)
            latest = bytes(self.buffer[:frame_end])
            del self.buffer[:frame_end]

    def read(self, timeout_s: float = 2.0) -> tuple[bool, Optional[np.ndarray]]:
        deadline = time.monotonic() + timeout_s
        fd = self.process.stdout.fileno()
        while time.monotonic() < deadline:
            jpeg = self._pop_latest_jpeg()
            if jpeg is not None:
                frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    return True, frame
                continue
            if self.process.poll() is not None:
                return False, None
            ready, _, _ = select.select([fd], [], [], max(0.0, min(0.1, deadline - time.monotonic())))
            if not ready:
                continue
            chunk = os.read(fd, self.chunk_size)
            if not chunk:
                return False, None
            self.buffer.extend(chunk)
        return False, None


def quote_remote_path(path: str) -> str:
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if "camera" not in config or "vision" not in config:
        raise ValueError(f"{path} must contain camera and vision sections")
    return config


def fit_text(text: str, max_width: int, scale: float) -> str:
    if cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0] <= max_width:
        return text
    clipped = text
    while len(clipped) > 4 and cv2.getTextSize(clipped + "...", cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0] > max_width:
        clipped = clipped[:-1]
    return clipped + "..."


def resize_for_vision(frame: np.ndarray, process_width: int) -> np.ndarray:
    if process_width <= 0 or frame.shape[1] == process_width:
        return frame
    scale = process_width / frame.shape[1]
    return cv2.resize(frame, (process_width, max(1, round(frame.shape[0] * scale))), interpolation=cv2.INTER_AREA)


def scale_detection(item: Detection, source_shape: tuple[int, ...], destination_shape: tuple[int, ...]) -> Detection:
    scale_x = destination_shape[1] / source_shape[1]
    scale_y = destination_shape[0] / source_shape[0]
    return replace(
        item,
        center_x=round(item.center_x * scale_x),
        center_y=round(item.center_y * scale_y),
        area_px=round(item.area_px * scale_x * scale_y, 1),
        bbox_x=round(item.bbox_x * scale_x),
        bbox_y=round(item.bbox_y * scale_y),
        bbox_w=round(item.bbox_w * scale_x),
        bbox_h=round(item.bbox_h * scale_y),
    )


def draw_overlay(
    frame: np.ndarray,
    detections: list[Detection],
    fps_recent: float,
    fps_avg: float,
    frame_count: int,
    mode: str,
    mask_coverage: dict[str, float],
    hit_status: dict[str, int],
    required_hits: int,
) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]
    image_center = (w // 2, h // 2)
    cv2.drawMarker(out, image_center, (255, 255, 255), cv2.MARKER_CROSS, 24, 1)

    for item in detections:
        colour = (0, 0, 255) if item.target == "red_triangle" else (255, 0, 0)
        cv2.rectangle(out, (item.bbox_x, item.bbox_y), (item.bbox_x + item.bbox_w, item.bbox_y + item.bbox_h), colour, 2)
        cv2.circle(out, (item.center_x, item.center_y), 5, colour, -1)
        cv2.line(out, image_center, (item.center_x, item.center_y), colour, 2, cv2.LINE_AA)
        error = math.hypot(item.center_x - image_center[0], item.center_y - image_center[1])
        label = f"{item.target} conf={item.confidence:.2f} err={error:.0f}px v={item.vertices}"
        cv2.putText(out, label, (item.bbox_x, max(18, item.bbox_y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)

    target_names = ",".join(item.target for item in detections) if detections else "none"
    confirmed = ",".join(target for target, hits in hit_status.items() if hits >= required_hits) or "none"
    scale = 0.48
    lines = [
        "Mission TEST | Mode N/A | WP N/A",
        "Action: standalone OpenCV camera and vision validation",
        f"Candidate: {target_names} | Confirmed: {confirmed}",
        f"Evidence: triangle {min(required_hits, hit_status.get('red_triangle', 0))}/{required_hits} | hexagon {min(required_hits, hit_status.get('blue_hexagon', 0))}/{required_hits}",
        "Payload: disabled | Mission telemetry: not connected",
        f"Vision: {mode} | FPS {fps_recent:.1f} recent / {fps_avg:.1f} avg | frames {frame_count}",
        f"Camera: {w}x{h}",
        f"Mask coverage: red {100.0 * mask_coverage.get('red', 0.0):.1f}% | blue {100.0 * mask_coverage.get('blue', 0.0):.1f}%",
        "Keys: q/esc quit | s snapshot | m masks",
    ]
    lines = [fit_text(line, w - 40, scale) for line in lines]
    line_h = 20
    panel_w = min(w - 16, max(cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0] for line in lines) + 20)
    panel_h = 14 + line_h * len(lines)
    panel = out.copy()
    cv2.rectangle(panel, (8, 8), (8 + panel_w, 8 + panel_h), (0, 0, 0), -1)
    cv2.addWeighted(panel, 0.48, out, 0.52, 0, out)
    y = 30
    for line in lines:
        cv2.putText(out, line, (18, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA)
        y += line_h
    return out


def write_snapshot(directory: Path, frame: np.ndarray) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"pi-camera-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
    cv2.imwrite(str(path), frame)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Open a live laptop window for the Pi Camera Module 3 and mission vision detector.")
    parser.add_argument("--config", type=Path, default=ROOT / "real_mission/parameter_config/mission2_target_payload.json")
    parser.add_argument("--ssh-alias", default="pi5")
    parser.add_argument("--remote-dir", default="~/FOR_COMP/wd-drone-autonomous-mission")
    parser.add_argument("--seconds", type=float, default=0.0, help="0 means run until q/esc")
    parser.add_argument("--raw-only", action="store_true", help="show camera only, without detector work")
    parser.add_argument("--show-masks", action="store_true")
    parser.add_argument(
        "--read-timeout",
        type=float,
        default=0.25,
        help="maximum seconds to wait for a frame before refreshing the GUI",
    )
    parser.add_argument("--camera-width", type=int, default=None)
    parser.add_argument("--camera-height", type=int, default=None)
    parser.add_argument("--camera-fps", type=float, default=None)
    parser.add_argument("--process-width", type=int, default=None)
    parser.add_argument(
        "--autofocus-mode",
        choices=("manual", "auto", "continuous"),
        default=None,
    )
    parser.add_argument("--snapshot-dir", type=Path, default=ROOT / "data/camera_snapshots")
    args = parser.parse_args()

    config = load_config(args.config)
    camera_config = dict(config["camera"])
    vision_config = dict(config["vision"])
    if args.camera_width is not None:
        camera_config["width"] = args.camera_width
    if args.camera_height is not None:
        camera_config["height"] = args.camera_height
    if args.camera_fps is not None:
        camera_config["framerate"] = args.camera_fps
    if args.process_width is not None:
        vision_config["process_width"] = args.process_width
    if args.autofocus_mode is not None:
        camera_config["autofocus_mode"] = args.autofocus_mode
        if args.autofocus_mode != "manual":
            camera_config.pop("lens_position", None)

    for name in ("width", "height"):
        if int(camera_config.get(name, 0)) <= 0:
            raise ValueError(f"camera {name} must be positive")
    if float(camera_config.get("framerate", 0.0)) <= 0:
        raise ValueError("camera FPS must be positive")
    if int(vision_config.get("process_width", 0)) <= 0:
        raise ValueError("vision process width must be positive")

    read_timeout_s = max(0.02, args.read_timeout)
    camera = RemoteMjpegCamera(args.ssh_alias, camera_config, args.remote_dir)
    detector = None if args.raw_only else create_detector(vision_config)
    required_hits = int(vision_config.get("required_hits", 3))
    tracker = HitTracker(
        required_hits=required_hits,
        window_s=float(vision_config.get("confirmation_window_s", 1.5)),
        max_jump_px=float(vision_config.get("max_lock_jump_px", 160.0)),
    )
    started_at = time.monotonic()
    frame_times: deque[float] = deque(maxlen=240)
    frames = 0
    masks_visible = bool(args.show_masks)
    masks_open = False
    print("[LIVE CAMERA] opening window; press q or esc to quit, s to save a snapshot, m to toggle masks")

    try:
        while args.seconds <= 0 or time.monotonic() - started_at < args.seconds:
            ok, frame = camera.read(timeout_s=read_timeout_s)
            now = time.monotonic()
            if not ok or frame is None:
                returncode = camera.returncode()
                if returncode is not None:
                    print(f"[LIVE CAMERA ERROR] camera stream process exited with code {returncode}")
                    return 2
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                time.sleep(0.02)
                continue
            frames += 1
            frame_times.append(now)
            recent_fps = 0.0
            if len(frame_times) >= 2:
                recent_fps = (len(frame_times) - 1) / max(1e-6, frame_times[-1] - frame_times[0])
            avg_fps = frames / max(1e-6, now - started_at)
            detections: list[Detection] = []
            masks = {"red": np.zeros(frame.shape[:2], dtype=np.uint8), "blue": np.zeros(frame.shape[:2], dtype=np.uint8)}
            hit_status = {"red_triangle": 0, "blue_hexagon": 0}
            if detector is not None:
                process_frame = resize_for_vision(frame, int(vision_config.get("process_width", frame.shape[1])))
                process_detections, masks = detector.search(process_frame)
                tracker.update(process_detections, {"red_triangle", "blue_hexagon"}, now)
                hit_status = tracker.status(now)
                detections = [scale_detection(item, process_frame.shape, frame.shape) for item in process_detections]
            mask_coverage = {name: cv2.countNonZero(mask) / float(mask.size) for name, mask in masks.items()}
            mode = "raw" if detector is None else str(vision_config.get("backend", "vision"))
            view = draw_overlay(frame, detections, recent_fps, avg_fps, frames, mode, mask_coverage, hit_status, required_hits)
            cv2.imshow("WD Drone Pi Camera Live Vision", view)
            if masks_visible:
                cv2.imshow("Pi Camera Red Mask", masks["red"])
                cv2.imshow("Pi Camera Blue Mask", masks["blue"])
                masks_open = True
            elif masks_open:
                cv2.destroyWindow("Pi Camera Red Mask")
                cv2.destroyWindow("Pi Camera Blue Mask")
                masks_open = False

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("m"):
                masks_visible = not masks_visible
            if key == ord("s"):
                path = write_snapshot(args.snapshot_dir, view)
                print(f"[SNAPSHOT] {path}")
    finally:
        camera.release()
        cv2.destroyAllWindows()

    if frames == 0:
        print("[LIVE CAMERA ERROR] no frames received")
        return 2
    print(f"[LIVE CAMERA OK] frames={frames} avg_fps={frames / max(1e-6, time.monotonic() - started_at):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
