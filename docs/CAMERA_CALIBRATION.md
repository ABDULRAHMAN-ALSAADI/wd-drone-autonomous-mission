# Camera Calibration

Gazebo camera images are not the same as Raspberry Pi Camera Module 3 images.
The simulator is good for mission logic, shape rules, and centering behavior,
but the real camera will differ in lens distortion, field of view, exposure,
white balance, motion blur, vibration, compression, and sunlight.

## Altitude Flexibility

The controller can run at 5 m, 7 m, or 10 m because it does not command AUTO
altitude by default. QGC and ArduPilot decide the mission altitude.

Vision still needs validation at each altitude because the target pixel area
changes with height. The current detector uses configurable minimum contour
areas:

```json
"vision": {
  "search_min_area_px": 220.0,
  "tracking_min_area_px": 120.0
}
```

If a real target is missed at 10 m, lower these values carefully and rerun the
rectangle rejection tests. Do not lower them blindly during flight.

## Real Camera First Steps

1. Mount the Pi Camera Module 3 rigidly and point it straight down.
2. Lock exposure and white balance if possible after field testing.
3. Record target frames on the ground before flying.
4. Replay those frames with `vision_lab/vision_lab.py`.
5. Confirm the blue runway rectangles and other blue objects are rejected.
6. Keep payload `simulate_only` enabled until repeated SITL and tethered tests
   are clean.

## Centring Check

If the drone moves the wrong way while centering:

```json
"image_y_to_forward_sign": 1.0
```

or:

```json
"image_x_to_right_sign": -1.0
```

Change only one axis at a time and test at low speed.
