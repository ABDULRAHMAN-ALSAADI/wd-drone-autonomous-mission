# Vision Model Plan

The mission should stay simple on the Raspberry Pi. Classical shape detection
is the default flight backend today. YOLO or AI HAT inference should be added
only after the model is trained, validated, and cooled hardware is installed.

## Current Backend

`target_mission_v2` uses:

```json
"vision": {
  "backend": "strict_shape"
}
```

`strict_shape` uses HSV colour masks plus polygon, extent, circularity,
solidity, aspect-ratio, and multi-frame confirmation rules. It is lightweight
and does not require model files.

## Future Backend

The recommended next backend is `yolo_shape_gate`:

```text
camera frame
-> YOLO candidate boxes
-> colour and geometry safety gate
-> multi-frame confirmation
-> GUIDED centering
```

Do not use raw YOLO boxes directly for payload decisions. The safety gate should
still reject blue runway rectangles, red squares, shadows, and partial objects.

## Dataset Labels

Use exactly these class names:

```text
red_triangle
blue_hexagon
```

Negative examples are just as important as positives. Include:

- blue runway rectangles and strips;
- red squares and red rectangles;
- grass without targets;
- target edges partly outside the image;
- motion blur;
- different altitudes such as 5 m, 7 m, and 10 m;
- different sun angles and exposure levels.

## Model Files

Keep large training outputs out of Git. The repository ignores:

```text
models/*.pt
models/*.onnx
models/*.hef
data/raw/
data/processed/
```

Store only small instructions, configs, and test summaries in Git.

## Acceptance Gate

A trained detector is not ready for flight until it passes:

- SITL mission tests;
- rectangle rejection tests;
- real-camera still-frame tests;
- real-camera video tests;
- props-off Pixhawk mode and payload tests;
- low-speed centering with payload simulation enabled.
