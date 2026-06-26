# Real Mission Flow

This is the recommended operator flow for the two competition tasks.

## Important Principle

Do not make the Raspberry Pi upload or choose between Mission 1 and Mission 2
in the air yet.

For the first real flights, keep mission selection simple:

1. Load the correct mission in Mission Planner or QGroundControl on the ground.
2. Start the matching Pi profile, or no target controller for Mission 1.
3. Use the RC flight-mode switch to start AUTO.

This avoids a risky situation where the companion computer uploads, swaps, or
starts the wrong mission in the air.

## Mission 1: Figure 8

Mission 1 should be a normal ArduPilot AUTO mission.

Recommended flow:

1. Upload the Figure 8 mission from Mission Planner/QGroundControl.
2. Verify altitude, speed, geofence, RTL, and failsafes.
3. Arm from the normal pilot procedure.
4. Use the RC mode switch to enter AUTO.
5. ArduPilot flies the two Figure 8 laps.
6. Pilot can switch to LOITER/STABILIZE/RTL at any time for safety.

The Raspberry Pi is not required to control Mission 1. It may run read-only
monitoring, but it should not send GUIDED or payload commands.

Normal Mission 1 command:

```text
do not run the target payload controller
```

Optional no-search Pi profile:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/run_mission1_no_search.sh
```

This profile keeps `mission.search_enabled` false, so the Pi cannot enter
SEARCH even if the waypoint number matches.

## Mission 2: Pole, Search Area, Target Payload

Mission 2 is an ArduPilot AUTO mission plus the Pi vision controller.

Recommended flow:

1. Upload the Mission 2 waypoint file from Mission Planner/QGroundControl.
2. The AUTO mission should handle takeoff, pole 2 outside pass, and travel to
   the search area.
3. Start the Mission 2 Pi profile before arming:

   ```bash
   cd ~/FOR_COMP/wd-drone-autonomous-mission
   ./real_mission/run_mission2_target_payload.sh
   ```

4. Start the laptop camera window if Wi-Fi/hotspot is available:

   ```bash
   ./real_mission/open_laptop_camera_window.sh
   ```

5. Use the RC mode switch to enter AUTO.
6. The Pi waits until:

   ```text
   armed == true
   mode == AUTO
   current waypoint >= mission.search_start_wp
   mission.search_enabled == true
   ```

7. After a target is confirmed, the Pi requests GUIDED, centers over the target,
   triggers the correct payload when enabled, then resumes AUTO or requests RTL
   after both targets are complete.

Tune the search start waypoint here:

```text
real_mission/parameter_config/mission2_target_payload.json
```

```json
"mission": {
  "search_start_wp": 2
}
```

## RC Switch Recommendation

For the first real tests, use one flight-mode switch with clear safety modes:

```text
Position 1: STABILIZE or ALT_HOLD
Position 2: LOITER
Position 3: AUTO
```

Use a separate RC option or switch for RTL only after the pilot and avionics
lead agree on the transmitter layout.

For an extra Mission 2 safety lock, you may assign one RC channel as search
enable. Example:

```json
"mission": {
  "search_enable_rc_channel": 7,
  "search_enable_pwm_min": 1700
}
```

That switch does not choose Mission 1 or Mission 2. It only allows Mission 2
search after the correct mission profile is already running.

To find the correct AT9S Pro channel, run this on the Raspberry Pi:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/mavlink/rc_channels.sh --seconds 30 --channels 5 6 7 8
```

Flip one switch at a time. The changed channel is printed with `*`.

## Active Target Mode Safety

During Mission 2, once a target is confirmed, the Pi should own the target job
until the payload step finishes or the target is aborted.

The real config therefore has:

```json
"safety": {
  "max_guided_auto_bounces_per_target": null,
  "camera_frame_timeout_s": 2.0,
  "active_target_abort_mode": "AUTO"
}
```

This means:

- if ArduPilot briefly reports AUTO during centering, the Pi immediately
  requests GUIDED again;
- if this keeps happening during the same target, the Pi keeps the target lock
  and keeps requesting GUIDED;
- normal AUTO resume still happens after a successful payload action when there
  is another target left.

If a pilot switch or failsafe puts the vehicle into LOITER, STABILIZE, RTL, LAND,
or another non-mission mode during target work, the Pi does not force the mission
back. It sends a zero-velocity command, clears the current target, and waits for
AUTO again.

## Why Not Two RC Buttons Yet?

A two-button system where one button starts Mission 1 and another starts Mission
2 sounds convenient, but it adds risk:

- ArduPilot normally has one active uploaded mission at a time.
- The companion would need to choose, upload, or jump between missions.
- A wrong RC read or mode conflict could start the wrong route.
- It makes safety review harder before the first real flights.

After repeated successful flights, a companion-controlled mission selector can
be designed as a separate feature. It should not be the first flight design.

## Simulation Versus Real World

The simulation proving the mission is necessary, but it is not enough.

Real flight adds:

- wind;
- GPS drift;
- compass/EKF errors;
- camera vibration;
- sunlight and shadows;
- motion blur;
- payload release timing;
- RC/GCS failsafe behavior;
- real motor/ESC response.

That is why the real-drone config keeps these defaults:

```json
"payload": {
  "simulate_only": true
},
"control": {
  "altitude_control": "off"
},
"navigation": {
  "search_speed_source": "qgc_mission"
}
```

The Pi should not control altitude or AUTO speed during the first real tests.

## Vision Confidence

The strict shape detector is useful and tested, but it is not perfect.

Before trusting it for payload release:

1. Test with printed or painted targets, not only phone/tablet images.
2. Test at the real camera mount angle.
3. Test at 5 m, 7 m, and 10 m.
4. Test in sunlight and shade.
5. Verify that blue rectangles and red rectangles are rejected.
6. Keep payload simulation enabled until detection and centering are proven.

YOLO/AI HAT+ can be added later as an optional backend, but the simple detector
should remain as a fallback until the trained model passes real-world tests.
