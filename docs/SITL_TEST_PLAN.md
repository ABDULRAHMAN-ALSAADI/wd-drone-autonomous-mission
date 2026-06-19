# SITL Test Plan

Run this matrix after detector or controller changes.

## Required Tests

1. Default run at 5 m:
   - detects blue hexagon;
   - centers;
   - simulates red payload;
   - resumes AUTO;
   - detects red triangle;
   - simulates blue payload;
   - enters RTL.

2. QGC altitude changed to 7 m:
   - overlay altitude follows vehicle altitude;
   - no `target=5 m` control claim appears;
   - centering still works.

3. QGC altitude changed to 10 m:
   - targets are still detected;
   - if detections are weak, record frames and tune offline.

4. Blue runway rectangle pass:
   - detector must not confirm a runway stripe as `blue_hexagon`;
   - hit count must remain below confirmation.

5. Repeat-run reset:
   - finish mission once;
   - start AUTO again without closing the process;
   - `MISSIONS DONE` increments and targets are searched again.

6. Failure handling:
   - cover target during centering;
   - controller returns to AUTO after target-lost timeout;
   - if centering cannot finish, controller returns to AUTO after safety timeout.

## Automated Tests

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
PYTHONPATH=src python3 -m unittest discover -s tests -v
cd target_mission_v2
python3 -m unittest -v test_mission_controller.py
```
