#!/usr/bin/env bash
# Run as root in a disposable Debian container with the repository at /source.
# systemd is stubbed: the helper only ever asks it three questions and starts
# one unit, and the stub records which, so the test can check that a refresh
# is refused while a render is running and started when it is not.

set -euo pipefail
IFS=$'\n\t'

fail() { echo "FAIL: $*" >&2; exit 1; }

[ "${EUID:-$(id -u)}" -eq 0 ] || { echo 'run this smoke test as root' >&2; exit 1; }
test_root=/tmp/avian-frame-smoke
rm -rf "$test_root"
mkdir -p "$test_root" /usr/local/bin /usr/local/sbin /etc/systemd/system

cp /source/frame/frame_control.sh /usr/local/sbin/avian-frame-control
chown root:root /usr/local/sbin/avian-frame-control
chmod 0755 /usr/local/sbin/avian-frame-control
: >/etc/systemd/system/birdframe-refresh.service

# ACTIVE_<unit> files stand in for systemd's view of each unit.
cat >/usr/local/bin/systemctl <<'STUB'
#!/usr/bin/env bash
root=/tmp/avian-frame-smoke
case "${1:-}" in
  is-active)
    unit=${2%.service}
    if [ -e "$root/active.$unit" ]; then echo active; else echo inactive; fi ;;
  start) shift; while [ "$#" -gt 1 ]; do shift; done; echo "$1" >>"$root/started" ;;
  show) [ "$4" = InvocationID ] && echo abc123 ;;
esac
exit 0
STUB
cat >/usr/local/bin/journalctl <<'STUB'
#!/usr/bin/env bash
# The last line the render printed: under the unit when idle, under the
# current invocation while running.
case "$1" in
  -u) printf '2026-09-16T20:09:10-0400 birdnet python[391818]: panel updated\n' ;;
  _SYSTEMD_INVOCATION_ID=abc123) printf '2026-09-16T20:11:02-0400 birdnet python[391900]: 20 species, 1 singing, 9 fading\n' ;;
esac
STUB
chmod 0755 /usr/local/bin/systemctl /usr/local/bin/journalctl

out=$(/usr/local/sbin/avian-frame-control)
grep -q '"state":"idle"' <<<"$out" || fail "initial status was not idle: $out"
grep -q '"detail":"panel updated"' <<<"$out" || fail "last outcome was not read from the journal: $out"
grep -q '"when":"2026-09-16T20:09:10-0400"' <<<"$out" || fail "outcome time was not read from the journal: $out"
grep -q '"installed":true' <<<"$out" || fail "status did not say installed: $out"

out=$(/usr/local/sbin/avian-frame-control refresh)
grep -q '"ok":true' <<<"$out" || fail "refresh was refused when idle: $out"
[ "$(cat "$test_root/started")" = birdframe-refresh.service ] \
  || fail 'refresh did not start birdframe-refresh.service'

touch "$test_root/active.birdframe"
/usr/local/sbin/avian-frame-control | grep -q '"state":"busy"' \
  || fail "the timer's own render was not reported as busy"
if /usr/local/sbin/avian-frame-control refresh >"$test_root/out" 2>&1; then
  fail 'refresh started over the top of a running render'
fi
grep -q 'already rendering' "$test_root/out" || fail "wrong refusal: $(cat "$test_root/out")"
rm -f "$test_root/active.birdframe"

touch "$test_root/active.birdframe-refresh"
out=$(/usr/local/sbin/avian-frame-control)
grep -q '"state":"running"' <<<"$out" || fail "a running refresh was not reported as running: $out"
grep -q '"detail":"20 species, 1 singing, 9 fading"' <<<"$out" \
  || fail "a running refresh reported a line that was not its own: $out"
if /usr/local/sbin/avian-frame-control refresh >/dev/null 2>&1; then
  fail 'a second refresh was started over the first'
fi
rm -f "$test_root/active.birdframe-refresh"

if /usr/local/sbin/avian-frame-control unknown >/dev/null 2>&1; then
  fail 'unknown action was accepted'
fi
if /usr/local/sbin/avian-frame-control refresh now >/dev/null 2>&1; then
  fail 'extra arguments were accepted'
fi
[ "$(wc -l <"$test_root/started")" -eq 1 ] || fail 'a refused refresh still started the unit'

echo 'frame control smoke: ok'
