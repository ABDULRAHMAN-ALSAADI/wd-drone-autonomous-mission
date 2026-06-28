# Mission Profiles

These JSON files are compatibility/SITL profiles for the tested mission engine.

For the real drone, use:

```text
real_mission/parameter_config/mission2_target_payload.json
```

## Files

- `sim_gazebo.json`: SITL/Gazebo profile on the Ubuntu laptop.
- `real_pi_camera_module_3.json`: older Raspberry Pi 5 + Cube Orange profile
  kept for compatibility. Prefer the named profiles in
  `real_mission/parameter_config/`.

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
