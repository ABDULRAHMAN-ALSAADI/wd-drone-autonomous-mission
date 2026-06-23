# Test Components

This folder is for bench tests and health checks. It is not the mission flight
folder.

Use it to prove each piece works before enabling the real mission:

- Pi camera stream and FPS;
- laptop live camera window;
- Pi to Cube MAVLink heartbeat and health;
- flight mode command authority;
- guarded arm/mode sequence requested by the avionics test;
- payload servo output;
- propeller-removed motor tests.

Start with `COMMANDS.md`. It explains what each command tests, where to run it,
and what a good result looks like.

## Safety Rule

Never run servo, arm, or motor tests with propellers installed.
