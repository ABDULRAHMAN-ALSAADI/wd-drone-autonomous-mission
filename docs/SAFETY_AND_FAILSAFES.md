# Safety And Failsafes

Safety is part of the mission design, not a final checkbox.

## Competition Safety Requirements To Track

The rules require pre-flight safety control. The important software/hardware
items are:

- all components securely mounted;
- wires and connectors sized correctly and strain-relieved;
- propeller, motor, and rotation direction checked;
- radio range sufficient for control and motor on/off;
- radio system protected against interference;
- fail-safe mode activates within 5 seconds after RC signal loss;
- emergency power cut-off switch cuts UAV power within 2 seconds;
- encrypted telemetry for fixed-wing and rotary-wing categories;
- telemetry and image transmission pre-tested before the competition area.

## Mission Planner / ArduPilot Items

Back up parameters before changing anything.

In Mission Planner, verify and document:

- frame class/type;
- motor order and direction;
- accelerometer calibration;
- compass calibration;
- RC calibration;
- flight modes on the transmitter switch;
- battery monitor;
- EKF health;
- GPS health;
- geofence if used;
- RTL behavior;
- RC failsafe behavior;
- battery failsafe behavior;
- GCS failsafe behavior if used;
- serial port protocol and baud for the Pi TELEM port.

## RC Failsafe

The official rotary-wing safety check expects controlled descent or throttle cut
behavior, not an uncontrolled mode fight.

Ground test with propellers removed:

1. Confirm the Cube is disarmed.
2. Confirm Mission Planner shows RC input.
3. Turn off the transmitter.
4. Confirm ArduPilot enters the configured failsafe behavior within 5 seconds.
5. Turn the transmitter back on.
6. Confirm control recovers.

Record the Mission Planner parameter backup after the test.

## Emergency Power Cut-Off

The UAV must have an accessible power cut-off mechanism that cuts power within
2 seconds.

Bench test:

1. Props off.
2. Power the system.
3. Start a stopwatch.
4. Use the emergency cut-off.
5. Confirm power is removed within 2 seconds.

## Companion Computer Boundary

The Raspberry Pi mission software should not own these by default:

- AUTO altitude;
- AUTO speed;
- ArduPilot failsafe decisions;
- arming;
- motor tests;
- parameter changes.

The default real mission profile keeps:

```json
"altitude_control": "off"
"search_speed_source": "qgc_mission"
"payload": {
  "simulate_only": true
}
```

During Mission 2 the companion only treats AUTO and GUIDED as mission-owned
modes. If ArduPilot briefly reports AUTO while the Pi is centering a confirmed
target, the Pi keeps the target lock and requests GUIDED again. If the pilot or
failsafe changes to LOITER, STABILIZE, RTL, LAND, or another non-mission mode,
the Pi sends zero velocity, drops the active target lock, and waits for AUTO
instead of fighting the aircraft.

`safety.camera_frame_timeout_s` protects active target work if the camera feed
stalls. After the timeout, the Pi holds position in GUIDED and reports a camera
timeout in the overlay/log instead of continuing the route blindly.

## Before Real Payload Release

Keep `payload.simulate_only` set to `true` until:

- SITL mission passes repeatedly;
- bench servo channel/PWM is verified;
- payload mechanism is mechanically safe;
- real camera target detection is proven;
- guided centering direction is verified at low speed;
- abort plan is agreed by the team.

## Wiring Notes

For Pi/Cube MAVLink:

- Cube TELEM TX -> Pi GPIO15 RXD.
- Cube TELEM RX -> Pi GPIO14 TXD.
- Cube GND -> Pi GND.
- Do not power the Pi from UART pins.

Use strain relief on screw-terminal or fragile wire entry points. The wiring PDF
mentions using hot glue for wire support; keep glue away from connectors that
must be serviced or inspected.

## Pi 5 Power And Cooling

Check before heavy work:

```bash
vcgencmd measure_temp
vcgencmd get_throttled
```

Stop heavy work near 80 C. Do not run long OpenCV/YOLO workloads without
cooling.
