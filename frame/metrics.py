#!/usr/bin/env python3
"""Where a render's time and memory actually go.

The frame had no instrumentation at all - the only wall-clock timer in the
repository is in scripts/utils/analysis.py - so when a render started taking
400 seconds on a Pi 3 A+ there was nothing to look at but the total. This
records the parts.

Three kinds of record, one JSONL file per render:

  phase   a named span with a duration. The steps of shoot() and display.py's
          run(), so "the render took 400s" becomes a list that adds up to 400s.
  sample  a periodic reading of what the machine is doing. Wall clock is not
          the interesting axis on a box with 415MB: `pressure` is, because it
          separates a render that is slow from a render that is swapping, and
          those want opposite fixes.
  event   a point fact worth counting rather than timing - how many times the
          page re-rendered itself during the capture, what each asset weighed.

Off unless a path is given, and a no-op object is returned when it is off, so
the call sites read the same either way and cost nothing in the default case.
That matters because the interesting machine is the Pi, and the way this gets
used there is one run with it switched on, not a permanent tax on every render.

Deliberately dependency-free: psutil is not installed on the frame and the
whole point is to be able to turn this on wherever the problem is happening.
"""
from __future__ import annotations

import json
import os
import threading
import time

# Kernel counters worth sampling, and why each one is here.
#
# memory.current    what the cgroup is holding now.
# memory.peak       the high-water mark, which is the number that decides
#                   whether the render fits at all.
# memory.swap.current  how much has been pushed to zram/SD. On the Pi this is
#                   the difference between working and thrashing.
# pgmajfault        major faults, read out of memory.stat: pages that had to
#                   come back off disk. A render that is computing has almost
#                   none; a render that is thrashing has millions.
_CGROUP_FILES = ("memory.current", "memory.peak", "memory.swap.current")

# Two cgroups get sampled, because they answer different questions and neither
# answers both.
#
# The process's OWN cgroup is what this render is using - under systemd, the
# service's slice. That is the honest figure for "what did the frame cost".
#
# The MOUNT ROOT is the ceiling it is pressing against: the container's limit in
# a private cgroup namespace, the whole machine on the Pi. That is where the cap
# lives and where contention with the analyser shows up, and it is why the
# pressure files are read there and not in the slice.
_CGROUP_MOUNT = "/sys/fs/cgroup"


def _meminfo():
    """MemTotal and MemAvailable, in bytes.

    Machine-wide and always available, which the cgroup figures are not: on the
    Pi nothing sets a limit, so there is no ceiling cgroup to read and the only
    honest answer to "was there room" comes from here.
    """
    out = {}
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable", "SwapFree", "SwapTotal"):
                    out[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


def _accounted(start):
    """Nearest cgroup at or above `start` that sets a real memory ceiling.

    The mount root was the obvious choice and it is wrong on real hardware: the
    cgroup v2 root has no memory.current at all, because nothing is outside it
    to account against. In a container the mount root is really a nested cgroup,
    so it answers - which is exactly the trap, because the lab read fine while
    the Pi silently reported null for every memory figure.

    "Sets a ceiling" rather than "reports memory", because the nearest cgroup
    that merely accounts can be a leaf holding almost nothing - init.scope
    reported 29MB inside a container capped at 415MB, which is true and useless.
    Returns None where nothing is capped, which is the normal case on a Pi; the
    machine-wide figures from _meminfo cover that.
    """
    path = start or _CGROUP_MOUNT
    root = os.path.abspath(_CGROUP_MOUNT)
    while True:
        try:
            with open(os.path.join(path, "memory.max"), "r", encoding="ascii") as f:
                if f.read().strip() != "max":
                    return path
        except OSError:
            pass
        if os.path.abspath(path) == root:
            return None
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def _read_int(path):
    try:
        with open(path, "r", encoding="ascii") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _cgroup_root():
    """This process's cgroup v2 directory, or None if there isn't one.

    In a container /proc/self/cgroup reads "0::/" and the limits live at the
    root of the mount. Under systemd on the Pi it reads the service's own
    slice, and the counters we want are the service's, not the machine's -
    which is exactly the distinction that makes this worth resolving properly
    rather than hardcoding /sys/fs/cgroup.
    """
    try:
        with open("/proc/self/cgroup", "r", encoding="ascii") as f:
            for line in f:
                parts = line.strip().split(":", 2)
                if len(parts) == 3 and parts[0] == "0":
                    root = os.path.join("/sys/fs/cgroup", parts[2].lstrip("/"))
                    return root if os.path.isdir(root) else None
    except OSError:
        pass
    return None


def _pressure(path):
    """The `some avg10` field of a PSI file, as a percentage.

    One number, and the most informative one here: the share of the last ten
    seconds in which at least one task was stalled waiting for that resource.
    Above about 20 on memory the box has stopped doing work and started moving
    pages around.
    """
    try:
        with open(path, "r", encoding="ascii") as f:
            for line in f:
                if line.startswith("some "):
                    for field in line.split():
                        if field.startswith("avg10="):
                            return float(field.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return None


def _self_rss_kb():
    """This process only. The cgroup figure covers the browser as well, which
    is the one that matters, but on a machine with no cgroup v2 - a laptop, a
    Mac - this is still better than nothing."""
    try:
        with open("/proc/self/status", "r", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


class _Off:
    """What every call site gets when metrics are switched off.

    A null object rather than `if self.metrics:` at each site: the phases are
    the readable part of shoot(), and wrapping them in conditionals to support
    a feature that is normally off would cost more than it measures.
    """

    enabled = False

    def phase(self, phase, **fields):
        return self

    def marks(self, **fixed):
        return lambda name, **fields: None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def event(self, event, **fields):
        pass

    def close(self, **fields):
        pass


class _Span:
    def __init__(self, sink, name, fields):
        self._sink, self._name, self._fields = sink, name, fields
        self._t0 = None

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        # Caller fields go in first so the span's own keys win: a phase that
        # passed name="apt.js" would otherwise rename itself in the log.
        rec = dict(self._fields)
        rec["kind"] = "phase"
        rec["name"] = self._name
        rec["secs"] = round(time.perf_counter() - self._t0, 4)
        if exc_type is not None:
            rec["failed"] = f"{exc_type.__name__}: {exc}"
        self._sink(rec)
        return False


class _Marks:
    """A stopwatch that records the gap since the last mark.

    The alternative is a `with` per step, which reads better but would mean
    re-indenting the whole of shoot() - a 130-line function nested four deep
    inside `with sync_playwright()` / `try:` / `if` - to measure it. Marks put
    one line between the steps instead and produce the same phase records.
    Consequently a mark names the work BEFORE it, not after.
    """

    def __init__(self, sink, fixed=None):
        self._sink = sink
        self._fixed = fixed or {}
        self._last = time.perf_counter()

    def __call__(self, name, **fields):
        now = time.perf_counter()
        rec = dict(self._fixed)
        rec.update(fields)
        rec["kind"] = "phase"
        rec["name"] = name
        rec["secs"] = round(now - self._last, 4)
        self._sink(rec)
        self._last = now


class Metrics:
    """A JSONL recorder with a background sampler.

    One file per render. JSONL rather than a summary because the question
    changes between runs - the first thing we wanted was the phase breakdown,
    the second was how many times the page re-rendered itself - and a log of
    facts can answer a question nobody had when it was written.
    """

    enabled = True

    def __init__(self, path, sample_hz=2.0):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._fh = open(path, "w", encoding="utf-8")
        self._lock = threading.Lock()
        self._t0 = time.perf_counter()
        self._cg = _cgroup_root()
        # Where the ceiling actually is, which is not always the mount root.
        self._box = _accounted(self._cg)
        self._stop = threading.Event()
        self._interval = 1.0 / sample_hz if sample_hz > 0 else 0
        self._write({"kind": "start", "pid": os.getpid(), "cgroup": self._cg,
                     "accounted_at": self._box, "wall": time.time()})
        self._thread = None
        if self._interval:
            self._thread = threading.Thread(target=self._sample_loop, daemon=True)
            self._thread.start()

    # --- recording ---------------------------------------------------------
    def _write(self, rec):
        rec.setdefault("t", round(time.perf_counter() - self._t0, 4))
        line = json.dumps(rec, separators=(",", ":"), default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()   # a render that dies is exactly the one worth reading

    def phase(self, phase, **fields):
        return _Span(self._write, phase, fields)

    def marks(self, **fixed):
        return _Marks(self._write, fixed)

    def event(self, event, **fields):
        rec = dict(fields)
        rec["kind"] = "event"
        rec["event"] = event
        self._write(rec)

    # --- sampling ----------------------------------------------------------
    def _sample(self):
        rec = {"kind": "sample", "rss_kb": _self_rss_kb()}
        rec.update({k.lower(): v for k, v in _meminfo().items()})
        if self._cg:
            for name in _CGROUP_FILES:
                rec[name.replace(".", "_")] = _read_int(os.path.join(self._cg, name))
            rec["pgmajfault"] = self._stat_field(self._cg, "pgmajfault")
        if self._box:
            for name in _CGROUP_FILES + ("memory.max",):
                rec["box_" + name.replace(".", "_")] = _read_int(
                    os.path.join(self._box, name))
            rec["box_pgmajfault"] = self._stat_field(self._box, "pgmajfault")
            # Pressure belongs to the ceiling, not to the slice. It measures the
            # share of the last ten seconds in which something was stalled
            # waiting, and what stalls a render is the box running out - the
            # render's own slice can look healthy while the machine thrashes.
            rec["mem_pressure"] = _pressure(os.path.join(self._box, "memory.pressure"))
            rec["cpu_pressure"] = _pressure(os.path.join(self._box, "cpu.pressure"))
            rec["io_pressure"] = _pressure(os.path.join(self._box, "io.pressure"))
        self._write(rec)

    def _stat_field(self, cg, key):
        try:
            with open(os.path.join(cg, "memory.stat"), "r", encoding="ascii") as f:
                for line in f:
                    name, _, value = line.partition(" ")
                    if name == key:
                        return int(value)
        except (OSError, ValueError):
            pass
        return None

    def _sample_loop(self):
        while not self._stop.wait(self._interval):
            try:
                self._sample()
            except Exception:      # a sampler must never take the render down
                pass

    # --- teardown ----------------------------------------------------------
    def close(self, **fields):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        try:
            self._sample()
        except Exception:
            pass
        rec = {"kind": "end", "total_secs": round(time.perf_counter() - self._t0, 4)}
        rec.update(fields)
        self._write(rec)
        with self._lock:
            self._fh.close()


OFF = _Off()


def open_metrics(path=None):
    """A recorder for `path`, or the no-op if there is nowhere to write.

    Also reads BIRDFRAME_METRICS, so a single run can be instrumented from the
    shell - `BIRDFRAME_METRICS=/tmp/r.jsonl display.py --force` - without
    editing the config on a machine you are only visiting.
    """
    path = path or os.environ.get("BIRDFRAME_METRICS") or ""
    if not path:
        return OFF
    try:
        return Metrics(os.path.expanduser(path))
    except OSError as e:
        print(f"metrics disabled: {e}", flush=True)
        return OFF
