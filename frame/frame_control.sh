#!/usr/bin/env bash
# Root-owned control plane for the one thing the station's web page can ask
# of the frame: draw the panel now.
#
# Installed by frame/install.sh as /usr/local/sbin/avian-frame-control, with a
# sudoers rule that lets the web server run exactly `status` and `refresh` and
# nothing else. The station's own helpers (scripts/*_control.sh) set the
# pattern this follows: a fixed root-owned script that validates its argument,
# starts a systemd unit rather than doing the work in the web request, and
# answers in JSON. What it starts is birdframe-refresh.service - the timer's
# unit with --force appended, so a refresh redraws even when the birds have not
# changed - and it never runs display.py itself: the unit carries the user, the
# venv and the config, and the render lock in display.py serialises it against
# the timer's own run.

set -Eeuo pipefail
IFS=$'\n\t'
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

readonly CONTROL_VERSION=1
readonly CONTROL_HELPER=/usr/local/sbin/avian-frame-control
readonly refresh_unit=birdframe-refresh.service
readonly timer_unit=birdframe.service

json_escape() {
  local value=${1-}
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  value=${value//$'\t'/\\t}
  printf '%s' "$value"
}

fail() {
  local message=${1:-frame control failed}
  printf '{"ok":false,"installed":true,"error":"%s"}\n' "$(json_escape "$message")"
  exit 1
}

[ "${EUID:-$(id -u)}" -eq 0 ] || fail 'frame control must run as root'
[ "$(readlink -f "$0")" = "$CONTROL_HELPER" ] \
  || fail "frame control must run as $CONTROL_HELPER"

unit_active() {
  local state
  state=$(systemctl is-active "$1" 2>/dev/null || true)
  [ "$state" = active ] || [ "$state" = activating ]
}

unit_prop() {
  systemctl show "$1" -p "$2" --value 2>/dev/null || true
}

# The last line display.py printed in the most recent forced run: "panel
# updated", "could not get image: ...", "another render is in progress;
# skipping". That line is the outcome, and it is the honest one - the unit's
# exit status is 0 for all three, because a skipped render is not an error.
last_outcome() {
  local iid
  iid=$(unit_prop "$refresh_unit" InvocationID)
  [ -n "$iid" ] || return 0
  journalctl "_SYSTEMD_INVOCATION_ID=$iid" -o cat --no-pager 2>/dev/null \
    | grep -v '^$' | tail -n 1 || true
}

print_status() {
  local state=idle detail started finished
  if unit_active "$refresh_unit"; then
    state=running
  elif unit_active "$timer_unit"; then
    # The timer's own run holds the render lock, so a refresh started now
    # would only report "another render is in progress". Say so first.
    state=busy
  fi
  detail=$(last_outcome)
  started=$(unit_prop "$refresh_unit" ExecMainStartTimestamp)
  finished=$(unit_prop "$refresh_unit" ExecMainExitTimestamp)
  printf '{"ok":true,"installed":true,"version":%s,"state":"%s","detail":"%s","started":"%s","finished":"%s"}\n' \
    "$CONTROL_VERSION" "$state" "$(json_escape "$detail")" \
    "$(json_escape "$started")" "$(json_escape "$finished")"
}

start_refresh() {
  if unit_active "$refresh_unit"; then
    fail 'a refresh is already running'
  fi
  if unit_active "$timer_unit"; then
    fail 'the frame is already rendering'
  fi
  [ -f "/etc/systemd/system/$refresh_unit" ] \
    || fail "$refresh_unit is not installed; re-run frame/install.sh"
  systemctl reset-failed "$refresh_unit" >/dev/null 2>&1 || true
  # --no-block: the request returns at once and the page polls status. A
  # render takes up to a couple of minutes and a web request must not.
  systemctl start --no-block "$refresh_unit" >/dev/null 2>&1 \
    || fail 'could not start the refresh'
  print_status
}

action=${1:-status}
[ "$#" -le 1 ] || fail 'unexpected arguments'
case "$action" in
  status) print_status ;;
  refresh) start_refresh ;;
  *) fail 'unknown action' ;;
esac
