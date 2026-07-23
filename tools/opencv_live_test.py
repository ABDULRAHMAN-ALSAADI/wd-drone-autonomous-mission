#!/usr/bin/env python3
"""Live OpenCV-only Camera Module 3 test with no MAVLink or payload control."""
from __future__ import annotations

import argparse
import math
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from opencv_common import (
    LatestCameraReader,
    LatestJpegServer,
    draw_panel,
    load_profile,
    resize_width,
)
from vision import Detection, HitTracker, create_detector


DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "target_mission_v2/configs/opencv_live_test.json"
)
TARGETS = {"red_triangle", "blue_hexagon"}


def scale_detection(
    item: Detection,
    source_shape: tuple[int, ...],
    destination_shape: tuple[int, ...],
) -> Detection:
    scale_x = destination_shape[1] / source_shape[1]
    scale_y = destination_shape[0] / source_shape[0]
    return replace(
        item,
        center_x=round(item.center_x * scale_x),
        center_y=round(item.center_y * scale_y),
        area_px=item.area_px * scale_x * scale_y,
        bbox_x=round(item.bbox_x * scale_x),
        bbox_y=round(item.bbox_y * scale_y),
        bbox_w=round(item.bbox_w * scale_x),
        bbox_h=round(item.bbox_h * scale_y),
    )


def desired_point(control: dict, width: int, height: int) -> tuple[int, int]:
    pixel_x = control.get("desired_drop_pixel_x")
    pixel_y = control.get("desired_drop_pixel_y")
    if pixel_x is not None and pixel_y is not None:
        return round(float(pixel_x)), round(float(pixel_y))
    x = float(control.get("desired_drop_x_normalized", 0.5))
    y = float(control.get("desired_drop_y_normalized", 0.5))
    return round(x * width), round(y * height)


def make_tracker(vision: dict) -> HitTracker:
    return HitTracker(
        required_hits=int(vision.get("required_hits", 3)),
        window_s=float(vision.get("confirmation_window_s", 1.5)),
        max_jump_px=float(vision.get("max_lock_jump_px", 160.0)),
        min_duration_s=float(vision.get("confirmation_min_duration_s", 0.0)),
        min_hit_ratio=float(vision.get("confirmation_min_hit_ratio", 0.60)),
        max_missing_ratio=float(
            vision.get("confirmation_max_missing_ratio", 0.40)
        ),
        max_center_std_px=float(
            vision.get("confirmation_max_center_std_px", 80.0)
        ),
        max_area_cv=float(vision.get("confirmation_max_area_cv", 0.75)),
        max_bbox_cv=float(vision.get("confirmation_max_bbox_cv", 0.75)),
        max_area_jump_ratio=float(
            vision.get("confirmation_max_area_jump_ratio", 4.0)
        ),
        min_colour_score=float(
            vision.get("confirmation_min_colour_score", 0.0)
        ),
        min_shape_score=float(
            vision.get("confirmation_min_shape_score", 0.0)
        ),
        min_total_score=float(
            vision.get("confirmation_min_total_score", 0.0)
        ),
    )


def draw_live_overlay(
    frame: np.ndarray,
    detections: list[Detection],
    desired: tuple[int, int],
    raw_fps: float,
    processed_fps: float,
    age_ms: float,
    state: str,
    locked_target: Optional[str],
    tracker: HitTracker,
    detector,
    metadata: dict,
) -> np.ndarray:
    output = frame.copy()
    cv2.drawMarker(
        output, desired, (255, 255, 255), cv2.MARKER_CROSS, 26, 1
    )
    for item in detections:
        colour = (0, 0, 255) if item.target == "red_triangle" else (255, 0, 0)
        cv2.rectangle(
            output,
            (item.bbox_x, item.bbox_y),
            (item.bbox_x + item.bbox_w, item.bbox_y + item.bbox_h),
            colour,
            2,
        )
        cv2.circle(output, (item.center_x, item.center_y), 5, colour, -1)
        cv2.line(
            output,
            desired,
            (item.center_x, item.center_y),
            colour,
            2,
            cv2.LINE_AA,
        )
        error = math.hypot(item.center_x - desired[0], item.center_y - desired[1])
        cv2.putText(
            output,
            (
                f"{item.target} total={item.total_score:.2f} "
                f"shape={item.shape_score:.2f} err={error:.0f}px "
                f"{item.source}"
            ),
            (item.bbox_x, max(18, item.bbox_y - 7)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            colour,
            1,
            cv2.LINE_AA,
        )
    hits = tracker.status()
    rejection = (
        detector.last_rejections[-1]["reason"]
        if detector.last_rejections
        else "none"
    )
    lines = [
        "OpenCV CAMERA TEST | MAVLink OFF | payload OFF",
        f"Vision {state} | lock {locked_target or 'none'}",
        f"Evidence triangle {hits['red_triangle']} | hexagon {hits['blue_hexagon']}",
        f"Camera FPS {raw_fps:.1f} | vision FPS {processed_fps:.1f} | age {age_ms:.1f}ms",
        f"Lens {metadata.get('LensPosition', 'n/a')} | exposure {metadata.get('ExposureTime', 'n/a')}us | gain {metadata.get('AnalogueGain', 'n/a')}",
        f"Last rejection: {rejection}",
        "Keys: q quit | s save | m masks | r reset | v record",
    ]
    return draw_panel(output, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--mode",
        choices=("raw", "masks", "search", "tracking", "full"),
        default="full",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--output-dir", type=Path, default=Path("~/camera_tests/opencv_live"))
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--stream-bind", default="127.0.0.1")
    parser.add_argument("--stream-port", type=int, default=5602)
    parser.add_argument(
        "--target",
        choices=("both", "red_triangle", "blue_hexagon"),
        default="both",
    )
    parser.add_argument("--debug-candidates", action="store_true")
    args = parser.parse_args()

    config = load_profile(args.config)
    vision = dict(config["vision"])
    vision["debug_rejects"] = bool(args.debug_candidates)
    detector = create_detector(vision)
    tracker = make_tracker(vision)
    allowed = TARGETS if args.target == "both" else {args.target}
    reader = LatestCameraReader.from_config(config["camera"])
    reader.start()
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    stream = (
        LatestJpegServer(
            bind=args.stream_bind,
            port=args.stream_port,
            fps=float(config["display"].get("mjpeg_stream_fps", 8.0)),
            quality=int(config["display"].get("mjpeg_stream_quality", 82)),
            width=int(config["display"].get("mjpeg_stream_width", 854)),
        )
        if args.stream
        else None
    )
    if stream is not None:
        stream.start()
        print(
            f"[STREAM] http://{args.stream_bind}:{args.stream_port}/stream.mjpg"
        )

    started = time.monotonic()
    camera_times: deque[float] = deque(maxlen=120)
    process_times: deque[float] = deque(maxlen=120)
    last_frame_id = 0
    locked_target: Optional[str] = None
    last_track_at = 0.0
    show_masks = args.mode == "masks"
    recording = False
    writer = None
    latest_raw = None
    latest_view = None
    latest_masks = None

    try:
        while args.duration <= 0 or time.monotonic() - started < args.duration:
            now = time.monotonic()
            camera_frame = reader.latest()
            if camera_frame is None or camera_frame.frame_id == last_frame_id:
                if reader.failure:
                    raise RuntimeError(f"camera worker failed: {reader.failure}")
                if not args.headless and cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
                time.sleep(0.002)
                continue
            last_frame_id = camera_frame.frame_id
            camera_times.append(now)
            latest_raw = camera_frame.image_bgr
            process_width = int(vision.get("process_width", latest_raw.shape[1]))
            process_image = resize_width(latest_raw, process_width)
            detections: list[Detection] = []
            processed = None
            vision_state = "RAW"

            if args.mode != "raw":
                captured_at = (
                    float(camera_frame.sensor_timestamp_ns) / 1_000_000_000.0
                    if camera_frame.has_sensor_timestamp
                    else camera_frame.received_monotonic_s
                )
                processed = detector.preprocess(
                    process_image,
                    frame_id=camera_frame.frame_id,
                    captured_at_s=captured_at,
                    received_at_s=camera_frame.received_monotonic_s,
                )
                latest_masks = processed.masks
                process_times.append(time.monotonic())
                if args.mode == "masks":
                    vision_state = "MASKS"
                elif locked_target is None or args.mode == "search":
                    detections = detector.search_processed(processed, allowed)
                    confirmed = tracker.update(
                        detections,
                        allowed,
                        now=now,
                        frame_id=camera_frame.frame_id,
                    )
                    vision_state = "SEARCH"
                    if (
                        confirmed is not None
                        and args.mode in {"tracking", "full"}
                    ):
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
                            detector.begin_tracking(selected, now)
                            last_track_at = now
                            vision_state = "TRACKING"
                else:
                    previous = detector.tracking_state
                    previous_center = (
                        (round(previous.center[0]), round(previous.center[1]))
                        if previous is not None
                        else (process_image.shape[1] // 2, process_image.shape[0] // 2)
                    )
                    tracked = detector.track_processed(
                        processed,
                        locked_target,
                        previous_center,
                        float(vision.get("max_lock_jump_px", 160.0)),
                    )
                    if tracked is not None:
                        detections = [tracked]
                        last_track_at = now
                        vision_state = "TRACKING"
                    else:
                        vision_state = "TEMPORARILY_LOST"
                        if now - last_track_at > 0.75:
                            reacquired = detector.search_processed(
                                processed, {locked_target}
                            )
                            if reacquired:
                                selected = max(
                                    reacquired,
                                    key=lambda item: item.total_score,
                                )
                                detector.begin_tracking(selected, now)
                                detections = [selected]
                                last_track_at = now
                                vision_state = "REACQUIRED"
                        if now - last_track_at > float(
                            config["safety"].get("max_lost_detection_s", 4.0)
                        ):
                            detector.reset_tracking()
                            tracker.reset()
                            locked_target = None
                            vision_state = "LOST"

            display_detections = [
                scale_detection(item, process_image.shape, latest_raw.shape)
                for item in detections
            ]
            raw_fps = (
                0.0
                if len(camera_times) < 2
                else (len(camera_times) - 1)
                / max(1e-6, camera_times[-1] - camera_times[0])
            )
            processed_fps = (
                0.0
                if len(process_times) < 2
                else (len(process_times) - 1)
                / max(1e-6, process_times[-1] - process_times[0])
            )
            desired = desired_point(
                config["control"], latest_raw.shape[1], latest_raw.shape[0]
            )
            latest_view = draw_live_overlay(
                latest_raw,
                display_detections,
                desired,
                raw_fps,
                processed_fps,
                camera_frame.capture_age_s(now) * 1000.0,
                vision_state,
                locked_target,
                tracker,
                detector,
                camera_frame.metadata,
            )
            if stream is not None:
                stream.publish(latest_view)
            if recording:
                if writer is None:
                    writer = cv2.VideoWriter(
                        str(output_dir / f"opencv-live-{time.strftime('%Y%m%d-%H%M%S')}.avi"),
                        cv2.VideoWriter_fourcc(*"MJPG"),
                        float(config["camera"].get("framerate", 30.0)),
                        (latest_view.shape[1], latest_view.shape[0]),
                    )
                writer.write(latest_view)
            if not args.headless:
                cv2.imshow("WD OpenCV Live Test", latest_view)
                if show_masks and latest_masks is not None:
                    cv2.imshow("OpenCV Red Mask", latest_masks["red"])
                    cv2.imshow("OpenCV Blue Mask", latest_masks["blue"])
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("s"):
                    stamp = time.strftime("%Y%m%d-%H%M%S")
                    cv2.imwrite(str(output_dir / f"{stamp}-raw.png"), latest_raw)
                    cv2.imwrite(str(output_dir / f"{stamp}-annotated.png"), latest_view)
                    if latest_masks is not None:
                        cv2.imwrite(str(output_dir / f"{stamp}-red-mask.png"), latest_masks["red"])
                        cv2.imwrite(str(output_dir / f"{stamp}-blue-mask.png"), latest_masks["blue"])
                    print(f"[SAVED] {output_dir}/{stamp}-*")
                elif key == ord("m"):
                    show_masks = not show_masks
                    if not show_masks:
                        cv2.destroyWindow("OpenCV Red Mask")
                        cv2.destroyWindow("OpenCV Blue Mask")
                elif key == ord("r"):
                    detector.reset_tracking()
                    tracker.reset()
                    locked_target = None
                    print("[TRACKER] reset")
                elif key == ord("v"):
                    recording = not recording
                    if not recording and writer is not None:
                        writer.release()
                        writer = None
                    print(f"[RECORDING] {'on' if recording else 'off'}")
    finally:
        reader.stop()
        if stream is not None:
            stream.stop()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()
    print(
        f"[DONE] captured={reader.captured} overwritten={reader.overwritten} "
        f"stream_encoded={0 if stream is None else stream.encoded}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
