# Motor Mapping Fix

Use this when the motor test spins the wrong physical motor.

## What Happened In Your Test

You reported:

| Command | Motor that actually spun |
| --- | --- |
| `--motor 1` | physical M3 |
| `--motor 2` | physical M4 |
| `--motor 3` | physical M1 |
| `--motor 4` | physical M2 |

That means the ESC signal wires are crossed in pairs:

```text
output 1 <-> output 3
output 2 <-> output 4
```

## Safe Physical Fix

With propellers removed, swap only the ESC signal wires:

| Pixhawk MAIN OUT | Should go to physical motor |
| --- | --- |
| MAIN OUT 1 | M1 |
| MAIN OUT 2 | M2 |
| MAIN OUT 3 | M3 |
| MAIN OUT 4 | M4 |

Based on your observed result, that means:

```text
swap the signal wires on MAIN OUT 1 and MAIN OUT 3
swap the signal wires on MAIN OUT 2 and MAIN OUT 4
```

Keep grounds common. Do not move battery power wires while powered.

## Frame Type Check

Your picture is a Quad X Betaflight motor layout. In ArduPilot, that frame
mixing is `FRAME_TYPE = 12` (`BetaFlightX`) with `FRAME_CLASS = 1` (`Quad`).

Set or verify this in Mission Planner before flight:

```text
FRAME_CLASS = 1
FRAME_TYPE  = 12
```

Changing `FRAME_TYPE` requires rebooting the flight controller.

## Retest Order

After rewiring or changing frame type, retest with propellers removed:

```bash
./test_components/mavlink/motor_test.sh --motor 1 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
./test_components/mavlink/motor_test.sh --motor 2 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
./test_components/mavlink/motor_test.sh --motor 3 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
./test_components/mavlink/motor_test.sh --motor 4 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
```

Do not continue to propeller testing until motor position and spin direction are
both correct.
