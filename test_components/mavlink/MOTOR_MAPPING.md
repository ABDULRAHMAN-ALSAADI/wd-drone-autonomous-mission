# Motor Mapping Fix

Use this when the motor test spins the wrong physical motor.

## Latest Test Result

You reported:

| Command | Motor that actually spun |
| --- | --- |
| `--motor 1` | physical M2 |
| `--motor 2` | physical M1 |
| `--motor 3` | not yet re-confirmed |
| `--motor 4` | not yet re-confirmed |

The latest result means the M1 and M2 ESC signal assignments are exchanged:

```text
output 1 <-> output 2
```

## Safe Physical Fix

With every propeller removed, disconnect the flight battery and all other
airframe power. Swap only the M1 and M2 ESC signal assignments:

| Pixhawk MAIN OUT | Should go to physical motor |
| --- | --- |
| MAIN OUT 1 | M1 |
| MAIN OUT 2 | M2 |
| MAIN OUT 3 | M3 |
| MAIN OUT 4 | M4 |

Based on the latest observed result:

```text
swap the signal assignments on MAIN OUT 1 and MAIN OUT 2
```

On a four-in-one ESC harness, correct the S1/S2 signal-pin order or the matching
output assignment. Keep signal grounds paired and common. Do not move any wire
while the aircraft is powered. Swapping two motor phase wires changes rotation
direction only; it does not correct motor position numbering.

Do not compensate by changing the Raspberry Pi motor-test command. ArduPilot
must own the correct motor mapping for every flight mode and failsafe.

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

After correcting the signal assignment or frame setup, retest all four outputs
with propellers removed:

```bash
./test_components/mavlink/motor_test.sh --motor 1 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
./test_components/mavlink/motor_test.sh --motor 2 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
./test_components/mavlink/motor_test.sh --motor 3 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
./test_components/mavlink/motor_test.sh --motor 4 --throttle-percent 5 --duration 1 --i-understand-props-off --i-accept-motor-spin
```

Do not continue to propeller testing until motor position and spin direction are
both correct.
