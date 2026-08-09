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
- [x] Validate both target classes against real Pi Camera Module 3 frames

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
- [x] Test real Cube MAIN OUT 5 selector output without propellers

## Phase 7 — Raspberry Pi And Camera

- [x] Configure Raspberry Pi UART as `/dev/ttyAMA0` at `921600`
- [x] Verify Cube vehicle heartbeat `src=1:1`
- [x] Configure and benchmark Camera Module 3
- [ ] Add systemd service and watchdog after manual launch is stable

## Phase 8 — Flight validation

- [ ] Bench tests without propellers
- [ ] Tethered or protected low-altitude tests
- [ ] Search-only test
- [ ] Centering-only test
- [ ] Dummy payload drop test
- [ ] Complete autonomous mission test

Physical evidence for completed hardware items is recorded in
[`HARDWARE_TEST_LOG.md`](HARDWARE_TEST_LOG.md). Flight-validation items remain
open.
