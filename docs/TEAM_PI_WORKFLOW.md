# Team Raspberry Pi Workflow

This guide is for team members who need to inspect, edit, sync, and test the
Raspberry Pi 5 mission computer.

## Rules

- Use the SSH alias `pi5`; do not hard-code the Pi IP address in scripts.
- Keep the Ubuntu laptop repo as the source of truth.
- Do not copy a laptop `.venv` to the Pi. The Pi builds its own ARM64 `.venv`.
- Do not use `sudo`, reboot, arm, move servos, spin motors, upload missions, or
  run the autonomous mission on hardware unless the test lead approves it.
- Remove propellers before any Pixhawk command test.
- Without fan/heatsink, keep Pi work light: sync, edit, import checks, and
  heartbeat tests only.

## Team Access

Each team member should have their own SSH public key on the Pi. From their
laptop, they generate a key if needed:

```bash
ssh-keygen -t ed25519 -C "team-member-name"
```

Then the Pi owner adds the public key to:

```text
/home/pi5/.ssh/authorized_keys
```

Each laptop should define the same SSH alias:

```text
Host pi5
    HostName raspberrypi5.local
    User pi5
    IdentityFile ~/.ssh/id_ed25519
```

After that:

```bash
ssh pi5
```

## Sync Code To The Pi

From the Ubuntu laptop repo:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
DRY_RUN=1 ./scripts/sync_to_pi.sh
./scripts/sync_to_pi.sh
```

The script excludes virtualenvs, caches, logs, raw data, model files, secrets,
and `.env`. It does not delete files on the Pi.

## Prepare And Validate The Pi Copy

From the Ubuntu laptop repo:

```bash
./scripts/pi_validate.sh
```

This creates/updates the Pi `.venv`, installs `requirements.txt`, compiles the
Python files, runs the lightweight package tests, and prints Pi temperature and
throttle status. It does not touch the Pixhawk.

## Editing Code

Recommended flow:

```bash
git pull
# edit on the Ubuntu laptop
python -m unittest discover -s tests -v
cd target_mission_v2
python -m unittest -v test_mission_controller.py
cd ..
./scripts/sync_to_pi.sh
./scripts/pi_validate.sh
```

Full laptop check before a serious sync:

```bash
./scripts/check_project.sh
```

Clean ignored local caches when the workspace gets noisy:

```bash
./scripts/clean_workspace.sh
```

Only commit and push after tests pass:

```bash
git status
git add .
git commit -m "Describe the change"
git push
```

## Safe MAVLink Bench Order

Before the Pixhawk is connected:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/pi_uart_preflight.sh
```

When the Pixhawk is connected and propellers are removed:

```bash
ssh pi5
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/mavlink/status.sh
```

Only after heartbeat works should the team test mode requests or servo output.
Motor tests are last and require explicit approval.

## GitHub Collaboration

Give teammates repository access from GitHub:

1. Open the repository on GitHub.
2. Go to `Settings` -> `Collaborators and teams`.
3. Add each teammate by GitHub username.
4. Use `Write` access for normal contributors.
5. Protect `main` later if the team starts working in branches.

For Pi access, GitHub permission is not enough. The Pi still needs each
teammate's SSH public key in `/home/pi5/.ssh/authorized_keys`.
