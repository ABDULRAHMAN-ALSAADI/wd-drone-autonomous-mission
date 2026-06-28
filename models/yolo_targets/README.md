# YOLO Target Model

This folder stores the first YOLO target detector delivered by the vision/data
team.

## Files

| File | Purpose |
| --- | --- |
| `best.pt` | Ultralytics/PyTorch model for local Ubuntu testing and training review. |
| `best.onnx` | Exported ONNX model for future runtime work such as Hailo conversion. |

## Current Model Metadata

| Item | Value |
| --- | --- |
| Task | Object detection |
| Input image size | 640 |
| Classes | `0: kirmzi`, `1: mavi` |
| `best.pt` SHA256 | `db52562e3b03329c26524c3ee83ea653a8d1e68e796ee18615ca4eddd3b2f724` |
| `best.onnx` SHA256 | `e0fcc5e15793eaf6f61cb1a764c0a9cfd2f90abf914ad5ab93de85aa68f5e3fd` |

## Important Safety Note

The class names mean red and blue, not explicitly triangle and hexagon. The
mission code maps:

```text
kirmzi/kirmizi -> red_triangle
mavi           -> blue_hexagon
```

The YOLO backend treats the model output as a candidate only. Before it returns
a mission target, it requires:

```json
"yolo_require_colour_sanity": true,
"yolo_require_strict_shape": true
```

That second gate runs the same strict triangle/hexagon checks used by the
classical detector. This is intentional: the model can confidently call a red
square/diamond `kirmzi`, but the drone must not fly toward it as a triangle.

## Current Use

The normal real-drone mission still uses:

```json
"backend": "strict_shape"
```

Use YOLO only when explicitly testing:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./simulation/run_target_mission.sh target_mission_v2/configs/sim_yolo_local.json
```

For Raspberry Pi 5 flight use, do not switch the real mission to YOLO until the
Pi has cooling and the selected runtime has been tested for FPS, temperature,
and missed detections.
