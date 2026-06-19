# Vision Lab

This folder is for development-machine vision work only. Do not copy it to the
Raspberry Pi 5 flight image unless you are intentionally debugging offline data
there.

The Pi runtime should stay focused on:

- `target_mission_v2/mission_controller.py`
- `target_mission_v2/vision.py`
- `target_mission_v2/control.py`
- mission configs and `run.sh`

## Record

Start the Gazebo camera stream:

```text
enable_camera
```

Record a labelled session:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json \
  record --label blue_hexagon --altitude-m 7 --frames 500 --show
```

Negative example:

```bash
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json \
  record --label none --altitude-m 7 --frames 300 --notes "blue runway rectangles"
```

Recordings go under `vision_lab/data/`, which is ignored by Git.

## Replay

```bash
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json \
  replay --input vision_lab/data/SESSION_FOLDER \
  --output-jsonl vision_lab/reports/session_report.jsonl
```

## Evaluate

If the session has a `manifest.json`, evaluation can read the expected target:

```bash
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json \
  evaluate --input vision_lab/data/SESSION_FOLDER \
  --output-json vision_lab/reports/eval.json
```

For old folders without a manifest:

```bash
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json \
  evaluate --input vision_lab/data/OLD_FOLDER --expected-target blue_hexagon
```

For negative folders:

```bash
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json \
  evaluate --input vision_lab/data/RUNWAY_FOLDER --negative
```
