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
cd ~/FOR_COMP/wd-drone-autonomous-mission/target_mission_v2
python3 vision_tools.py --config configs/sim_gazebo.json record --frames 300
```

With preview:

```bash
python3 vision_tools.py --config configs/sim_gazebo.json record --frames 300 --show
```

Recordings are written under:

```text
target_mission_v2/data/vision/
```

That directory is ignored by Git because recordings can become large.

## Replay A Session

```bash
python3 vision_tools.py --config configs/sim_gazebo.json replay \
  --input data/vision/SESSION_FOLDER \
  --output-jsonl data/vision/SESSION_FOLDER/report.jsonl
```

The JSONL report contains one record per frame with the detector output. This is
the fastest way to prove whether a blue runway rectangle is being rejected and a
real blue hexagon is still accepted.

## Recommended Dataset

Keep local, untracked recordings for:

- valid red triangle at 5 m, 7 m, and 10 m;
- valid blue hexagon at 5 m, 7 m, and 10 m;
- blue runway rectangles from multiple headings;
- partial target at image edges;
- motion blur during AUTO survey;
- real outdoor grass, shadows, and sunlight when the Pi camera is available.
