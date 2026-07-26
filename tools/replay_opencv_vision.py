#!/usr/bin/env python3
"""Replay images/video through the OpenCV detector without changing mission config."""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np

from opencv_common import draw_panel, load_profile, resize_width, save_json
from opencv_live_test import make_tracker
from vision import Detection, create_detector


DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "target_mission_v2/configs/opencv_replay.json"
)


def image_frames(directory: Path) -> Iterator[tuple[int, np.ndarray, str]]:
    paths = sorted(
        path
        for path in directory.iterdir()
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}
    )
    for frame_id, path in enumerate(paths, start=1):
        image = cv2.imread(str(path))
        if image is not None:
            yield frame_id, image, path.name


def video_frames(path: Path) -> Iterator[tuple[int, np.ndarray, str]]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"could not open {path}")
    frame_id = 0
    try:
        while True:
            ok, image = capture.read()
            if not ok:
                break
            frame_id += 1
            yield frame_id, image, str(frame_id)
    finally:
        capture.release()


def load_labels(path: Optional[Path]) -> dict[str, set[str]]:
    if path is None:
        return {}
    labels: dict[str, set[str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = row.get("file") or row.get("frame") or row.get("frame_id")
            target = row.get("target") or row.get("label")
            if key and target:
                labels.setdefault(str(key), set()).add(str(target))
    return labels


def crop_box(value: str) -> tuple[int, int, int, int]:
    try:
        values = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--crop must be x,y,width,height"
        ) from exc
    if len(values) != 4 or min(values) < 0 or values[2] <= 0 or values[3] <= 0:
        raise argparse.ArgumentTypeError(
            "--crop must contain non-negative x/y and positive width/height"
        )
    return values


def apply_tuning(vision: dict, args: argparse.Namespace) -> dict:
    output = dict(vision)
    overrides = {
        "search_min_area_px": args.min_area,
        "morphology_kernel_size": args.morphology_kernel,
        "triangle_score_threshold": args.triangle_threshold,
        "hexagon_score_threshold": args.hexagon_threshold,
        "confirmation_min_duration_s": args.confirmation_duration,
        "tracking_roi_scale": args.tracking_roi_scale,
    }
    output.update({key: value for key, value in overrides.items() if value is not None})
    return output


def draw_detections(image: np.ndarray, detections: list[Detection], state: str) -> np.ndarray:
    output = image.copy()
    center = (image.shape[1] // 2, image.shape[0] // 2)
    cv2.drawMarker(output, center, (255, 255, 255), cv2.MARKER_CROSS, 24, 1)
    for item in detections:
        colour = (0, 0, 255) if item.target == "red_triangle" else (255, 0, 0)
        cv2.rectangle(
            output,
            (item.bbox_x, item.bbox_y),
            (item.bbox_x + item.bbox_w, item.bbox_y + item.bbox_h),
            colour,
            2,
        )
        cv2.circle(output, (item.center_x, item.center_y), 4, colour, -1)
        cv2.putText(
            output,
            f"{item.target} {item.total_score:.2f}",
            (item.bbox_x, max(18, item.bbox_y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            colour,
            1,
            cv2.LINE_AA,
        )
    return draw_panel(
        output,
        [
            f"OpenCV replay | {state}",
            f"detections {','.join(item.target for item in detections) or 'none'}",
            "No MAVLink | no payload",
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--input-fps", type=float, default=10.0)
    parser.add_argument(
        "--crop",
        type=crop_box,
        help="Optional x,y,width,height crop before production preprocessing.",
    )
    parser.add_argument("--save-masks", action="store_true")
    parser.add_argument("--save-rois", action="store_true")
    parser.add_argument("--min-area", type=float)
    parser.add_argument("--morphology-kernel", type=int)
    parser.add_argument("--triangle-threshold", type=float)
    parser.add_argument("--hexagon-threshold", type=float)
    parser.add_argument("--confirmation-duration", type=float)
    parser.add_argument("--tracking-roi-scale", type=float)
    args = parser.parse_args()
    if args.input_fps <= 0:
        raise ValueError("--input-fps must be positive")

    config = load_profile(args.config)
    vision = apply_tuning(config["vision"], args)
    detector = create_detector(vision)
    tracker = make_tracker(vision)
    labels = load_labels(args.labels)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    false_dir = output_dir / "false_positives"
    missed_dir = output_dir / "missed_targets"
    mask_dir = output_dir / "masks"
    roi_dir = output_dir / "rois"
    source = (
        image_frames(args.input)
        if args.input.is_dir()
        else video_frames(args.input)
    )
    detection_rows: list[dict] = []
    state_rows: list[dict] = []
    rejection_rows: list[dict] = []
    confirmation_times: dict[str, float] = {}
    first_seen: dict[str, float] = {}
    centers: dict[str, list[tuple[float, float]]] = {
        "red_triangle": [],
        "blue_hexagon": [],
    }
    target_counts: Counter[str] = Counter()
    confirmation_counts: Counter[str] = Counter()
    valid_locks: Counter[str] = Counter()
    payload_authorizations: Counter[str] = Counter()
    temporary_colour_tracking_frames = 0
    strict_tracking_frames = 0
    lock_drops = 0
    locked_target: Optional[str] = None
    payload_authorized_targets: set[str] = set()
    writer = None
    total_ms: list[float] = []

    try:
        for frame_id, image, source_key in source:
            timestamp = (frame_id - 1) / args.input_fps
            started = time.perf_counter()
            if args.crop is not None:
                crop_x, crop_y, crop_width, crop_height = args.crop
                if (
                    crop_x + crop_width > image.shape[1]
                    or crop_y + crop_height > image.shape[0]
                ):
                    raise ValueError(
                        f"--crop {args.crop} exceeds frame "
                        f"{image.shape[1]}x{image.shape[0]}"
                    )
                image = image[
                    crop_y : crop_y + crop_height,
                    crop_x : crop_x + crop_width,
                ]
            process_image = resize_width(
                image, int(vision.get("process_width", image.shape[1]))
            )
            processed = detector.preprocess(
                process_image,
                frame_id=frame_id,
                captured_at_s=timestamp,
                received_at_s=timestamp,
            )
            confirmed = None
            state = "SEARCH"
            if locked_target is None:
                detections = detector.search_processed(processed)
                confirmed = tracker.update(
                    detections,
                    {"red_triangle", "blue_hexagon"},
                    now=timestamp,
                    frame_id=frame_id,
                )
                if confirmed is not None:
                    selected = next(
                        (
                            item
                            for item in detections
                            if item.target == confirmed.target
                        ),
                        None,
                    )
                    if selected is not None:
                        locked_target = selected.target
                        detector.begin_tracking(selected, timestamp)
                        confirmation_counts[selected.target] += 1
                        valid_locks[selected.target] += 1
                        state = f"CONFIRMED:{selected.target}"
                        confirmation_times.setdefault(
                            selected.target,
                            timestamp
                            - first_seen.get(selected.target, timestamp),
                        )
            else:
                tracking = detector.tracking_state
                previous_center = (
                    (round(tracking.center[0]), round(tracking.center[1]))
                    if tracking is not None
                    else (
                        process_image.shape[1] // 2,
                        process_image.shape[0] // 2,
                    )
                )
                tracked = detector.track_processed(
                    processed,
                    locked_target,
                    previous_center,
                    float(vision.get("max_lock_jump_px", 160.0)),
                )
                detections = [] if tracked is None else [tracked]
                if tracked is not None:
                    state = (
                        "TEMP_COLOUR_TRACK"
                        if tracked.source == "colour_track"
                        else "STRICT_TRACK"
                    )
                    if tracked.source == "colour_track":
                        temporary_colour_tracking_frames += 1
                    else:
                        strict_tracking_frames += 1

                    desired_x = process_image.shape[1] / 2.0
                    desired_y = process_image.shape[0] / 2.0
                    tolerance = float(
                        config.get("control", {}).get(
                            "center_tolerance_px",
                            20.0,
                        )
                    )
                    center_error = math.hypot(
                        tracked.center_x - desired_x,
                        tracked.center_y - desired_y,
                    )
                    if (
                        locked_target not in payload_authorized_targets
                        and center_error <= tolerance
                    ):
                        strict_payload = detector.verify_target_frame(
                            image,
                            locked_target,
                            expected_center_normalized=(
                                tracked.center_x
                                / max(1.0, float(process_image.shape[1])),
                                tracked.center_y
                                / max(1.0, float(process_image.shape[0])),
                            ),
                            frame_id=frame_id,
                            captured_at_s=timestamp,
                            received_at_s=timestamp,
                        )
                        if strict_payload is not None:
                            payload_authorized_targets.add(locked_target)
                            payload_authorizations[locked_target] += 1
                elif detector.tracking_state is None:
                    state = "LOCK_DROPPED"
                    lock_drops += 1
                    locked_target = None
                    tracker.reset()
                else:
                    state = "STRICT_RECHECK_FAILED"
            for item in detections:
                first_seen.setdefault(item.target, timestamp)
                target_counts[item.target] += 1
                centers[item.target].append((item.center_x, item.center_y))
                detection_rows.append(
                    {
                        "frame_id": frame_id,
                        "source": source_key,
                        **asdict(item),
                    }
                )
            state_rows.append(
                {
                    "frame_id": frame_id,
                    "source": source_key,
                    "timestamp_s": timestamp,
                    "red_state": tracker.state("red_triangle").value,
                    "blue_state": tracker.state("blue_hexagon").value,
                    "confirmed": None if confirmed is None else confirmed.target,
                    "lock": locked_target,
                    "state": state,
                }
            )
            for rejection in detector.last_rejections:
                rejection_rows.append(
                    {"frame_id": frame_id, "source": source_key, **rejection}
                )

            expected = labels.get(source_key, labels.get(str(frame_id), set()))
            actual = {item.target for item in detections}
            if labels:
                if actual - expected:
                    false_dir.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(false_dir / f"{frame_id:08d}.png"), image)
                if expected - actual:
                    missed_dir.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(missed_dir / f"{frame_id:08d}.png"), image)
            if args.save_masks:
                mask_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(mask_dir / f"{frame_id:08d}-red.png"), processed.red_mask)
                cv2.imwrite(str(mask_dir / f"{frame_id:08d}-blue.png"), processed.blue_mask)
            if args.save_rois:
                roi_dir.mkdir(parents=True, exist_ok=True)
                for index, item in enumerate(detections):
                    roi = process_image[
                        item.bbox_y : item.bbox_y + item.bbox_h,
                        item.bbox_x : item.bbox_x + item.bbox_w,
                    ]
                    if roi.size:
                        cv2.imwrite(
                            str(roi_dir / f"{frame_id:08d}-{index}-{item.target}.png"),
                            roi,
                        )
            annotated = draw_detections(process_image, detections, state)
            if writer is None:
                writer = cv2.VideoWriter(
                    str(output_dir / "annotated.avi"),
                    cv2.VideoWriter_fourcc(*"MJPG"),
                    args.input_fps,
                    (annotated.shape[1], annotated.shape[0]),
                )
                if not writer.isOpened():
                    raise RuntimeError("could not create annotated video")
            writer.write(annotated)
            total_ms.append((time.perf_counter() - started) * 1000.0)
    finally:
        if writer is not None:
            writer.release()

    def write_rows(
        name: str,
        rows: list[dict],
        default_fields: tuple[str, ...],
    ) -> None:
        fieldnames: list[str] = list(default_fields)
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        with (output_dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer_csv = csv.DictWriter(
                handle, fieldnames=fieldnames, extrasaction="ignore"
            )
            writer_csv.writeheader()
            for row in rows:
                writer_csv.writerow(
                    {
                        key: (
                            json.dumps(value, sort_keys=True)
                            if isinstance(value, (dict, list, tuple))
                            else value
                        )
                        for key, value in row.items()
                    }
                )

    detection_fields = tuple(
        dict.fromkeys(("frame_id", "source", *Detection.__dataclass_fields__))
    )
    write_rows("detections.csv", detection_rows, detection_fields)
    write_rows(
        "temporal_states.csv",
        state_rows,
        (
            "frame_id",
            "source",
            "timestamp_s",
            "red_state",
            "blue_state",
            "confirmed",
        ),
    )
    write_rows(
        "candidate_rejections.csv",
        rejection_rows,
        ("frame_id", "source", "target", "reason", "metrics"),
    )

    jitter = {}
    for target, values in centers.items():
        if len(values) < 2:
            jitter[target] = None
            continue
        points = np.asarray(values, dtype=np.float64)
        jitter[target] = float(
            math.sqrt(np.var(points[:, 0]) + np.var(points[:, 1]))
        )
    summary = {
        "frames": len(state_rows),
        "detections": {
            target: int(target_counts[target])
            for target in ("red_triangle", "blue_hexagon")
        },
        "confirmed_targets": {
            target: int(confirmation_counts[target])
            for target in ("red_triangle", "blue_hexagon")
        },
        "valid_locks": {
            target: int(valid_locks[target])
            for target in ("red_triangle", "blue_hexagon")
        },
        "payload_authorizations": {
            target: int(payload_authorizations[target])
            for target in ("red_triangle", "blue_hexagon")
        },
        "temporary_colour_tracking_frames": temporary_colour_tracking_frames,
        "strict_tracking_frames": strict_tracking_frames,
        "lock_drops": lock_drops,
        "confirmation_time_s": confirmation_times,
        "center_jitter_px": jitter,
        "processing_ms": {
            "mean": None if not total_ms else sum(total_ms) / len(total_ms),
            "max": None if not total_ms else max(total_ms),
        },
        "false_positive_images": (
            0 if not false_dir.exists() else len(list(false_dir.glob("*.png")))
        ),
        "missed_target_images": (
            0 if not missed_dir.exists() else len(list(missed_dir.glob("*.png")))
        ),
    }
    save_json(output_dir / "summary.json", summary)
    save_json(output_dir / "proposed_opencv_settings.json", {"vision": vision})
    print(f"[REPLAY] frames={len(state_rows)} report={output_dir / 'summary.json'}")
    return 0 if state_rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
