#!/usr/bin/env bash
# Copy the working tree's frame and frontend into the installed clone.
#
#   docker compose exec -u bird pi perf-sync
#
# The clone at ~/BirdNET-Pi is what actually runs - it has the venv, the config
# and the systemd units - while /source is the host checkout, read-only and
# possibly uncommitted. Testing a change means getting it from one to the other,
# and re-running perf-install for a one-line edit would take half an hour.
#
# Only the files a render reads: the frame's python, and the collage the station
# serves. Deliberately not the venv, not the config, not birds.db - those are
# the container's state and re-copying them would throw away the install and the
# seeded detections that make two runs comparable.
set -euo pipefail

SOURCE=${SOURCE:-/source}
DEST=${DEST:-$HOME/BirdNET-Pi}

for rel in frame avian/frontend; do
  [ -d "$SOURCE/$rel" ] || { echo "missing $SOURCE/$rel" >&2; exit 1; }
  # -a without -delete: a stray file in the clone is harmless, but deleting the
  # venv because /source has no .venv would not be.
  cp -a "$SOURCE/$rel/." "$DEST/$rel/"
done

# The station serves its own copy of the collage under the webroot; the frame
# screenshots that, not the one in frame/. install.sh's step 5/6 does this at
# install time - do it again here so a frontend edit actually reaches the page.
if [ -d "$DEST/avian/frontend" ]; then
  "$DEST/frame/.venv/bin/python" "$DEST/frame/shoot.py" \
    --check-frontend "$DEST/avian/frontend/apt.js" \
    || echo "warning: the synced collage is missing tunables the frame rewrites" >&2
fi

echo "synced frame/ and avian/frontend/ into $DEST"
