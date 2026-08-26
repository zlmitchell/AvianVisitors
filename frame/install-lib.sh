# Sourced by install.sh - not something to run. It defines two functions and
# returns; there is no installer in here.
#
# It holds the interactive bits, so install.sh stays a straight read: source
# this, call frame_pick_mode, and the answers come back in the same
# MODE / ZIP / IMAGE_URL / EBIRD_KEY variables the flags already set.
#
# Nothing here runs unless install.sh was given no arguments AND has a
# terminal on stdin. `curl ... | bash` has a pipe there, not a terminal, so an
# unattended install is untouched, and so is any run that passes a flag.
# Pressing enter through every question lands on exactly the default install.
#
# Validation stays in install.sh: these answers go through the same checks the
# flags do, so there is only one place that decides what a good ZIP looks like.

# Read one line from the terminal, not from stdin - so this still works if
# install.sh itself was piped something. The two indirections are a test seam:
# smoke_install_lib.sh points them at ordinary files, which is the only way to
# exercise a prompt that deliberately bypasses stdin.
FRAME_TTY_IN=${FRAME_TTY_IN:-/dev/tty}
FRAME_TTY_OUT=${FRAME_TTY_OUT:-/dev/tty}

frame_ask() {
  local prompt="$1" default="${2:-}" answer=""
  if [ -n "$default" ]; then
    printf '%s [%s]: ' "$prompt" "$default" > "$FRAME_TTY_OUT"
  else
    printf '%s: ' "$prompt" > "$FRAME_TTY_OUT"
  fi
  # fd 3, not a fresh open per question: frame_ask runs inside a command
  # substitution, and re-opening a file there would hand every question the
  # same first line back.
  IFS= read -r answer <&3 || answer=""
  printf '%s' "${answer:-$default}"
}

frame_pick_mode() {
  exec 3< "$FRAME_TTY_IN"
  cat > "$FRAME_TTY_OUT" <<'MENU'

How should this frame get its birds?

  1) Mirror the BirdNET-Pi on your network (birdnet.local), rendered here.
     The default, and what you want if you have a BirdNET-Pi listening.
  2) BirdWeather, for a ZIP code. No microphone needed anywhere.
  3) Fetch a ready-made frame PNG from a URL.

MENU
  local choice
  choice="$(frame_ask "Pick 1, 2 or 3" 1)"
  case "$choice" in
    1|"") MODE=local ;;
    2) MODE=birdweather
       ZIP="$(frame_ask 'ZIP or postal code' '')"
       echo "" > "$FRAME_TTY_OUT"
       echo "A free eBird key fills in birds BirdWeather has no station for near" > "$FRAME_TTY_OUT"
       echo "you. Leave it blank unless you are outside a well-covered area." > "$FRAME_TTY_OUT"
       echo "Get one at ebird.org/api/keygen" > "$FRAME_TTY_OUT"
       EBIRD_KEY="$(frame_ask 'eBird API key (optional)' '')"
       ;;
    3) MODE=image
       IMAGE_URL="$(frame_ask 'URL of the frame PNG' '')"
       ;;
    *) echo "Not one of 1, 2 or 3 - carrying on with the default." > "$FRAME_TTY_OUT" ;;
  esac
  echo "" > "$FRAME_TTY_OUT"
  exec 3<&-
}
