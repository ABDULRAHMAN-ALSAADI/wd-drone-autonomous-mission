#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$ROOT/real_mission/parameter_config/mission2_target_payload.json"
cd "$ROOT"

python_bin="$ROOT/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
    python_bin="python3"
fi

target="red_triangle"
forwarded_args=()
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --target)
            [[ "$#" -ge 2 ]] || {
                echo "--target requires red_triangle or blue_hexagon" >&2
                exit 2
            }
            target="$2"
            shift 2
            ;;
        *)
            forwarded_args+=("$1")
            shift
            ;;
    esac
done

case "$target" in
    red_triangle|blue_hexagon) ;;
    *)
        echo "--target must be red_triangle or blue_hexagon" >&2
        exit 2
        ;;
esac

read -r connection baud channel release_pwm reset_pwm hold_s < <(
    PYTHONPATH="$ROOT/target_mission_v2" "$python_bin" -c \
        'import json,sys; from payload import payload_output_for_target; c=json.load(open(sys.argv[1])); o=payload_output_for_target(c["payload"],sys.argv[2]); print(c["mavlink"]["connection"],c["mavlink"]["baud"],o["servo_channel"],o["release_pwm"],o["reset_pwm"],c["payload"]["release_hold_s"])' \
        "$CONFIG" "$target"
)

echo "[PAYLOAD PROFILE] target=$target release=$release_pwm hold=${hold_s}s reset=$reset_pwm"

exec ./scripts/mavlink_bench.sh servo \
    --connection "$connection" \
    --baud "$baud" \
    --channel "$channel" \
    --pwm "$release_pwm" \
    --reset-pwm "$reset_pwm" \
    --hold "$hold_s" \
    "${forwarded_args[@]}"
