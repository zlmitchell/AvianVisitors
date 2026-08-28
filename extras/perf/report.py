#!/usr/bin/env python3
"""Read a render's metrics log and say where the time and the memory went.

Usage:  report.py <render.jsonl> [...]

Phases are printed in the order they happened rather than sorted by cost,
because the shape of a render is the point: a browser launch that dominates and
a wait that dominates want completely different fixes, and the order is what
tells them apart. The share column does the sorting for you.

Assets are grouped by the last path segment, and the summary line carries the
number this was built to find - how many times the page re-rendered itself
while it was being photographed.
"""
import collections
import json
import sys


def load(path):
    recs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass          # a run killed mid-write leaves a partial line
    return recs


def _bar(share, width=28):
    return "#" * max(0, min(width, round(share * width)))


def phases(recs):
    rows = [r for r in recs if r.get("kind") == "phase"]
    if not rows:
        return
    total = sum(r.get("secs") or 0 for r in rows)
    print(f"\n  PHASES                       {total:8.1f}s accounted for")
    for r in rows:
        secs = r.get("secs") or 0
        share = secs / total if total else 0
        flag = "  <-- " + r["failed"] if r.get("failed") else ""
        print(f"    {r['name']:<24} {secs:8.2f}s {share * 100:5.1f}%  {_bar(share)}{flag}")


def memory(recs):
    rows = [r for r in recs if r.get("kind") == "sample"]
    if not rows:
        return

    def peak(key):
        vals = [r.get(key) for r in rows if r.get(key) is not None]
        return max(vals) if vals else None

    cur, swap = peak("memory_current"), peak("memory_swap_current")
    hard, faults = peak("memory_peak"), peak("pgmajfault")
    press = peak("mem_pressure")
    if cur is None and hard is None and press is None:
        print("\n  MEMORY: no cgroup v2 counters here (not Linux, or no cgroup)")
        return
    print("\n  MEMORY")
    for label, val in (("peak in cgroup", hard), ("high water (current)", cur),
                       ("peak swapped out", swap)):
        if val is not None:
            print(f"    {label:<24} {val / 1048576:8.1f} MB")
    if faults is not None:
        print(f"    {'major faults':<24} {faults:8d}")
    if press is not None:
        # PSI is the whole reason this samples at all: it is what separates a
        # render that is computing from one that is only moving pages about.
        verdict = "thrashing" if press > 20 else "some stalling" if press > 5 else "healthy"
        print(f"    {'memory pressure (avg10)':<24} {press:8.1f}%  {verdict}")


def assets(recs):
    rows = [r for r in recs if r.get("event") == "resource"]
    if not rows:
        return
    by = collections.defaultdict(lambda: [0, 0, 0])
    for r in rows:
        name = (r.get("name") or "?").split("?")[0].rsplit("/", 1)[-1] or "/"
        slot = by[name]
        slot[0] += 1
        slot[1] += r.get("bytes") or 0
        slot[2] += r.get("ms") or 0
    print(f"\n  ASSETS ({len(rows)} requests)")
    for name, (n, b, ms) in sorted(by.items(), key=lambda kv: -kv[1][1])[:14]:
        print(f"    {name:<34} x{n:<4} {b / 1024:9.1f} KB {ms:7d} ms")


def summary(recs):
    end = next((r for r in recs if r.get("kind") == "end"), None)
    page = next((r for r in recs if r.get("event") == "page.summary"), None)
    if page:
        print(f"\n  PAGE   {page.get('requests')} requests, "
              f"{(page.get('bytes') or 0) / 1048576:.2f} MB, "
              f"{page.get('api_requests')} API calls "
              f"= {page.get('refresh_passes')} full re-renders during the capture")
    if end:
        extra = " ".join(f"{k}={v}" for k, v in end.items()
                         if k not in ("kind", "t", "total_secs"))
        print(f"  TOTAL  {end.get('total_secs')}s  {extra}")


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    for path in argv:
        print(f"\n=== {path} ===")
        recs = load(path)
        phases(recs)
        memory(recs)
        assets(recs)
        summary(recs)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
