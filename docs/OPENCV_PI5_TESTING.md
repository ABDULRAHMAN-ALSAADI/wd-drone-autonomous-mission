# OpenCV Camera Module 3 Testing

This is the hardware-test path for the OpenCV-only mission. It processes
Camera Module 3 frames directly on the Raspberry Pi 5 through Picamera2.
Camera tests never connect to MAVLink and never send motor, mode, or payload
commands.

## 1. Install Once On The Pi

Run on the Raspberry Pi:

```bash
sudo apt update
sudo apt install -y python3-venv python3-opencv python3-numpy python3-picamera2

cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/setup.sh
```

The virtual environment is created with `--system-site-packages` so it can use
the Raspberry Pi OS Picamera2 and OpenCV packages.

Verify imports:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
.venv/bin/python -c "import cv2,numpy,picamera2; print(cv2.__version__); print(picamera2.__file__)"
```

## 2. Verify The Camera And Pi Health

The physical CAM/DISP connector number is not the Picamera2 index. With one
detected camera, use camera index `0` even when the ribbon is connected to the
CAM/DISP 1 connector.

```bash
vcgencmd measure_temp
vcgencmd get_throttled

cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/camera/opencv_test.sh list
./test_components/camera/opencv_test.sh diagnostic --seconds 20
```

Expected results:

- the IMX708 Camera Module 3 is listed;
- resolution is 1280x720 at 30 FPS;
- source is `picamera2`, not `rpicam_mjpeg`;
- `get_throttled=0x0`;
- frame age stays bounded;
- a report is written below `~/camera_tests/opencv_diagnostics/`.

Measured on the project Raspberry Pi 5 on 2026-07-23:

- direct Picamera2 diagnostic: stable 30.0 FPS at 1280x720;
- full OpenCV pipeline: 29.61 FPS, 14.63 ms mean processing latency, and
  16.32 ms p95 processing latency;
- full pipeline plus laptop-monitor stream: 29.34 FPS, 13.19 ms mean
  processing latency, and 15.54 ms p95 processing latency;
- monitoring stream: approximately 7.8 FPS at JPEG quality 82 while the
  detector continued near 30 FPS;
- peak measured temperature: 53.8 C;
- throttling status: `0x0`;
- peak measured process memory: approximately 217 MiB.

These measurements verify camera and processing performance only. They do not
verify target accuracy, centering control, payload behavior, or flight safety.
Repeat the benchmark after changing camera controls or OpenCV thresholds.

Record raw local evidence without a display:

```bash
./test_components/camera/opencv_test.sh diagnostic \
  --seconds 20 \
  --save-every-s 1 \
  --record
```

## 3. Test OpenCV On The Pi

The camera-only profile is explicitly blocked from starting the MAVLink mission
controller. Run it through the camera test wrapper:

```bash
./test_components/camera/opencv_test.sh live \
  --config target_mission_v2/configs/opencv_real_no_control.json \
  --mode raw \
  --headless \
  --duration 20
```

Run the individual OpenCV stages:

```bash
./test_components/camera/opencv_test.sh live --mode raw --headless --duration 20
./test_components/camera/opencv_test.sh live --mode masks --headless --duration 20
./test_components/camera/opencv_test.sh live --mode search --headless --duration 20
./test_components/camera/opencv_test.sh live --mode tracking --headless --duration 20
./test_components/camera/opencv_test.sh live --mode full --headless --duration 20
```

Then start the Pi-side detector and a low-rate annotated stream:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./test_components/camera/opencv_test.sh live \
  --mode full \
  --headless \
  --stream \
  --stream-bind 127.0.0.1
```

This terminal must remain open. It runs Picamera2 and OpenCV on the Pi. It
retains only the newest camera frame, so a slow detector drops frames instead
of building latency.

On the Ubuntu laptop, open a second terminal:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./real_mission/open_laptop_camera_window.sh
```

The laptop program creates an SSH tunnel to `pi5` and displays the exact
annotated JPEG frames produced by the Pi process. The stream is for monitoring;
it is not the mission's vision input.

Test each class separately:

```bash
./test_components/camera/opencv_test.sh live \
  --mode full --headless --stream --target red_triangle

./test_components/camera/opencv_test.sh live \
  --mode full --headless --stream --target blue_hexagon
```

## 4. Capture More Real Images

Place only a red triangle in view, vary distance, rotation, lighting, and
background, then run:

```bash
./test_components/camera/opencv_test.sh diagnostic \
  --seconds 30 \
  --save-every-s 1 \
  --output-dir ~/camera_tests/dataset/red_triangle
```

For the blue hexagon:

```bash
./test_components/camera/opencv_test.sh diagnostic \
  --seconds 30 \
  --save-every-s 1 \
  --output-dir ~/camera_tests/dataset/blue_hexagon
```

Also collect negative scenes with red and blue rectangles, circles, fabric,
chairs, runway markings, shadows, and empty ground:

```bash
./test_components/camera/opencv_test.sh diagnostic \
  --seconds 30 \
  --save-every-s 1 \
  --output-dir ~/camera_tests/dataset/negative
```

The captures are raw PNG files. Do not tune from annotated screenshots.

## 5. Measure Focus At Real Distances

Run one sweep while the printed target is at each representative distance:

```bash
./test_components/camera/opencv_test.sh focus \
  --distance-label search \
  --start 0 --stop 10 --step 0.5

./test_components/camera/opencv_test.sh focus \
  --distance-label centering \
  --start 0 --stop 10 --step 0.5

./test_components/camera/opencv_test.sh focus \
  --distance-label payload_release \
  --start 0 --stop 10 --step 0.5
```

Review the saved images and CSV files. The tool reports a range for manual
review; it never changes mission configuration automatically.

After selecting a value, edit only `camera.lens_position` in the intended
profile and keep `camera.autofocus_mode` set to `manual`. Re-run the diagnostic
and confirm the reported lens position before using that profile.

## 6. Benchmark Before Tuning

Run each workload on the Pi:

```bash
./test_components/camera/opencv_test.sh benchmark --mode camera --seconds 30
./test_components/camera/opencv_test.sh benchmark --mode masks --seconds 30
./test_components/camera/opencv_test.sh benchmark --mode search --seconds 30
./test_components/camera/opencv_test.sh benchmark --mode tracking --seconds 30
./test_components/camera/opencv_test.sh benchmark --mode full --seconds 30
./test_components/camera/opencv_test.sh benchmark --mode full-stream --seconds 30
```

Reports are saved under `~/camera_tests/opencv_benchmark/`. Compare full versus
full-stream to measure monitoring overhead.

## 7. Calibrate The Drop Reference

This GUI tool needs a desktop display or trusted X forwarding:

```bash
./test_components/camera/opencv_test.sh calibrate
```

Click the payload outlet reference, fine-tune with W/A/S/D, and press Enter.
The result is saved separately at
`~/camera_tests/payload_drop_pixel.json`. Review it before manually copying the
pixel values into a mission profile.

## 8. Replay Captured Data On Ubuntu

Copy a dataset to Ubuntu and run:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
python3 tools/replay_opencv_vision.py \
  --config target_mission_v2/configs/opencv_replay.json \
  --input /path/to/images \
  --output-dir /tmp/opencv-replay
```

The replay tool writes an annotated video, temporal states, candidate
rejections, performance summary, and proposed settings. It never overwrites a
mission profile.

## 9. Run Software Tests

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./scripts/check_project.sh
```

Run only the focused OpenCV refactor tests:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission/target_mission_v2
../.venv/bin/python -m unittest -v test_opencv_refactor.py
```

Run only the mission-controller tests:

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission/target_mission_v2
../.venv/bin/python -m unittest -v test_mission_controller.py
```

For telemetry-only Cube inspection, use the read-only commands in
`test_components/COMMANDS.md`. Do not run the real mission during camera
tuning.

Do not run the real mission until camera colour order, focus, false-positive
rejection, frame age, and thermal behavior have been measured with real data.
