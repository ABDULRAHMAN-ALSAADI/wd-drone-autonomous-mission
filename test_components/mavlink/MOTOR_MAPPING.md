# Motor Test Numbering

This project uses ArduPilot `FRAME_CLASS=1`, `FRAME_TYPE=12`
(`BetaFlightX`). Its MAVLink motor-test command uses a test-sequence number,
which is not the same as the physical M-number for M1 and M2.

## Latest Test Result

You reported:

| Command | Motor that actually spun |
| --- | --- |
| `--motor 1` | physical M2 |
| `--motor 2` | physical M1 |
| `--motor 3` | not yet re-confirmed |
| `--motor 4` | not yet re-confirmed |

Because the same motors run correctly from Mission Planner, this observation
does not show an ESC wiring fault. The old recommendation to swap M1/M2 signal
wires was incorrect and must not be followed.

The Pi bench command now accepts the physical motor label and translates it to
ArduPilot's BetaFlightX motor-test sequence:

| Requested physical motor | MAVLink test sequence |
| --- | --- |
| M1 | 2 |
| M2 | 1 |
| M3 | 3 |
| M4 | 4 |

This translation exists only in the guarded bench tool. It does not change
ArduPilot motor mixing, normal flight outputs, or mission runtime.

## Frame Type Requirement

Your picture is a Quad X Betaflight motor layout. In ArduPilot, that frame
mixing is `FRAME_TYPE = 12` (`BetaFlightX`) with `FRAME_CLASS = 1` (`Quad`).

Verify this in Mission Planner:

```text
FRAME_CLASS = 1
FRAME_TYPE  = 12
```

Do not use this bench mapping with another frame type.

## Retest Order

Retest all four physical labels with propellers removed:

```bash
./test_components/mavlink/motor_test.sh --motor 1 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
./test_components/mavlink/motor_test.sh --motor 2 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
./test_components/mavlink/motor_test.sh --motor 3 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
./test_components/mavlink/motor_test.sh --motor 4 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
```

Do not continue to propeller testing until motor position and spin direction are
both correct.
