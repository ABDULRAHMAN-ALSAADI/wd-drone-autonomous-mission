# System Architecture

```mermaid
flowchart TD
    GCS[Mission Planner or QGroundControl]
    RFD[RFD900x telemetry]
    CUBE[Cube Orange Plus]
    PI[Raspberry Pi 5]
    HAILO[AI HAT+]
    CAM[Camera Module 3 Standard]
    PAYLOAD[Payload servos]
    GPS[HERE3+ GNSS]

    GCS <--> RFD
    RFD <--> CUBE
    GPS --> CUBE
    CUBE <--> PI
    CAM --> PI
    HAILO <--> PI
    CUBE --> PAYLOAD
```

## Responsibility boundary

### Cube Orange Plus

- Flight stabilization
- EKF and navigation
- AUTO waypoint execution
- GUIDED position or velocity setpoints
- Flight-mode and radio failsafes
- Payload PWM output

### Raspberry Pi 5

- Mission state machine
- Target detection and tracking
- Target-centering calculations
- Correct payload selection
- Annotated video generation
- Mission event logging

The Raspberry Pi must never send raw motor commands.
