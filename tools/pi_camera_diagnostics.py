#!/usr/bin/env python3
"""Diagnose Camera Module 3 directly on a Pi without MAVLink or payload code."""
from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

import cv2

from opencv_common import (
    LatestCameraReader,
    draw_panel,
    load_profile,
    save_json,
    vcgencmd,
)


DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "target_mission_v2/configs/opencv_camera_diagnostics.json"
)


def list_cameras() -> int:
    try:
        from picamera2 import Picamera2
    except ImportError:
        print("[CAMERA LIST ERROR] Picamera2 is not installed in this Python.")
        return 2
    cameras = Picamera2.global_camera_info()
    if not cameras:
        print("[CAMERA LIST] no cameras reported")
        return 2
    for index, info in enumerate(cameras):
        print(f"[CAMERA {index}] {info}")
    return 0


def metadata_value(metadata: dict[str, Any], name: str) -> str:
    value = metadata.get(name)
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--list-cameras", action="store_true")
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--save-every-s", type=float, default=0.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("~/camera_tests/opencv_diagnostics"),
    )
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    if args.list_cameras:
        return list_cameras()
    if args.seconds <= 0:
        raise ValueError("--seconds must be positive")

    config = load_profile(args.config)
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    reader = LatestCameraReader.from_config(config["camera"])
    reader.start()
    started = time.monotonic()
    processed_times: deque[float] = deque(maxlen=120)
    ages_ms: list[float] = []
    rows: list[dict[str, Any]] = []
    last_frame_id = 0
    last_report_at = 0.0
    last_save_at = 0.0
    temperature = vcgencmd("measure_temp")
    throttled = vcgencmd("get_throttled")
    writer = None

    try:
        while time.monotonic() - started < args.seconds:
            now = time.monotonic()
            frame = reader.latest()
            if frame is None or frame.frame_id == last_frame_id:
                if reader.failure:
                    raise RuntimeError(f"camera worker failed: {reader.failure}")
                time.sleep(0.002)
                continue
            last_frame_id = frame.frame_id
            processed_times.append(now)
            age_ms = frame.capture_age_s(now) * 1000.0
            ages_ms.append(age_ms)
            recent_fps = 0.0
            if len(processed_times) >= 2:
                recent_fps = (len(processed_times) - 1) / max(
                    1e-6, processed_times[-1] - processed_times[0]
                )
            if now - last_report_at >= 1.0:
                temperature = vcgencmd("measure_temp")
                throttled = vcgencmd("get_throttled")
                metadata = frame.metadata
                row = {
                    "elapsed_s": round(now - started, 3),
                    "frame_id": frame.frame_id,
                    "fps": round(recent_fps, 3),
                    "frame_age_ms": round(age_ms, 3),
                    "captured": reader.captured,
                    "overwritten": reader.overwritten,
                    "exposure_time_us": metadata.get("ExposureTime"),
                    "analogue_gain": metadata.get("AnalogueGain"),
                    "colour_gains": metadata.get("ColourGains"),
                    "lens_position": metadata.get("LensPosition"),
                    "af_state": metadata.get("AfState"),
                    "temperature": temperature,
                    "throttled": throttled,
                }
                rows.append(row)
                print(
                    "[CAMERA] "
                    f"id={frame.frame_id} {frame.width}x{frame.height} "
                    f"fps={recent_fps:.1f} age={age_ms:.1f}ms "
                    f"captured={reader.captured} overwritten={reader.overwritten} "
                    f"exposure={metadata_value(metadata, 'ExposureTime')}us "
                    f"gain={metadata_value(metadata, 'AnalogueGain')} "
                    f"lens={metadata_value(metadata, 'LensPosition')} "
                    f"AF={metadata_value(metadata, 'AfState')} "
                    f"{temperature} {throttled}"
                )
                last_report_at = now

            lines = [
                f"Camera diagnostic | {frame.source} | {frame.width}x{frame.height}",
                f"FPS {recent_fps:.1f} | age {age_ms:.1f}ms | frame {frame.frame_id}",
                f"Exposure {metadata_value(frame.metadata, 'ExposureTime')}us | gain {metadata_value(frame.metadata, 'AnalogueGain')}",
                f"Lens {metadata_value(frame.metadata, 'LensPosition')} | AF {metadata_value(frame.metadata, 'AfState')}",
                f"Captured {reader.captured} | overwritten {reader.overwritten}",
                f"{temperature} | {throttled}",
                "Camera only: MAVLink and payload are disabled",
            ]
            view = draw_panel(frame.image_bgr, lines)
            if args.save_every_s > 0 and now - last_save_at >= args.save_every_s:
                cv2.imwrite(
                    str(output_dir / f"raw-{frame.frame_id:08d}.png"),
                    frame.image_bgr,
                )
                last_save_at = now
            if args.record:
                if writer is None:
                    writer = cv2.VideoWriter(
                        str(output_dir / "camera-diagnostic.avi"),
                        cv2.VideoWriter_fourcc(*"MJPG"),
                        float(config["camera"].get("framerate", 30.0)),
                        (frame.width, frame.height),
                    )
                    if not writer.isOpened():
                        raise RuntimeError("could not open diagnostic video output")
                writer.write(frame.image_bgr)
            if args.show:
                cv2.imshow("WD Pi Camera Diagnostic", view)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    finally:
        reader.stop()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()

    if not ages_ms:
        print("[CAMERA ERROR] no frames captured")
        return 2
    fieldnames = list(rows[0]) if rows else []
    if rows:
        with (output_dir / "samples.csv").open("w", newline="", encoding="utf-8") as handle:
            csv_writer = csv.DictWriter(handle, fieldnames=fieldnames)
            csv_writer.writeheader()
            csv_writer.writerows(rows)
    summary = {
        "profile": str(args.config),
        "duration_s": round(time.monotonic() - started, 3),
        "frames_processed": len(ages_ms),
        "camera": reader.metrics(),
        "frame_age_ms": {
            "mean": sum(ages_ms) / len(ages_ms),
            "max": max(ages_ms),
        },
        "temperature": temperature,
        "throttled": throttled,
    }
    save_json(output_dir / "summary.json", summary)
    print(f"[CAMERA OK] report={output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(130)
