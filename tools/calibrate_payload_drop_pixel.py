#!/usr/bin/env python3
"""Select the payload-drop reference pixel using the live Pi camera only."""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Optional

import cv2

from opencv_common import LatestCameraReader, draw_panel, load_profile, save_json


DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "target_mission_v2/configs/opencv_camera_diagnostics.json"
)


class Selection:
    """Maintain a full-resolution point while the preview may be resized."""

    def __init__(self) -> None:
        self.point: Optional[tuple[int, int]] = None
        self.preview_size = (1, 1)
        self.sensor_size = (1, 1)

    def set_sizes(
        self,
        preview_width: int,
        preview_height: int,
        sensor_width: int,
        sensor_height: int,
    ) -> None:
        self.preview_size = (preview_width, preview_height)
        self.sensor_size = (sensor_width, sensor_height)
        if self.point is None:
            self.point = (sensor_width // 2, sensor_height // 2)

    def mouse(self, event: int, x: int, y: int, _flags: int, _data: object) -> None:
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        preview_width, preview_height = self.preview_size
        sensor_width, sensor_height = self.sensor_size
        sensor_x = round(x * sensor_width / max(1, preview_width))
        sensor_y = round(y * sensor_height / max(1, preview_height))
        self.point = (
            min(sensor_width - 1, max(0, sensor_x)),
            min(sensor_height - 1, max(0, sensor_y)),
        )

    def move(self, dx: int, dy: int) -> None:
        if self.point is None:
            return
        sensor_width, sensor_height = self.sensor_size
        self.point = (
            min(sensor_width - 1, max(0, self.point[0] + dx)),
            min(sensor_height - 1, max(0, self.point[1] + dy)),
        )

    def preview_point(self) -> tuple[int, int]:
        if self.point is None:
            return (0, 0)
        preview_width, preview_height = self.preview_size
        sensor_width, sensor_height = self.sensor_size
        return (
            round(self.point[0] * preview_width / max(1, sensor_width)),
            round(self.point[1] * preview_height / max(1, sensor_height)),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("~/camera_tests/payload_drop_pixel.json"),
    )
    parser.add_argument("--display-width", type=int, default=960)
    parser.add_argument("--step", type=int, default=1)
    args = parser.parse_args()
    if args.display_width < 320:
        raise ValueError("--display-width must be at least 320")
    if args.step < 1:
        raise ValueError("--step must be positive")

    config = load_profile(args.config)
    reader = LatestCameraReader.from_config(config["camera"])
    reader.start()
    window = "WD Payload Drop Pixel Calibration"
    selection = Selection()
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, selection.mouse)
    last_frame_id = 0
    selected_frame_id = 0
    selected_metadata: dict = {}

    try:
        while True:
            frame = reader.latest()
            if frame is None or frame.frame_id == last_frame_id:
                if reader.failure:
                    raise RuntimeError(f"camera worker failed: {reader.failure}")
                time.sleep(0.003)
                continue
            last_frame_id = frame.frame_id
            sensor_height, sensor_width = frame.image_bgr.shape[:2]
            preview_width = min(args.display_width, sensor_width)
            preview_height = max(
                1, round(sensor_height * preview_width / sensor_width)
            )
            preview = cv2.resize(
                frame.image_bgr,
                (preview_width, preview_height),
                interpolation=cv2.INTER_AREA,
            )
            selection.set_sizes(
                preview_width,
                preview_height,
                sensor_width,
                sensor_height,
            )
            selected_frame_id = frame.frame_id
            selected_metadata = frame.metadata
            image_center = (preview_width // 2, preview_height // 2)
            selected = selection.preview_point()
            cv2.drawMarker(
                preview,
                image_center,
                (0, 255, 255),
                cv2.MARKER_CROSS,
                26,
                1,
            )
            cv2.drawMarker(
                preview,
                selected,
                (0, 0, 255),
                cv2.MARKER_CROSS,
                34,
                2,
            )
            cv2.line(preview, image_center, selected, (0, 0, 255), 1)
            point = selection.point or (sensor_width // 2, sensor_height // 2)
            view = draw_panel(
                preview,
                [
                    "Payload-drop pixel calibration | camera only",
                    f"Image center: {sensor_width // 2},{sensor_height // 2}",
                    f"Selected: {point[0]},{point[1]} | frame {frame.frame_id}",
                    "Click or use W/A/S/D | Enter saves | Esc cancels",
                    "No MAVLink | no servo command | mission config unchanged",
                ],
            )
            cv2.imshow(window, view)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                print("[CALIBRATION] cancelled; no file written")
                return 1
            if key in (10, 13):
                break
            if key == ord("w"):
                selection.move(0, -args.step)
            elif key == ord("a"):
                selection.move(-args.step, 0)
            elif key == ord("s"):
                selection.move(0, args.step)
            elif key == ord("d"):
                selection.move(args.step, 0)
    finally:
        reader.stop()
        cv2.destroyAllWindows()

    point = selection.point
    if point is None:
        raise RuntimeError("no calibration point selected")
    sensor_width, sensor_height = selection.sensor_size
    output = args.output.expanduser()
    save_json(
        output,
        {
            "created_unix_s": time.time(),
            "camera_profile": str(args.config),
            "camera_resolution": {
                "width": sensor_width,
                "height": sensor_height,
            },
            "desired_drop_pixel_x": point[0],
            "desired_drop_pixel_y": point[1],
            "desired_drop_x_normalized": point[0] / sensor_width,
            "desired_drop_y_normalized": point[1] / sensor_height,
            "frame_id": selected_frame_id,
            "camera_metadata": {
                key: selected_metadata.get(key)
                for key in (
                    "LensPosition",
                    "ExposureTime",
                    "AnalogueGain",
                    "ColourGains",
                )
            },
            "note": (
                "Review this calibration manually, then copy the selected values "
                "into the intended mission profile."
            ),
        },
    )
    print(f"[CALIBRATION SAVED] {output}")
    print(
        f"[SELECTED] x={point[0]} y={point[1]} "
        f"normalized=({point[0] / sensor_width:.6f},"
        f"{point[1] / sensor_height:.6f})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
