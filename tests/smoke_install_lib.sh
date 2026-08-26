#!/usr/bin/env bash
# The installer's question flow, and the promise that it stays out of the way.
#
# frame_pick_mode deliberately reads the terminal rather than stdin, so that an
# install piped something still asks a person; FRAME_TTY_IN/OUT are the seam
# that lets that be checked here. What is being pinned down is mostly the
# not-asking: pressing enter, or being piped, has to land on exactly the
# install people already had.
set -euo pipefail

FRAME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../frame" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAILED=0

check() {
  local label="$1" want="$2" got="$3"
  if [ "$want" = "$got" ]; then
    echo "ok   $label"
  else
    echo "FAIL $label: wanted '$want', got '$got'"
    FAILED=1
  fi
}

# Answer the questions from a file and throw the prompts away.
pick() {
  printf '%s\n' "$@" > "$WORK/answers"
  MODE=local ZIP="" IMAGE_URL="" EBIRD_KEY=""
  FRAME_TTY_IN="$WORK/answers" FRAME_TTY_OUT=/dev/null
  # shellcheck disable=SC1091
  . "$FRAME/install-lib.sh"
  frame_pick_mode
}

# 1 - the default install, which is the one that must not change.
pick 1
check "choice 1 is local mode" "local" "$MODE"

# Enter through the first question means the same thing. Anyone who has run
# this before expects a bare ./install.sh to still be the BirdNET-Pi mirror.
pick ""
check "empty answer is local mode" "local" "$MODE"

# A fat-fingered answer is not a reason to stop an install.
pick "banana"
check "unknown answer falls back to local" "local" "$MODE"

# 2 - BirdWeather, with and without the optional key.
pick 2 "94107" "abc123"
check "choice 2 is birdweather" "birdweather" "$MODE"
check "the zip is carried out" "94107" "$ZIP"
check "the ebird key is carried out" "abc123" "$EBIRD_KEY"

pick 2 "SW1A 1AA" ""
check "a postal code with a space survives" "SW1A 1AA" "$ZIP"
check "the ebird key stays empty" "" "$EBIRD_KEY"

# 3 - a ready-made PNG.
pick 3 "https://bird.example/frame.png"
check "choice 3 is image mode" "image" "$MODE"
check "the url is carried out" "https://bird.example/frame.png" "$IMAGE_URL"

# The values the questions produce have to survive install.sh's own checks -
# there is one definition of a good ZIP and it is not in the question file.
zip_ok() { printf '%s' "$1" | LC_ALL=C grep -qE '^[A-Za-z0-9][A-Za-z0-9 -]{1,9}$'; }
if zip_ok "94107" && zip_ok "SW1A 1AA"; then
  echo "ok   answered zips pass install.sh's own check"
else
  echo "FAIL answered zips pass install.sh's own check"
  FAILED=1
fi

# And the guard itself: piped, install.sh must never reach the questions.
GUARD='if [ "$GIVEN_ARGS" -eq 0 ] && [ -t 0 ] && [ -r /dev/tty ]; then'
if grep -qF "$GUARD" "$FRAME/install.sh"; then
  echo "ok   install.sh only asks with no flags and a terminal"
else
  echo "FAIL install.sh's guard on the questions has moved"
  FAILED=1
fi

# curl | bash: stdin is a pipe, so the guard is false and nothing is asked.
if printf '' | bash -c '[ -t 0 ]'; then
  echo "FAIL a pipe on stdin looks like a terminal here"
  FAILED=1
else
  echo "ok   a piped install is not a terminal, so it is never asked"
fi

exit "$FAILED"
