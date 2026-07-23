# Mission Profiles

These JSON files are explicit profiles for the OpenCV mission engine. Profiles
may use `extends` to inherit a base configuration; paths are resolved relative
to the child file.

For the real drone, use:

```text
real_mission/parameter_config/mission2_target_payload.json
```

## Files

- `sim_gazebo.json`: SITL/Gazebo profile on the Ubuntu laptop.
- `real_pi_camera_module_3.json`: legacy Raspberry Pi 5 + Cube profile retained
  for compatibility.
- `opencv_camera_diagnostics.json`: direct Picamera2 diagnostics, no fallback.
- `opencv_live_test.json`: camera-only live OpenCV test with no MAVLink.
- `opencv_replay.json`: deterministic image/video replay on Ubuntu.
- `opencv_simulation.json`: OpenCV mission in SITL/Gazebo.
- `opencv_real_no_control.json`: real camera profile with MAVLink control
  disabled.
- `opencv_real_simulated_payload.json`: real camera and mission control with
  payload commands simulated.
- `opencv_physical_payload_template.json`: non-default template that must be
  reviewed and explicitly completed before a props-off payload bench test.

## Real Profile Safety Defaults

The real profile starts conservative:

```json
"altitude_control": "off"
"search_speed_source": "qgc_mission"
"payload": {
  "simulate_only": true
}
```

That means QGC/ArduPilot owns AUTO altitude and speed, and the Pi does not move
the physical payload until the team intentionally enables it.

Do not silently substitute one profile for another. Verify direct Picamera2
colour order, focus, frame age, thermal behavior, and false-positive rejection
before migrating the normal real-mission launcher.
