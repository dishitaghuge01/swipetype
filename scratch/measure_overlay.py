#!/usr/bin/env python3
"""
scratch/measure_overlay.py

Measures memory (PSS, falling back to RSS) and CPU of the overlay
process TREE: swipetype_overlay.py plus every descendant (WebKit web
process, network process, bubblewrap wrappers).

Usage:
    python scratch/measure_overlay.py [seconds]      # default 10

Run it twice and compare:
  1. idle:    overlay running, swipe mode on, finger off the trackpad.
  2. swiping: start this, then swipe continuously for the whole window.

PSS counts shared memory pages proportionally, so summing it across
processes doesn't double-count shared libraries the way RSS does.
"""

import os
import sys
import time

PAGE_KB = os.sysconf("SC_PAGE_SIZE") / 1024
CLK_TCK = os.sysconf("SC_CLK_TCK")


def read_stat(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
    except OSError:
        return None
    rparen = data.rindex(")")
    comm = data[data.index("(") + 1:rparen]
    fields = data[rparen + 2:].split()
    ppid = int(fields[1])
    ticks = int(fields[11]) + int(fields[12])  # utime + stime
    return comm, ppid, ticks


def read_mem_kb(pid):
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Pss:"):
                    return int(line.split()[1]), "PSS"
    except OSError:
        pass
    try:
        with open(f"/proc/{pid}/statm") as f:
            return int(int(f.read().split()[1]) * PAGE_KB), "RSS"
    except OSError:
        return 0, "?"


def all_pids():
    return [int(p) for p in os.listdir("/proc") if p.isdigit()]


def find_overlay_root():
    me = os.getpid()
    for pid in all_pids():
        if pid == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if any(a.endswith(b"swipetype_overlay.py") for a in args):
            return pid
    return None


def descendants(root):
    children = {}
    for pid in all_pids():
        st = read_stat(pid)
        if st:
            children.setdefault(st[1], []).append(pid)
    found, stack = [root], [root]
    while stack:
        for child in children.get(stack.pop(), []):
            found.append(child)
            stack.append(child)
    return found


def sample(root):
    out = {}
    for pid in descendants(root):
        st = read_stat(pid)
        if st is None:
            continue
        comm, _ppid, ticks = st
        mem_kb, kind = read_mem_kb(pid)
        out[pid] = (comm, ticks, mem_kb, kind)
    return out


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    root = find_overlay_root()
    if root is None:
        print("No running swipetype_overlay.py found. Start the overlay first.")
        sys.exit(1)

    print(f"Overlay root PID {root}. Sampling for {seconds:.0f}s ...")
    s1 = sample(root)
    t1 = time.monotonic()
    time.sleep(seconds)
    s2 = sample(root)
    elapsed = time.monotonic() - t1

    total_cpu = 0.0
    total_mem = 0
    kind = "PSS"
    print(f"\n{'PID':>8}  {'process':<24} {'mem MB':>8}  {'CPU %':>6}")
    for pid, (comm, ticks2, mem_kb, k) in sorted(s2.items()):
        kind = k
        if pid in s1:
            cpu = (ticks2 - s1[pid][1]) / CLK_TCK / elapsed * 100.0
        else:
            cpu = 0.0
        total_cpu += cpu
        total_mem += mem_kb
        print(f"{pid:>8}  {comm:<24} {mem_kb / 1024:>8.1f}  {cpu:>6.1f}")
    print(f"{'':>8}  {'TOTAL (' + kind + ')':<24} {total_mem / 1024:>8.1f}  {total_cpu:>6.1f}")


if __name__ == "__main__":
    main()