# Competition Requirements For This Software

Source files reviewed:

- `2026_İHA_Yarışmaları_Şartnamesi_EN_v1_qO7Lq.pdf`
- `Teknofest Wiring .pdf`

This is a working summary for the software team, not a replacement for the
official rules.

## Category And Mission

The active software targets the International UAV Competition Rotary Wing
Category second mission.

The mission requires the UAV to:

- fly autonomously with flight-control software;
- detect two unknown target locations using image processing;
- release one payload per target;
- release the correct payload color for the detected target;
- complete the mission inside the 10 minute second-mission flight limit.

## Target And Payload Mapping

For the International Rotary Wing second mission:

- Blue regular hexagon, side length 2 m: release the red payload.
- Red equilateral triangle, side length 1 m: release the blue payload.
- Target order is not fixed; release the correct payload on whichever target is
  detected first.

The code mapping is:

```text
blue_hexagon -> red payload
red_triangle -> blue payload
```

## Vision Rules That Matter

The mission requires image processing proof. A hit without image processing does
not receive target-hit points.

Rotary-wing UAVs must not detect fixed-wing targets in the same field:

- fixed-wing blue square/rectangle targets;
- fixed-wing red square/rectangle targets.

This is why `vision.py` strongly rejects blue rectangles and red rectangles.

## Scoring-Relevant Behavior

The target distance is measured from the final resting position of the payload to
the target center. For rotary wing, measurement is only made up to 10 m from the
target center.

Software implication:

- center over the target before payload;
- avoid dropping while still correcting aggressively;
- keep payload simulation enabled until bench and flight tests prove the release
  channel and centering direction.

## Flight Timing

The second mission maximum flight time is 10 minutes.

The mission config keeps:

```json
"max_flight_time_s": 600.0
```

## Camera Rule

For image processing, the rules allow a single camera integrated into the
auxiliary computer. A second camera used to view the area is prohibited.

Software implication:

- use one mission camera stream for target detection;
- do not add a separate scouting camera feed to the competition mission.

## Safety And Hardware Notes From The Wiring PDF

The wiring notes mention:

- Cube Orange documentation and ArduPilot firmware setup;
- recording firmware/data state before reprogramming;
- Pi 5 power current configuration for a 5 A supply;
- strain relief/hot glue where wires enter screw terminals.

These are tracked in `docs/SAFETY_AND_FAILSAFES.md`.
