#!/usr/bin/env bash
#
# Chartroom offline demo: a complete, repeatable walkthrough of the tool.
#
#   bash demo.sh                                  # throwaway store in $TMPDIR
#   CHARTROOM_STORE=/tmp/cr-demo.json bash demo.sh # explicit store path
#
# No network, no accounts, no interactive input, no install step. The transcript
# is written to demo_output.txt (override with CHARTROOM_DEMO_OUT).

set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON:-python3}"
TRANSCRIPT="${CHARTROOM_DEMO_OUT:-demo_output.txt}"
HOME_DIR="${HOME:-/nonexistent}"
REAL_STORE="$HOME_DIR/.chartroom/store.json"

if [ -n "${CHARTROOM_STORE:-}" ]; then
  STORE="$CHARTROOM_STORE"
else
  STORE="$(mktemp -t chartroom-demo-XXXXXX)"
fi

if [ "$STORE" = "$REAL_STORE" ]; then
  echo "demo.sh: refusing to overwrite the real store at $STORE" >&2
  exit 1
fi

# Start from a clean, empty store so the transcript is identical every run.
rm -f "$STORE"

run() {
  printf '\n$ %s\n' "$*"
  set +e
  "$@"
  status=$?
  set -e
  printf '[exit %s]\n' "$status"
  return 0
}

demo_main() {
  printf 'Chartroom offline demo\n'
  printf 'store: %s (throwaway file - %s is never touched)\n' "$STORE" "$REAL_STORE"
  printf 'network: not used by any command below\n'

  printf '\n== 1. inventory the charts aboard ==\n'
  run "$PYTHON_BIN" chartroom.py charts add --number 1234 --title 'Solent Approach' \
    --region Solent --scale 25000 --store "$STORE"
  run "$PYTHON_BIN" chartroom.py charts add --number 1236 --title 'The Needles' \
    --region 'West Solent' --scale 25000 --store "$STORE"
  run "$PYTHON_BIN" chartroom.py charts add --number BA2450 --title 'Falmouth to Fowey' \
    --region Cornwall --scale 50000 --store "$STORE"
  run "$PYTHON_BIN" chartroom.py charts list --store "$STORE"

  printf '\n== 2. plot a course and read its great-circle legs ==\n'
  run "$PYTHON_BIN" chartroom.py course plot --name 'Solent Training Run' --chart 1234 \
    --waypoint 'Chichester Bar:50.73100,-0.94200' \
    --waypoint 'Nab Tower:50.67250,-0.98250' \
    --waypoint 'Cowes Roads:50.75700,-1.29500' \
    --waypoint 'Lymington:50.72800,-1.53000' --store "$STORE"
  run "$PYTHON_BIN" chartroom.py course plot --name 'Needles Passage' --chart 1236 \
    --waypoint 'Yarmouth:50.70500,-1.50600' \
    --waypoint 'Needles Fairway:50.67580,-1.58100' \
    --waypoint 'Bridge Buoy:50.68200,-1.63000' --store "$STORE"
  run "$PYTHON_BIN" chartroom.py course list --store "$STORE"
  run "$PYTHON_BIN" chartroom.py course show --name 'Solent Training Run' --store "$STORE"

  printf '\n== 3. log observed tide heights ==\n'
  run "$PYTHON_BIN" chartroom.py tide add --station Portsmouth --time 2024-06-01T03:00 \
    --height -0.4 --store "$STORE"
  run "$PYTHON_BIN" chartroom.py tide add --station Portsmouth --time 2024-06-01T09:00 \
    --height 2.4 --store "$STORE"
  run "$PYTHON_BIN" chartroom.py tide add --station Cowes --time 2024-06-01T15:00 \
    --height 3.1 --store "$STORE"
  run "$PYTHON_BIN" chartroom.py tide list --store "$STORE"

  printf '\n== 4. failures print one line and exit 2, saving nothing ==\n'
  run "$PYTHON_BIN" chartroom.py charts add --number 1234 --title 'Duplicate Chart' \
    --store "$STORE"
  run "$PYTHON_BIN" chartroom.py course plot --name 'Off Soundings' \
    --waypoint 'Bell Rock:95.0,-1.0' --waypoint 'End:50.8,-1.6' --store "$STORE"
  run "$PYTHON_BIN" chartroom.py tide remove --id 99 --store "$STORE"

  printf '\n== 5. the command reference ==\n'
  run "$PYTHON_BIN" chartroom.py --help

  printf '\nDemo complete. The charts, courses and tides above are still in %s.\n' "$STORE"
}

set +e
demo_main 2>&1 | tee "$TRANSCRIPT"
demo_status="${PIPESTATUS[0]}"
set -e
exit "$demo_status"
