#!/usr/bin/env bash
# Run inside a disposable Debian container with the repository at /source.
# The frame is optional, so the first thing checked is the answer when there
# is none: a plain installed:false, not an error, because that is what tells
# the Tools page to leave the card out.

set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }

body=/tmp/frame-api-body
mkdir -p /tmp/frame-api-bin
cat >/tmp/frame-api-bin/sudo <<'STUB'
#!/bin/sh
[ "${1:-}" = -n ] && shift
exec "$@"
STUB
chmod 0755 /tmp/frame-api-bin/sudo
export PATH="/tmp/frame-api-bin:$PATH"

# No helper: nothing at the path frame.php is told to use.
AV_FRAME_CONTROL=/tmp/frame-control-absent php -S 127.0.0.1:8896 -t /source \
  >/tmp/frame-api-server.log 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
for _ in 1 2 3 4 5; do
  curl -fsS http://127.0.0.1:8896/avian/api/frame.php >"$body" 2>/dev/null && break
  sleep 1
done
grep -q '"installed":false' "$body" || fail "absent frame should answer installed:false: $(cat "$body")"
grep -q '"ok":true' "$body" || fail "absent frame is not an error: $(cat "$body")"
code=$(curl -sS -o "$body" -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' -H 'X-Avian-Action: 1' \
  --data '{"action":"refresh"}' http://127.0.0.1:8896/avian/api/frame.php)
[ "$code" = 503 ] || fail "refresh with no frame installed should be 503, got $code"
kill "$server_pid"; wait "$server_pid" 2>/dev/null || true; trap - EXIT

# A helper that answers, so the routing can be checked.
cat >/tmp/frame-control-test <<'STUB'
#!/bin/sh
printf '{"ok":true,"installed":true,"action":"%s","state":"idle"}\n' "$1"
STUB
chmod 0755 /tmp/frame-control-test
AV_FRAME_CONTROL=/tmp/frame-control-test php -S 127.0.0.1:8896 -t /source \
  >>/tmp/frame-api-server.log 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
for _ in 1 2 3 4 5; do
  curl -fsS http://127.0.0.1:8896/avian/api/frame.php >"$body" 2>/dev/null && break
  sleep 1
done
grep -q '"action":"status"' "$body" || fail "local GET status"

code=$(curl -sS -o "$body" -w '%{http_code}' \
  -H 'X-Forwarded-For: 203.0.113.8' http://127.0.0.1:8896/avian/api/frame.php)
[ "$code" = 401 ] || fail "forwarded request guard"

code=$(curl -sS -o "$body" -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' --data '{}' http://127.0.0.1:8896/avian/api/frame.php)
[ "$code" = 403 ] || fail "action header guard"

code=$(curl -sS -o "$body" -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' -H 'X-Avian-Action: 1' \
  --data '{"action":"reboot"}' http://127.0.0.1:8896/avian/api/frame.php)
[ "$code" = 400 ] || fail "only refresh is an action, got $code"

code=$(curl -sS -o "$body" -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' -H 'X-Avian-Action: 1' \
  --data '{"action":"refresh"}' http://127.0.0.1:8896/avian/api/frame.php)
[ "$code" = 200 ] || fail "refresh, got $code"
grep -q '"action":"refresh"' "$body" || fail "refresh routing"

echo 'frame api smoke: ok'
