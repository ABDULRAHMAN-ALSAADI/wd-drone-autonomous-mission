#!/usr/bin/env python3
"""OpenCV-only target detection, temporal confirmation, and local tracking.

The mission recognizes two colour-and-shape pairs: a red triangle and a blue
hexagon. Colour segmentation proposes candidates; geometry must approve an
initial lock. Once confirmed, a bounded ROI tracker can temporarily tolerate
blur without allowing a colour-only blob to create a new mission target.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque, Dict, Iterable, Optional

import cv2
import numpy as np


VISION_BACKEND_STRICT_SHAPE = "strict_shape"
SUPPORTED_VISION_BACKENDS = {VISION_BACKEND_STRICT_SHAPE}


class TargetTrackState(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    TRACKING = "TRACKING"
    TEMPORARILY_LOST = "TEMPORARILY_LOST"
    LOST = "LOST"


@dataclass(frozen=True)
class Detection:
    # The first sixteen fields retain the original constructor/API.
    target: str
    center_x: int
    center_y: int
    area_px: float
    confidence: float
    vertices: int
    triangle_votes: int
    four_corner_votes: int
    hexagon_votes: int
    extent: float
    circularity: float
    solidity: float
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    frame_id: int = 0
    timestamp_s: float = 0.0
    area_fraction: float = 0.0
    colour_score: float = 0.0
    shape_score: float = 0.0
    total_score: float = 0.0
    partially_visible: bool = False
    source: str = "geometry_search"
    status: str = "valid_shape"
    debug_metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def bounding_box(self) -> tuple[int, int, int, int]:
        return self.bbox_x, self.bbox_y, self.bbox_w, self.bbox_h


@dataclass(frozen=True)
class ConfirmedTarget:
    target: str
    center_x: int
    center_y: int
    confidence: float
    hits: int
    observation_duration_s: float = 0.0
    hit_ratio: float = 0.0


@dataclass(frozen=True)
class TargetObservation:
    frame_id: int
    timestamp_s: float
    detected: bool
    target_class: Optional[str]
    center_px: Optional[tuple[float, float]]
    area_px: Optional[float]
    bounding_box: Optional[tuple[int, int, int, int]]
    colour_score: float
    shape_score: float
    total_score: float


@dataclass
class ProcessedVisionFrame:
    frame_id: int
    captured_at_s: float
    received_at_s: float
    bgr: np.ndarray
    hsv: np.ndarray
    red_mask: np.ndarray
    blue_mask: np.ndarray
    width: int
    height: int
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def masks(self) -> dict[str, np.ndarray]:
        return {"red": self.red_mask, "blue": self.blue_mask}

    def age_s(self, now: Optional[float] = None) -> float:
        return max(0.0, (time.monotonic() if now is None else now) - self.received_at_s)


@dataclass
class LocalTrackingState:
    target: str
    center: tuple[float, float]
    bbox: tuple[int, int, int, int]
    area_px: float
    velocity_px_s: tuple[float, float]
    last_valid_at: float
    last_strong_geometry_at: float
    confidence: float
    frame_id: int


class HitTracker:
    """FPS-independent target confirmation that records hits and misses."""

    TARGETS = ("red_triangle", "blue_hexagon")

    def __init__(
        self,
        required_hits: int = 3,
        window_s: float = 1.5,
        max_jump_px: float = 160.0,
        min_duration_s: float = 0.0,
        min_hit_ratio: float = 0.60,
        max_missing_ratio: float = 0.40,
        max_center_std_px: float = 80.0,
        max_area_cv: float = 0.75,
        max_bbox_cv: float = 0.75,
        max_area_jump_ratio: float = 4.0,
        min_colour_score: float = 0.0,
        min_shape_score: float = 0.0,
        min_total_score: float = 0.0,
    ) -> None:
        self.required_hits = int(required_hits)
        self.window_s = float(window_s)
        self.max_jump_px = float(max_jump_px)
        self.min_duration_s = float(min_duration_s)
        self.min_hit_ratio = float(min_hit_ratio)
        self.max_missing_ratio = float(max_missing_ratio)
        self.max_center_std_px = float(max_center_std_px)
        self.max_area_cv = float(max_area_cv)
        self.max_bbox_cv = float(max_bbox_cv)
        self.max_area_jump_ratio = float(max_area_jump_ratio)
        self.min_colour_score = float(min_colour_score)
        self.min_shape_score = float(min_shape_score)
        self.min_total_score = float(min_total_score)
        if not 0.0 <= self.min_hit_ratio <= 1.0:
            raise ValueError("confirmation minimum hit ratio must be between 0 and 1")
        if not 0.0 <= self.max_missing_ratio <= 1.0:
            raise ValueError("confirmation maximum missing ratio must be between 0 and 1")
        if self.max_area_jump_ratio < 1.0:
            raise ValueError("confirmation maximum area jump ratio must be at least 1")
        self.history: Dict[str, Deque[TargetObservation]] = {
            target: deque() for target in self.TARGETS
        }
        self.states = {target: TargetTrackState.NOT_FOUND for target in self.TARGETS}

    def reset(self) -> None:
        for target, history in self.history.items():
            history.clear()
            self.states[target] = TargetTrackState.NOT_FOUND

    def _prune(self, target: str, timestamp: float) -> None:
        history = self.history[target]
        while history and timestamp - history[0].timestamp_s > self.window_s:
            history.popleft()

    def status(self, now: Optional[float] = None) -> dict[str, int]:
        timestamp = time.monotonic() if now is None else float(now)
        output: dict[str, int] = {}
        for target in self.TARGETS:
            self._prune(target, timestamp)
            output[target] = sum(1 for item in self.history[target] if item.detected)
        return output

    def state(self, target: str) -> TargetTrackState:
        return self.states.get(target, TargetTrackState.NOT_FOUND)

    def update(
        self,
        detections: Iterable[Detection],
        allowed_targets: set[str],
        now: Optional[float] = None,
        frame_id: int = 0,
    ) -> Optional[ConfirmedTarget]:
        timestamp = time.monotonic() if now is None else float(now)
        by_target: dict[str, Detection] = {}
        for detection in detections:
            if detection.target in allowed_targets:
                current = by_target.get(detection.target)
                if current is None or detection.total_score > current.total_score:
                    by_target[detection.target] = detection

        confirmed: list[ConfirmedTarget] = []
        for target in self.TARGETS:
            if target not in allowed_targets:
                self.history[target].clear()
                self.states[target] = TargetTrackState.NOT_FOUND
                continue
            self._prune(target, timestamp)
            history = self.history[target]
            detection = by_target.get(target)

            if detection is not None:
                last_hit = next((item for item in reversed(history) if item.detected), None)
                if last_hit is not None and last_hit.center_px is not None:
                    jump = math.hypot(
                        detection.center_x - last_hit.center_px[0],
                        detection.center_y - last_hit.center_px[1],
                    )
                    area_ratio = (
                        max(float(detection.area_px), float(last_hit.area_px))
                        / max(1e-6, min(float(detection.area_px), float(last_hit.area_px)))
                        if last_hit.area_px is not None
                        else 1.0
                    )
                    if jump > self.max_jump_px or area_ratio > self.max_area_jump_ratio:
                        history.clear()
                observation = TargetObservation(
                    frame_id=detection.frame_id or frame_id,
                    timestamp_s=timestamp,
                    detected=True,
                    target_class=target,
                    center_px=(float(detection.center_x), float(detection.center_y)),
                    area_px=float(detection.area_px),
                    bounding_box=detection.bounding_box,
                    colour_score=float(detection.colour_score or detection.confidence),
                    shape_score=float(detection.shape_score or detection.confidence),
                    total_score=float(detection.total_score or detection.confidence),
                )
            else:
                observation = TargetObservation(
                    frame_id=frame_id,
                    timestamp_s=timestamp,
                    detected=False,
                    target_class=None,
                    center_px=None,
                    area_px=None,
                    bounding_box=None,
                    colour_score=0.0,
                    shape_score=0.0,
                    total_score=0.0,
                )
            history.append(observation)
            self._prune(target, timestamp)

            hits = [item for item in history if item.detected]
            self.states[target] = TargetTrackState.CANDIDATE if hits else TargetTrackState.NOT_FOUND
            if len(hits) < self.required_hits:
                continue
            duration = hits[-1].timestamp_s - hits[0].timestamp_s
            hit_ratio = len(hits) / max(1, len(history))
            missing_ratio = 1.0 - hit_ratio
            if (
                duration + 1e-9 < self.min_duration_s
                or hit_ratio < self.min_hit_ratio
                or missing_ratio > self.max_missing_ratio
            ):
                continue

            centers = np.asarray([item.center_px for item in hits if item.center_px is not None], dtype=np.float64)
            areas = np.asarray([item.area_px for item in hits if item.area_px is not None], dtype=np.float64)
            box_sizes = np.asarray(
                [
                    (item.bounding_box[2], item.bounding_box[3])
                    for item in hits
                    if item.bounding_box is not None
                ],
                dtype=np.float64,
            )
            if centers.size == 0 or areas.size == 0:
                continue
            center_std = float(np.sqrt(np.var(centers[:, 0]) + np.var(centers[:, 1])))
            area_mean = float(np.mean(areas))
            area_cv = float(np.std(areas) / max(1e-6, area_mean))
            bbox_cv = 0.0
            if box_sizes.size:
                width_cv = float(
                    np.std(box_sizes[:, 0]) / max(1e-6, np.mean(box_sizes[:, 0]))
                )
                height_cv = float(
                    np.std(box_sizes[:, 1]) / max(1e-6, np.mean(box_sizes[:, 1]))
                )
                bbox_cv = max(width_cv, height_cv)
            colour_average = float(np.mean([item.colour_score for item in hits]))
            shape_average = float(np.mean([item.shape_score for item in hits]))
            total_average = float(np.mean([item.total_score for item in hits]))
            if (
                center_std > self.max_center_std_px
                or area_cv > self.max_area_cv
                or bbox_cv > self.max_bbox_cv
            ):
                continue
            if colour_average < self.min_colour_score or shape_average < self.min_shape_score:
                continue
            if total_average < self.min_total_score:
                continue

            self.states[target] = TargetTrackState.CONFIRMED
            confirmed.append(
                ConfirmedTarget(
                    target=target,
                    center_x=round(float(np.mean(centers[:, 0]))),
                    center_y=round(float(np.mean(centers[:, 1]))),
                    confidence=total_average,
                    hits=len(hits),
                    observation_duration_s=duration,
                    hit_ratio=hit_ratio,
                )
            )
        return max(confirmed, key=lambda item: item.confidence) if confirmed else None


class StrictShapeDetector:
    DEFAULT_RED_HSV_RANGES = (
        ((0, 80, 45), (13, 255, 255)),
        ((168, 80, 45), (180, 255, 255)),
    )
    DEFAULT_BLUE_HSV_RANGE = ((88, 60, 35), (145, 255, 255))

    def __init__(
        self,
        search_min_area_px: float = 220.0,
        tracking_min_area_px: float = 120.0,
        debug_rejects: bool = False,
        red_hsv_ranges: Optional[Iterable[Iterable[Iterable[int]]]] = None,
        blue_hsv_range: Optional[Iterable[Iterable[int]]] = None,
        search_max_area_fraction: float = 0.45,
        search_border_margin_px: int = 2,
        min_area_fraction: float = 0.00015,
        min_bbox_width_px: int = 9,
        min_bbox_height_px: int = 9,
        min_contour_points: int = 3,
        morphology_kernel_shape: str = "ellipse",
        morphology_kernel_size: int = 3,
        opening_iterations: int = 1,
        closing_iterations: int = 1,
        fill_holes: bool = False,
        search_width: int = 480,
        max_candidates_per_colour: int = 4,
        roi_padding_fraction: float = 0.20,
        triangle_score_threshold: float = 0.57,
        hexagon_score_threshold: float = 0.55,
        tracking_roi_scale: float = 2.2,
        tracking_roi_min_padding_px: int = 36,
        tracking_max_image_speed_fraction_s: float = 0.80,
        strong_verify_interval_s: float = 0.5,
    ) -> None:
        self.search_min_area_px = float(search_min_area_px)
        self.tracking_min_area_px = float(tracking_min_area_px)
        self.debug_rejects = bool(debug_rejects)
        self.red_hsv_ranges = self._parse_hsv_ranges(
            red_hsv_ranges or self.DEFAULT_RED_HSV_RANGES, expected=2
        )
        self.blue_hsv_range = self._parse_hsv_ranges(
            (blue_hsv_range or self.DEFAULT_BLUE_HSV_RANGE,), expected=1
        )[0]
        self.search_max_area_fraction = float(search_max_area_fraction)
        self.search_border_margin_px = max(0, int(search_border_margin_px))
        self.min_area_fraction = max(0.0, float(min_area_fraction))
        self.min_bbox_width_px = max(1, int(min_bbox_width_px))
        self.min_bbox_height_px = max(1, int(min_bbox_height_px))
        self.min_contour_points = max(3, int(min_contour_points))
        self.opening_iterations = max(0, int(opening_iterations))
        self.closing_iterations = max(0, int(closing_iterations))
        self.fill_holes = bool(fill_holes)
        self.search_width = max(64, int(search_width))
        self.max_candidates_per_colour = max(1, int(max_candidates_per_colour))
        self.roi_padding_fraction = max(0.0, float(roi_padding_fraction))
        self.triangle_score_threshold = float(triangle_score_threshold)
        self.hexagon_score_threshold = float(hexagon_score_threshold)
        self.tracking_roi_scale = max(1.1, float(tracking_roi_scale))
        self.tracking_roi_min_padding_px = max(4, int(tracking_roi_min_padding_px))
        self.tracking_max_image_speed_fraction_s = max(
            0.05, float(tracking_max_image_speed_fraction_s)
        )
        self.strong_verify_interval_s = max(0.05, float(strong_verify_interval_s))
        if not 0.0 < self.search_max_area_fraction <= 1.0:
            raise ValueError("vision.search_max_area_fraction must be in (0, 1]")
        if morphology_kernel_size < 1 or morphology_kernel_size % 2 == 0:
            raise ValueError("vision.morphology_kernel_size must be a positive odd number")
        kernel_shapes = {
            "ellipse": cv2.MORPH_ELLIPSE,
            "rect": cv2.MORPH_RECT,
            "cross": cv2.MORPH_CROSS,
        }
        if morphology_kernel_shape not in kernel_shapes:
            raise ValueError("vision.morphology_kernel_shape must be ellipse, rect, or cross")
        self.kernel = cv2.getStructuringElement(
            kernel_shapes[morphology_kernel_shape],
            (int(morphology_kernel_size), int(morphology_kernel_size)),
        )
        self.epsilons = (0.008, 0.012, 0.016, 0.021, 0.027, 0.034)
        self.preprocess_counts = {"hsv": 0, "red_mask": 0, "blue_mask": 0}
        self.last_rejections: list[dict[str, Any]] = []
        self.tracking_state: Optional[LocalTrackingState] = None
        self._triangle_template = np.asarray(
            [[[50, 4]], [[4, 96]], [[96, 96]]], dtype=np.int32
        )
        self._hexagon_template = np.asarray(
            [[[6, 50]], [[28, 10]], [[72, 10]], [[94, 50]], [[72, 90]], [[28, 90]]],
            dtype=np.int32,
        )

    @staticmethod
    def _parse_hsv_ranges(
        values: Iterable[Iterable[Iterable[int]]], expected: int
    ) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
        parsed: list[tuple[np.ndarray, np.ndarray]] = []
        for value in values:
            endpoints = [list(endpoint) for endpoint in value]
            if len(endpoints) != 2 or any(len(endpoint) != 3 for endpoint in endpoints):
                raise ValueError("HSV ranges must contain lower and upper three-value endpoints")
            parsed.append(
                (
                    np.asarray(endpoints[0], dtype=np.uint8),
                    np.asarray(endpoints[1], dtype=np.uint8),
                )
            )
        if len(parsed) != expected:
            raise ValueError(f"Expected {expected} HSV range(s), received {len(parsed)}")
        return tuple(parsed)

    def reset_instrumentation(self) -> None:
        for key in self.preprocess_counts:
            self.preprocess_counts[key] = 0

    def _record_rejection(self, target: str, reason: str, metrics: dict[str, Any]) -> None:
        record = {"target": target, "reason": reason, **metrics}
        self.last_rejections.append(record)
        if len(self.last_rejections) > 200:
            del self.last_rejections[:-200]
        if self.debug_rejects:
            values = " ".join(
                f"{key}={value:.3f}" if isinstance(value, float) else f"{key}={value}"
                for key, value in metrics.items()
            )
            print(f"[VISION REJECT] {target}: {reason}; {values}")

    @staticmethod
    def _fill_mask_holes(mask: np.ndarray) -> np.ndarray:
        flood = mask.copy()
        padded = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), dtype=np.uint8)
        cv2.floodFill(flood, padded, (0, 0), 255)
        return cv2.bitwise_or(mask, cv2.bitwise_not(flood))

    def preprocess(
        self,
        frame: np.ndarray,
        frame_id: int = 0,
        captured_at_s: Optional[float] = None,
        received_at_s: Optional[float] = None,
    ) -> ProcessedVisionFrame:
        started = time.perf_counter()
        received = time.monotonic() if received_at_s is None else float(received_at_s)
        captured = received if captured_at_s is None else float(captured_at_s)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        self.preprocess_counts["hsv"] += 1
        hsv_done = time.perf_counter()

        red = cv2.bitwise_or(
            *(cv2.inRange(hsv, lower, upper) for lower, upper in self.red_hsv_ranges)
        )
        self.preprocess_counts["red_mask"] += 1
        red_done = time.perf_counter()
        blue = cv2.inRange(hsv, *self.blue_hsv_range)
        self.preprocess_counts["blue_mask"] += 1
        blue_done = time.perf_counter()

        red = self._morph(red)
        blue = self._morph(blue)
        morph_done = time.perf_counter()
        return ProcessedVisionFrame(
            frame_id=int(frame_id),
            captured_at_s=captured,
            received_at_s=received,
            bgr=frame,
            hsv=hsv,
            red_mask=red,
            blue_mask=blue,
            width=int(frame.shape[1]),
            height=int(frame.shape[0]),
            timings_ms={
                "hsv": (hsv_done - started) * 1000.0,
                "red_mask": (red_done - hsv_done) * 1000.0,
                "blue_mask": (blue_done - red_done) * 1000.0,
                "morphology": (morph_done - blue_done) * 1000.0,
                "preprocess_total": (morph_done - started) * 1000.0,
            },
        )

    def _morph(self, mask: np.ndarray) -> np.ndarray:
        output = mask
        if self.opening_iterations:
            output = cv2.morphologyEx(
                output, cv2.MORPH_OPEN, self.kernel, iterations=self.opening_iterations
            )
        if self.closing_iterations:
            output = cv2.morphologyEx(
                output, cv2.MORPH_CLOSE, self.kernel, iterations=self.closing_iterations
            )
        if self.fill_holes:
            output = self._fill_mask_holes(output)
        return output

    def masks(self, frame: np.ndarray) -> dict[str, np.ndarray]:
        return self.preprocess(frame).masks

    @staticmethod
    def empty_masks(frame: np.ndarray) -> dict[str, np.ndarray]:
        shape = frame.shape[:2]
        return {
            "red": np.zeros(shape, dtype=np.uint8),
            "blue": np.zeros(shape, dtype=np.uint8),
        }

    @staticmethod
    def _metrics(contour: np.ndarray, area: float, perimeter: float) -> tuple[float, float, float]:
        hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
        solidity = area / hull_area if hull_area > 0 else 0.0
        width, height = cv2.minAreaRect(contour)[1]
        rect_area = float(width * height)
        extent = area / rect_area if rect_area > 0 else 0.0
        circularity = 4.0 * math.pi * area / (perimeter * perimeter) if perimeter > 0 else 0.0
        return solidity, extent, circularity

    def _approximations(self, contour: np.ndarray, perimeter: float) -> list[np.ndarray]:
        return [cv2.approxPolyDP(contour, epsilon * perimeter, True) for epsilon in self.epsilons]

    @staticmethod
    def _side_ratio(approximation: np.ndarray) -> float:
        points = approximation.reshape(-1, 2).astype(np.float64)
        if len(points) < 3:
            return float("inf")
        lengths = [
            float(np.linalg.norm(points[(idx + 1) % len(points)] - points[idx]))
            for idx in range(len(points))
        ]
        return max(lengths) / max(1e-6, min(lengths))

    @staticmethod
    def _angles(approximation: np.ndarray) -> list[float]:
        points = approximation.reshape(-1, 2).astype(np.float64)
        angles: list[float] = []
        for idx in range(len(points)):
            a = points[idx - 1] - points[idx]
            b = points[(idx + 1) % len(points)] - points[idx]
            denominator = max(1e-9, float(np.linalg.norm(a) * np.linalg.norm(b)))
            cosine = float(np.clip(np.dot(a, b) / denominator, -1.0, 1.0))
            angles.append(math.degrees(math.acos(cosine)))
        return angles

    @staticmethod
    def _closeness(value: float, ideal: float, tolerance: float) -> float:
        return max(0.0, 1.0 - abs(value - ideal) / max(1e-9, tolerance))

    def _scale_status(
        self, contour: np.ndarray, area: float, processed: ProcessedVisionFrame
    ) -> tuple[bool, str, dict[str, Any]]:
        x, y, width, height = cv2.boundingRect(contour)
        area_fraction = area / float(max(1, processed.width * processed.height))
        metrics = {
            "area_px": area,
            "area_fraction": area_fraction,
            "bbox_w": width,
            "bbox_h": height,
            "contour_points": len(contour),
        }
        minimum_area = max(
            self.search_min_area_px,
            self.min_area_fraction * processed.width * processed.height,
        )
        if area < minimum_area:
            return False, "too_small_for_shape_verification", metrics
        if width < self.min_bbox_width_px or height < self.min_bbox_height_px:
            return False, "rejected_scale", metrics
        if len(contour) < self.min_contour_points:
            return False, "rejected_scale", metrics
        if area_fraction > self.search_max_area_fraction:
            return False, "rejected_scale", metrics
        return True, "valid_shape", metrics

    def _is_partially_visible(
        self, contour: np.ndarray, processed: ProcessedVisionFrame
    ) -> bool:
        x, y, width, height = cv2.boundingRect(contour)
        margin = self.search_border_margin_px
        return (
            x <= margin
            or y <= margin
            or x + width >= processed.width - margin
            or y + height >= processed.height - margin
        )

    @staticmethod
    def _global_contour(contour: np.ndarray, offset_x: int, offset_y: int) -> np.ndarray:
        output = contour.copy()
        output[:, 0, 0] += int(offset_x)
        output[:, 0, 1] += int(offset_y)
        return output

    def _coarse_candidates(
        self, mask: np.ndarray, processed: ProcessedVisionFrame
    ) -> list[np.ndarray]:
        if processed.width > self.search_width:
            scale = self.search_width / processed.width
            low_size = (self.search_width, max(1, round(processed.height * scale)))
            low_mask = cv2.resize(mask, low_size, interpolation=cv2.INTER_NEAREST)
        else:
            scale = 1.0
            low_mask = mask
        count, _, stats, _ = cv2.connectedComponentsWithStats(low_mask, connectivity=8)
        ranked: list[tuple[int, int, int, int, int]] = []
        for label in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[label])
            if area <= 0 or width < 2 or height < 2:
                continue
            ranked.append((area, x, y, width, height))
        ranked.sort(reverse=True)

        contours: list[np.ndarray] = []
        for _, low_x, low_y, low_w, low_h in ranked[: self.max_candidates_per_colour]:
            inv_scale = 1.0 / scale
            x = max(0, math.floor(low_x * inv_scale))
            y = max(0, math.floor(low_y * inv_scale))
            right = min(processed.width, math.ceil((low_x + low_w) * inv_scale))
            bottom = min(processed.height, math.ceil((low_y + low_h) * inv_scale))
            padding = round(max(right - x, bottom - y) * self.roi_padding_fraction)
            x = max(0, x - padding)
            y = max(0, y - padding)
            right = min(processed.width, right + padding)
            bottom = min(processed.height, bottom + padding)
            roi = mask[y:bottom, x:right]
            roi_contours, _ = cv2.findContours(
                roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            if not roi_contours:
                continue
            largest = max(roi_contours, key=cv2.contourArea)
            contours.append(self._global_contour(largest, x, y))
        return contours

    def _make_detection(
        self,
        contour: np.ndarray,
        approximation: np.ndarray,
        target: str,
        processed: ProcessedVisionFrame,
        confidence: float,
        triangle_votes: int,
        four_corner_votes: int,
        hexagon_votes: int,
        extent: float,
        circularity: float,
        solidity: float,
        colour_score: float,
        shape_score: float,
        partially_visible: bool,
        source: str,
        debug_metrics: dict[str, Any],
    ) -> Optional[Detection]:
        moments = cv2.moments(contour)
        if abs(moments["m00"]) < 1e-9:
            return None
        area = float(cv2.contourArea(contour))
        x, y, width, height = cv2.boundingRect(contour)
        total_score = 0.25 * colour_score + 0.75 * shape_score
        return Detection(
            target=target,
            center_x=int(moments["m10"] / moments["m00"]),
            center_y=int(moments["m01"] / moments["m00"]),
            area_px=round(area, 1),
            confidence=round(confidence, 3),
            vertices=len(approximation),
            triangle_votes=triangle_votes,
            four_corner_votes=four_corner_votes,
            hexagon_votes=hexagon_votes,
            extent=round(extent, 3),
            circularity=round(circularity, 3),
            solidity=round(solidity, 3),
            bbox_x=x,
            bbox_y=y,
            bbox_w=width,
            bbox_h=height,
            frame_id=processed.frame_id,
            timestamp_s=processed.received_at_s,
            area_fraction=area / float(max(1, processed.width * processed.height)),
            colour_score=round(colour_score, 3),
            shape_score=round(shape_score, 3),
            total_score=round(total_score, 3),
            partially_visible=partially_visible,
            source=source,
            status="partially_visible" if partially_visible else "valid_shape",
            debug_metrics=debug_metrics,
        )

    def _triangle(
        self, contour: np.ndarray, processed: ProcessedVisionFrame, source: str
    ) -> Optional[Detection]:
        area = float(cv2.contourArea(contour))
        perimeter = float(cv2.arcLength(contour, True))
        if perimeter <= 0:
            return None
        approximations = self._approximations(contour, perimeter)
        counts = [len(item) for item in approximations]
        triangles = [
            item for item in approximations if len(item) == 3 and cv2.isContourConvex(item)
        ]
        triangle_votes = len(triangles)
        four_votes = sum(count == 4 for count in counts)
        hex_votes = sum(5 <= count <= 7 for count in counts)
        solidity, extent, circularity = self._metrics(contour, area, perimeter)
        x, y, width, height = cv2.boundingRect(contour)
        rotated_width, rotated_height = cv2.minAreaRect(contour)[1]
        aspect = max(rotated_width, rotated_height) / max(
            1e-6, min(rotated_width, rotated_height)
        )
        partially_visible = self._is_partially_visible(contour, processed)
        colour_score = min(1.0, area / float(max(1, width * height)) / 0.72)

        _, enclosing_triangle = cv2.minEnclosingTriangle(
            contour.astype(np.float32)
        )
        enclosing_area = (
            float(cv2.contourArea(enclosing_triangle.astype(np.float32)))
            if enclosing_triangle is not None
            else 0.0
        )
        enclosing_ratio = area / enclosing_area if enclosing_area > 0 else 0.0
        match_value = float(
            cv2.matchShapes(contour, self._triangle_template, cv2.CONTOURS_MATCH_I1, 0.0)
        )
        match_score = math.exp(-5.0 * max(0.0, match_value))
        approximation = (
            triangles[len(triangles) // 2]
            if triangles
            else min(approximations, key=lambda item: abs(len(item) - 3))
        )
        angles = self._angles(approximation) if len(approximation) == 3 else []
        angle_score = (
            1.0
            if angles and min(angles) >= 20.0 and max(angles) <= 140.0
            else 0.35 if angles else 0.0
        )
        side_ratio = self._side_ratio(approximation)
        side_score = max(0.0, 1.0 - (side_ratio - 1.0) / 3.0)
        vertex_score = triangle_votes / len(self.epsilons)
        shape_score = (
            0.25 * vertex_score
            + 0.20 * self._closeness(enclosing_ratio, 0.98, 0.38)
            + 0.15 * match_score
            + 0.10 * self._closeness(extent, 0.50, 0.28)
            + 0.08 * self._closeness(circularity, 0.60, 0.25)
            + 0.07 * min(1.0, solidity)
            + 0.07 * angle_score
            + 0.05 * side_score
            + 0.03 * float(cv2.isContourConvex(cv2.convexHull(contour)))
        )
        debug = {
            "vertex_counts": counts,
            "enclosing_triangle_ratio": round(enclosing_ratio, 4),
            "match_shapes": round(match_value, 4),
            "side_ratio": round(side_ratio, 3),
            "angles": [round(value, 1) for value in angles],
            "rotated_aspect": round(aspect, 3),
            "colour_score": round(colour_score, 3),
            "shape_score": round(shape_score, 3),
        }
        if partially_visible:
            self._record_rejection("red_triangle", "partially_visible", debug)
            return None
        if aspect > 3.2 or solidity < 0.72:
            self._record_rejection("red_triangle", "thin_or_low_solidity", debug)
            return None
        if four_votes >= 2 and triangle_votes < 2:
            self._record_rejection("red_triangle", "obvious_four_corner_shape", debug)
            return None
        if extent > 0.76 and triangle_votes < 2:
            self._record_rejection("red_triangle", "obvious_rectangle", debug)
            return None
        if circularity > 0.86 and triangle_votes == 0:
            self._record_rejection("red_triangle", "obvious_circle", debug)
            return None
        if shape_score < self.triangle_score_threshold:
            self._record_rejection("red_triangle", "triangle_score_below_threshold", debug)
            return None
        return self._make_detection(
            contour,
            approximation,
            "red_triangle",
            processed,
            shape_score,
            triangle_votes,
            four_votes,
            hex_votes,
            extent,
            circularity,
            solidity,
            colour_score,
            shape_score,
            partially_visible,
            source,
            debug,
        )

    def _hexagon(
        self, contour: np.ndarray, processed: ProcessedVisionFrame, source: str
    ) -> Optional[Detection]:
        hull = cv2.convexHull(contour)
        area = float(cv2.contourArea(hull))
        perimeter = float(cv2.arcLength(hull, True))
        if area <= 0 or perimeter <= 0:
            return None
        approximations = self._approximations(hull, perimeter)
        counts = [len(item) for item in approximations]
        triangle_votes = sum(count == 3 for count in counts)
        four_votes = sum(count == 4 for count in counts)
        near_hexagons = [
            item
            for item in approximations
            if 5 <= len(item) <= 7 and cv2.isContourConvex(item)
        ]
        exact_hexagons = [item for item in near_hexagons if len(item) == 6]
        hex_votes = len(exact_hexagons)
        approximation = (
            min(near_hexagons, key=lambda item: abs(len(item) - 6))
            if near_hexagons
            else min(approximations, key=lambda item: abs(len(item) - 6))
        )
        solidity, extent, circularity = self._metrics(hull, area, perimeter)
        x, y, width, height = cv2.boundingRect(hull)
        rotated_width, rotated_height = cv2.minAreaRect(hull)[1]
        aspect = max(rotated_width, rotated_height) / max(
            1e-6, min(rotated_width, rotated_height)
        )
        partially_visible = self._is_partially_visible(hull, processed)
        colour_score = min(1.0, area / float(max(1, width * height)) / 0.82)
        match_value = float(
            cv2.matchShapes(hull, self._hexagon_template, cv2.CONTOURS_MATCH_I1, 0.0)
        )
        match_score = math.exp(-6.0 * max(0.0, match_value))
        side_ratio = self._side_ratio(approximation)
        side_score = max(0.0, 1.0 - (side_ratio - 1.0) / 1.8)
        angles = self._angles(approximation)
        if len(approximation) == 6 and angles:
            angle_score = float(
                np.mean([self._closeness(value, 120.0, 50.0) for value in angles])
            )
        elif len(approximation) in {5, 7}:
            angle_score = 0.55
        else:
            angle_score = 0.0
        vertex_scores = [
            1.0 if count == 6 else 0.65 if count in {5, 7} else 0.0 for count in counts
        ]
        vertex_score = float(np.mean(vertex_scores))
        hull_ratio = float(cv2.contourArea(contour)) / max(1e-6, area)
        shape_score = (
            0.24 * vertex_score
            + 0.16 * match_score
            + 0.12 * self._closeness(extent, 0.75, 0.22)
            + 0.12 * self._closeness(circularity, 0.84, 0.24)
            + 0.09 * self._closeness(aspect, 1.0, 0.65)
            + 0.08 * side_score
            + 0.07 * angle_score
            + 0.07 * min(1.0, solidity)
            + 0.05 * min(1.0, hull_ratio)
        )
        debug = {
            "vertex_counts": counts,
            "match_shapes": round(match_value, 4),
            "side_ratio": round(side_ratio, 3),
            "angles": [round(value, 1) for value in angles],
            "rotated_aspect": round(aspect, 3),
            "hull_ratio": round(hull_ratio, 3),
            "colour_score": round(colour_score, 3),
            "shape_score": round(shape_score, 3),
        }
        if partially_visible:
            self._record_rejection("blue_hexagon", "partially_visible", debug)
            return None
        if aspect > 1.85 or solidity < 0.78:
            self._record_rejection("blue_hexagon", "thin_or_low_solidity", debug)
            return None
        if four_votes >= 2 and not near_hexagons:
            self._record_rejection("blue_hexagon", "obvious_four_corner_shape", debug)
            return None
        if extent > 0.84 and hex_votes == 0:
            self._record_rejection("blue_hexagon", "obvious_rectangle", debug)
            return None
        if circularity > 0.94 and not near_hexagons:
            self._record_rejection("blue_hexagon", "obvious_circle", debug)
            return None
        if shape_score < self.hexagon_score_threshold:
            self._record_rejection("blue_hexagon", "hexagon_score_below_threshold", debug)
            return None
        return self._make_detection(
            hull,
            approximation,
            "blue_hexagon",
            processed,
            shape_score,
            triangle_votes,
            four_votes,
            hex_votes,
            extent,
            circularity,
            solidity,
            colour_score,
            shape_score,
            partially_visible,
            source,
            debug,
        )

    def search_processed(
        self,
        processed: ProcessedVisionFrame,
        allowed_targets: Optional[set[str]] = None,
    ) -> list[Detection]:
        self.last_rejections.clear()
        allowed = allowed_targets or {"red_triangle", "blue_hexagon"}
        detections: list[Detection] = []
        for target, mask in (
            ("red_triangle", processed.red_mask),
            ("blue_hexagon", processed.blue_mask),
        ):
            if target not in allowed:
                continue
            best: Optional[Detection] = None
            for contour in self._coarse_candidates(mask, processed):
                area = float(cv2.contourArea(contour))
                valid, reason, metrics = self._scale_status(contour, area, processed)
                if not valid:
                    self._record_rejection(target, reason, metrics)
                    continue
                item = (
                    self._triangle(contour, processed, "geometry_search")
                    if target == "red_triangle"
                    else self._hexagon(contour, processed, "geometry_search")
                )
                if item is not None and (
                    best is None or item.total_score > best.total_score
                ):
                    best = item
            if best is not None:
                detections.append(best)
        return detections

    def search(
        self, frame: np.ndarray
    ) -> tuple[list[Detection], dict[str, np.ndarray]]:
        processed = self.preprocess(frame)
        return self.search_processed(processed), processed.masks

    def begin_tracking(self, detection: Detection, now: Optional[float] = None) -> None:
        timestamp = time.monotonic() if now is None else float(now)
        self.tracking_state = LocalTrackingState(
            target=detection.target,
            center=(float(detection.center_x), float(detection.center_y)),
            bbox=detection.bounding_box,
            area_px=float(detection.area_px),
            velocity_px_s=(0.0, 0.0),
            last_valid_at=timestamp,
            last_strong_geometry_at=timestamp,
            confidence=float(detection.total_score or detection.confidence),
            frame_id=detection.frame_id,
        )

    def reset_tracking(self) -> None:
        self.tracking_state = None

    def _tracking_roi(
        self,
        processed: ProcessedVisionFrame,
        target: str,
        previous_center: tuple[int, int],
        max_jump_px: float,
    ) -> tuple[int, int, int, int, tuple[float, float], float]:
        state = self.tracking_state
        now = processed.received_at_s
        if state is None or state.target != target:
            box_size = max(64, round(max_jump_px * 1.20))
            bbox = (
                round(previous_center[0] - box_size / 2),
                round(previous_center[1] - box_size / 2),
                box_size,
                box_size,
            )
            predicted = (float(previous_center[0]), float(previous_center[1]))
            dt = 0.0
        else:
            dt = max(0.0, now - state.last_valid_at)
            predicted = (
                state.center[0] + state.velocity_px_s[0] * dt,
                state.center[1] + state.velocity_px_s[1] * dt,
            )
            bbox = state.bbox
        _, _, width, height = bbox
        half_width = max(
            self.tracking_roi_min_padding_px,
            round(width * self.tracking_roi_scale / 2.0),
        )
        half_height = max(
            self.tracking_roi_min_padding_px,
            round(height * self.tracking_roi_scale / 2.0),
        )
        dynamic_gate = max_jump_px + dt * self.tracking_max_image_speed_fraction_s * max(
            processed.width, processed.height
        )
        left = max(0, round(predicted[0] - half_width))
        top = max(0, round(predicted[1] - half_height))
        right = min(processed.width, round(predicted[0] + half_width))
        bottom = min(processed.height, round(predicted[1] + half_height))
        return left, top, right, bottom, predicted, dynamic_gate

    def _colour_tracking_detection(
        self,
        contour: np.ndarray,
        target: str,
        processed: ProcessedVisionFrame,
        previous: Optional[LocalTrackingState],
    ) -> Optional[Detection]:
        area = float(cv2.contourArea(contour))
        perimeter = float(cv2.arcLength(contour, True))
        if perimeter <= 0:
            return None
        solidity, extent, circularity = self._metrics(contour, area, perimeter)
        x, y, width, height = cv2.boundingRect(contour)
        aspect = max(width, height) / max(1.0, min(width, height))
        if previous is not None:
            area_ratio = area / max(1.0, previous.area_px)
            old_width = max(1, previous.bbox[2])
            old_height = max(1, previous.bbox[3])
            scale_ratio = max(width / old_width, height / old_height)
            if not 0.42 <= area_ratio <= 2.40 or not 0.55 <= scale_ratio <= 1.85:
                return None
        if target == "red_triangle":
            valid = solidity >= 0.68 and extent <= 0.83 and 0.20 <= circularity <= 0.84
        elif target == "blue_hexagon":
            valid = (
                solidity >= 0.76
                and 0.46 <= extent <= 0.86
                and 0.50 <= circularity <= 0.98
                and aspect <= 1.85
            )
        else:
            return None
        if not valid:
            return None
        approximation = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        colour_score = min(1.0, area / float(max(1, width * height)) / 0.80)
        return self._make_detection(
            contour,
            approximation,
            target,
            processed,
            0.55,
            0,
            0,
            0,
            extent,
            circularity,
            solidity,
            colour_score,
            0.40,
            self._is_partially_visible(contour, processed),
            "colour_track",
            {"tracking_fallback": True, "rotated_aspect": round(aspect, 3)},
        )

    def track_processed(
        self,
        processed: ProcessedVisionFrame,
        target: str,
        previous_center: tuple[int, int],
        max_jump_px: float = 220.0,
    ) -> Optional[Detection]:
        if target not in {"red_triangle", "blue_hexagon"}:
            return None
        left, top, right, bottom, predicted, movement_gate = self._tracking_roi(
            processed, target, previous_center, max_jump_px
        )
        mask = processed.red_mask if target == "red_triangle" else processed.blue_mask
        roi = mask[top:bottom, left:right]
        contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        previous = self.tracking_state if self.tracking_state and self.tracking_state.target == target else None
        options: list[tuple[float, Detection, bool]] = []
        now = processed.received_at_s
        require_strong = (
            previous is None
            or now - previous.last_strong_geometry_at >= self.strong_verify_interval_s
        )
        for local_contour in contours:
            contour = self._global_contour(local_contour, left, top)
            area = float(cv2.contourArea(contour))
            minimum_area = max(
                self.tracking_min_area_px,
                self.min_area_fraction * processed.width * processed.height * 0.5,
            )
            if area < minimum_area:
                continue
            strict = (
                self._triangle(contour, processed, "geometry_track")
                if target == "red_triangle"
                else self._hexagon(contour, processed, "geometry_track")
            )
            item = strict
            if item is None:
                item = self._colour_tracking_detection(contour, target, processed, previous)
            if item is None:
                continue
            distance = math.hypot(item.center_x - predicted[0], item.center_y - predicted[1])
            if distance > movement_gate:
                continue
            area_penalty = 0.0
            if previous is not None:
                area_penalty = abs(math.log(max(1e-6, item.area_px / previous.area_px)))
            association_cost = distance / max(1.0, movement_gate) + 0.35 * area_penalty
            options.append((association_cost, item, strict is not None))
        if not options:
            return None

        _, selected, strong = min(options, key=lambda value: value[0])
        if previous is None:
            velocity = (0.0, 0.0)
            last_strong = now if strong else 0.0
        else:
            dt = max(1e-3, now - previous.last_valid_at)
            measured_velocity = (
                (selected.center_x - previous.center[0]) / dt,
                (selected.center_y - previous.center[1]) / dt,
            )
            velocity = (
                0.55 * measured_velocity[0] + 0.45 * previous.velocity_px_s[0],
                0.55 * measured_velocity[1] + 0.45 * previous.velocity_px_s[1],
            )
            last_strong = now if strong else previous.last_strong_geometry_at
        self.tracking_state = LocalTrackingState(
            target=target,
            center=(float(selected.center_x), float(selected.center_y)),
            bbox=selected.bounding_box,
            area_px=float(selected.area_px),
            velocity_px_s=velocity,
            last_valid_at=now,
            last_strong_geometry_at=last_strong,
            confidence=float(selected.total_score or selected.confidence),
            frame_id=processed.frame_id,
        )
        return selected

    def track_colour(
        self,
        frame: np.ndarray,
        target: str,
        previous_center: tuple[int, int],
        max_jump_px: float = 220.0,
    ) -> tuple[Optional[Detection], dict[str, np.ndarray]]:
        processed = self.preprocess(frame)
        return (
            self.track_processed(processed, target, previous_center, max_jump_px),
            processed.masks,
        )


def create_detector(vision_config: dict[str, Any]) -> StrictShapeDetector:
    backend = vision_config.get("backend", VISION_BACKEND_STRICT_SHAPE)
    if backend != VISION_BACKEND_STRICT_SHAPE:
        supported = ", ".join(sorted(SUPPORTED_VISION_BACKENDS))
        raise ValueError(
            f"Unsupported vision.backend {backend!r}. Supported backends: {supported}"
        )
    return StrictShapeDetector(
        search_min_area_px=float(vision_config["search_min_area_px"]),
        tracking_min_area_px=float(vision_config["tracking_min_area_px"]),
        debug_rejects=bool(vision_config.get("debug_rejects", False)),
        red_hsv_ranges=vision_config.get("red_hsv_ranges"),
        blue_hsv_range=vision_config.get("blue_hsv_range"),
        search_max_area_fraction=float(
            vision_config.get("search_max_area_fraction", 0.45)
        ),
        search_border_margin_px=int(
            vision_config.get("search_border_margin_px", 2)
        ),
        min_area_fraction=float(vision_config.get("min_area_fraction", 0.00015)),
        min_bbox_width_px=int(vision_config.get("min_bbox_width_px", 9)),
        min_bbox_height_px=int(vision_config.get("min_bbox_height_px", 9)),
        min_contour_points=int(vision_config.get("min_contour_points", 3)),
        morphology_kernel_shape=str(
            vision_config.get("morphology_kernel_shape", "ellipse")
        ),
        morphology_kernel_size=int(
            vision_config.get("morphology_kernel_size", 3)
        ),
        opening_iterations=int(vision_config.get("opening_iterations", 1)),
        closing_iterations=int(vision_config.get("closing_iterations", 1)),
        fill_holes=bool(vision_config.get("fill_holes", False)),
        search_width=int(vision_config.get("search_width", 480)),
        max_candidates_per_colour=int(
            vision_config.get("max_candidates_per_colour", 4)
        ),
        roi_padding_fraction=float(
            vision_config.get("roi_padding_fraction", 0.20)
        ),
        triangle_score_threshold=float(
            vision_config.get("triangle_score_threshold", 0.57)
        ),
        hexagon_score_threshold=float(
            vision_config.get("hexagon_score_threshold", 0.55)
        ),
        tracking_roi_scale=float(vision_config.get("tracking_roi_scale", 2.2)),
        tracking_roi_min_padding_px=int(
            vision_config.get("tracking_roi_min_padding_px", 36)
        ),
        tracking_max_image_speed_fraction_s=float(
            vision_config.get("tracking_max_image_speed_fraction_s", 0.80)
        ),
        strong_verify_interval_s=float(
            vision_config.get("strong_verify_interval_s", 0.5)
        ),
    )
