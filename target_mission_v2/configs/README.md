# Mission Profiles

These JSON files choose how the active mission controller connects to vehicle
and camera hardware.

## Files

- `sim_gazebo.json`: SITL/Gazebo profile on the Ubuntu laptop.
- `real_pi_camera_module_3.json`: starting real Raspberry Pi 5 + Cube Orange
  profile.

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
