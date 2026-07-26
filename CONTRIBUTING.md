# Contributing

Thank you for helping improve the project. You do not need to be an expert:
clear documentation fixes, reproducible bug reports, tests, and small focused
changes are valuable.

## Your First Contribution

1. Complete `UBUNTU_SETUP.txt`, read the root `README.md`, and run
   `./scripts/check_project.sh`.
2. Search existing issues before opening a new one.
3. Ask to be added as a collaborator or fork the repository. Use only your own
   GitHub account, token, and SSH keys.
4. Configure your identity once, then create a focused branch:

   ```bash
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   git switch -c fix/short-description
   ```

5. Make the change and add or update tests and documentation.
6. Run `./scripts/check_project.sh` again.
7. Commit with a short description of the observable change.
8. Push your branch and open a pull request using the template.

For a large change, a new dependency, or anything that affects flight control,
payload behavior, arming, modes, or failsafes, open an issue before investing
significant work.

## Source Of Truth

Edit and review code on an Ubuntu clone. Synchronize tested files to the
Raspberry Pi; do not treat ad-hoc Pi edits as the permanent source.

## Branches

`main` is the supported OpenCV Mission 2 implementation. Create a short feature
branch for every change and open a pull request back to `main`.

Do not merge experimental flight behavior directly into `main` during a test
day.

## Before A Pull Request

```bash
./scripts/check_project.sh
```

Also run the relevant SITL scenario when mission, control, MAVLink or vision
behavior changes. State clearly what was and was not tested on real hardware.

Continuous integration runs the software suite, but it cannot validate a
camera, UART connection, flight controller, payload mechanism, or aircraft.

## Change Rules

- Keep Mission Planner responsible for AUTO route, speed and altitude.
- Keep mission runtime free of RC override, raw motor output, motor test,
  flight termination and in-flight disarm commands.
- Put hazardous bench commands only under guarded test tools.
- Do not weaken target-shape rejection without positive and negative tests.
- Do not silently change the tested `/dev/ttyAMA0` connection, baud `921600`,
  servo channel, PWM,
  search waypoint or return behavior.
- Update configuration documentation with every new tunable field.
- Prefer a focused change over a broad rewrite during flight-test preparation.

## Data And Models

Do not commit camera dumps, logs, private data, model weights or generated HEF
compiler output unless the team explicitly approves the release. Keep a record
of model source, class mapping, training version and checksum outside Git until
then.

Never commit credentials, `.env` files, SSH keys, telemetry containing private
locations, or personal data. If sensitive information is committed, stop and
follow `SECURITY.md`; deleting it in a later commit is not enough.

## Commit Message

Describe observable intent, for example:

```text
Reject rectangular blue targets during search
Add Pi camera readiness check
Document Mission 2 operator sequence
```

## Review Checklist

- Does this alter arming, mode, velocity, payload or return behavior?
- Can stale telemetry or a stale camera frame reach a flight decision?
- Can the pilot still take control?
- Are positive, negative and timeout paths tested?
- Does the overlay/log make the state understandable?
- Are docs and example configs consistent with the code?
- Were propellers removed for every hardware bench test?
