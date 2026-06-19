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

- [ ] Implement guarded mode-change helper
- [ ] Implement GUIDED velocity command with timeout
- [ ] Implement immediate stop command
- [ ] Restore AUTO at the saved mission waypoint
- [ ] Test without camera and without payload

## Phase 4 — Simulated target detection

- [ ] Add simulated triangle and hexagon targets
- [ ] Add OpenCV colour and polygon detector
- [ ] Display annotated detection output
- [ ] Reject fixed-wing rectangular targets

## Phase 5 — Visual centering

- [ ] Convert image error to body-frame velocity
- [ ] Add proportional speed reduction near target
- [ ] Add target-loss recovery
- [ ] Require stable centering before descent

## Phase 6 — Payload logic

- [ ] Map red payload to blue hexagon
- [ ] Map blue payload to red triangle
- [ ] Add release interlocks
- [ ] Simulate servo output
- [ ] Test real Cube AUX output without propellers

## Phase 7 — Raspberry Pi and AI HAT+

- [ ] Configure Raspberry Pi UART
- [ ] Verify Cube heartbeat over `/dev/serial0`
- [ ] Configure Camera Module 3
- [ ] Train and validate target model
- [ ] Convert model to Hailo HEF
- [ ] Run inference on AI HAT+
- [ ] Add systemd service and watchdog

## Phase 8 — Flight validation

- [ ] Bench tests without propellers
- [ ] Tethered or protected low-altitude tests
- [ ] Search-only test
- [ ] Centering-only test
- [ ] Dummy payload drop test
- [ ] Complete autonomous mission test
