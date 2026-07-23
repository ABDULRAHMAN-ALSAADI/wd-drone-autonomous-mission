#!/usr/bin/env python3
"""Benchmark OpenCV camera, masks, search, tracking, overlay, and streaming."""
from __future__ import annotations

import argparse
import csv
import json
import resource
import statistics
import time
from pathlib import Path
from typing import Optional

import cv2
from opencv_common import (
    LatestCameraReader,
    LatestJpegServer,
    draw_panel,
    load_profile,
    resize_width,
    save_json,
    vcgencmd,
)
from vision import Detection, create_detector


DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "target_mission_v2/configs/opencv_live_test.json"
)


def distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None, "p95": None, "stdev": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "p95": ordered[p95_index],
        "stdev": statistics.pstdev(values),
    }


class InputFrames:
    def __init__(self, config: dict, input_path: Optional[Path]) -> None:
        self.reader = None
        self.capture = None
        self.images: list[Path] = []
        self.image_index = 0
        self.frame_id = 0
        self.input_path = input_path
        if input_path is None:
            self.reader = LatestCameraReader.from_config(config["camera"])
            self.reader.start()
        elif input_path.is_dir():
            self.images = sorted(
                path
                for path in input_path.iterdir()
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}
            )
            if not self.images:
                raise ValueError(f"no images found in {input_path}")
        else:
            self.capture = cv2.VideoCapture(str(input_path))
            if not self.capture.isOpened():
                raise RuntimeError(f"could not open {input_path}")

    def read(self, previous_id: int):
        if self.reader is not None:
            frame = self.reader.latest()
            if frame is None or frame.frame_id == previous_id:
                return None
            return frame
        if self.images:
            if self.image_index >= len(self.images):
                return None
            image = cv2.imread(str(self.images[self.image_index]))
            self.image_index += 1
        else:
            ok, image = self.capture.read()
            if not ok:
                return None
        self.frame_id += 1
        from camera_sources import CameraFrame

        return CameraFrame(
            frame_id=self.frame_id,
            sensor_timestamp_ns=None,
            received_monotonic_s=time.monotonic(),
            image_bgr=image,
            metadata={},
            source="replay",
        )

    @property
    def exhausted(self) -> bool:
        if self.reader is not None:
            return bool(self.reader.failure)
        if self.images:
            return self.image_index >= len(self.images)
        return self.capture is not None and not self.capture.isOpened()

    def metrics(self):
        return (
            {"captured": self.frame_id, "overwritten": 0}
            if self.reader is None
            else self.reader.metrics()
        )

    def close(self) -> None:
        if self.reader is not None:
            self.reader.stop()
        if self.capture is not None:
            self.capture.release()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--input", type=Path)
    parser.add_argument(
        "--mode",
        choices=("camera", "hsv", "masks", "search", "tracking", "full", "full-stream"),
        default="full",
    )
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--output-dir", type=Path, default=Path("~/camera_tests/opencv_benchmark"))
    args = parser.parse_args()
    if args.seconds <= 0:
        raise ValueError("--seconds must be positive")

    config = load_profile(args.config)
    detector = create_detector(config["vision"])
    inputs = InputFrames(config, args.input)
    stream = None
    if args.mode == "full-stream":
        stream = LatestJpegServer(
            bind="127.0.0.1",
            port=int(config["display"].get("mjpeg_stream_port", 5602)),
            fps=float(config["display"].get("mjpeg_stream_fps", 8.0)),
            quality=int(config["display"].get("mjpeg_stream_quality", 82)),
            width=int(config["display"].get("mjpeg_stream_width", 854)),
        )
        stream.start()

    output_dir = args.output_dir.expanduser() / args.mode
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, float | int | str]] = []
    started = time.monotonic()
    cpu_started = time.process_time()
    last_frame_id = 0
    tracked: Optional[Detection] = None
    last_health_at = 0.0
    temperature = "unavailable"
    throttled = "unavailable"

    try:
        while time.monotonic() - started < args.seconds:
            camera_frame = inputs.read(last_frame_id)
            if camera_frame is None:
                if args.input is not None:
                    break
                time.sleep(0.002)
                continue
            last_frame_id = camera_frame.frame_id
            frame_started = time.perf_counter()
            image = resize_width(
                camera_frame.image_bgr,
                int(config["vision"].get("process_width", camera_frame.width)),
            )
            processed = None
            detections: list[Detection] = []
            search_ms = 0.0
            tracking_ms = 0.0
            overlay_ms = 0.0
            manual_timings: dict[str, float] = {}
            if args.mode == "hsv":
                stage_started = time.perf_counter()
                cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
                manual_timings["hsv"] = (
                    time.perf_counter() - stage_started
                ) * 1000.0
            elif args.mode != "camera":
                processed = detector.preprocess(
                    image,
                    frame_id=camera_frame.frame_id,
                    received_at_s=camera_frame.received_monotonic_s,
                )
            if args.mode in {"search", "tracking", "full", "full-stream"}:
                stage_started = time.perf_counter()
                if args.mode == "tracking" and tracked is not None:
                    item = detector.track_processed(
                        processed,
                        tracked.target,
                        (tracked.center_x, tracked.center_y),
                        float(config["vision"].get("max_lock_jump_px", 160.0)),
                    )
                    detections = [] if item is None else [item]
                    if item is not None:
                        tracked = item
                    tracking_ms = (time.perf_counter() - stage_started) * 1000.0
                else:
                    detections = detector.search_processed(processed)
                    search_ms = (time.perf_counter() - stage_started) * 1000.0
                    if args.mode == "tracking" and detections:
                        tracked = max(detections, key=lambda item: item.total_score)
                        detector.begin_tracking(tracked)
            if args.mode in {"full", "full-stream"}:
                overlay_started = time.perf_counter()
                view = draw_panel(
                    image,
                    [
                        f"OpenCV benchmark {args.mode}",
                        f"frame {camera_frame.frame_id} detections {len(detections)}",
                    ],
                )
                overlay_ms = (time.perf_counter() - overlay_started) * 1000.0
                if stream is not None:
                    stream.publish(view)
            now = time.monotonic()
            if now - last_health_at >= 1.0:
                temperature = vcgencmd("measure_temp")
                throttled = vcgencmd("get_throttled")
                last_health_at = now
            timings = (
                manual_timings if processed is None else processed.timings_ms
            )
            rows.append(
                {
                    "frame_id": camera_frame.frame_id,
                    "frame_age_ms": camera_frame.capture_age_s(now) * 1000.0,
                    "hsv_ms": timings.get("hsv", 0.0),
                    "red_mask_ms": timings.get("red_mask", 0.0),
                    "blue_mask_ms": timings.get("blue_mask", 0.0),
                    "morphology_ms": timings.get("morphology", 0.0),
                    "preprocess_ms": timings.get("preprocess_total", 0.0),
                    "candidate_extraction_ms": (
                        timings.get("red_triangle_candidate_extraction", 0.0)
                        + timings.get("blue_hexagon_candidate_extraction", 0.0)
                    ),
                    "triangle_verification_ms": timings.get(
                        "red_triangle_verification", 0.0
                    ),
                    "hexagon_verification_ms": timings.get(
                        "blue_hexagon_verification", 0.0
                    ),
                    "search_ms": search_ms,
                    "tracking_ms": tracking_ms,
                    "overlay_ms": overlay_ms,
                    "total_ms": (time.perf_counter() - frame_started) * 1000.0,
                    "detections": len(detections),
                    "temperature": temperature,
                    "throttled": throttled,
                }
            )
    finally:
        inputs.close()
        if stream is not None:
            stream.stop()

    wall_s = max(1e-6, time.monotonic() - started)
    cpu_s = time.process_time() - cpu_started
    if not rows:
        print("[BENCHMARK ERROR] no frames processed")
        return 2
    with (output_dir / "samples.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    metric_names = (
        "frame_age_ms",
        "hsv_ms",
        "red_mask_ms",
        "blue_mask_ms",
        "morphology_ms",
        "preprocess_ms",
        "candidate_extraction_ms",
        "triangle_verification_ms",
        "hexagon_verification_ms",
        "search_ms",
        "tracking_ms",
        "overlay_ms",
        "total_ms",
    )
    summary = {
        "mode": args.mode,
        "profile": str(args.config),
        "input": "camera" if args.input is None else str(args.input),
        "duration_s": wall_s,
        "processed_frames": len(rows),
        "processed_fps": len(rows) / wall_s,
        "process_cpu_percent_one_core": 100.0 * cpu_s / wall_s,
        "max_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0,
        "camera": inputs.metrics(),
        "stream": (
            None
            if stream is None
            else {
                "submitted": stream.submitted,
                "encoded": stream.encoded,
                "overwritten": stream.overwritten,
            }
        ),
        "temperature": temperature,
        "throttled": throttled,
        "metrics": {
            name: distribution([float(row[name]) for row in rows])
            for name in metric_names
        },
    }
    save_json(output_dir / "summary.json", summary)
    (output_dir / "report.txt").write_text(
        "\n".join(
            [
                f"mode: {args.mode}",
                f"frames: {len(rows)}",
                f"processed FPS: {len(rows) / wall_s:.2f}",
                f"total latency mean: {summary['metrics']['total_ms']['mean']:.2f} ms",
                f"total latency p95: {summary['metrics']['total_ms']['p95']:.2f} ms",
                f"camera: {json.dumps(summary['camera'], sort_keys=True)}",
                f"temperature: {temperature}",
                f"throttled: {throttled}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print((output_dir / "report.txt").read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
