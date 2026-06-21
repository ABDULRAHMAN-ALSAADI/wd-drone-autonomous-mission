#!/usr/bin/env python3
"""Target detection for the active mission.

The current backend is intentionally strict classical computer vision. It looks
for the competition shapes and rejects fixed-wing rectangle/square targets. A
future YOLO/AI-HAT backend should be added beside this code, not by removing the
shape-safety checks.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, Optional

import cv2
import numpy as np


VISION_BACKEND_STRICT_SHAPE = "strict_shape"
SUPPORTED_VISION_BACKENDS = {VISION_BACKEND_STRICT_SHAPE}


@dataclass(frozen=True)
class Detection:
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


@dataclass(frozen=True)
class ConfirmedTarget:
    target: str
    center_x: int
    center_y: int
    confidence: float
    hits: int


class HitTracker:
    def __init__(self, required_hits: int = 3, window_s: float = 1.5, max_jump_px: float = 160.0) -> None:
        self.required_hits = required_hits
        self.window_s = window_s
        self.max_jump_px = max_jump_px
        self.history: Dict[str, Deque[tuple[float, Detection]]] = {
            "red_triangle": deque(),
            "blue_hexagon": deque(),
        }

    def reset(self) -> None:
        for history in self.history.values():
            history.clear()

    def status(self, now: Optional[float] = None) -> dict[str, int]:
        timestamp = time.monotonic() if now is None else now
        out: dict[str, int] = {}
        for target, history in self.history.items():
            while history and timestamp - history[0][0] > self.window_s:
                history.popleft()
            out[target] = len(history)
        return out

    def update(self, detections: Iterable[Detection], allowed_targets: set[str], now: Optional[float] = None) -> Optional[ConfirmedTarget]:
        timestamp = time.monotonic() if now is None else now
        by_target = {item.target: item for item in detections if item.target in allowed_targets}
        confirmed: list[ConfirmedTarget] = []

        for target in allowed_targets:
            history = self.history[target]
            while history and timestamp - history[0][0] > self.window_s:
                history.popleft()

            item = by_target.get(target)
            if item is None:
                continue

            if history:
                previous = history[-1][1]
                if math.hypot(item.center_x - previous.center_x, item.center_y - previous.center_y) > self.max_jump_px:
                    history.clear()

            history.append((timestamp, item))
            if len(history) < self.required_hits:
                continue

            recent = list(history)[-self.required_hits:]
            confirmed.append(
                ConfirmedTarget(
                    target=target,
                    center_x=round(sum(x.center_x for _, x in recent) / len(recent)),
                    center_y=round(sum(x.center_y for _, x in recent) / len(recent)),
                    confidence=sum(x.confidence for _, x in recent) / len(recent),
                    hits=len(history),
                )
            )

        return max(confirmed, key=lambda x: x.confidence) if confirmed else None


class StrictShapeDetector:
    def __init__(self, search_min_area_px: float = 220.0, tracking_min_area_px: float = 120.0, debug_rejects: bool = False) -> None:
        self.search_min_area_px = search_min_area_px
        self.tracking_min_area_px = tracking_min_area_px
        self.debug_rejects = debug_rejects
        self.kernel = np.ones((3, 3), dtype=np.uint8)
        self.epsilons = (0.008, 0.012, 0.016, 0.021, 0.027, 0.034)

    def _debug(self, target: str, reason: str, area: float) -> None:
        if self.debug_rejects:
            print(f"[VISION REJECT] {target}: {reason}; area={area:.0f}")

    def masks(self, frame: np.ndarray) -> dict[str, np.ndarray]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        red = cv2.bitwise_or(
            cv2.inRange(hsv, np.array([0, 65, 45], np.uint8), np.array([13, 255, 255], np.uint8)),
            cv2.inRange(hsv, np.array([168, 65, 45], np.uint8), np.array([180, 255, 255], np.uint8)),
        )
        blue = cv2.inRange(hsv, np.array([82, 35, 30], np.uint8), np.array([158, 255, 255], np.uint8))
        for mask_name, mask in (("red", red), ("blue", blue)):
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel, iterations=1)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.kernel, iterations=1)
            if mask_name == "red":
                red = mask
            else:
                blue = mask
        return {"red": red, "blue": blue}

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
        return [cv2.approxPolyDP(contour, e * perimeter, True) for e in self.epsilons]

    @staticmethod
    def _side_ratio(approximation: np.ndarray) -> float:
        points = approximation.reshape(-1, 2).astype(np.float64)
        if len(points) < 3:
            return float("inf")
        lengths = [
            float(np.linalg.norm(points[(idx + 1) % len(points)] - points[idx]))
            for idx in range(len(points))
        ]
        shortest = max(1e-6, min(lengths))
        return max(lengths) / shortest

    @staticmethod
    def _angle_range(approximation: np.ndarray) -> tuple[float, float]:
        points = approximation.reshape(-1, 2).astype(np.float64)
        angles: list[float] = []
        for idx in range(len(points)):
            prev_point = points[idx - 1]
            point = points[idx]
            next_point = points[(idx + 1) % len(points)]
            a = prev_point - point
            b = next_point - point
            denom = max(1e-9, float(np.linalg.norm(a) * np.linalg.norm(b)))
            cos_angle = float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))
            angles.append(math.degrees(math.acos(cos_angle)))
        return min(angles), max(angles)

    @staticmethod
    def _make(contour: np.ndarray, approximation: np.ndarray, target: str, area: float, confidence: float,
              triangle_votes: int, four_corner_votes: int, hexagon_votes: int,
              extent: float, circularity: float, solidity: float) -> Optional[Detection]:
        m = cv2.moments(contour)
        if abs(m["m00"]) < 1e-9:
            return None
        x, y, w, h = cv2.boundingRect(contour)
        return Detection(
            target=target,
            center_x=int(m["m10"] / m["m00"]),
            center_y=int(m["m01"] / m["m00"]),
            area_px=round(area, 1),
            confidence=round(confidence, 3),
            vertices=len(approximation),
            triangle_votes=triangle_votes,
            four_corner_votes=four_corner_votes,
            hexagon_votes=hexagon_votes,
            extent=round(extent, 3),
            circularity=round(circularity, 3),
            solidity=round(solidity, 3),
            bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h,
        )

    def _triangle(self, contour: np.ndarray, area: float, perimeter: float) -> Optional[Detection]:
        approximations = self._approximations(contour, perimeter)
        counts = [len(x) for x in approximations]
        triangles = [x for x in approximations if len(x) == 3 and cv2.isContourConvex(x)]
        t_votes = len(triangles)
        q_votes = sum(c == 4 for c in counts)
        h_votes = sum(5 <= c <= 8 for c in counts)
        solidity, extent, circularity = self._metrics(contour, area, perimeter)

        if extent > 0.72:
            self._debug("red_triangle", f"square/rectangle extent={extent:.2f} vertices={counts}", area)
            return None
        if q_votes >= 2:
            self._debug("red_triangle", f"four-corner votes={counts}", area)
            return None
        if t_votes < 2:
            self._debug("red_triangle", f"triangle votes={counts}", area)
            return None
        if solidity < 0.82 or not 0.30 <= circularity <= 0.78:
            return None

        approx = triangles[len(triangles) // 2]
        confidence = (
            0.45 * min(1.0, t_votes / 4.0)
            + 0.30 * max(0.0, 1.0 - abs(extent - 0.50) / 0.22)
            + 0.15 * max(0.0, 1.0 - abs(circularity - 0.60) / 0.22)
            + 0.10 * min(1.0, solidity)
        )
        if confidence < 0.58:
            return None
        return self._make(contour, approx, "red_triangle", area, confidence, t_votes, q_votes, h_votes, extent, circularity, solidity)

    def _hexagon(self, contour: np.ndarray, area: float, perimeter: float) -> Optional[Detection]:
        hull = cv2.convexHull(contour)
        hull_area = float(cv2.contourArea(hull))
        hull_perimeter = float(cv2.arcLength(hull, True))
        if hull_area <= 0 or hull_perimeter <= 0:
            return None
        approximations = self._approximations(hull, hull_perimeter)
        counts = [len(x) for x in approximations]
        t_votes = sum(c == 3 for c in counts)
        q_votes = sum(c == 4 for c in counts)
        hexes = [x for x in approximations if len(x) == 6 and cv2.isContourConvex(x)]
        h_votes = len(hexes)
        solidity, extent, circularity = self._metrics(hull, hull_area, hull_perimeter)
        rect_w, rect_h = cv2.minAreaRect(hull)[1]
        shortest = max(1e-6, min(rect_w, rect_h))
        rotated_aspect = max(rect_w, rect_h) / shortest
        x, y, w, h = cv2.boundingRect(hull)
        aspect = w / h if h > 0 else 0.0

        if extent > 0.80:
            self._debug("blue_hexagon", f"square/rectangle extent={extent:.2f} vertices={counts}", area)
            return None
        if q_votes >= 3 and extent >= 0.83:
            self._debug("blue_hexagon", f"four-corner votes={counts}", area)
            return None
        if h_votes < 1:
            self._debug("blue_hexagon", f"hexagon votes={counts}", area)
            return None
        valid_hexes = []
        for candidate in hexes:
            side_ratio = self._side_ratio(candidate)
            min_angle, max_angle = self._angle_range(candidate)
            if side_ratio <= 2.25 and min_angle >= 70.0 and max_angle <= 165.0:
                valid_hexes.append(candidate)
        if not valid_hexes:
            self._debug("blue_hexagon", f"irregular hex side/angle vertices={counts}", area)
            return None
        if rotated_aspect > 1.65:
            self._debug("blue_hexagon", f"rotated aspect={rotated_aspect:.2f} vertices={counts}", area)
            return None
        if not 0.55 <= aspect <= 1.85:
            self._debug("blue_hexagon", f"aspect={aspect:.2f} vertices={counts}", area)
            return None
        if solidity < 0.84 or not 0.56 <= extent <= 0.80 or not 0.62 <= circularity <= 0.99:
            return None

        approx = min(valid_hexes, key=self._side_ratio)
        confidence = (
            0.45 * min(1.0, h_votes / 4.0)
            + 0.25 * max(0.0, 1.0 - abs(extent - 0.75) / 0.17)
            + 0.20 * max(0.0, 1.0 - abs(circularity - 0.88) / 0.20)
            + 0.10 * min(1.0, solidity)
        )
        if confidence < 0.55:
            return None
        return self._make(contour, approx, "blue_hexagon", area, confidence, t_votes, q_votes, h_votes, extent, circularity, solidity)

    def search(self, frame: np.ndarray) -> tuple[list[Detection], dict[str, np.ndarray]]:
        masks = self.masks(frame)
        detections: list[Detection] = []
        for target, colour in (("red_triangle", "red"), ("blue_hexagon", "blue")):
            contours, _ = cv2.findContours(masks[colour], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            best: Optional[Detection] = None
            for contour in contours:
                area = float(cv2.contourArea(contour))
                if area < self.search_min_area_px:
                    continue
                perimeter = float(cv2.arcLength(contour, True))
                if perimeter <= 0:
                    continue
                item = self._triangle(contour, area, perimeter) if target == "red_triangle" else self._hexagon(contour, area, perimeter)
                if item is not None and (best is None or item.confidence > best.confidence):
                    best = item
            if best is not None:
                detections.append(best)
        return detections, masks

    def track_colour(
        self,
        frame: np.ndarray,
        target: str,
        previous_center: tuple[int, int],
        max_jump_px: float = 220.0,
    ) -> tuple[Optional[Detection], dict[str, np.ndarray]]:
        masks = self.masks(frame)
        if target not in {"red_triangle", "blue_hexagon"}:
            return None, masks
        colour = "red" if target == "red_triangle" else "blue"
        contours, _ = cv2.findContours(masks[colour], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        options: list[tuple[float, Detection]] = []
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < self.tracking_min_area_px:
                continue
            perimeter = float(cv2.arcLength(contour, True))
            if perimeter <= 0:
                continue
            item = self._triangle(contour, area, perimeter) if target == "red_triangle" else self._hexagon(contour, area, perimeter)
            if item is None:
                continue
            distance = math.hypot(item.center_x - previous_center[0], item.center_y - previous_center[1])
            options.append((distance, item))
        if not options:
            return None, masks
        distance, item = min(options, key=lambda x: x[0])
        if distance > max_jump_px:
            return None, masks
        return item, masks


def create_detector(vision_config: dict[str, Any]) -> StrictShapeDetector:
    backend = vision_config.get("backend", VISION_BACKEND_STRICT_SHAPE)
    if backend != VISION_BACKEND_STRICT_SHAPE:
        supported = ", ".join(sorted(SUPPORTED_VISION_BACKENDS))
        raise ValueError(f"Unsupported vision.backend {backend!r}. Supported backends: {supported}")
    return StrictShapeDetector(
        search_min_area_px=float(vision_config["search_min_area_px"]),
        tracking_min_area_px=float(vision_config["tracking_min_area_px"]),
        debug_rejects=bool(vision_config.get("debug_rejects", False)),
    )
