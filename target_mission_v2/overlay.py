"""OpenCV mission diagnostics overlay."""
from __future__ import annotations

import math
import time
from typing import Any, Optional

import cv2


def draw_mission_overlay(controller: Any, frame: Any, detections: list[Any]) -> Any:
    """Draw the existing mission view without owning mission decisions."""
    out = frame.copy()
    h, w = out.shape[:2]
    desired = controller.desired_drop_point(w, h)
    desired_center = (round(desired[0]), round(desired[1]))
    cv2.drawMarker(out, desired_center, (255, 255, 255), cv2.MARKER_CROSS, 26, 1)
    if controller.current_target:
        cv2.circle(out, desired_center, round(controller.center_tolerance_px()), (0, 255, 255), 1, cv2.LINE_AA)

    candidate_colours = {
        "colour_candidate": (0, 220, 255),
        "geometric_candidate": (0, 140, 255),
        "confirmed_geometry": (0, 255, 0),
        "temporary_colour_tracking": (0, 220, 255),
        "rejected": (150, 150, 150),
    }
    candidate_labels = {
        "colour_candidate": "COLOUR CANDIDATE",
        "geometric_candidate": "GEOMETRIC CANDIDATE",
        "confirmed_geometry": "STRICT GEOMETRY",
        "temporary_colour_tracking": "TEMP COLOUR TRACK",
        "rejected": "COLOUR CANDIDATE REJECTED",
    }
    for candidate in getattr(controller.detector, "last_candidates", []):
        status = str(candidate.get("status", "colour_candidate"))
        bbox = candidate.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x, y, box_width, box_height = (int(value) for value in bbox)
        colour = candidate_colours.get(status, (0, 220, 255))
        thickness = 1 if status == "rejected" else 2
        cv2.rectangle(
            out,
            (x, y),
            (x + box_width, y + box_height),
            colour,
            thickness,
        )
        if status == "rejected":
            cv2.line(
                out,
                (x, y),
                (x + box_width, y + box_height),
                colour,
                1,
                cv2.LINE_AA,
            )
            cv2.line(
                out,
                (x + box_width, y),
                (x, y + box_height),
                colour,
                1,
                cv2.LINE_AA,
            )
        label = candidate_labels.get(status, status.upper())
        if status == "rejected" and candidate.get("reason"):
            label = f"REJECTED: {candidate['reason']}"
        cv2.putText(
            out,
            label,
            (x, max(16, y - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            colour,
            1,
            cv2.LINE_AA,
        )

    draw_items = list(detections)
    if controller.last_detection and all(item.target != controller.last_detection.target for item in draw_items):
        draw_items.append(controller.last_detection)
    for item in draw_items:
        source = str(getattr(item, "source", ""))
        if source == "colour_track":
            status_label = "TEMP COLOUR TRACK"
            colour = (0, 220, 255)
        elif source == "geometry_track":
            status_label = "STRICT TRACK"
            colour = (0, 255, 0)
        elif source == "geometry_payload":
            status_label = "PAYLOAD GEOMETRY"
            colour = (255, 255, 0)
        elif controller.current_target == item.target:
            status_label = "CONFIRMED TARGET"
            colour = (0, 255, 0)
        else:
            status_label = "GEOMETRIC CANDIDATE"
            colour = (0, 140, 255)
        cv2.rectangle(out, (item.bbox_x, item.bbox_y), (item.bbox_x + item.bbox_w, item.bbox_y + item.bbox_h), colour, 2)
        cv2.circle(out, (item.center_x, item.center_y), 5, colour, -1)
        cv2.line(out, desired_center, (item.center_x, item.center_y), colour, 2, cv2.LINE_AA)
        error = math.hypot(item.center_x - desired_center[0], item.center_y - desired_center[1])
        cv2.putText(out, f"{status_label}: {item.target} {item.confidence:.2f} err {error:.0f}px",
                    (item.bbox_x, max(18, item.bbox_y - 7)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, colour, 1, cv2.LINE_AA)
    alt = "unknown" if controller.vehicle.relative_alt_m is None else f"{controller.vehicle.relative_alt_m:.2f} m"
    centered_for = 0.0 if controller.centered_since is None else time.monotonic() - controller.centered_since
    center_error = "none" if controller.last_center_error_px is None else f"{controller.last_center_error_px:.0f}px"

    def fmt(value: Optional[float], unit: str) -> str:
        return "n/a" if value is None else f"{value:.2f}{unit}"

    done = ",".join(sorted(controller.completed_targets)) if controller.completed_targets else "none"
    drop_locks = ", ".join(
        f"{target.replace('_', ' ')} {error:.0f}px"
        for target, error in sorted(controller.completed_center_errors.items())
    ) or "none"
    gate_enabled, gate_reason = controller.search_gate_status()
    gate = "enabled" if gate_enabled else f"blocked: {gate_reason}"
    tracking_state = getattr(controller.detector, "tracking_state", None)
    if tracking_state is None:
        strict_status = "no lock"
    else:
        strict_age = max(
            0.0,
            time.monotonic() - tracking_state.last_strong_geometry_at,
        )
        strict_status = (
            f"age {strict_age:.2f}s | failed checks "
            f"{tracking_state.failed_geometry_checks}/"
            f"{getattr(controller.detector, 'max_failed_geometry_checks', 2)}"
        )
    evidence_source = (
        "none"
        if controller.last_detection is None
        else str(controller.last_detection.source)
    )
    payload_vision = (
        "VERIFIED"
        if controller.last_payload_geometry_detection is not None
        else controller.last_payload_geometry_reason
    )
    font_scale = float(controller.config["display"].get("overlay_font_scale", 0.46))
    max_text_width = max(120, w - 42)

    def fit_line(text: str) -> str:
        if cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)[0][0] <= max_text_width:
            return text
        clipped = text
        while len(clipped) > 4 and cv2.getTextSize(clipped + "...", cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)[0][0] > max_text_width:
            clipped = clipped[:-1]
        return clipped + "..."

    lines = [fit_line(line) for line in [
        f"Mission {controller.state.value} | Mode {controller.vehicle.mode} | WP {controller.vehicle.mission_seq}",
        f"Action: {controller.status_message}",
        f"Target: {controller.current_target or 'none'} | Err {center_error} | Hold {centered_for:.1f}s",
        f"Vision: {evidence_source} | Strict {strict_status}",
        f"Payload geometry: {payload_vision}",
        f"Search gate: {gate}",
        f"Done: {done} | Runs {controller.mission_done_count} | Guided bounces {controller.guided_bounce_count}",
        f"Completed payload locks: {drop_locks}",
        f"Alt {alt} | Hspd {fmt(controller.vehicle.horizontal_speed_m_s, 'm/s')} | Vspd {fmt(None if controller.vehicle.velocity_down_m_s is None else -controller.vehicle.velocity_down_m_s, 'm/s')} | Acc {fmt(controller.vehicle.acceleration_m_s2, 'm/s2')}",
    ]]
    line_height = max(16, int(38 * font_scale))
    panel_width = min(w - 16, max(cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)[0][0] for line in lines) + 20)
    panel_height = 12 + line_height * len(lines)
    panel = out.copy()
    cv2.rectangle(panel, (8, 8), (8 + panel_width, 8 + panel_height), (0, 0, 0), -1)
    alpha = float(controller.config["display"].get("overlay_background_alpha", 0.42))
    cv2.addWeighted(panel, alpha, out, 1.0 - alpha, 0, out)
    y = 28
    for line in lines:
        cv2.putText(out, line, (18, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), 1, cv2.LINE_AA)
        y += line_height
    return out
