#!/usr/bin/env bash
# Install the station and the frame inside the perf container. Run as the
# unprivileged user, once, after systemd is up:
#
#   docker compose exec -u bird pi perf-install
#
# newinstaller.sh is deliberately NOT used. It clones from GitHub rather than
# from the checkout under test, refuses to run as root, and ends in `sudo
# reboot` - none of which is wanted here. It is bypassed the same way the
# existing smoke tests bypass it: drive the real installer directly against a
# repository the harness placed.
set -euo pipefail

SOURCE=${SOURCE:-/source}

# The installers are written for a login shell and read $USER and $HOME
# directly - install_birdnet.sh passes both on to install_services.sh, and
# install_config.sh runs `sudo -u $USER tee`. `docker exec` provides neither, so
# an unset $USER turns that into `sudo -u tee`, which fails with the memorable
# "unknown user tee". Set them here rather than patching the installers: this is
# the harness's job to provide, not theirs to defend against.
export USER=${USER:-$(id -un)}
export HOME=${HOME:-$(getent passwd "$USER" | cut -d: -f6)}

DEST=$HOME/BirdNET-Pi

say() { printf '\n=== %s ===\n' "$*"; }

[ -d "$SOURCE/.git" ] || { echo "no repository at $SOURCE" >&2; exit 1; }

# /source is a bind mount of the host checkout, so it is owned by a uid this
# container knows nothing about, and git refuses to touch a repository it thinks
# belongs to someone else. Saying so explicitly is the fix; the mount is
# read-only and disposable, so there is nothing here to protect.
# Both paths: git names the worktree in some checks and the gitdir in others,
# and the message it prints asks for the gitdir.
git config --global --add safe.directory "$SOURCE" || true
git config --global --add safe.directory "$SOURCE/.git" || true

if [ -d "$DEST/.git" ]; then
  say "$DEST already exists, leaving it"
else
  # A clone, not a bind mount. install_birdnet.sh hardcodes my_dir=$HOME/BirdNET-Pi
  # and runs `git log` there, so it has to be a real repository at that path -
  # and the installer writes a venv, a requirements_custom.txt and an install log
  # into it, which has no business landing in the host checkout. --shared keeps
  # the 438MB of illustrations from being copied.
  say "cloning $SOURCE -> $DEST"
  git clone --shared --no-hardlinks "$SOURCE" "$DEST" 2>/dev/null \
    || git clone "$SOURCE" "$DEST"
fi

say "station: scripts/install_birdnet.sh"
cd "$DEST/scripts"
./install_birdnet.sh

say "frame: frame/install.sh"
cd "$DEST/frame"
# --bird-weather would need a ZIP and reach the network; local mode points the
# frame at this container's own station, which is the topology being measured.
./install.sh </dev/null

say "done"
