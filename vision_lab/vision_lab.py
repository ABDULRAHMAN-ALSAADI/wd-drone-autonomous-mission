#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import cv2

REPO_ROOT = Path(__file__).resolve().parents[1]
MISSION_DIR = REPO_ROOT / "target_mission_v2"
if str(MISSION_DIR) not in sys.path:
    sys.path.insert(0, str(MISSION_DIR))

from mission_controller import open_camera, validate_config  # noqa: E402
from vision import Detection, StrictShapeDetector  # noqa: E402


IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
TARGETS = {"red_triangle", "blue_hexagon"}
DEFAULT_CONFIG = MISSION_DIR / "mission_config.json"
DEFAULT_DATA_DIR = REPO_ROOT / "vision_lab" / "data"


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
    if not path.exists():
        if path.name == "SESSION_FOLDER":
            raise ValueError("SESSION_FOLDER is a placeholder. Run `python3 vision_lab/vision_lab.py list` and use a real folder name.")
        raise ValueError(f"Input path does not exist: {path}")
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


def default_recording_dir(label: str) -> Path:
    safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)
    return DEFAULT_DATA_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{safe_label}"


def image_count(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return 1 if path.suffix.lower() in IMAGE_EXTENSIONS else 0
    return sum(1 for item in path.iterdir() if item.suffix.lower() in IMAGE_EXTENSIONS)


def directory_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total / (1024.0 * 1024.0)


def load_manifest(path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def list_sessions(data_dir: Path = DEFAULT_DATA_DIR) -> list[dict[str, Any]]:
    if not data_dir.exists():
        return []
    sessions: list[dict[str, Any]] = []
    for session in sorted((item for item in data_dir.iterdir() if item.is_dir()), reverse=True):
        manifest = load_manifest(session)
        sessions.append(
            {
                "path": str(session),
                "label": manifest.get("label", "unknown"),
                "expected_targets": manifest.get("expected_targets", []),
                "altitude_m": manifest.get("altitude_m"),
                "notes": manifest.get("notes", ""),
                "frames": image_count(session),
                "size_mb": round(directory_size_mb(session), 2),
            }
        )
    return sessions


def print_sessions(sessions: list[dict[str, Any]]) -> None:
    if not sessions:
        print("No vision sessions found.")
        return
    for item in sessions:
        print(
            f"{item['path']} | label={item['label']} | frames={item['frames']} | "
            f"alt={item['altitude_m']} | expected={item['expected_targets']} | "
            f"size={item['size_mb']:.2f} MB | notes={item['notes']}"
        )


def expected_targets_from_label(label: str) -> list[str]:
    if label in TARGETS:
        return [label]
    if label in {"none", "unknown"}:
        return []
    raise ValueError(f"Unknown label: {label}")


def write_manifest(
    destination: Path,
    config_path: Path,
    label: str,
    frames: int,
    seconds: Optional[float],
    altitude_m: Optional[float],
    notes: str,
    save_raw: bool,
    jpeg_quality: int,
) -> None:
    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": str(config_path),
        "label": label,
        "expected_targets": expected_targets_from_label(label),
        "frames_requested": frames,
        "seconds_requested": seconds,
        "altitude_m": altitude_m,
        "notes": notes,
        "save_raw": save_raw,
        "jpeg_quality": jpeg_quality,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_frames(
    config_path: Path,
    output_dir: Optional[Path] = None,
    frames: int = 300,
    seconds: Optional[float] = None,
    show: bool = False,
    label: str = "unknown",
    altitude_m: Optional[float] = None,
    notes: str = "",
    save_raw: bool = False,
    jpeg_quality: int = 85,
    progress_every: int = 25,
) -> Path:
    config = load_config(config_path)
    detector = build_detector(config)
    destination = output_dir or default_recording_dir(label)
    destination.mkdir(parents=True, exist_ok=True)
    write_manifest(destination, config_path, label, frames, seconds, altitude_m, notes, save_raw, jpeg_quality)
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
                saved_frame = frame if save_raw else processed
                cv2.imwrite(str(frame_path), saved_frame, [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)])
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
                if progress_every > 0 and count % progress_every == 0:
                    print(f"recorded {count}/{frames} frames -> {destination}", flush=True)
    finally:
        cap.release()
        if show:
            cv2.destroyAllWindows()
    return destination


def load_expected_targets(session: Path, fallback: Optional[set[str]], negative: bool) -> set[str]:
    if not session.exists():
        if session.name == "SESSION_FOLDER":
            raise ValueError("SESSION_FOLDER is a placeholder. Run `python3 vision_lab/vision_lab.py list` and use a real folder name.")
        raise ValueError(f"Input path does not exist: {session}")
    if negative:
        return set()
    if fallback is not None:
        return set(fallback)
    manifest_path = session / "manifest.json"
    if manifest_path.exists():
        manifest = load_manifest(session)
        return set(manifest.get("expected_targets", []))
    raise ValueError(f"{session} has no manifest.json. Pass --expected-target or --negative.")


def empty_target_stats() -> dict[str, dict[str, int]]:
    return {
        target: {"true_frames": 0, "missed_frames": 0, "false_frames": 0}
        for target in sorted(TARGETS)
    }


def evaluate_sessions(
    config_path: Path,
    input_paths: Sequence[Path],
    expected_targets: Optional[set[str]] = None,
    negative: bool = False,
    output_json: Optional[Path] = None,
) -> dict:
    summary = {
        "config": str(config_path),
        "frames": 0,
        "sessions": [],
        "targets": empty_target_stats(),
        "clean_negative_frames": 0,
        "false_positive_frames": 0,
    }
    for session in input_paths:
        expected = load_expected_targets(session, expected_targets, negative)
        records = replay_images(config_path, session)
        session_summary = {
            "path": str(session),
            "expected_targets": sorted(expected),
            "frames": len(records),
            "false_positive_frames": 0,
        }
        for record in records:
            detected = {item["target"] for item in record["detections"]}
            false_targets = detected - expected
            missed_targets = expected - detected
            for target in TARGETS:
                if target in expected and target in detected:
                    summary["targets"][target]["true_frames"] += 1
                if target in missed_targets:
                    summary["targets"][target]["missed_frames"] += 1
                if target in false_targets:
                    summary["targets"][target]["false_frames"] += 1
            if false_targets:
                summary["false_positive_frames"] += 1
                session_summary["false_positive_frames"] += 1
            elif not expected:
                summary["clean_negative_frames"] += 1
        summary["frames"] += len(records)
        summary["sessions"].append(session_summary)
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def print_evaluation(summary: dict) -> None:
    print(f"Evaluated {summary['frames']} frames from {len(summary['sessions'])} session(s)")
    for target, stats in summary["targets"].items():
        print(
            f"{target}: true={stats['true_frames']} "
            f"missed={stats['missed_frames']} false={stats['false_frames']}"
        )
    print(f"false_positive_frames={summary['false_positive_frames']}")
    print(f"clean_negative_frames={summary['clean_negative_frames']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record, replay, and evaluate target-mission vision frames.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="record camera frames and detection JSONL")
    record.add_argument("--output", type=Path)
    record.add_argument("--frames", type=int, default=300)
    record.add_argument("--seconds", type=float)
    record.add_argument("--show", action="store_true")
    record.add_argument("--label", choices=sorted(TARGETS | {"none", "unknown"}), default="unknown")
    record.add_argument("--altitude-m", type=float)
    record.add_argument("--notes", default="")
    record.add_argument("--save-raw", action="store_true", help="save full raw camera frames instead of processed-width frames")
    record.add_argument("--jpg-quality", type=int, default=85)
    record.add_argument("--progress-every", type=int, default=25)

    replay = subparsers.add_parser("replay", help="run detection on saved frames")
    replay.add_argument("--input", type=Path, required=True)
    replay.add_argument("--output-jsonl", type=Path)
    replay.add_argument("--show", action="store_true")

    evaluate = subparsers.add_parser("evaluate", help="summarize detection quality on one or more sessions")
    evaluate.add_argument("--input", type=Path, nargs="+", required=True)
    evaluate.add_argument("--expected-target", action="append", choices=sorted(TARGETS))
    evaluate.add_argument("--negative", action="store_true")
    evaluate.add_argument("--output-json", type=Path)

    list_cmd = subparsers.add_parser("list", help="list recorded sessions")
    list_cmd.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "record":
        destination = record_frames(
            args.config,
            args.output,
            args.frames,
            args.seconds,
            args.show,
            args.label,
            args.altitude_m,
            args.notes,
            args.save_raw,
            args.jpg_quality,
            args.progress_every,
        )
        print(f"Recorded vision session: {destination}")
        return 0
    if args.command == "replay":
        results = replay_images(args.config, args.input, args.output_jsonl, args.show)
        total = sum(len(item["detections"]) for item in results)
        print(f"Replayed {len(results)} frames; detections={total}")
        return 0
    if args.command == "evaluate":
        if args.negative and args.expected_target:
            parser.error("--negative cannot be combined with --expected-target")
        expected = set(args.expected_target) if args.expected_target else None
        summary = evaluate_sessions(args.config, args.input, expected, args.negative, args.output_json)
        print_evaluation(summary)
        return 0
    if args.command == "list":
        print_sessions(list_sessions(args.data_dir))
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
