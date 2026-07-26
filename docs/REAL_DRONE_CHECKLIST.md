# Real Drone And First-Flight Checklist

Use this as a set of gates, not as a promise that the aircraft is safe. The
pilot and team safety lead must approve every flight. Do not make the full
autonomous target mission the aircraft's first flight.

> [!CAUTION]
> Never bypass an ArduPilot pre-arm failure to make a test continue. Find and
> correct its cause. Keep propellers removed for every motor, servo, UART,
> arming, and mode-command bench test.

## What One Bench Test Verified

The 2026-07-26 propeller-off bench setup verified:

- Cube heartbeat on `/dev/ttyAMA0` at `921600` baud;
- Pi Camera Module 3 capture and annotated laptop monitoring;
- strict recognition of the red triangle and blue hexagon;
- selector positions `1300` and `1700`;
- three-second hold followed by neutral `1500`;
- live video throughout both servo actions.

See [Hardware Test Log](HARDWARE_TEST_LOG.md) for the exact scope and commit.
Everything below remains a separate test.

## Current No-Go Items

Do not begin autonomous flight testing until these are closed and recorded:

- [ ] Re-test all four physical motors after the mapping problem recorded in
  [`MOTOR_MAPPING.md`](../test_components/mavlink/MOTOR_MAPPING.md). Confirm
  both position and rotation direction with propellers removed.
- [ ] Obtain an outdoor health report with sane battery, GPS, position, EKF,
  vibration, and power data. The earlier servo-bench report showed these fields
  as unavailable.
- [ ] Complete the SITL scenarios in [SITL Test Plan](SITL_TEST_PLAN.md).
- [ ] Verify RC pilot takeover and the chosen non-GPS recovery mode.
- [ ] Verify RC, battery, and any intentionally enabled GCS failsafes.
- [ ] Verify home position, RTL behavior/altitude, and geofence in a clear area.
- [ ] Review every real AUTO waypoint, altitude, speed, frame, and final action.
- [ ] Calibrate camera focus and the payload reference at representative target
  distances and outdoor lighting.

## Gate 1 — Repository And Configuration

- [ ] The laptop and Pi run the same reviewed Git commit.
- [ ] `./scripts/check_project.sh` passes from a clean checkout.
- [ ] The uploaded Mission Planner/QGC mission matches the selected Pi profile.
- [ ] A fresh ArduPilot parameter backup is saved with the test record.
- [ ] Mission 1 uses no target controller or the no-search profile.
- [ ] Early Mission 2 flights keep:

```json
"payload": {
  "simulate_only": true
}
```

- [ ] `navigation.search_speed_source` remains `qgc_mission` unless a reviewed
  test explicitly changes ownership.
- [ ] `control.altitude_control` remains `off` for early flights.
- [ ] The team has reviewed these intentional Mission 2 choices:
  `search_enable_rc_channel=null`, `require_battery=false`,
  `require_ekf_status=false`, and unlimited `max_center_time_s`.
- [ ] When physical output is eventually enabled, `payload_state.enabled` is
  also enabled and its persistent state is reset only by the ground team before
  an authorized new attempt.

For the first target-control flight, using and testing an RC search-enable gate
is strongly recommended. Regardless of that choice, the pilot must know how to
select a non-mission mode that makes the companion stand down.

## Gate 2 — Propeller-Off Airframe And Avionics

Inspect and record:

- [ ] Frame class/type and flight-controller orientation are correct.
- [ ] Accelerometer, level, compass, and RC calibrations are current.
- [ ] GPS/compass orientation and CAN/serial health are correct.
- [ ] Battery monitor voltage and current agree with trusted measurements.
- [ ] Battery, power module, BEC, servo rail, Pi supply, and grounds are secure.
- [ ] Center of gravity is reasonable with the flight battery and payload.
- [ ] Fasteners, arms, landing gear, antennas, camera, GPS mast, and wiring are
  secure and strain-relieved.
- [ ] No propeller is installed during the motor-order test.
- [ ] Each motor command spins the expected physical motor in the expected
  direction.
- [ ] Propeller type and orientation are checked separately immediately before
  flight.
- [ ] Pi cooling is installed and `vcgencmd get_throttled` reports `0x0`.

Run read-only checks first:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
source .venv/bin/activate
./test_components/mavlink/status.sh
./test_components/mavlink/health.sh
```

Then follow the guarded mode, velocity, servo, and motor sections in
[Component Test Commands](../test_components/COMMANDS.md). Never run two
programs against the same Pi UART at once.

## Gate 3 — RC, Failsafes, And Recovery

- [ ] RC calibration covers the full stick and switch range.
- [ ] Flight-mode switch positions are labelled and verified.
- [ ] The safety pilot can immediately select the agreed recovery mode.
- [ ] RC range testing passes for the intended operating area.
- [ ] Transmitter-loss behavior is tested safely and matches the team plan.
- [ ] Low and critical battery actions are configured and tested without
  exhausting a flight battery.
- [ ] GCS failsafe is either deliberately configured and tested or deliberately
  disabled because the mission must not depend on the laptop.
- [ ] EKF/GPS failure behavior is understood by the safety pilot.
- [ ] Geofence boundaries and breach action are reviewed.
- [ ] RTL altitude clears local obstacles, home is correct, and the home area is
  free of people and equipment.
- [ ] Emergency disarm/power-cut procedure, roles, and callouts are rehearsed.

## Gate 4 — Vision At Representative Conditions

- [ ] Use physical printed/painted targets, not only a phone display.
- [ ] Test target size at representative 5 m, 7 m, and 10 m viewing distances.
- [ ] Test sun, shade, rotation, wind movement, blur, and clutter.
- [ ] Capture raw positive scenes for each target.
- [ ] Capture negative scenes containing rectangles, runway markings, clothing,
  vehicles, shadows, and empty ground.
- [ ] Replay every dataset and inspect false confirmations.
- [ ] Calibrate manual focus at search, centering, and release distances.
- [ ] Calibrate the payload outlet/reference pixel.
- [ ] Confirm bounded frame age and no Pi thermal throttling.

Follow [OpenCV Pi 5 Testing](OPENCV_PI5_TESTING.md). Reliable, fresh detections
matter more than increasing the displayed FPS.

## Gate 5 — SITL And Failure Injection

Pass the complete [SITL Test Plan](SITL_TEST_PLAN.md), including:

- [ ] both targets through `AUTO → GUIDED → AUTO → GUIDED → RTL`;
- [ ] 5 m, 7 m, and 10 m mission altitudes;
- [ ] non-target red/blue objects and runway markings;
- [ ] temporary target loss and reacquisition;
- [ ] camera interruption;
- [ ] pilot takeover during centering;
- [ ] repeat mission without restarting the process;
- [ ] final competition waypoint and `search_start_wp`.

Also verify, in SITL or a propeller-off bench setup:

- [ ] stopping the Pi controller does not remove pilot/autopilot authority;
- [ ] losing Pi-to-Cube MAVLink produces no repeated release;
- [ ] a camera failure produces zero horizontal velocity and a clear fault;
- [ ] selecting LOITER, STABILIZE, RTL, LAND, or another non-mission mode makes
  the companion stop commanding and remain stood down until disarm.

## Gate 6 — Staged Flight Progression

Use a large clear field, competent safety pilot, spotter, conservative limits,
and the required local authorization. Stop after each stage, download logs, and
fix one problem at a time.

1. **Basic hover:** companion target controller off; verify controlled
   Stabilize flight and landing.
2. **Position hold:** verify Loiter/position behavior and immediate pilot
   recovery.
3. **RTL:** verify home and RTL from a short, unobstructed distance.
4. **AUTO route:** fly a small reviewed route with target control disabled.
5. **One target:** Mission 2 at low speed with one target and simulated payload.
   Confirm the real image-axis movement direction.
6. **Two targets:** repeat with both targets and simulated payload.
7. **Repeated full simulation:** require consistent results and reviewed logs.
8. **Dummy physical payload:** enable only after every earlier gate passes.
9. **Competition mission:** full route and payload are the final step, not the
   first-flight test.

After the first hover, review the dataflash log for vibration, clipping,
attitude/EKF consistency, battery sag, and failsafe messages before proceeding.

## Go/No-Go Brief Immediately Before Every Flight

- [ ] Named pilot, safety lead, spotter, and mission operator.
- [ ] Weather, people, obstacles, airspace, and field permission are acceptable.
- [ ] Correct mission/profile and payload simulation state are read aloud.
- [ ] Battery is identified, charged, secured, and logged.
- [ ] GPS fix, EKF, compass, home, fence, battery, and RC are healthy.
- [ ] No unresolved pre-arm message.
- [ ] Recovery mode and abort call are rehearsed.
- [ ] Laptop video is treated as optional monitoring, never flight control.
- [ ] Nobody stands near or under the aircraft or payload zone.

Any uncertain item is a **no-go**.

## Official ArduPilot References

- [Pre-Arm Safety Checks](https://ardupilot.org/copter/docs/common-prearm-safety-checks.html)
- [Copter Pre-Flight Checklist](https://ardupilot.org/copter/docs/checklist.html)
- [First Flight With Copter](https://ardupilot.org/copter/docs/flying-arducopter.html)
- [Motor Order And Connections](https://ardupilot.org/copter/docs/connect-escs-and-motors.html)
- [Radio Failsafe](https://ardupilot.org/copter/docs/radio-failsafe.html)
- [Battery Failsafe](https://ardupilot.org/copter/docs/failsafe-battery.html)
- [EKF Failsafe](https://ardupilot.org/copter/docs/ekf-inav-failsafe.html)
- [Fences](https://ardupilot.org/copter/docs/common-geofencing-landing-page.html)
- [RTL Mode](https://ardupilot.org/copter/docs/rtl-mode.html)
- [Measuring Vibration](https://ardupilot.org/copter/docs/common-measuring-vibration.html)
- [Downloading And Reviewing Logs](https://ardupilot.org/planner/docs/common-downloading-and-analyzing-data-logs-in-mission-planner.html)
