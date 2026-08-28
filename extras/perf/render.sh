#!/usr/bin/env bash
# One instrumented render inside the container, to a preview file so no panel is
# needed (display.py imports Inky lazily - see push_panel).
#
#   docker compose exec -u bird pi perf-render [name]
#
# Writes /source/extras/perf/out/<name>.jsonl and .png, which land in the host
# checkout through the bind mount so report.py can read them without copying
# anything back out.
set -euo pipefail

NAME=${1:-render-$(date +%H%M%S)}
OUT=${OUT:-/source/extras/perf/out}
FRAME=$HOME/BirdNET-Pi/frame
mkdir -p "$OUT"

cd "$FRAME"
BIRDFRAME_METRICS="$OUT/$NAME.jsonl" \
  .venv/bin/python display.py --config "$HOME/.birdframe/config.toml" \
  --preview "$OUT/$NAME.png" "${@:2}"

echo "wrote $OUT/$NAME.jsonl"
