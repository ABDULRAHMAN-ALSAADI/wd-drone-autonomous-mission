# WD DRONE Autonomous Mission

Autonomous rotary-wing UAV mission software for the 2026 TÜBİTAK/TEKNOFEST UAV competition.

The flight stack is divided into two systems:

- **Cube Orange Plus / ArduPilot:** flight control, navigation, failsafes and payload outputs.
- **Raspberry Pi 5:** mission supervision, computer vision, target centering and payload decisions.

The first repository version contains only a **read-only MAVLink monitor**. It does not arm, change mode, move the aircraft or release a payload.

## Hardware target

- Cube Orange Plus
- Raspberry Pi 5
- Raspberry Pi AI HAT+
- Raspberry Pi Camera Module 3 Standard
- HERE3+ GNSS
- RFD900x telemetry
- Two payload release channels

## Repository structure

```text
wd-drone-autonomous-mission/
├── config/
│   └── settings.json
├── docs/
│   ├── ARCHITECTURE.md
│   └── ROADMAP.md
├── scripts/
│   ├── run_sitl_monitor.sh
│   ├── run_uart_monitor.sh
│   └── setup.sh
├── src/
│   └── wd_drone/
│       ├── __init__.py
│       ├── config.py
│       ├── main.py
│       ├── mavlink_client.py
│       └── vehicle_status.py
├── tests/
│   └── test_config.py
├── .gitignore
├── README.md
└── requirements.txt
```

## 1. Install

```bash
cd ~/wd-drone-autonomous-mission
chmod +x scripts/*.sh
./scripts/setup.sh
```

## 2. Start SITL with a dedicated application output

Your current `sitl` alias must forward MAVLink to UDP port `14551`.

A direct command has this structure:

```bash
cd ~/ardupilot/ArduCopter

../Tools/autotest/sim_vehicle.py \
  -v ArduCopter \
  -f gazebo-iris \
  --model JSON \
  --map \
  --console \
  --out=udp:127.0.0.1:14551
```

Keep Gazebo and SITL running.

## 3. Run the Phase 1 monitor

Open another terminal:

```bash
cd ~/wd-drone-autonomous-mission
./scripts/run_sitl_monitor.sh
```

Expected result:

```text
Connected with profile 'sitl'. This Phase 1 program is read-only.
link=OK sys=1 mode=STABILIZE state=DISARMED gps_fix=3 sats=10 alt=0.0m ...
```

Stop it with `Ctrl+C`.

## 4. Run tests

```bash
cd ~/wd-drone-autonomous-mission
source .venv/bin/activate
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 5. Connection profiles

The profiles are stored in `config/settings.json`.

### SITL

```json
{
  "connection": "udpin:0.0.0.0:14551",
  "baud": null
}
```

### Cube Orange to Raspberry Pi UART

```json
{
  "connection": "/dev/serial0",
  "baud": 921600
}
```

Do not use the real-aircraft profile until the Cube–Pi wiring and serial parameters have been verified without propellers.

## 6. Initial Git setup

```bash
cd ~/wd-drone-autonomous-mission

git init
git add .
git commit -m "feat: add Phase 1 MAVLink telemetry monitor"
git branch -M main
```

Create an empty GitHub repository named:

```text
wd-drone-autonomous-mission
```

Then connect and push it:

```bash
git remote add origin https://github.com/YOUR_USERNAME/wd-drone-autonomous-mission.git
git push -u origin main
```

Replace `YOUR_USERNAME` with the GitHub account name.

## Safety boundary

Phase 1 is deliberately read-only. Flight commands will be added only after heartbeat reception, telemetry staleness detection, state logging and failure handling are verified in SITL.
