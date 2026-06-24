# Simulation

This folder is the clean entry point for running the Gazebo + ArduPilot SITL
version of the mission.

It does not contain ArduPilot or the Gazebo plugin source. Those are external
simulation dependencies already installed on the Ubuntu laptop:

```text
~/ardupilot
~/ardupilot_gazebo
```

## One-Terminal-Per-Job Start Order

Open four terminals on the Ubuntu laptop.

### 1. Gazebo

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./simulation/start_gazebo.sh
```

If you want to force NVIDIA offload:

```bash
./simulation/start_gazebo.sh --nvidia
```

### 2. ArduPilot SITL

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./simulation/start_sitl.sh
```

This broadcasts MAVLink to:

```text
127.0.0.1:14550  for QGC/Mission Planner/MAVProxy
127.0.0.1:14551  for the target mission controller
```

### 3. Gazebo Camera Stream

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./simulation/enable_gazebo_camera.sh
```

### 4. Target Mission Controller

```bash
cd ~/FOR_COMP/wd-drone-autonomous-mission
./simulation/run_target_mission.sh
```

This uses:

```text
target_mission_v2/configs/sim_gazebo.json
```

## Fast Commands From The Old Laptop Setup

The old personal shortcuts were:

```text
drone         -> ~/start_sim.sh
sitl          -> ArduPilot sim_vehicle.py with Gazebo JSON backend
enable_camera -> Gazebo camera streaming topic command
```

Those shortcuts are useful on your laptop, but team members should use the
scripts in this folder because they are version-controlled and easier to read.

## Mission Test Checklist

Before changing real-drone behavior, prove it here first:

1. AUTO mission flies to the configured search area.
2. Blue hexagon is detected and centered.
3. Red triangle is detected and centered.
4. Blue runway rectangles are rejected as non-targets.
5. Temporary target loss does not immediately abort centering.
6. After both targets, the controller requests RTL.

The detailed test matrix is in:

```text
docs/SITL_TEST_PLAN.md
```

