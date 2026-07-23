#!/usr/bin/env python3
"""Measure Camera Module 3 sharpness across manual lens positions."""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import cv2
import numpy as np

from opencv_common import LatestCameraReader, load_profile, save_json


DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "target_mission_v2/configs/opencv_camera_diagnostics.json"
)


def sharpness_scores(image: np.ndarray) -> tuple[float, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    laplacian = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    tenengrad = float(np.mean(grad_x * grad_x + grad_y * grad_y))
    return laplacian, tenengrad


def positions(start: float, stop: float, step: float) -> list[float]:
    if step <= 0 or stop < start:
        raise ValueError("lens range requires stop >= start and step > 0")
    values: list[float] = []
    current = start
    while current <= stop + step * 0.25:
        values.append(round(current, 4))
        current += step
    return values


def wait_for_new_frame(
    reader: LatestCameraReader,
    previous_id: int,
    timeout_s: float = 2.0,
):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        frame = reader.latest()
        if frame is not None and frame.frame_id != previous_id:
            return frame
        if reader.failure:
            raise RuntimeError(f"camera worker failed: {reader.failure}")
        time.sleep(0.003)
    raise TimeoutError("camera did not provide a new frame")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--stop", type=float, default=10.0)
    parser.add_argument("--step", type=float, default=0.5)
    parser.add_argument("--settle-s", type=float, default=0.6)
    parser.add_argument("--frames-per-position", type=int, default=5)
    parser.add_argument(
        "--distance-label",
        choices=("search", "centering", "payload_release"),
        required=True,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("~/camera_tests/focus_sweep"),
    )
    args = parser.parse_args()
    if args.frames_per_position < 1:
        raise ValueError("--frames-per-position must be positive")

    config = load_profile(args.config)
    camera_config = dict(config["camera"])
    camera_config.update(
        {
            "source": "picamera2",
            "fallback_source": None,
            "autofocus_mode": "manual",
            "lens_position": args.start,
        }
    )
    output_dir = args.output_dir.expanduser() / args.distance_label
    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    reader = LatestCameraReader.from_config(camera_config)
    set_controls = getattr(reader.camera, "set_controls", None)
    if not callable(set_controls):
        reader.stop()
        raise RuntimeError("focus sweep requires the direct Picamera2 backend")
    reader.start()
    rows: list[dict[str, float | int | str]] = []
    summaries: list[dict[str, float | str]] = []
    last_id = 0

    try:
        for lens_position in positions(args.start, args.stop, args.step):
            set_controls({"LensPosition": lens_position})
            time.sleep(max(0.0, args.settle_s))
            samples: list[tuple[float, float, np.ndarray, int]] = []
            for sample_index in range(args.frames_per_position):
                frame = wait_for_new_frame(reader, last_id)
                last_id = frame.frame_id
                laplacian, tenengrad = sharpness_scores(frame.image_bgr)
                samples.append(
                    (laplacian, tenengrad, frame.image_bgr, frame.frame_id)
                )
                rows.append(
                    {
                        "distance_label": args.distance_label,
                        "lens_position": lens_position,
                        "sample": sample_index,
                        "frame_id": frame.frame_id,
                        "laplacian_variance": laplacian,
                        "tenengrad": tenengrad,
                    }
                )
            lap_mean = float(np.mean([item[0] for item in samples]))
            ten_mean = float(np.mean([item[1] for item in samples]))
            representative = max(
                samples,
                key=lambda item: item[0] / max(1e-6, lap_mean)
                + item[1] / max(1e-6, ten_mean),
            )
            image_path = image_dir / f"lens-{lens_position:05.2f}.png"
            cv2.imwrite(str(image_path), representative[2])
            summaries.append(
                {
                    "lens_position": lens_position,
                    "laplacian_mean": lap_mean,
                    "tenengrad_mean": ten_mean,
                    "image": str(image_path),
                }
            )
            print(
                f"[FOCUS] lens={lens_position:.2f} "
                f"laplacian={lap_mean:.1f} tenengrad={ten_mean:.1f}"
            )
    finally:
        reader.stop()

    with (output_dir / "focus_samples.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    by_laplacian = sorted(
        summaries, key=lambda item: float(item["laplacian_mean"]), reverse=True
    )
    by_tenengrad = sorted(
        summaries, key=lambda item: float(item["tenengrad_mean"]), reverse=True
    )
    top_positions = sorted(
        {
            float(item["lens_position"])
            for item in by_laplacian[:3] + by_tenengrad[:3]
        }
    )
    report = {
        "distance_label": args.distance_label,
        "camera_profile": str(args.config),
        "range": {
            "start": args.start,
            "stop": args.stop,
            "step": args.step,
        },
        "results": summaries,
        "best_laplacian": by_laplacian[0],
        "best_tenengrad": by_tenengrad[0],
        "recommended_manual_review_range": [
            min(top_positions),
            max(top_positions),
        ],
        "note": (
            "Review the saved images at the actual flight distance before "
            "copying a lens position into a mission profile."
        ),
    }
    save_json(output_dir / "summary.json", report)
    print(
        "[FOCUS RESULT] manually review lens positions "
        f"{min(top_positions):.2f} to {max(top_positions):.2f}"
    )
    print(f"[FOCUS RESULT] {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
