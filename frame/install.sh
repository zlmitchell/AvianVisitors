#!/usr/bin/env bash
# Install the AvianVisitors e-ink frame (display side) on a Raspberry Pi.
# Enables SPI + I2C, installs deps, makes a venv, installs the systemd timer.
#
# Three ways to feed the frame, pick one:
#   ./install.sh                            mirror the BirdNET-Pi on your network
#                                           (birdnet.local), rendered on this Pi
#   ./install.sh --image-url <URL>          fetch a ready-made frame PNG instead
#                                           (e.g. a public Cloudflare Worker)
#   ./install.sh --bird-weather --zip <ZIP> standalone from BirdWeather, no mic
#                                           (add --ebird-key <KEY> for remote ZIPs)
set -euo pipefail
cd "$(dirname "$0")"
FRAME="$(pwd)"
REPO="$(dirname "$FRAME")"   # the checkout this frame was installed from

MODE=local            # local | image | birdweather
ZIP=""
IMAGE_URL=""
EBIRD_KEY=""
GIVEN_ARGS=$#
while [ $# -gt 0 ]; do
  case "$1" in
    --bird-weather) MODE=birdweather; shift ;;
    --zip) [ $# -ge 2 ] || { echo "--zip needs a value, e.g. --zip 94107" >&2; exit 1; }
           ZIP="$2"; shift 2 ;;
    --zip=*) ZIP="${1#*=}"; shift ;;
    --image-url) [ $# -ge 2 ] || { echo "--image-url needs a URL, e.g. --image-url https://bird.example/frame.png" >&2; exit 1; }
                 MODE=image; IMAGE_URL="$2"; shift 2 ;;
    --image-url=*) MODE=image; IMAGE_URL="${1#*=}"; shift ;;
    --ebird-key) [ $# -ge 2 ] || { echo "--ebird-key needs a value (a free key from ebird.org/api/keygen)" >&2; exit 1; }
                 EBIRD_KEY="$2"; shift 2 ;;
    --ebird-key=*) EBIRD_KEY="${1#*=}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Given no flags at a terminal, ask instead of assuming. Piped (curl | bash)
# or given any flag, this is a no-op and the old behaviour stands; the answers
# come back in the same variables and go through the same checks below.
if [ "$GIVEN_ARGS" -eq 0 ] && [ -t 0 ] && [ -r /dev/tty ]; then
  . "$FRAME/install-lib.sh"
  frame_pick_mode
fi

if [ -n "$ZIP" ] && [ "$MODE" != birdweather ]; then
  echo "--zip only applies with --bird-weather" >&2
  exit 1
fi
if [ -n "$EBIRD_KEY" ] && [ "$MODE" != birdweather ]; then
  echo "--ebird-key only applies with --bird-weather" >&2
  exit 1
fi

# Validate inputs up front: a bad value would otherwise land in a config file or
# a systemd unit verbatim. These checks also reject a flag passed as a value
# (e.g. "--zip --image-url"), which would fail the format below.
if [ "$MODE" = birdweather ]; then
  if [ -z "$ZIP" ]; then
    echo "--bird-weather needs --zip <ZIP code>, e.g. install.sh --bird-weather --zip 94107" >&2
    exit 1
  fi
  if ! printf '%s' "$ZIP" | LC_ALL=C grep -qE '^[A-Za-z0-9][A-Za-z0-9 -]{1,9}$'; then
    echo "--zip should look like a postal code, e.g. 94107 or SW1A 1AA" >&2
    exit 1
  fi
  if [ -n "$EBIRD_KEY" ] && ! printf '%s' "$EBIRD_KEY" | LC_ALL=C grep -qE '^[A-Za-z0-9]+$'; then
    echo "--ebird-key should be the alphanumeric token from ebird.org/api/keygen" >&2
    exit 1
  fi
fi
if [ "$MODE" = image ]; then
  if [ -z "$IMAGE_URL" ]; then
    echo "--image-url needs a URL, e.g. install.sh --image-url https://bird.example/frame.png" >&2
    exit 1
  fi
  case "$IMAGE_URL" in
    http://*|https://*) ;;
    *) echo "--image-url must start with http:// or https://" >&2; exit 1 ;;
  esac
  if printf '%s' "$IMAGE_URL" | LC_ALL=C grep -q '[^A-Za-z0-9._~:/?#@!$&()*+,;=%-]'; then
    echo "--image-url has characters that are not allowed in a URL" >&2
    exit 1
  fi
fi

# local + birdweather render on the Pi (need a browser); image only fetches.
NEEDS_BROWSER=1
if [ "$MODE" = image ]; then NEEDS_BROWSER=0; fi

CONFIG_TXT=/boot/firmware/config.txt
[ -f "$CONFIG_TXT" ] || CONFIG_TXT=/boot/config.txt

# Every step below is a no-op on a re-run if what it installs is already there.
# A fresh install is unchanged; a re-run (or an upgrade) skips straight to the
# config and the render instead of spending minutes re-checking apt and pip.

echo "1/6  Enabling SPI + I2C (Inky needs both; SPI with no chip-select)..."
if [ "$(sudo raspi-config nonint get_spi 2>/dev/null || echo 1)" = 0 ] \
   && [ "$(sudo raspi-config nonint get_i2c 2>/dev/null || echo 1)" = 0 ]; then
  echo "     SPI + I2C already enabled."
else
  sudo raspi-config nonint do_spi 0
  sudo raspi-config nonint do_i2c 0
fi
grep -q "^dtoverlay=spi0-0cs" "$CONFIG_TXT" || echo "dtoverlay=spi0-0cs" | sudo tee -a "$CONFIG_TXT" >/dev/null

echo "2/6  Installing system packages (build tools to compile spidev, libatlas3-base for numpy)..."
APT_PKGS="python3-venv python3-dev build-essential libatlas3-base"
MISSING_PKGS=""
for pkg in $APT_PKGS; do
  dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed" \
    || MISSING_PKGS="$MISSING_PKGS $pkg"
done
if [ -z "$MISSING_PKGS" ]; then
  echo "     All present, skipping apt."
else
  # Only refresh the package lists when there is actually something to fetch.
  sudo apt-get update -qq
  sudo apt-get install -y $MISSING_PKGS
fi

echo "3/6  Creating venv and installing Python deps..."
if [ -x .venv/bin/pip ]; then
  echo "     venv already exists, reusing it."
else
  python3 -m venv .venv
fi
# Stamp the requirements hash into the venv: pip re-resolving an already-satisfied
# requirements file costs ~30s on a Zero 2 W and changes nothing.
REQ_STAMP=".venv/.requirements-frame.sha"
REQ_SHA="$(sha256sum requirements-frame.txt | cut -d' ' -f1)"
if [ "$(cat "$REQ_STAMP" 2>/dev/null || true)" = "$REQ_SHA" ]; then
  echo "     Python deps already match requirements-frame.txt, skipping."
else
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements-frame.txt
  printf '%s\n' "$REQ_SHA" > "$REQ_STAMP"
fi
if [ "$NEEDS_BROWSER" = 1 ]; then
  # Chromium unpacks to ~/.cache/ms-playwright/chromium-<build>; if a build is
  # there and the module imports, the download and the apt dep pass are both done.
  HAVE_CHROMIUM=0
  for d in "$HOME"/.cache/ms-playwright/chromium-*; do
    if [ -d "$d" ]; then HAVE_CHROMIUM=1; break; fi
  done
  if [ "$HAVE_CHROMIUM" = 1 ] && .venv/bin/python -c 'import playwright' 2>/dev/null; then
    echo "     Playwright + Chromium already installed, skipping."
  else
    echo "     Installing Playwright + Chromium so the Pi can render the collage (a few minutes)..."
    .venv/bin/pip install -q playwright
    sudo .venv/bin/playwright install-deps chromium
    .venv/bin/playwright install chromium
  fi
fi

echo "4/6  Writing config..."
CONFIG="$HOME/.birdframe/config.toml"
if [ -f "$CONFIG" ]; then
  EXISTING="$(sed -n 's/^# birdframe-mode: //p' "$CONFIG" | head -1)"
  if [ -n "$EXISTING" ] && [ "$EXISTING" != "$MODE" ]; then
    echo "     $CONFIG is set up for '$EXISTING' mode, not '$MODE'." >&2
    echo "     Run 'birdframe' to switch it over, or remove it and re-run:" >&2
    echo "       rm $CONFIG" >&2
    exit 1
  fi
  echo "     $CONFIG already exists, leaving it untouched."
else
  # config.example.toml is the one description of every setting the frame has.
  # This writes it out with the mode's values already set, rather than keeping
  # a second, shorter, drifting copy of the same defaults in this file. --zip
  # and --image-url are ignored by the modes that do not use them.
  .venv/bin/python config_tui.py --config "$CONFIG" --init "$MODE" --zip "$ZIP" --image-url "$IMAGE_URL"
fi

sudo ln -sfn "$FRAME/birdframe-names" /usr/local/bin/birdframe-names
sudo ln -sfn "$FRAME/birdframe" /usr/local/bin/birdframe

echo "5/6  Checking the station's collage..."
# Only local mode screenshots someone else's website; the other two render here
# or fetch a finished PNG, and neither cares what any station serves.
#
# A station install and a frame install are two clones of this repository, and
# only one of them is this one: newinstaller.sh clones the station from upstream
# into ~/BirdNET-Pi, while the frame is installed from wherever you cloned it.
# So a frame built against a newer collage than its station serves is the
# DEFAULT arrangement here, not an edge case - and the frame then screenshots a
# page carrying none of what it is about to ask for. shoot.py refuses that
# outright rather than ship a half-tuned plate, which is right, but it finds out
# at render time: on a Pi, after launching a browser, once every 15 minutes,
# for as long as nobody reads the log.
#
# Ask the question here instead, where the answer costs a line of output, and if
# the station is behind hand it the collage this frame was built against. Two
# files, both pure frontend - the collage renderer and its stylesheet.
#
# What makes copying into another checkout safe to automate is that none of it
# is assumed. The copy happens only when the station is actually missing a
# tunable, what it replaces is backed up, and the result is checked against the
# same list shoot.py rewrites at capture time. If that list ever outgrows these
# two files, this fails loudly with the station put back as it was, instead of
# leaving a frontend half-patched.
STATION="${BIRDNET_PI_DIR:-$HOME/BirdNET-Pi}"
STATION_JS="$STATION/avian/frontend/apt.js"
COLLAGE_FILES="avian/frontend/apt.js avian/frontend/styles.css"
if [ "$MODE" != local ]; then
  echo "     nothing to check in $MODE mode."
elif [ ! -f "$STATION_JS" ]; then
  echo "     no station checkout here ($STATION); assuming the frame mirrors one on the network."
elif .venv/bin/python shoot.py --check-frontend "$STATION_JS" 2>/dev/null; then
  echo "     $STATION already serves a collage this frame can drive."
else
  STAMP="$(date +%s)"
  echo "     $STATION serves an older collage than this frame needs; updating it."
  for f in $COLLAGE_FILES; do
    [ -f "$STATION/$f" ] && cp -p "$STATION/$f" "$STATION/$f.bak-$STAMP"
    cp "$REPO/$f" "$STATION/$f"
  done
  if .venv/bin/python shoot.py --check-frontend "$STATION_JS"; then
    echo "     updated (replaced files backed up alongside as *.bak-$STAMP)."
    echo "     Note: a station update re-clones from upstream and undoes this."
    echo "     Re-run this installer afterwards, or merge the frame branch upstream."
  else
    echo "     still missing tunables after the copy - putting $STATION back." >&2
    for f in $COLLAGE_FILES; do
      [ -f "$STATION/$f.bak-$STAMP" ] && mv "$STATION/$f.bak-$STAMP" "$STATION/$f"
    done
    echo "     The frame needs collage files this step does not know to copy." >&2
    exit 1
  fi
fi

echo "6/6  Installing systemd service + timer..."
# Every mode runs display.py against the config on the standard 15-minute timer;
# only the config differs. display.py renders inline for local + birdweather and
# pushes to the panel only when the birds change.
sed "s|/home/monalisa/AvianVisitors/frame|$FRAME|g; s|/home/monalisa|$HOME|g; s|User=monalisa|User=$USER|" \
  systemd/birdframe.service | sudo tee /etc/systemd/system/birdframe.service >/dev/null
# BirdWeather's remote-ZIP eBird fallback reads its key from the unit environment.
if [ "$MODE" = birdweather ] && [ -n "$EBIRD_KEY" ]; then
  echo "Environment=EBIRD_API_KEY=$EBIRD_KEY" | sudo tee -a /etc/systemd/system/birdframe.service >/dev/null
fi
sudo cp systemd/birdframe.timer /etc/systemd/system/birdframe.timer
sudo systemctl daemon-reload
sudo systemctl enable birdframe.timer
# restart, not `enable --now`: on a re-run the timer is already active and
# --now leaves a running unit alone, so a changed unit file would not take
# effect until the next boot. Restarting also resets OnActiveSec, so the first
# scheduled render is 2 min from now rather than whenever the old timer was due.
sudo systemctl restart birdframe.timer

case "$MODE" in
  local)
    cat <<DONE

Installed. The frame mirrors birdnet.local on your network and refreshes every
15 min, only when the birds change. Until the mic has heard its first bird it
shows a plain title card.
DONE
    ;;
  image)
    cat <<DONE

Installed. The frame fetches its image from
  $IMAGE_URL
and refreshes every 15 min, only when the birds change.
DONE
    ;;
  birdweather)
    cat <<DONE

Installed in BirdWeather mode for ZIP $ZIP. The frame renders the top birds near
you on the Pi and refreshes every 15 min, only when the local top birds change.
DONE
    # The bundled illustrations center on the western U.S. If birds near this ZIP
    # aren't in the cloned set the frame quietly skips them, which has tripped
    # people up - surface it here and point at the generator.
    MISSING="$("$FRAME/.venv/bin/python" "$FRAME/birdweather.py" "$ZIP" --missing 2>/dev/null || true)"
    if [ -n "$MISSING" ]; then
      N="$(printf '%s\n' "$MISSING" | grep -c . || true)"
      NAMES="$(printf '%s\n' "$MISSING" | head -8 | sed 's/.*|/    /')"
      if [ "$N" -gt 8 ]; then NAMES="$NAMES
    ... and $((N - 8)) more"; fi
      cat <<FLAG
Heads up: $N local bird(s) near you aren't in the illustration set you cloned, so
the frame will skip them:
$NAMES
To add them, run this on a laptop or workstation (it needs rembg, which the Pi
can't fit) and commit or copy the new cutouts over:
  python3 $FRAME/generate_illustrations.py --zip $ZIP --gemini-key <KEY>
A paid Google Gemini API key is needed: https://ai.google.dev
FLAG
    fi
    ;;
esac

cat <<'SETTINGS'
To change anything - the title, the names, which way up it hangs, or the whole
layout if you take the matboard out - run:
  birdframe
SETTINGS

# SPI only takes effect on a reboot, so do it for the user. Skip if SPI is
# already up (e.g. a re-run) so we don't bounce a working frame.
if [ -e /dev/spidev0.0 ]; then
  echo "SPI already active, no reboot needed."
  # Draw the panel now. An upgrade changes how the frame renders but not what
  # the birds are doing, so the change signature is identical and the timer
  # would decide there is nothing to do - leaving the previous picture up until
  # a new species turns up or the daily heal fires, which looks exactly like the
  # install having failed. --force is the only thing that shows this build's work.
  echo "Drawing the panel now (a Zero 2 W takes 1-2 min)..."
  if ! "$FRAME/.venv/bin/python" "$FRAME/display.py" --config "$CONFIG" --force; then
    echo "     That render did not finish. The timer retries within 15 min;" >&2
    echo "     'journalctl -u birdframe -n 50' has the reason." >&2
  fi
else
  echo "Rebooting to bring SPI up (back on its own in ~1 min)..."
  sleep 4
  sudo reboot
fi
