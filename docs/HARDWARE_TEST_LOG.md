# Hardware Test Log

This file records what was physically tested, on which code, and what the
result actually proves. It is evidence for the team, not an approval to fly.

## 2026-07-26 — Camera, Target Recognition, And Payload Selector

| Item | Tested value |
| --- | --- |
| Code | Commit `b463d68` on `opencv-pi5-camera-refactor-v3` |
| Companion computer | Raspberry Pi 5 |
| Camera | Raspberry Pi Camera Module 3 / IMX708 through Picamera2 |
| Flight controller | Cube/Pixhawk running ArduPilot |
| MAVLink | `/dev/ttyAMA0`, `921600` baud, vehicle heartbeat `src=1:1` |
| Payload output | Pixhawk MAIN OUT 5 / servo channel 5 |
| Safety condition | Propellers removed, vehicle disarmed, payload area clear |
| Evidence | Live operator observation on the Pi and Ubuntu laptop |

Passed:

- The Pi received the Cube vehicle heartbeat.
- OpenCV automatically recognized a red triangle and then a blue hexagon
  during the same run.
- A centered red triangle commanded `1300` PWM, held for three seconds, and
  returned to neutral `1500`.
- A centered blue hexagon commanded `1700` PWM, held for three seconds, and
  returned to neutral `1500`.
- Each target triggered once during the observed run.
- The annotated laptop stream stayed live while the servo moved.
- After both targets completed, monitoring stayed open until manual shutdown.
- Final shutdown commanded neutral.
- The exact implementation passed 133 software tests on Ubuntu and 15 focused
  non-hardware tests on the Raspberry Pi.

Software tests additionally verified that either configured target may be
presented first, each target can trigger at most once per run, and a lost or
rejected servo command ACK latches a fault and keeps retrying neutral without
blocking the video stream. Those cases were not all induced during this
physical run.

Problems found and corrected during the test:

- The tested Cube UART is `/dev/ttyAMA0`. On this Raspberry Pi,
  `/dev/serial0` pointed to `/dev/ttyAMA10` and received no Cube heartbeat.
- Blocking servo ACK waits and the three-second hold originally froze the
  monitoring stream. Commit `b463d68` replaced them with a non-blocking state
  machine.
- The old process exited after both targets. It now remains in monitoring mode.
- Software fault-injection tests verify that a lost servo ACK latches a fault,
  retries neutral without blocking video, and never repeats the release.

This test does **not** prove:

- motor order, motor direction, propeller installation, or airframe stability;
- GPS, compass, EKF, battery monitor, geofence, RTL, or RC failsafes;
- that a MAVLink ACK means a payload physically left the aircraft;
- that image-to-body velocity signs move the aircraft correctly in flight;
- target performance at real altitude, sun, shadow, motion blur, or clutter;
- autonomous-flight readiness.

Repeatable instructions are in
[Component Test Commands](../test_components/COMMANDS.md#8-payload-servo-and-opencv-integration-test).
Remaining gates are in [Real Drone Checklist](REAL_DRONE_CHECKLIST.md).

## Template For The Next Test

Copy this block for every meaningful hardware or flight test:

```text
Date:
Test lead:
Safety pilot:
Airframe/configuration:
Git commit:
ArduPilot firmware and parameter backup:
Weather/location:
Safety setup:
Steps performed:
Expected result:
Observed result:
PASS / FAIL / PARTIAL:
Logs, photos, or video:
Problems found:
Follow-up owner:
```
