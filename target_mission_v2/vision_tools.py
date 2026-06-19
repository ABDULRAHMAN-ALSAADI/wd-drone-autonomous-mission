#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Optional

import cv2

from mission_controller import open_camera, validate_config
from vision import Detection, StrictShapeDetector


IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    validate_config(config)
    return config


def build_detector(config: dict) -> StrictShapeDetector:
    vision = config["vision"]
    return StrictShapeDetector(
        float(vision["search_min_area_px"]),
        float(vision["tracking_min_area_px"]),
        bool(vision["debug_rejects"]),
    )


def resize_for_processing(frame, process_width: int):
    if process_width <= 0 or frame.shape[1] == process_width:
        return frame
    scale = process_width / frame.shape[1]
    return cv2.resize(frame, (process_width, max(1, round(frame.shape[0] * scale))), interpolation=cv2.INTER_AREA)


def iter_image_files(path: Path) -> Iterable[Path]:
    if path.is_dir():
        yield from sorted(item for item in path.iterdir() if item.suffix.lower() in IMAGE_EXTENSIONS)
    elif path.suffix.lower() in IMAGE_EXTENSIONS:
        yield path
    else:
        raise ValueError(f"No supported image files found at {path}")


def draw_detection_overlay(frame, detections: list[Detection]):
    out = frame.copy()
    h, w = out.shape[:2]
    cv2.drawMarker(out, (w // 2, h // 2), (255, 255, 255), cv2.MARKER_CROSS, 34, 2)
    for item in detections:
        colour = (0, 0, 255) if item.target == "red_triangle" else (255, 0, 0)
        cv2.rectangle(out, (item.bbox_x, item.bbox_y), (item.bbox_x + item.bbox_w, item.bbox_y + item.bbox_h), colour, 2)
        cv2.circle(out, (item.center_x, item.center_y), 5, colour, -1)
        cv2.putText(
            out,
            f"{item.target} conf={item.confidence:.2f} v={item.vertices} ext={item.extent:.2f}",
            (item.bbox_x, max(24, item.bbox_y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            colour,
            2,
            cv2.LINE_AA,
        )
    return out


def analyse_frame(config: dict, detector: StrictShapeDetector, frame) -> tuple[object, list[Detection]]:
    processed = resize_for_processing(frame, int(config["vision"]["process_width"]))
    detections, _ = detector.search(processed)
    return processed, detections


def replay_images(
    config_path: Path,
    input_path: Path,
    output_jsonl: Optional[Path] = None,
    show: bool = False,
) -> list[dict]:
    config = load_config(config_path)
    detector = build_detector(config)
    results: list[dict] = []
    writer = None
    if output_jsonl is not None:
        output_jsonl.parent.mkdir(parents=True, exist_ok=True)
        writer = output_jsonl.open("w", encoding="utf-8")
    try:
        for index, image_path in enumerate(iter_image_files(input_path)):
            frame = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if frame is None:
                raise RuntimeError(f"Could not read image: {image_path}")
            processed, detections = analyse_frame(config, detector, frame)
            record = {
                "index": index,
                "frame": str(image_path),
                "detections": [asdict(item) for item in detections],
            }
            results.append(record)
            if writer is not None:
                writer.write(json.dumps(record, sort_keys=True) + "\n")
            if show:
                cv2.imshow("WD DRONE Vision Replay", draw_detection_overlay(processed, detections))
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    finally:
        if writer is not None:
            writer.close()
        if show:
            cv2.destroyAllWindows()
    return results


def default_recording_dir() -> Path:
    return Path("data") / "vision" / time.strftime("%Y%m%d-%H%M%S")


def record_frames(
    config_path: Path,
    output_dir: Optional[Path] = None,
    frames: int = 300,
    seconds: Optional[float] = None,
    show: bool = False,
) -> Path:
    config = load_config(config_path)
    detector = build_detector(config)
    destination = output_dir or default_recording_dir()
    destination.mkdir(parents=True, exist_ok=True)
    cap = open_camera(int(config["camera"]["udp_port"]))
    report_path = destination / "detections.jsonl"
    deadline = time.monotonic() + seconds if seconds is not None else None
    count = 0
    try:
        with report_path.open("w", encoding="utf-8") as report:
            while count < frames:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.02)
                    continue
                processed, detections = analyse_frame(config, detector, frame)
                frame_path = destination / f"{count:06d}.jpg"
                cv2.imwrite(str(frame_path), frame)
                record = {
                    "index": count,
                    "frame": str(frame_path),
                    "detections": [asdict(item) for item in detections],
                }
                report.write(json.dumps(record, sort_keys=True) + "\n")
                if show:
                    cv2.imshow("WD DRONE Vision Record", draw_detection_overlay(processed, detections))
                    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                        break
                count += 1
    finally:
        cap.release()
        if show:
            cv2.destroyAllWindows()
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record and replay target-mission vision frames.")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("mission_config.json"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="record camera frames and detection JSONL")
    record.add_argument("--output", type=Path)
    record.add_argument("--frames", type=int, default=300)
    record.add_argument("--seconds", type=float)
    record.add_argument("--show", action="store_true")

    replay = subparsers.add_parser("replay", help="run detection on saved frames")
    replay.add_argument("--input", type=Path, required=True)
    replay.add_argument("--output-jsonl", type=Path)
    replay.add_argument("--show", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "record":
        destination = record_frames(args.config, args.output, args.frames, args.seconds, args.show)
        print(f"Recorded vision session: {destination}")
        return 0
    if args.command == "replay":
        results = replay_images(args.config, args.input, args.output_jsonl, args.show)
        total = sum(len(item["detections"]) for item in results)
        print(f"Replayed {len(results)} frames; detections={total}")
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
