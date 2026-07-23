# Tools

Developer and bench helper programs live here.

MAVLink bench tool:

- `mavlink_bench.py`: implementation behind `scripts/mavlink_bench.sh`.

OpenCV-only tools:

- `pi_camera_diagnostics.py`: direct Camera Module 3 metadata, raw captures,
  and optional local recording without MAVLink.
- `pi_camera_focus_sweep.py`: manual lens-position sharpness sweep.
- `opencv_live_test.py`: raw, masks, search, tracking, or full OpenCV test;
  never connects to MAVLink.
- `benchmark_opencv_pipeline.py`: camera and OpenCV timing measurements.
- `replay_opencv_vision.py`: deterministic image/video replay and reports.
- `calibrate_payload_drop_pixel.py`: saves a proposed drop-reference pixel to
  a separate calibration file.
- `mission_stream_view.py`: laptop viewer for the annotated stream produced on
  the Pi.

Most users should use the wrappers documented in
`docs/OPENCV_PI5_TESTING.md` instead of calling these modules directly.
