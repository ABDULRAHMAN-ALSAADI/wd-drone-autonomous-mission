# Real Mission Parameter Config

Edit the named mission profile for the real Raspberry Pi 5 + Cube Orange mission:

```text
mission1_no_search.json
mission2_target_payload.json
```

Do not edit Python code for normal tuning. Start here first.

## Most Common Values

| Field | What it controls | Safe starting idea |
| --- | --- | --- |
| `mission.name` | Human-readable mission profile name. | Use `mission1_figure8_no_search` or `mission2_target_payload`. |
| `mission.search_enabled` | Whether the Pi may enter target search. | `false` for Mission 1, `true` for Mission 2. |
| `mission.search_start_wp` | AUTO mission item where vision search starts. | Set to the waypoint after your survey enters the target area. |
| `mission.search_enable_rc_channel` | Optional RC switch that must be high before search. | Use `null` first, or channel `7`/`8` after RC testing. |
| `mission.search_enable_pwm_min` | PWM threshold for the optional search-enable RC channel. | Usually `1700`. |
| `navigation.search_speed_source` | Who controls AUTO search speed. | Keep `qgc_mission` so QGC/Mission Planner owns AUTO speed. |
| `control.center_max_speed_m_s` | Max GUIDED centering speed. | Start low, around `0.25` to `0.35`. |
| `control.center_tolerance_px` | How close the target must be to camera center. | Larger is safer, smaller is more precise. |
| `control.center_hold_s` | How long the target must stay centered before payload. | `1.0` to `1.5` seconds. |
| `safety.guided_auto_bounce_grace_s` | Time label for AUTO bounce diagnostics. | Keep `null` so active target GUIDED lock has no time limit. |
| `safety.max_guided_auto_bounces_per_target` | Diagnostic counter for repeated `GUIDED -> AUTO` bounces. | Keep `null`; active target bounces should not abort centering. |
| `safety.active_target_abort_mode` | Fallback mode for explicit abort paths, not normal target tracking. | Use `AUTO` only when you intentionally want the mission to continue after abort. |
| `vision.required_hits` | Number of stable detections before target lock. | Higher is safer but slower. |
| `vision.search_min_area_px` | Smallest target area accepted during search. | Lower for higher altitude, higher to reject noise. |
| `payload.simulate_only` | If `true`, no servo command is sent. | Keep `true` until servo bench passes. |
| `payload.servo_channel` | Pixhawk output channel for payload. | `5` when the servo signal is on MAIN OUT / signal 5. |
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

## Pixhawk Output Number Warning

The payload command sends `MAV_CMD_DO_SET_SERVO` to the configured ArduPilot
servo output number.

For the wiring you showed:

```json
"servo_channel": 5
```

is correct only if the payload servo signal wire is on MAIN OUT / signal 5.
If your wire is on AUX OUT 5 instead, stop and remap the channel before testing.

## Optional RC Mission 2 Enable

Before setting `mission.search_enable_rc_channel`, identify the switch channel:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/mavlink/rc_channels.sh --seconds 30 --channels 5 6 7 8
```

Flip one AT9S Pro switch at a time. The channel marked with `*` is the one that
changed. After you choose the Mission 2 enable switch, put that channel number
in `mission2_target_payload.json`:

```json
"mission": {
  "search_enable_rc_channel": 7,
  "search_enable_pwm_min": 1700
}
```

Keep Mission 1 on `mission1_no_search.json`, where `search_enabled` is `false`.
