# Test Components

This folder is for bench tests and health checks. It is not the mission flight
folder.

Use it to prove each piece works before enabling the real mission:

- Pi camera stream and FPS;
- laptop live camera window;
- Pi to Cube MAVLink heartbeat and health;
- AT9S Pro RC switch/channel mapping;
- flight mode command authority;
- guarded arm/mode sequence requested by the avionics test;
- payload servo output;
- propeller-removed motor tests.

Start with `COMMANDS.md`. It explains what each command tests, where to run it,
and what a good result looks like.

Use the
[complete repeatable camera-recognition-servo procedure](COMMANDS.md#8-payload-servo-and-opencv-integration-test).
Record physical results in the
[hardware test log](../docs/HARDWARE_TEST_LOG.md), then use the
[real-drone checklist](../docs/REAL_DRONE_CHECKLIST.md) to decide what remains
before flight.

## Safety Rule

Never run servo, arm, or motor tests with propellers installed.
