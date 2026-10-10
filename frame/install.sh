#!/bin/bash -p
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
#   ./install.sh --station-id <ID>          follow one public BirdWeather station
set -euo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LC_ALL=C
export PATH LC_ALL

install_fail() {
  printf 'install.sh: %s\n' "$1" >&2
  exit 1
}

OWNER_TEMP=""
ENVIRONMENT_TEMP=""
EBIRD_KEY_FILE=""
cleanup_private_temps() {
  if [ -n "$OWNER_TEMP" ]; then
    /bin/rm -f -- "$OWNER_TEMP"
  fi
  if [ -n "$ENVIRONMENT_TEMP" ]; then
    /bin/rm -f -- "$ENVIRONMENT_TEMP"
  fi
  if [ -n "$EBIRD_KEY_FILE" ]; then
    /bin/rm -f -- "$EBIRD_KEY_FILE"
  fi
}

restart_without_ebird_key_in_argv() {
  exec /bin/bash -p "$FRAME/${0##*/}" \
    "${SANITIZED_ARGS[@]}" --internal-ebird-key-file "$EBIRD_KEY_FILE"
}

main() {
# Bash parses this complete function before executing it. Keeping every later
# privileged step in the parsed command tree prevents same-UID dependency code
# from rewriting unread installer bytes after pip or Playwright returns.
INSTALL_UID=$(/usr/bin/id -u)
if [ "$INSTALL_UID" = 0 ]; then
  install_fail "run install.sh as a regular user, without sudo"
fi
INSTALL_USER=$(/usr/bin/id -un) || install_fail "could not resolve the current user"
ACCOUNT=$(/usr/bin/getent passwd "$INSTALL_USER") \
  || install_fail "could not resolve the current user's home directory"
IFS=: read -r ACCOUNT_USER _ ACCOUNT_UID _ _ ACCOUNT_HOME _ <<< "$ACCOUNT"
if [ "$ACCOUNT_USER" != "$INSTALL_USER" ] || [ "$ACCOUNT_UID" != "$INSTALL_UID" ]; then
  install_fail "the current account identity is inconsistent"
fi
case "$ACCOUNT_HOME" in
  /*) ;;
  *) install_fail "the current user's home directory is unsafe" ;;
esac
INSTALL_HOME=$(CDPATH='' cd -P -- "$ACCOUNT_HOME" && pwd -P) \
  || install_fail "the current user's home directory is unavailable"
case "$INSTALL_USER" in
  ''|*[!A-Za-z0-9_.-]*)
    install_fail "the current user name contains unsupported characters"
    ;;
esac
case "$INSTALL_HOME" in
  *[!A-Za-z0-9_./-]*)
    install_fail "the current user's home path contains unsupported characters"
    ;;
esac
HOME=$INSTALL_HOME
USER=$INSTALL_USER
LOGNAME=$INSTALL_USER
export HOME USER LOGNAME

cd -P -- "$(/usr/bin/dirname -- "$0")"
FRAME=$(pwd -P)
REPO=$(/usr/bin/dirname -- "$FRAME")   # the checkout this frame was installed from
case "$FRAME" in
  *[!A-Za-z0-9_./-]*)
    install_fail "the checkout path contains unsupported characters"
    ;;
esac

# No UID other than root or the invoking user may be able to replace a checkout
# path component while sudo is prompting. Root-owned sticky directories such as
# /tmp are safe because their sticky bit prevents cross-UID replacement.
CHECKOUT_COMPONENT=$FRAME
while :; do
  if [ ! -d "$CHECKOUT_COMPONENT" ] || [ -L "$CHECKOUT_COMPONENT" ]; then
    install_fail "the checkout path contains an unsafe directory"
  fi
  CHECKOUT_METADATA=$(/usr/bin/stat -c '%u:%a' -- "$CHECKOUT_COMPONENT") \
    || install_fail "could not inspect the checkout path"
  CHECKOUT_OWNER=${CHECKOUT_METADATA%%:*}
  CHECKOUT_MODE=${CHECKOUT_METADATA#*:}
  if [ "$CHECKOUT_OWNER" != 0 ] && [ "$CHECKOUT_OWNER" != "$INSTALL_UID" ]; then
    install_fail "the checkout path has unsafe ownership"
  fi
  CHECKOUT_PERMISSIONS=${CHECKOUT_MODE: -3}
  CHECKOUT_GROUP_MODE=${CHECKOUT_PERMISSIONS:1:1}
  CHECKOUT_OTHER_MODE=${CHECKOUT_PERMISSIONS:2:1}
  CHECKOUT_WRITABLE=0
  case "$CHECKOUT_GROUP_MODE" in
    2|3|6|7) CHECKOUT_WRITABLE=1 ;;
  esac
  case "$CHECKOUT_OTHER_MODE" in
    2|3|6|7) CHECKOUT_WRITABLE=1 ;;
  esac
  if [ "$CHECKOUT_WRITABLE" = 1 ]; then
    case "$CHECKOUT_OWNER:$CHECKOUT_MODE" in
      0:[1357][0-7][0-7][0-7]) ;;
      *) install_fail "the checkout path has unsafe permissions" ;;
    esac
  fi
  if [ "$CHECKOUT_COMPONENT" = / ]; then
    break
  fi
  CHECKOUT_COMPONENT=${CHECKOUT_COMPONENT%/*}
  if [ -z "$CHECKOUT_COMPONENT" ]; then
    CHECKOUT_COMPONENT=/
  fi
done
FRAME_OWNER=$(/usr/bin/stat -c '%u' -- "$FRAME") \
  || install_fail "could not inspect the checkout directory"
if [ "$FRAME_OWNER" != "$INSTALL_UID" ]; then
  install_fail "the checkout directory is not owned by $INSTALL_USER"
fi

preflight_directory() {
  local path=$1
  local metadata
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return
  fi
  if [ ! -d "$path" ] || [ -L "$path" ]; then
    install_fail "$path exists but is not a managed directory"
  fi
  metadata=$(/usr/bin/stat -c '%u:%g:%a' -- "$path") \
    || install_fail "could not inspect $path"
  [ "$metadata" = 0:0:755 ] \
    || install_fail "$path has unsafe ownership or permissions"
}

preflight_managed_file() {
  local path=$1
  local expected_mode=$2
  local metadata
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return
  fi
  if [ ! -f "$path" ] || [ -L "$path" ]; then
    install_fail "$path exists but is not a managed file"
  fi
  metadata=$(/usr/bin/stat -c '%u:%g:%a' -- "$path") \
    || install_fail "could not inspect $path"
  [ "$metadata" = "0:0:$expected_mode" ] \
    || install_fail "$path has unsafe ownership or permissions"
}

preflight_managed_link() {
  local destination=$1
  local existing
  local allowed
  shift
  if [ ! -e "$destination" ] && [ ! -L "$destination" ]; then
    return
  fi
  if [ ! -L "$destination" ]; then
    install_fail "$destination exists and was not installed by BirdFrame"
  fi
  existing=$(/usr/bin/readlink -- "$destination") \
    || install_fail "could not inspect $destination"
  for allowed in "$@"; do
    if [ "$existing" = "$allowed" ]; then
      return
    fi
  done
  install_fail "$destination points somewhere not managed by BirdFrame"
}

MODE=local            # local | image | birdweather
ZIP=""
STATION_ID=""
IMAGE_URL=""
EBIRD_KEY=""
GIVEN_ARGS=$#
EBIRD_KEY_FILE=""
EBIRD_KEY_SOURCE=none
SANITIZED_ARGS=()
PLAYWRIGHT_VERSION=1.62.0
while [ $# -gt 0 ]; do
  case "$1" in
    --bird-weather)
      BIRD_WEATHER=1
      MODE=birdweather
      SANITIZED_ARGS+=(--bird-weather)
      shift
      ;;
    --zip) [ $# -ge 2 ] || { echo "--zip needs a value, e.g. --zip 94107" >&2; exit 1; }
           ZIP="$2"; SANITIZED_ARGS+=(--zip "$2"); shift 2 ;;
    --zip=*) ZIP="${1#*=}"; SANITIZED_ARGS+=(--zip "$ZIP"); shift ;;
    --station-id) [ $# -ge 2 ] || { echo "--station-id needs a value, e.g. --station-id 12345" >&2; exit 1; }
                  MODE=birdweather; STATION_ID="$2"; SANITIZED_ARGS+=(--station-id "$2"); shift 2 ;;
    --station-id=*) MODE=birdweather; STATION_ID="${1#*=}"; SANITIZED_ARGS+=(--station-id "$STATION_ID"); shift ;;
    --image-url) [ $# -ge 2 ] || { echo "--image-url needs a URL, e.g. --image-url https://bird.example/frame.png" >&2; exit 1; }
                 MODE=image; IMAGE_URL="$2"; SANITIZED_ARGS+=(--image-url "$2"); shift 2 ;;
    --image-url=*) MODE=image; IMAGE_URL="${1#*=}"; SANITIZED_ARGS+=(--image-url "$IMAGE_URL"); shift ;;
    --ebird-key) [ $# -ge 2 ] || { echo "--ebird-key needs a value (a free key from ebird.org/api/keygen)" >&2; exit 1; }
                 [ "$EBIRD_KEY_SOURCE" = none ] || install_fail "--ebird-key may only be supplied once"
                 EBIRD_KEY="$2"; EBIRD_KEY_SOURCE=argument; shift 2 ;;
    --ebird-key=*) [ "$EBIRD_KEY_SOURCE" = none ] || install_fail "--ebird-key may only be supplied once"
                   EBIRD_KEY="${1#*=}"; EBIRD_KEY_SOURCE=argument; shift ;;
    --internal-ebird-key-file)
      [ $# -ge 2 ] || install_fail "invalid private eBird key handoff"
      [ "$EBIRD_KEY_SOURCE" = none ] || install_fail "--ebird-key may only be supplied once"
      EBIRD_KEY_FILE=$2
      EBIRD_KEY_SOURCE="file"
      shift 2
      ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [ "$EBIRD_KEY_SOURCE" = file ]; then
  case "$EBIRD_KEY_FILE" in
    /tmp/avian-birdframe-ebird.*) ;;
    *) install_fail "invalid private eBird key handoff" ;;
  esac
  if [ ! -f "$EBIRD_KEY_FILE" ] || [ -L "$EBIRD_KEY_FILE" ]; then
    install_fail "invalid private eBird key handoff"
  fi
  EBIRD_KEY_FILE_METADATA=$(/usr/bin/stat -c '%u:%a' -- "$EBIRD_KEY_FILE") \
    || install_fail "invalid private eBird key handoff"
  if [ "$EBIRD_KEY_FILE_METADATA" != "$INSTALL_UID:600" ]; then
    install_fail "invalid private eBird key handoff"
  fi
  EBIRD_KEY=$(<"$EBIRD_KEY_FILE")
  /bin/rm -f -- "$EBIRD_KEY_FILE"
  EBIRD_KEY_FILE=""
fi

# Given no flags at a terminal, ask instead of assuming. Piped (curl | bash)
# or given any flag, this is a no-op and the old behaviour stands; the answers
# come back in the same variables and go through the same checks below.
if [ "$GIVEN_ARGS" -eq 0 ] && [ -t 0 ] && [ -r /dev/tty ]; then
  . "$FRAME/install-lib.sh"
  frame_pick_mode
  if [ "$MODE" = birdweather ]; then BIRD_WEATHER=1; fi
fi

if [ -n "$IMAGE_URL" ] && { [ "$BIRD_WEATHER" = 1 ] || [ -n "$ZIP" ] || [ -n "$STATION_ID" ] || [ -n "$EBIRD_KEY" ]; }; then
  echo "--image-url cannot be combined with BirdWeather options" >&2
  exit 1
fi
if [ -n "$ZIP" ] && [ "$MODE" != birdweather ]; then
  echo "--zip only applies with --bird-weather" >&2
  exit 1
fi
if [ -n "$EBIRD_KEY" ] && [ "$MODE" != birdweather ]; then
  echo "--ebird-key only applies with --bird-weather" >&2
  exit 1
fi
if [ -n "$ZIP" ] && [ -n "$STATION_ID" ]; then
  echo "use either --zip or --station-id, not both" >&2
  exit 1
fi
if [ -n "$STATION_ID" ] && [ -n "$EBIRD_KEY" ]; then
  echo "--ebird-key only applies to BirdWeather ZIP mode" >&2
  exit 1
fi

# Validate inputs up front: a bad value would otherwise land in a config file or
# a systemd unit verbatim. These checks also reject a flag passed as a value
# (e.g. "--zip --image-url"), which would fail the format below.
if [ "$MODE" = birdweather ]; then
  if [ -z "$ZIP" ] && [ -z "$STATION_ID" ]; then
    echo "BirdWeather mode needs --zip <ZIP code> or --station-id <ID>" >&2
    exit 1
  fi
  if [ -n "$ZIP" ] && ! printf '%s' "$ZIP" | LC_ALL=C grep -qE '^[A-Za-z0-9][A-Za-z0-9 -]{0,8}[A-Za-z0-9]$'; then
    echo "--zip should look like a postal code, e.g. 94107 or SW1A 1AA" >&2
    exit 1
  fi
  if [ -n "$STATION_ID" ]; then
    if ! printf '%s' "$STATION_ID" | LC_ALL=C grep -qE '^[1-9][0-9]{0,9}$' \
        || [ "$STATION_ID" -gt 2147483647 ]; then
      echo "--station-id must be a number from 1 through 2147483647" >&2
      exit 1
    fi
  fi
  if [ "$EBIRD_KEY_SOURCE" != none ]; then
    case "$EBIRD_KEY" in
      ''|*[!A-Za-z0-9]*)
        install_fail "--ebird-key should be the alphanumeric token from ebird.org/api/keygen" ;;
    esac
    [ "${#EBIRD_KEY}" -le 4095 ] || install_fail "--ebird-key is too long"
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

if [ "$EBIRD_KEY_SOURCE" = argument ]; then
  EBIRD_KEY_FILE=$(/usr/bin/mktemp /tmp/avian-birdframe-ebird.XXXXXX) \
    || install_fail "could not create the private eBird key handoff"
  trap cleanup_private_temps EXIT
  printf '%s\n' "$EBIRD_KEY" > "$EBIRD_KEY_FILE"
  /bin/chmod 0600 "$EBIRD_KEY_FILE"
  restart_without_ebird_key_in_argv
fi

# An existing config is left alone, so before anything on the host changes it
# has to still select the source this run asked for - a re-run must not claim
# a different station or ZIP was installed while preserving an older file.
# config_contract.py is a semantic TOML check rather than a grep for the mode
# marker: it accepts a local config that fetches its picture from a render
# server (shoot = false, image_url set), which the marker alone cannot tell
# from a broken one.
CONFIG="$HOME/.birdframe/config.toml"
CONFIG_EXISTS=0
verify_existing_config() {
  local command_prefix=("$@")
  local config_python=""
  local config_args=("$CONFIG" --mode "$MODE")
  CONFIG_EXISTS=0
  if [ ! -f "$CONFIG" ]; then
    return
  fi
  CONFIG_EXISTS=1
  if "${command_prefix[@]}" python3 -I -c 'import tomllib' >/dev/null 2>&1 \
      || "${command_prefix[@]}" python3 -I -c 'import tomli' >/dev/null 2>&1; then
    config_python=python3
  else
    echo "Cannot safely verify the existing frame config without Python tomllib or tomli." >&2
    echo "Install system Python 3.11 or the system python3-tomli package before running the installer again." >&2
    exit 1
  fi
  if [ -n "$STATION_ID" ]; then config_args+=(--station-id "$STATION_ID"); fi
  if [ -n "$ZIP" ]; then config_args+=(--zip "$ZIP"); fi
  if [ -n "$IMAGE_URL" ]; then config_args+=(--image-url "$IMAGE_URL"); fi
  if ! "${command_prefix[@]}" "$config_python" -I \
      "$FRAME/config_contract.py" "${config_args[@]}" >/dev/null 2>&1; then
    case "$MODE" in
      birdweather)
        if [ -n "$STATION_ID" ]; then
          echo "$CONFIG does not select BirdWeather station $STATION_ID." >&2
        else
          echo "$CONFIG does not select BirdWeather ZIP $ZIP." >&2
        fi
        ;;
      image) echo "$CONFIG does not select image URL $IMAGE_URL." >&2 ;;
      local) echo "$CONFIG does not select a supported local or preserved image source." >&2 ;;
    esac
    echo "Run 'birdframe' to switch it over, or remove it and re-run the installer." >&2
    exit 1
  fi
}

# Validate an existing source before the first privileged change. Run the same
# semantic check under no-new-privileges after dependency setup to catch races.
verify_existing_config

if [ -n "$STATION_ID" ] && [ "$CONFIG_EXISTS" = 0 ]; then
  echo "Checking public BirdWeather station $STATION_ID..."
  if ! python3 -I "$FRAME/birdweather.py" \
      --station-id "$STATION_ID" --check-station >/dev/null; then
    echo "BirdWeather station $STATION_ID could not be verified. Check the public station-page URL and try again." >&2
    exit 1
  fi
fi

LAUNCHER_DIR=/usr/local/lib/avian-birdframe
INSTALLED_LAUNCHER=$LAUNCHER_DIR/avian-bundle
OWNER_FILE=$LAUNCHER_DIR/owner-uid
CONTROL_LINK=$LAUNCHER_DIR/control
ENVIRONMENT_DIR=/etc/avian-birdframe
ENVIRONMENT_FILE=$ENVIRONMENT_DIR/environment
BUNDLE_COMMAND=/usr/local/bin/avian-bundle
NAMES_COMMAND=/usr/local/bin/birdframe-names
SETTINGS_COMMAND=/usr/local/bin/birdframe
REFRESH_UNIT=/etc/systemd/system/birdframe-refresh.service
SERVICE_UNIT=/etc/systemd/system/birdframe.service
TIMER_UNIT=/etc/systemd/system/birdframe.timer
LAUNCHER_SOURCE=$FRAME/avian-bundle-installed
CONTROL_SOURCE=$FRAME/vendor/avian-bundle-control

if [ ! -f "$LAUNCHER_SOURCE" ] || [ -L "$LAUNCHER_SOURCE" ]; then
  install_fail "$LAUNCHER_SOURCE is missing or unsafe"
fi
LAUNCHER_SOURCE_METADATA=$(/usr/bin/stat -c '%u:%a' -- "$LAUNCHER_SOURCE") \
  || install_fail "could not inspect $LAUNCHER_SOURCE"
LAUNCHER_SOURCE_OWNER=${LAUNCHER_SOURCE_METADATA%%:*}
LAUNCHER_SOURCE_MODE=${LAUNCHER_SOURCE_METADATA#*:}
if [ "$LAUNCHER_SOURCE_OWNER" != "$INSTALL_UID" ]; then
  install_fail "$LAUNCHER_SOURCE is not owned by $INSTALL_USER"
fi
case "$LAUNCHER_SOURCE_MODE" in
  [4567][0145][0145]) ;;
  *) install_fail "$LAUNCHER_SOURCE has unsafe permissions" ;;
esac
if [ ! -f "$CONTROL_SOURCE" ] || [ -L "$CONTROL_SOURCE" ] || [ ! -x "$CONTROL_SOURCE" ]; then
  install_fail "$CONTROL_SOURCE is missing or unsafe"
fi
CONTROL_METADATA=$(/usr/bin/stat -c '%u:%a' -- "$CONTROL_SOURCE") \
  || install_fail "could not inspect $CONTROL_SOURCE"
CONTROL_OWNER=${CONTROL_METADATA%%:*}
CONTROL_MODE=${CONTROL_METADATA#*:}
if [ "$CONTROL_OWNER" != "$INSTALL_UID" ]; then
  install_fail "$CONTROL_SOURCE is not owned by $INSTALL_USER"
fi
case "$CONTROL_MODE" in
  [57][0145][0145]) ;;
  *) install_fail "$CONTROL_SOURCE has unsafe executable permissions" ;;
esac
preflight_directory "$LAUNCHER_DIR"
preflight_managed_file "$INSTALLED_LAUNCHER" 755
preflight_managed_file "$OWNER_FILE" 644
preflight_directory "$ENVIRONMENT_DIR"
preflight_managed_file "$ENVIRONMENT_FILE" 600
preflight_managed_file "$SERVICE_UNIT" 644
preflight_managed_file "$TIMER_UNIT" 644
preflight_managed_file "$REFRESH_UNIT" 644
preflight_managed_link "$NAMES_COMMAND" "$FRAME/birdframe-names"
preflight_managed_link "$SETTINGS_COMMAND" "$FRAME/birdframe"
preflight_managed_link "$CONTROL_LINK" "$CONTROL_SOURCE"
preflight_managed_link \
  "$BUNDLE_COMMAND" \
  ../lib/avian-birdframe/avian-bundle \
  /usr/local/lib/avian-birdframe/avian-bundle \
  "$FRAME/avian-bundle"

PRIVATE_EBIRD_DIR=$HOME/.birdframe
PRIVATE_EBIRD_FILE=$PRIVATE_EBIRD_DIR/ebird-api-key
PRIVATE_EBIRD_KEY=""
preflight_private_credential_directory() {
  if [ ! -e "$PRIVATE_EBIRD_DIR" ] && [ ! -L "$PRIVATE_EBIRD_DIR" ]; then
    return
  fi
  if [ ! -d "$PRIVATE_EBIRD_DIR" ] || [ -L "$PRIVATE_EBIRD_DIR" ]; then
    install_fail "the private eBird credential directory is unsafe"
  fi
  PRIVATE_DIR_METADATA=$(/usr/bin/stat -c '%u:%a' -- "$PRIVATE_EBIRD_DIR")
  case "$PRIVATE_DIR_METADATA" in
    "$INSTALL_UID":[1357][0145][0145]) ;;
    *) install_fail "the private eBird credential directory is unsafe" ;;
  esac
}

read_credential_text() {
  local raw=""
  # read -d rejects embedded NUL rather than silently removing it, and preserves
  # newlines so only one final LF is accepted by the literal token check.
  if IFS= read -r -d '' raw < "$1"; then
    install_fail "the stored eBird credential is invalid"
  fi
  CREDENTIAL_TEXT=${raw%$'\n'}
}

if [ -e "$PRIVATE_EBIRD_FILE" ] || [ -L "$PRIVATE_EBIRD_FILE" ]; then
  preflight_private_credential_directory
  if [ ! -f "$PRIVATE_EBIRD_FILE" ] || [ -L "$PRIVATE_EBIRD_FILE" ]; then
    install_fail "the private eBird credential is unsafe"
  fi
  [ "$(/usr/bin/stat -c '%u:%a:%h' -- "$PRIVATE_EBIRD_FILE")" = "$INSTALL_UID:600:1" ] \
    || install_fail "the private eBird credential is unsafe"
  PRIVATE_KEY_SIZE=$(/usr/bin/stat -c '%s' -- "$PRIVATE_EBIRD_FILE")
  if [ "$PRIVATE_KEY_SIZE" -le 0 ] || [ "$PRIVATE_KEY_SIZE" -gt 4096 ]; then
    install_fail "the private eBird credential is invalid"
  fi
  read_credential_text "$PRIVATE_EBIRD_FILE"
  PRIVATE_EBIRD_KEY=$CREDENTIAL_TEXT
  case "$PRIVATE_EBIRD_KEY" in
    ''|*[!A-Za-z0-9]*) install_fail "the private eBird credential is invalid" ;;
  esac
  [ "${#PRIVATE_EBIRD_KEY}" -le 4095 ] || install_fail "the private eBird credential is invalid"
fi

if [ "$EBIRD_KEY_SOURCE" = none ]; then
  if [ -f "$ENVIRONMENT_FILE" ]; then
    ENVIRONMENT_TEMP=$(/usr/bin/mktemp /tmp/avian-birdframe-environment.XXXXXX)
    trap cleanup_private_temps EXIT
    { /usr/bin/sudo /usr/bin/head -c 4111 -- "$ENVIRONMENT_FILE"; } > "$ENVIRONMENT_TEMP"
    read_credential_text "$ENVIRONMENT_TEMP"
    case "$CREDENTIAL_TEXT" in
      '')
        [ ! -s "$ENVIRONMENT_TEMP" ] || install_fail "the stored service eBird credential is invalid"
        [ -z "$PRIVATE_EBIRD_KEY" ] || install_fail "the stored eBird credentials disagree" ;;
      EBIRD_API_KEY=*)
        EBIRD_KEY=${CREDENTIAL_TEXT#EBIRD_API_KEY=}
        [ -n "$EBIRD_KEY" ] || install_fail "the stored service eBird credential is invalid" ;;
      *) install_fail "the stored service eBird credential is invalid" ;;
    esac
    /bin/rm -f -- "$ENVIRONMENT_TEMP"
    ENVIRONMENT_TEMP=""
    trap - EXIT
  elif [ -f "$SERVICE_UNIT" ]; then
    # Migrate only the exact line emitted by the old installer, never evaluate it.
    LEGACY_EBIRD_LINE=$(/usr/bin/grep '^Environment=EBIRD_API_KEY=' "$SERVICE_UNIT" || true)
    if [ -n "$LEGACY_EBIRD_LINE" ]; then
      EBIRD_KEY=${LEGACY_EBIRD_LINE#Environment=EBIRD_API_KEY=}
    else
      EBIRD_KEY=$PRIVATE_EBIRD_KEY
    fi
  else
    EBIRD_KEY=$PRIVATE_EBIRD_KEY
  fi
  case "$EBIRD_KEY" in
    *[!A-Za-z0-9]*) install_fail "the stored service eBird credential is invalid" ;;
  esac
  [ "${#EBIRD_KEY}" -le 4095 ] || install_fail "the stored service eBird credential is invalid"
fi
if [ -n "$EBIRD_KEY" ]; then
  preflight_private_credential_directory
fi

# Capture the trusted launcher in its root-owned location before any Python
# package tooling can modify the user-writable checkout.
/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0755 "$LAUNCHER_DIR"
/usr/bin/sudo /usr/bin/install -T -o root -g root -m 0755 \
  "$LAUNCHER_SOURCE" "$INSTALLED_LAUNCHER"
OWNER_TEMP=$(/usr/bin/mktemp /tmp/avian-birdframe-owner.XXXXXX) \
  || install_fail "could not create the owner metadata"
trap cleanup_private_temps EXIT
printf '%s\n' "$INSTALL_UID" > "$OWNER_TEMP"
/usr/bin/sudo /usr/bin/install -T -o root -g root -m 0644 \
  "$OWNER_TEMP" "$OWNER_FILE"
/bin/rm -f -- "$OWNER_TEMP"
OWNER_TEMP=
trap - EXIT

# Store the optional eBird credential outside the world-readable unit. The
# value is never placed in sudo's argv, and the user-owned staging file exists
# only before third-party package code is run.
ENVIRONMENT_TEMP=$(/usr/bin/mktemp /tmp/avian-birdframe-environment.XXXXXX) \
  || install_fail "could not create the service environment"
trap cleanup_private_temps EXIT
if [ -n "$EBIRD_KEY" ]; then
  printf 'EBIRD_API_KEY=%s\n' "$EBIRD_KEY" > "$ENVIRONMENT_TEMP"
else
  : > "$ENVIRONMENT_TEMP"
fi
/bin/chmod 0600 "$ENVIRONMENT_TEMP"
/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0755 "$ENVIRONMENT_DIR"
/usr/bin/sudo /usr/bin/install -T -o root -g root -m 0600 \
  "$ENVIRONMENT_TEMP" "$ENVIRONMENT_FILE"
/bin/rm -f -- "$ENVIRONMENT_TEMP"
ENVIRONMENT_TEMP=
trap - EXIT

if [ -n "$EBIRD_KEY" ]; then
  if [ ! -d "$PRIVATE_EBIRD_DIR" ]; then
    /bin/mkdir -m 0700 -- "$PRIVATE_EBIRD_DIR"
  fi
  EBIRD_KEY_FILE=$(/usr/bin/mktemp "$PRIVATE_EBIRD_DIR/.ebird-api-key.XXXXXX")
  trap cleanup_private_temps EXIT
  printf '%s\n' "$EBIRD_KEY" > "$EBIRD_KEY_FILE"
  /bin/chmod 0600 "$EBIRD_KEY_FILE"
  /bin/mv -fT -- "$EBIRD_KEY_FILE" "$PRIVATE_EBIRD_FILE"
  EBIRD_KEY_FILE=""
  trap - EXIT
fi

/usr/bin/sudo /usr/bin/ln -sfnT "$FRAME/birdframe-names" "$NAMES_COMMAND"
/usr/bin/sudo /usr/bin/ln -sfnT "$FRAME/birdframe" "$SETTINGS_COMMAND"
/usr/bin/sudo /usr/bin/ln -sfnT "$CONTROL_SOURCE" "$CONTROL_LINK"
/usr/bin/sudo /usr/bin/ln -sfnT \
  ../lib/avian-birdframe/avian-bundle "$BUNDLE_COMMAND"

# Render fixed unit definitions entirely inside a root-owned shell, before any
# user-writable Python package code runs. The units remain inert until the final
# systemctl step succeeds.
/usr/bin/sudo /bin/sh -c '
  set -eu
  unit_user=$1
  frame=$2
  home=$3
  service_temp=$(/usr/bin/mktemp /etc/systemd/system/.birdframe.service.XXXXXX)
  timer_temp=$(/usr/bin/mktemp /etc/systemd/system/.birdframe.timer.XXXXXX) || {
    /bin/rm -f -- "$service_temp"
    exit 1
  }
  refresh_temp=$(/usr/bin/mktemp /etc/systemd/system/.birdframe-refresh.service.XXXXXX) || {
    /bin/rm -f -- "$service_temp" "$timer_temp"
    exit 1
  }
  cleanup() {
    /bin/rm -f -- "$service_temp" "$timer_temp" "$refresh_temp"
  }
  trap cleanup EXIT
  {
    printf "%s\n" "[Unit]"
    printf "%s\n" "Description=AvianVisitors e-ink frame update"
    printf "%s\n" "Documentation=https://github.com/Twarner491/AvianVisitors"
    printf "%s\n" "Wants=network-online.target"
    printf "%s\n\n" "After=network-online.target"
    printf "%s\n" "[Service]"
    printf "%s\n" "Type=oneshot"
    printf "User=%s\n" "$unit_user"
    printf "WorkingDirectory=%s\n" "$frame"
    printf "ExecStart=%s/.venv/bin/python %s/display.py --config %s/.birdframe/config.toml\n" \
      "$frame" "$frame" "$home"
    printf "%s\n" "Environment=PYTHONUNBUFFERED=1"
    printf "%s\n" "EnvironmentFile=-/etc/avian-birdframe/environment"
    printf "%s\n" "NoNewPrivileges=true"
    printf "%s\n" "Nice=10"
    printf "%s\n" "TimeoutStartSec=300"
  } > "$service_temp"
  # The same unit with --force, for the redraw card on the station Tools page.
  # Its own unit because systemctl start takes no arguments; the render lock in
  # display.py keeps it from overlapping a timer run.
  /bin/sed "s|^Description=.*|Description=AvianVisitors e-ink frame refresh (forced, from the station page)|; s|^ExecStart=.*|& --force|" \
    "$service_temp" > "$refresh_temp"
  {
    printf "%s\n" "[Unit]"
    printf "%s\n\n" "Description=Update the e-ink bird frame on a schedule"
    printf "%s\n" "[Timer]"
    printf "%s\n" "OnActiveSec=2min"
    printf "%s\n" "OnBootSec=2min"
    printf "%s\n" "OnUnitActiveSec=15min"
    printf "%s\n\n" "Persistent=true"
    printf "%s\n" "[Install]"
    printf "%s\n" "WantedBy=timers.target"
  } > "$timer_temp"
  /usr/bin/install -T -o root -g root -m 0644 "$service_temp" \
    /etc/systemd/system/birdframe.service
  /usr/bin/install -T -o root -g root -m 0644 "$timer_temp" \
    /etc/systemd/system/birdframe.timer
  /usr/bin/install -T -o root -g root -m 0644 "$refresh_temp" \
    /etc/systemd/system/birdframe-refresh.service
' birdframe-units "$USER" "$FRAME" "$HOME"

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
SYSTEM_PACKAGES=(
  python3-venv
  python3-dev
  build-essential
  libatlas3-base
  util-linux
)
if [ "$NEEDS_BROWSER" = 1 ]; then
  # Keep the privileged side of Playwright setup inside apt. Never execute the
  # user-writable venv's install-deps helper through sudo.
  SYSTEM_PACKAGES+=(
    xvfb
    fonts-noto-color-emoji
    fonts-unifont
    libfontconfig1
    libfreetype6
    xfonts-scalable
    fonts-liberation
    fonts-ipafont-gothic
    fonts-wqy-zenhei
    fonts-tlwg-loma-otf
    fonts-freefont-ttf
    libasound2
    libatk-bridge2.0-0
    libatk1.0-0
    libatspi2.0-0
    libcairo2
    libcups2
    libdbus-1-3
    libdrm2
    libgbm1
    libglib2.0-0
    libnspr4
    libnss3
    libpango-1.0-0
    libx11-6
    libxcb1
    libxcomposite1
    libxdamage1
    libxext6
    libxfixes3
    libxkbcommon0
    libxrandr2
  )
fi
MISSING_PKGS=()
for pkg in "${SYSTEM_PACKAGES[@]}"; do
  /usr/bin/dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed" \
    || MISSING_PKGS+=("$pkg")
done
if [ "${#MISSING_PKGS[@]}" -eq 0 ]; then
  echo "     All present, skipping apt."
else
  # Only refresh the package lists when there is actually something to fetch.
  sudo /usr/bin/apt-get update -qq
  sudo /usr/bin/apt-get install -y --no-install-recommends "${MISSING_PKGS[@]}"
fi

echo "3/6  Creating venv and installing Python deps..."
[ -x /usr/bin/setpriv ] || install_fail "util-linux did not install setpriv"
if [ -x .venv/bin/pip ]; then
  echo "     venv already exists, reusing it."
else
  /usr/bin/setpriv --no-new-privs -- python3 -m venv .venv
fi
# Stamp the requirements hash into the venv: pip re-resolving an already-satisfied
# requirements file costs ~30s on a Zero 2 W and changes nothing.
REQ_STAMP=".venv/.requirements-frame.sha"
REQ_SHA="$(cat requirements-frame.txt requirements-bundle-species.txt | sha256sum | cut -d' ' -f1)-$MODE"
if [ "$(cat "$REQ_STAMP" 2>/dev/null || true)" = "$REQ_SHA" ]; then
  echo "     Python deps already match the requirements files, skipping."
else
  /usr/bin/setpriv --no-new-privs -- .venv/bin/pip install -q --upgrade pip
  /usr/bin/setpriv --no-new-privs -- .venv/bin/pip install -q -r requirements-frame.txt
  if [ "$MODE" = birdweather ]; then
    /usr/bin/setpriv --no-new-privs -- \
      .venv/bin/pip install -q -r requirements-bundle-species.txt
  fi
  printf '%s\n' "$REQ_SHA" > "$REQ_STAMP"
fi
if [ "$NEEDS_BROWSER" = 1 ]; then
  # Chromium unpacks to ~/.cache/ms-playwright/chromium-<build>. With the pinned
  # playwright already in the venv and a build present, skip the download - but
  # always prove it launches, which is the check that actually matters.
  HAVE_CHROMIUM=0
  for d in "$HOME"/.cache/ms-playwright/chromium-*; do
    if [ -d "$d" ]; then HAVE_CHROMIUM=1; break; fi
  done
  HAVE_PLAYWRIGHT="$(/usr/bin/setpriv --no-new-privs -- .venv/bin/python -c \
    'import importlib.metadata as m; print(m.version("playwright"))' 2>/dev/null || true)"
  if [ "$HAVE_CHROMIUM" = 1 ] && [ "$HAVE_PLAYWRIGHT" = "$PLAYWRIGHT_VERSION" ]; then
    echo "     Playwright $PLAYWRIGHT_VERSION + Chromium already installed, skipping."
  else
    echo "     Installing Playwright + Chromium so the Pi can render the collage (a few minutes)..."
    /usr/bin/setpriv --no-new-privs -- \
      .venv/bin/pip install -q "playwright==$PLAYWRIGHT_VERSION"
    /usr/bin/setpriv --no-new-privs -- .venv/bin/playwright install chromium
  fi
  echo "     Verifying that Chromium launches with the installed native libraries..."
  /usr/bin/setpriv --no-new-privs -- .venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    browser.close()
PY
fi

echo "4/6  Writing config..."
mkdir -p "$HOME/.birdframe"
verify_existing_config /usr/bin/setpriv --no-new-privs --
if [ "$CONFIG_EXISTS" = 1 ]; then
  echo "     $CONFIG already exists, leaving it untouched."
else
  # config.example.toml is the one description of every setting the frame has.
  # This writes it out with the mode's values already set, rather than keeping
  # a second, shorter, drifting copy of the same defaults in this file. --zip,
  # --station-id and --image-url are ignored by the modes that do not use them.
  /usr/bin/setpriv --no-new-privs -- .venv/bin/python config_tui.py --config "$CONFIG" --init "$MODE" \
    --zip "$ZIP" --station-id "$STATION_ID" --image-url "$IMAGE_URL"
fi

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
# the station is behind hand it the files this frame was built against: the
# collage renderer and its stylesheet, the cutout endpoint it asks for sized
# images from, and the small API behind the "redraw the frame" card on the
# station's Tools page - which is why this runs in every mode and not only
# local: a frame fed by a render server still wants that button.
#
# What makes copying into another checkout safe to automate is that none of it
# is assumed. The copy happens only when the station is actually missing
# something - a tunable, or the frame card - what it replaces is backed up, and
# the result is checked against the same list shoot.py rewrites at capture
# time. If that list ever outgrows these files, this fails loudly with the
# station put back as it was, instead of leaving a frontend half-patched.
STATION="${BIRDNET_PI_DIR:-$HOME/BirdNET-Pi}"
STATION_JS="$STATION/avian/frontend/apt.js"
STATION_FILES="avian/frontend/apt.js avian/frontend/styles.css avian/api/cutout.php avian/api/frame.php"
# data-frame-token is the handshake shoot.py waits on before it captures.
station_is_current() {
  /usr/bin/setpriv --no-new-privs -- .venv/bin/python shoot.py --check-frontend "$STATION_JS" 2>/dev/null \
    && grep -q 'avian/api/frame.php' "$STATION_JS" \
    && grep -q 'data-frame-token' "$STATION_JS" \
    && [ -f "$STATION/avian/api/frame.php" ]
}
if [ ! -f "$STATION_JS" ]; then
  echo "     no station checkout here ($STATION); assuming the frame mirrors one on the network."
elif station_is_current; then
  echo "     $STATION already serves a collage this frame can drive."
elif [ ! -f "$STATION/avian/api/bundle-runtime.php" ]; then
  # These files are v1.3 and assume the illustration-bundle runtime beside them:
  # cutout.php requires it outright. Dropping them into an older station would
  # break its pictures, so this is the one case left to a station update.
  echo "     $STATION predates illustration bundles (v1.3); update the station first." >&2
  echo "     Until then this frame cannot capture its collage." >&2
else
  STAMP="$(date +%s)"
  echo "     $STATION serves an older collage than this frame needs; updating it."
  for f in $STATION_FILES; do
    [ -f "$STATION/$f" ] && cp -p "$STATION/$f" "$STATION/$f.bak-$STAMP"
    cp "$REPO/$f" "$STATION/$f"
  done
  if station_is_current; then
    echo "     updated (replaced files backed up alongside as *.bak-$STAMP)."
    echo "     Note: a station update re-clones from upstream and undoes this."
    echo "     Re-run this installer afterwards, or merge the frame branch upstream."
  else
    echo "     still missing tunables after the copy - putting $STATION back." >&2
    for f in $STATION_FILES; do
      [ -f "$STATION/$f.bak-$STAMP" ] && mv "$STATION/$f.bak-$STAMP" "$STATION/$f"
    done
    echo "     The frame needs collage files this step does not know to copy." >&2
    exit 1
  fi
fi
# The station's Caddy publishes an explicit list of API entry points and
# answers 404 for anything else under avian/api - so a frame.php copied into
# the checkout is invisible until it is on that list. A station whose
# Caddyfile came from a checkout that predates the frame card has to be told.
# One line, validated before it is installed, and only ever added, never
# removed: a station update regenerates the file and picks the entry up from
# its own scripts/update_caddyfile.sh.
CADDYFILE=/etc/caddy/Caddyfile
if [ -f "$STATION_JS" ] && [ -f "$CADDYFILE" ] && command -v caddy >/dev/null 2>&1 \
   && sudo grep -q 'not path /avian/api/.*maintenance.php' "$CADDYFILE" \
   && ! sudo grep -q 'not path /avian/api/.*/avian/api/frame.php' "$CADDYFILE"; then
  CADDY_TMP="$(sudo mktemp /etc/caddy/.Caddyfile.XXXXXX)"
  sudo sed 's|\(not path /avian/api/.*\)/avian/api/maintenance.php|\1/avian/api/frame.php /avian/api/maintenance.php|' "$CADDYFILE" \
    | sudo tee "$CADDY_TMP" >/dev/null
  if sudo caddy validate --config "$CADDY_TMP" --adapter caddyfile >/dev/null 2>&1; then
    sudo install -o root -g root -m 0644 "$CADDY_TMP" "$CADDYFILE"
    sudo systemctl reload-or-restart caddy
    echo "     told the station's Caddy about avian/api/frame.php."
  else
    echo "     could not add avian/api/frame.php to $CADDYFILE; the station page will not show the frame card." >&2
  fi
  sudo rm -f "$CADDY_TMP"
fi

echo "6/6  Enabling systemd service + timer..."
# What the station's web server may ask of the frame: a root-owned helper that
# takes exactly `status` or `refresh`, and a sudo rule that names those two
# and nothing else. This is the shape the station's own Tools actions take
# (scripts/*_control.sh, /etc/sudoers.d/020_avian-admin); its own file so a
# station security refresh, which rewrites that one, leaves this alone.
sudo install -o root -g root -m 0755 frame_control.sh /usr/local/sbin/avian-frame-control
WEB_USER=caddy
getent passwd "$WEB_USER" >/dev/null 2>&1 || WEB_USER=www-data
SUDOERS_TMP="$(sudo mktemp /etc/sudoers.d/.021_avian-frame.XXXXXX)"
printf '# The station page may ask the e-ink frame to redraw, through its root-owned helper.\n%s ALL=(root) NOPASSWD: /usr/local/sbin/avian-frame-control status, /usr/local/sbin/avian-frame-control refresh\n' "$WEB_USER" \
  | sudo tee "$SUDOERS_TMP" >/dev/null
if sudo visudo -cf "$SUDOERS_TMP" >/dev/null; then
  sudo install -o root -g root -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/021_avian-frame
else
  echo "     the sudoers rule for the frame did not validate; the station page will not be able to refresh the frame." >&2
fi
sudo rm -f "$SUDOERS_TMP"
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
    if [ -n "$STATION_ID" ]; then
      SOURCE_LABEL="BirdWeather station $STATION_ID"
      MISSING_ARGS=(--station-id "$STATION_ID" --missing)
      GENERATE_ARG="--station-id $STATION_ID"
      cat <<DONE

Installed for BirdWeather station $STATION_ID. The frame renders that station's
birds on the Pi and refreshes every 15 min, only when its top birds change.
DONE
    else
      SOURCE_LABEL="the area near $ZIP"
      MISSING_ARGS=("$ZIP" --missing)
      GENERATE_ARG="--zip $ZIP"
      cat <<DONE

Installed in BirdWeather mode for ZIP $ZIP. The frame renders the top birds near
you on the Pi and refreshes every 15 min, only when the local top birds change.
DONE
    fi
    # Surface drawable-species gaps and point at the matching generator mode.
    MISSING="$(/usr/bin/setpriv --no-new-privs -- \
      "$FRAME/.venv/bin/python" "$FRAME/birdweather.py" "${MISSING_ARGS[@]}" \
      2>/dev/null || true)"
    if [ -n "$MISSING" ]; then
      N="$(printf '%s\n' "$MISSING" | grep -c . || true)"
      NAMES="$(printf '%s\n' "$MISSING" | head -8 | sed 's/.*|/    /')"
      if [ "$N" -gt 8 ]; then NAMES="$NAMES
    ... and $((N - 8)) more"; fi
      cat <<FLAG
Heads up: $N bird(s) from $SOURCE_LABEL aren't in the illustration set you cloned, so
the frame will skip them:
$NAMES
To add them, run this on a laptop or workstation (it needs rembg, which the Pi
can't fit) and commit or copy the new cutouts over:
  python3 $FRAME/generate_illustrations.py $GENERATE_ARG --gemini-key <KEY>
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
  if ! /usr/bin/setpriv --no-new-privs -- \
      "$FRAME/.venv/bin/python" "$FRAME/display.py" --config "$CONFIG" --force --wait-lock; then
    echo "     That render did not finish. The timer retries within 15 min;" >&2
    echo "     'journalctl -u birdframe -n 50' has the reason." >&2
  fi
else
  echo "Rebooting to bring SPI up (back on its own in ~1 min)..."
  sleep 4
  sudo reboot
fi
cleanup_private_temps
}

{ main "$@"; exit; }
