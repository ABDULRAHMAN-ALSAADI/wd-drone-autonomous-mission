## What changed?

Describe the problem and the solution in plain language.

## How was it tested?

- [ ] `./scripts/check_project.sh`
- [ ] Gazebo / ArduPilot SITL, if relevant
- [ ] Propeller-off hardware bench test, if relevant
- [ ] Real flight test, if relevant and team-approved

List the exact scenarios and results. Clearly state anything that was not
tested.

## Safety impact

- [ ] No effect on arming, flight modes, MAVLink commands, velocity, payload,
      return behavior, or failsafes
- [ ] Safety-critical behavior changed and is explained below

Describe new failure modes, limits, recovery behavior, and pilot override
behavior.

## Checklist

- [ ] The change is focused and documented.
- [ ] Tests cover positive, negative, and timeout paths where relevant.
- [ ] No secrets, private flight data, logs, datasets, or model weights are
      included.
- [ ] Configuration documentation matches the code.
