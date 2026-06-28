# Mission Profiles

These JSON files are compatibility/SITL profiles for the tested mission engine.

For the real drone, use:

```text
real_mission/parameter_config/mission2_target_payload.json
```

## Files

- `sim_gazebo.json`: SITL/Gazebo profile on the Ubuntu laptop.
- `sim_yolo_local.json`: SITL/Gazebo profile that explicitly enables the
  optional Ultralytics YOLO backend with `models/yolo_targets/best.pt`.
- `real_pi_camera_module_3.json`: older Raspberry Pi 5 + Cube Orange profile
  kept for compatibility. Prefer the named profiles in
  `real_mission/parameter_config/`.

## YOLO Test Run

Keep the normal mission on `strict_shape` unless you are testing the neural
detector. To test the YOLO-assisted backend on Ubuntu:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./simulation/run_target_mission.sh target_mission_v2/configs/sim_yolo_local.json
```

The YOLO model classes are colour names, so the code still applies colour and
geometry sanity checks before accepting mission targets.

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
