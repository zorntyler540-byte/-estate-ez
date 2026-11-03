"""Process RSS helpers for the catalog worker (parent stays small; children must exit)."""

from __future__ import annotations

import os
from collections.abc import Iterable


def process_rss_mb(pid: int | None = None) -> float | None:
    """Resident set size in MiB from ``/proc/<pid>/statm``. None if unavailable."""
    target = os.getpid() if pid is None else int(pid)
    try:
        with open(f"/proc/{target}/statm", encoding="ascii") as fh:
            parts = fh.read().split()
        pages = int(parts[1])
        page = int(os.sysconf("SC_PAGE_SIZE"))
        return pages * page / (1024 * 1024)
    except (OSError, IndexError, ValueError):
        return None


def child_pids(pid: int) -> list[int]:
    """Direct children of ``pid`` (Linux ``/proc``). Empty when not available."""
    try:
        with open(f"/proc/{pid}/task/{pid}/children", encoding="ascii") as fh:
            return [int(x) for x in fh.read().split() if x.isdigit()]
    except (OSError, ValueError):
        pass
    found: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for name in entries:
        if not name.isdigit():
            continue
        child = int(name)
        try:
            with open(f"/proc/{child}/stat", encoding="ascii") as fh:
                stat = fh.read()
            close = stat.rfind(")")
            if close < 0:
                continue
            fields = stat[close + 2 :].split()
            # After comm, field 2 is ppid (1-indexed field 4 of the whole stat line).
            if int(fields[1]) == pid:
                found.append(child)
        except (OSError, IndexError, ValueError):
            continue
    return found


def descendant_pids(pid: int) -> list[int]:
    """All descendants of ``pid``, breadth-first."""
    out: list[int] = []
    stack = list(child_pids(pid))
    seen = {pid}
    while stack:
        child = stack.pop()
        if child in seen:
            continue
        seen.add(child)
        out.append(child)
        stack.extend(child_pids(child))
    return out


def process_tree_rss_mb(pid: int | None = None) -> float:
    """Sum RSS of ``pid`` plus descendants — what a Railway cgroup roughly bills."""
    root = os.getpid() if pid is None else int(pid)
    total = 0.0
    for target in (root, *descendant_pids(root)):
        rss = process_rss_mb(target)
        if rss is not None:
            total += rss
    return total


def live_descendant_count(pid: int | None = None) -> int:
    root = os.getpid() if pid is None else int(pid)
    return len(descendant_pids(root))


def pids_still_running(pids: Iterable[int]) -> list[int]:
    live: list[int] = []
    for pid in pids:
        try:
            os.kill(int(pid), 0)
        except OSError:
            continue
        live.append(int(pid))
    return live
