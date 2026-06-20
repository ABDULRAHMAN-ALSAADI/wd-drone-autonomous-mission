# Development Roadmap

## Phase 1 — MAVLink link

- [x] Repository structure
- [x] SITL and Cube UART connection profiles
- [x] Read-only heartbeat and telemetry monitor
- [x] Confirm stable SITL connection on port 14551
- [x] Run unit tests

## Phase 2 — Mission observation

- [x] Read current AUTO mission index
- [x] Detect entry into the search area
- [x] Create mission event logger
- [x] Add deterministic mission-state transitions
- [x] Add unit tests for mission-state transitions
- [ ] Validate waypoint changes during a real SITL AUTO mission
- [ ] Set the final search-start waypoint from the competition mission

## Phase 3 — Safe AUTO/GUIDED control

- [x] Implement guarded GUIDED/AUTO/RTL mode requests
- [x] Implement GUIDED velocity command with timeout
- [x] Stop horizontal velocity when leaving GUIDED
- [x] Resume AUTO after the first target
- [x] Keep QGC/ArduPilot owner of AUTO speed and altitude by default
- [ ] Test mode transitions on Cube without propellers

## Phase 4 — Simulated target detection

- [x] Add OpenCV colour and polygon detector
- [x] Display annotated detection output
- [x] Reject fixed-wing rectangular targets
- [x] Add multi-frame target confirmation
- [ ] Validate against real Pi Camera Module 3 frames

## Phase 5 — Visual centering

- [x] Convert image error to body-frame velocity
- [x] Add proportional speed reduction near target
- [x] Add target-loss recovery
- [x] Require stable centering before payload action
- [ ] Tune centering gain on real camera/video

## Phase 6 — Payload logic

- [x] Map red payload to blue hexagon
- [x] Map blue payload to red triangle
- [x] Add release interlocks
- [x] Simulate servo output
- [ ] Test real Cube AUX output without propellers

## Phase 7 — Raspberry Pi and AI HAT+

- [ ] Configure Raspberry Pi UART
- [ ] Verify Cube heartbeat over `/dev/serial0`
- [ ] Configure Camera Module 3
- [ ] Train and validate target model
- [ ] Convert model to Hailo HEF
- [ ] Add `yolo_shape_gate` backend after model validation
- [ ] Run inference on AI HAT+
- [ ] Add systemd service and watchdog after manual launch is stable

## Phase 8 — Flight validation

- [ ] Bench tests without propellers
- [ ] Tethered or protected low-altitude tests
- [ ] Search-only test
- [ ] Centering-only test
- [ ] Dummy payload drop test
- [ ] Complete autonomous mission test
