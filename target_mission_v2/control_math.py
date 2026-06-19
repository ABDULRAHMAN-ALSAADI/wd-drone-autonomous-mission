#!/usr/bin/env python3
from __future__ import annotations

from typing import Optional


def clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def altitude_velocity_down(
    current_altitude_m: Optional[float],
    target_altitude_m: float,
    tolerance_m: float,
    kp: float,
    max_speed_m_s: float,
) -> float:
    if current_altitude_m is None:
        return 0.0
    error = current_altitude_m - target_altitude_m
    if abs(error) <= tolerance_m:
        return 0.0
    return clamp(kp * error, max_speed_m_s)
