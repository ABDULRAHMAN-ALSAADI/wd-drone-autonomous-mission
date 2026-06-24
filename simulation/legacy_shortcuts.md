# Legacy Laptop Shortcuts

This records the old local shortcuts that existed on the Ubuntu laptop before
the version-controlled `simulation/` scripts were added.

## Shell Aliases

From `~/.bashrc`:

```bash
alias drone='~/start_sim.sh'
alias sitl='cd ~/ardupilot/Tools/autotest && python3 sim_vehicle.py -v ArduCopter -f gazebo-iris --model JSON --map --console --out=udp:127.0.0.1:14550 --out=udp:127.0.0.1:14551'
```

## Camera Stream Shortcut

From `~/bin/enable_camera`:

```bash
gz topic -t /world/iris_runway/model/iris_with_gimbal/model/gimbal/link/pitch_link/sensor/camera/image/enable_streaming \
  -m gz.msgs.Boolean \
  -p "data: true"
```

## Why Use The New Scripts Instead

The old shortcuts are personal machine setup. The scripts in `simulation/` are
kept in GitHub, documented, and safer for teammates to run consistently.

