# Real Mission Parameter Config

Edit `real_drone.json` for the real Raspberry Pi 5 + Cube Orange mission.

Do not edit Python code for normal tuning. Start here first.

## Most Common Values

| Field | What it controls | Safe starting idea |
| --- | --- | --- |
| `mission.search_start_wp` | AUTO mission item where vision search starts. | Set to the waypoint after your survey enters the target area. |
| `navigation.search_speed_source` | Who controls AUTO search speed. | Keep `qgc_mission` so QGC/Mission Planner owns AUTO speed. |
| `control.center_max_speed_m_s` | Max GUIDED centering speed. | Start low, around `0.25` to `0.35`. |
| `control.center_tolerance_px` | How close the target must be to camera center. | Larger is safer, smaller is more precise. |
| `control.center_hold_s` | How long the target must stay centered before payload. | `1.0` to `1.5` seconds. |
| `vision.required_hits` | Number of stable detections before target lock. | Higher is safer but slower. |
| `vision.search_min_area_px` | Smallest target area accepted during search. | Lower for higher altitude, higher to reject noise. |
| `payload.simulate_only` | If `true`, no servo command is sent. | Keep `true` until servo bench passes. |
| `payload.servo_channel` | Pixhawk output channel for payload. | Must match Mission Planner servo setup. |
| `payload.release_pwm` | PWM sent to release payload. | Test on bench before flight. |
| `payload.reset_pwm` | PWM sent after release hold time. | Test on bench before flight. |
| `display.show_main_window` | Opens local OpenCV window on the Pi. | Keep `false` on Pi OS Lite. |

## Camera Window

The laptop viewer also reads this config for Camera Module 3 and vision tuning:

```bash
./real_mission/open_laptop_camera_window.sh
```

The laptop viewer needs Wi-Fi/SSH to the Pi. It is not carried by RFD900x.

## Payload Servo

The mission already knows the competition payload mapping:

```text
blue hexagon  -> red payload
red triangle  -> blue payload
```

Physical servo output only happens when:

```json
"payload": {
  "simulate_only": false
}
```

Leave it `true` until the payload mechanism and channel mapping are tested with
propellers removed.
