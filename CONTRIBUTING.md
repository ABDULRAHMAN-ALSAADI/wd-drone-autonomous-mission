# Contributing

## Source Of Truth

Edit and review code on an Ubuntu clone. Synchronize tested files to the
Raspberry Pi; do not treat ad-hoc Pi edits as the permanent source.

## Branches

- `main`: OpenCV-only reference mission.
- `yolov8-mission-pi5`: YOLOv8/Hailo candidate detection plus OpenCV shape
  verification.

Use a short feature branch for normal work. Do not merge experimental flight
behavior directly into `main` during a test day.

## Before A Pull Request

For `main`:

```bash
./scripts/check_project.sh
```

For `yolov8-mission-pi5`:

```bash
./check_project.sh
```

Also run the relevant SITL scenario when mission, control, MAVLink or vision
behavior changes. State clearly what was and was not tested on real hardware.

## Change Rules

- Keep Mission Planner responsible for AUTO route, speed and altitude.
- Keep mission runtime free of RC override, raw motor output, motor test,
  flight termination and in-flight disarm commands.
- Put hazardous bench commands only under guarded test tools.
- Do not weaken target-shape rejection without positive and negative tests.
- Do not silently change `/dev/serial0`, baud `921600`, servo channel, PWM,
  search waypoint or return behavior.
- Update configuration documentation with every new tunable field.
- Prefer a focused change over a broad rewrite during flight-test preparation.

## Data And Models

Do not commit camera dumps, logs, private data, model weights or generated HEF
compiler output unless the team explicitly approves the release. Keep a record
of model source, class mapping, training version and checksum outside Git until
then.

## Commit Message

Describe observable intent, for example:

```text
Reject rectangular blue targets during search
Add Pi camera readiness check
Document Mission 2 operator sequence
```

## Review Checklist

- Does this alter arming, mode, velocity, payload or return behavior?
- Can stale telemetry or a stale camera frame reach a flight decision?
- Can the pilot still take control?
- Are positive, negative and timeout paths tested?
- Does the overlay/log make the state understandable?
- Are docs and example configs consistent with the code?
- Were propellers removed for every hardware bench test?
