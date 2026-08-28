#!/usr/bin/env python3
"""Sample a container's memory and CPU from outside its ceiling.

    monitor.py <container-name> <out.jsonl> [interval-seconds]

metrics.py already samples the cgroup from inside the container, and does it
better - it can read the PSI pressure files, which is the reading that says
whether the box is computing or only moving pages about. But it only runs while
a render runs, and it spends part of the very budget under test to do it. This
watches from outside: the baseline between renders, and the independent check
that the 415MB cap is real rather than something compose accepted and ignored.

Talks to the Docker socket directly over HTTP rather than importing the docker
SDK, so the sidecar needs nothing installed beyond the standard library.
"""
import http.client
import json
import socket
import sys
import time


class _UnixHTTP(http.client.HTTPConnection):
    """http.client over a unix socket. The Docker API is plain HTTP; the only
    unusual thing about it is where the socket lives."""

    def __init__(self, path):
        super().__init__("localhost")
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(30)
        self.sock.connect(self._path)


def _get(path, sock="/var/run/docker.sock"):
    conn = _UnixHTTP(sock)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            return None
        return json.loads(body)
    finally:
        conn.close()


def sample(name):
    """One reading, or None if the container is not up yet.

    stream=false gives a single snapshot. CPU is reported as cumulative
    nanoseconds, so a rate needs two readings - the caller keeps the previous
    one rather than this holding state.
    """
    s = _get(f"/containers/{name}/stats?stream=false")
    if not s:
        return None
    mem = s.get("memory_stats") or {}
    stats = mem.get("stats") or {}
    cpu = (s.get("cpu_stats") or {}).get("cpu_usage") or {}
    return {
        "wall": time.time(),
        "mem_used": mem.get("usage"),
        "mem_limit": mem.get("limit"),
        # `usage` counts page cache; this is the figure that decides whether the
        # render fits, and the one the kernel uses when it decides to reclaim.
        "mem_anon": stats.get("anon"),
        "mem_file": stats.get("file"),
        "swap": stats.get("swap"),
        "pgmajfault": stats.get("pgmajfault"),
        "cpu_ns": cpu.get("total_usage"),
    }


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    name, out = argv[0], argv[1]
    interval = float(argv[2]) if len(argv) > 2 else 2.0
    prev = None
    with open(out, "a", encoding="utf-8") as fh:
        while True:
            try:
                rec = sample(name)
            except (OSError, ValueError, json.JSONDecodeError) as e:
                rec = {"wall": time.time(), "error": str(e)}
            if rec:
                if prev and rec.get("cpu_ns") and prev.get("cpu_ns"):
                    span = rec["wall"] - prev["wall"]
                    if span > 0:
                        rec["cpu_pct"] = round(
                            (rec["cpu_ns"] - prev["cpu_ns"]) / 1e9 / span * 100, 1)
                prev = rec if rec.get("cpu_ns") else prev
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
                fh.flush()
            time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
