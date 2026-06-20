# Vision Replay Workflow

Use replay before changing detector thresholds. Screenshots are useful for
discussion, but recorded frames let every change be tested against the same
input.

## Record From SITL

Start the Gazebo camera stream first:

```text
enable_camera
```

Record frames:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json \
  record --label blue_hexagon --altitude-m 7 --frames 300
```

With preview:

```bash
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json \
  record --label blue_hexagon --altitude-m 7 --frames 300 --show
```

If the computer feels heavy while Gazebo/QGC are running, do not use `--show`.
The recorder saves processed-width frames by default to keep sessions smaller.
Use `--save-raw` only for full-resolution camera debugging.

Recordings are written under:

```text
vision_lab/data/
```

That directory is ignored by Git because recordings can become large. It is a
development-laptop folder, not a Pi runtime folder.

## Replay A Session

List real session folders first:

```bash
python3 vision_lab/vision_lab.py list
```

```bash
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json replay \
  --input vision_lab/data/REAL_SESSION_FOLDER \
  --output-jsonl vision_lab/reports/session_report.jsonl
```

The JSONL report contains one record per frame with the detector output. This is
the fastest way to prove whether a blue runway rectangle is being rejected and a
real blue hexagon is still accepted.

## Evaluate Sessions

Labelled recordings include `manifest.json`, so evaluation can summarize
detector quality:

```bash
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json evaluate \
  --input vision_lab/data/REAL_SESSION_FOLDER \
  --output-json vision_lab/reports/eval.json
```

For a negative runway-rectangle folder without a manifest:

```bash
python3 vision_lab/vision_lab.py --config target_mission_v2/configs/sim_gazebo.json evaluate \
  --input vision_lab/data/RUNWAY_FOLDER --negative
```

## Recommended Dataset

Keep local, untracked recordings for:

- valid red triangle at 5 m, 7 m, and 10 m;
- valid blue hexagon at 5 m, 7 m, and 10 m;
- blue runway rectangles from multiple headings;
- partial target at image edges;
- motion blur during AUTO survey;
- real outdoor grass, shadows, and sunlight when the Pi camera is available.
