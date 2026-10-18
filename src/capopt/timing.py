"""Timing controls for optioneering: forced start windows and sequencing (precedence) rules.

Forced start windows
    `with_windows(raw, {project: (a, b)})` restricts each named project's start to [a, b], inclusive (start-year
    indices, or calendar years with `calendar=True`); a = b pins a single year. The optimiser stays free inside the
    window, subject to every other constraint and layer. It is an instance transform: the project's allowed starts
    (and their PV) are cut to the window, so every solver component (the decomposition, the cash-floor bound, the
    screen, ALNS, warm-start repair) handles it unchanged. A window that leaves a project no allowed start is an error,
    not a silent relaxation. The windows are recorded under raw["timing"]["windows"] for provenance.
"""
from __future__ import annotations

import copy

import numpy as np

# ---- forced start windows ------------------------------------------------------------------------------------------

def _index(raw: dict, key: str | int) -> int:
    if isinstance(key, (int, np.integer)):
        return int(key)
    for i, p in enumerate(raw["projects"]):
        if p["id"] == key:
            return i
    raise KeyError(f"no project {key!r}")


def with_windows(raw: dict, windows: dict[str | int, tuple[int, int]], calendar: bool = False,
                 name: str | None = None) -> dict:
    """A copy of an instance dict where each project in `windows` must start in [a, b] (inclusive). Keys are project
    ids or indices; a and b are start-year indices, or calendar years when `calendar` (raw["base_year"] is year 0).
    Raises ValueError when a window holds none of the project's allowed starts."""
    out = copy.deepcopy(raw)
    base = int(raw.get("base_year", 0)) if calendar else 0
    rec = out.setdefault("timing", {}).setdefault("windows", {})
    for key, (a, b) in windows.items():
        i = _index(out, key)
        p = out["projects"][i]
        lo, hi = int(a) - base, int(b) - base
        if lo > hi:
            raise ValueError(f"{p['id']}: empty window [{a}, {b}]")
        keep = [j for j, s in enumerate(p["allowed_starts"]) if lo <= s <= hi]
        if not keep:
            raise ValueError(f"{p['id']}: window [{a}, {b}] holds none of its allowed starts "
                             f"({p['allowed_starts'][0] + base}-{p['allowed_starts'][-1] + base})")
        p["allowed_starts"] = [p["allowed_starts"][j] for j in keep]
        p["pv_int"] = [p["pv_int"][j] for j in keep]
        rec[p["id"]] = [lo, hi]
    if name is not None:
        out["name"] = name
    return out


def pin(raw: dict, starts: dict[str | int, int], calendar: bool = False, name: str | None = None) -> dict:
    """Single-year windows (a = b): pin each project in `starts` to that start year."""
    return with_windows(raw, {k: (s, s) for k, s in starts.items()}, calendar, name)
