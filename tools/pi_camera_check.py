#!/usr/bin/env python3
"""Lightweight Raspberry Pi camera health and FPS checker.

This tool is safe for bench use: it only opens the camera, reads frames, prints
FPS/temperature/throttling, and saves a preview image so a headless Pi can show
what the camera sees.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
MISSION_DIR = ROOT / "target_mission_v2"
sys.path.insert(0, str(MISSION_DIR))

import cv2  # noqa: E402

from camera_sources import open_camera  # noqa: E402


def vcgencmd(*args: str) -> str:
    if shutil.which("vcgencmd") is None:
        return "n/a"
    try:
        result = subprocess.run(["vcgencmd", *args], check=False, capture_output=True, text=True, timeout=2.0)
    except Exception as exc:
        return f"error:{exc}"
    text = (result.stdout or result.stderr).strip()
    return text or "n/a"


def draw_status(frame, text_lines: list[str]):
    out = frame.copy()
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.52
    line_h = 22
    width = max(cv2.getTextSize(line, font, scale, 1)[0][0] for line in text_lines) + 20
    height = 14 + line_h * len(text_lines)
    panel = out.copy()
    cv2.rectangle(panel, (8, 8), (8 + width, 8 + height), (0, 0, 0), -1)
    cv2.addWeighted(panel, 0.45, out, 0.55, 0, out)
    y = 30
    for line in text_lines:
        cv2.putText(out, line, (18, y), font, scale, (255, 255, 255), 1, cv2.LINE_AA)
        y += line_h
    return out


def load_camera_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if "camera" not in config:
        raise ValueError(f"{path} has no camera section")
    return config["camera"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Raspberry Pi camera frame stream, FPS, and health.")
    parser.add_argument("--config", type=Path, default=ROOT / "real_mission/parameter_config/real_drone.json")
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--output", type=Path, default=Path("~/camera_tests/module3_live_latest.jpg"))
    parser.add_argument("--print-every-s", type=float, default=1.0)
    parser.add_argument("--save-every-s", type=float, default=1.0)
    parser.add_argument("--show", action="store_true", help="Open an OpenCV window when a desktop is available.")
    args = parser.parse_args()

    camera_config = load_camera_config(args.config)
    output = args.output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    camera = open_camera(camera_config)
    frame_times: deque[float] = deque(maxlen=240)
    started_at = time.monotonic()
    last_print_at = 0.0
    last_save_at = 0.0
    last_frame = None
    frames = 0

    try:
        while time.monotonic() - started_at < args.seconds:
            ok, frame = camera.read()
            now = time.monotonic()
            if not ok or frame is None:
                time.sleep(0.02)
                continue
            frames += 1
            frame_times.append(now)
            elapsed = max(1e-6, now - started_at)
            recent_fps = 0.0
            if len(frame_times) >= 2:
                recent_fps = (len(frame_times) - 1) / max(1e-6, frame_times[-1] - frame_times[0])
            avg_fps = frames / elapsed
            h, w = frame.shape[:2]
            temp = vcgencmd("measure_temp")
            throttled = vcgencmd("get_throttled")
            lines = [
                f"Pi Camera Module 3 | {w}x{h}",
                f"FPS recent {recent_fps:.1f} | avg {avg_fps:.1f} | frames {frames}",
                f"{temp} | {throttled}",
            ]
            last_frame = draw_status(frame, lines)
            if now - last_save_at >= args.save_every_s:
                cv2.imwrite(str(output), last_frame)
                last_save_at = now
            if now - last_print_at >= args.print_every_s:
                print(
                    f"[CAMERA] t={elapsed:.1f}s frame={w}x{h} "
                    f"fps={recent_fps:.1f} avg={avg_fps:.1f} frames={frames} "
                    f"{temp} {throttled} preview={output}"
                )
                last_print_at = now
            if args.show:
                cv2.imshow("Pi Camera Module 3 Check", last_frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    finally:
        if last_frame is not None:
            cv2.imwrite(str(output), last_frame)
        camera.release()
        cv2.destroyAllWindows()

    if frames == 0:
        print("[CAMERA ERROR] no frames received")
        return 2
    print(f"[CAMERA OK] frames={frames} preview={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
