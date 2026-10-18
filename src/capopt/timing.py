"""Timing controls for optioneering: forced start windows and sequencing (precedence) rules.

Forced start windows
    `with_windows(raw, {project: (a, b)})` restricts each named project's start to [a, b], inclusive (start-year
    indices, or calendar years with `calendar=True`); a = b pins a single year. The optimiser stays free inside the
    window, subject to every other constraint and layer. It is an instance transform: the project's allowed starts
    (and their PV) are cut to the window, so every solver component (the decomposition, the cash-floor bound, the
    screen, ALNS, warm-start repair) handles it unchanged. A window that leaves a project no allowed start is an error,
    not a silent relaxation. The windows are recorded under raw["timing"]["windows"] for provenance.

Sequencing rules
    An instance may carry a "precedence" section: [{"a": id, "b": id, "lo": int | null, "hi": int | null, "label"}],
    meaning lo <= start_b - start_a <= hi (null = no bound on that side). The builders cover the usual rule types:
        after_start(a, b, lag)               B starts no earlier than `lag` years after A starts (lag 0: not before A)
        after_finish(raw, a, b, lag, within) B starts no earlier than A's completion (start + duration) plus `lag`;
                                             with `within` = k, also no later than k years after that (k = 0: exactly
                                             when A finishes)
        not_after(a, b)                      B starts no later than A
        lag_between(a, b, lo, hi)            any general lag window
    `evaluate` rejects a schedule that breaks a rule. In the fixed-last-year model each bound is written in the strong
    time-indexed form: start_b - start_a >= lo holds iff [start_b <= t] <= [start_a <= t - lo] for every year t. With
    the step ("started-by") encoding that is z_b,t <= z_a,t-lo, a two-term row per year; the pulse encoding gets the
    same rows summed over options. The rules restrict start choices only and do not depend on the last build year L, so
    MIP(L) still equals the true objective at a schedule's own L and over-estimates it at a later L: the decomposition
    OPT = min over L of MIP(L) is unchanged. Earliest starts implied by chains of rules tighten Lmin
    (`earliest_starts`). tests/test_timing.py checks every rule type against brute force.
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


# ---- sequencing (precedence) rules -----------------------------------------------------------------------------------

def lag_between(a: str | int, b: str | int, lo: int | None = None, hi: int | None = None, label: str = "") -> dict:
    """lo <= start_b - start_a <= hi (None: unbounded on that side)."""
    if lo is None and hi is None:
        raise ValueError("a sequencing rule needs lo or hi")
    if lo is not None and hi is not None and lo > hi:
        raise ValueError(f"empty lag window [{lo}, {hi}]")
    return {"a": a, "b": b, "lo": None if lo is None else int(lo), "hi": None if hi is None else int(hi),
            "label": label}


def after_start(a: str | int, b: str | int, lag: int = 0, label: str = "") -> dict:
    """B cannot start before A starts (plus `lag` years)."""
    return lag_between(a, b, lo=lag, label=label or f"{b} starts >= {lag}y after {a} starts")


def duration(raw: dict, key: str | int) -> int:
    p = raw["projects"][_index(raw, key)]
    return int(p.get("duration", len(p["spend_S"])))


def after_finish(raw: dict, a: str | int, b: str | int, lag: int = 0, within: int | None = None,
                 label: str = "") -> dict:
    """B cannot start before A finishes (completion = start + duration, the year after A's last build year), plus
    `lag`; with `within` = k, B also starts no later than k years after that (k = 0: B starts exactly when A finishes)."""
    lo = duration(raw, a) + int(lag)
    return lag_between(a, b, lo=lo, hi=None if within is None else lo + int(within),
                       label=label or f"{b} starts {'>= ' if within is None else ''}{lag}y after {a} finishes"
                       + ("" if within is None else f" (within {within}y)"))


def not_after(a: str | int, b: str | int, label: str = "") -> dict:
    """B cannot start after A (B starts no later than A)."""
    return lag_between(a, b, hi=0, label=label or f"{b} starts no later than {a}")


def with_precedence(raw: dict, rules: list[dict], name: str | None = None) -> dict:
    """A copy of an instance dict with these sequencing rules added to its "precedence" section."""
    out = copy.deepcopy(raw)
    for r in rules:
        _index(out, r["a"]), _index(out, r["b"])  # unknown projects fail here, not inside a solve
    out["precedence"] = out.get("precedence", []) + [dict(r) for r in rules]
    if name is not None:
        out["name"] = name
    return out


def parse(raw: dict) -> list[tuple[int, int, int | None, int | None]]:
    """The instance's rules as (index a, index b, lo, hi)."""
    out = []
    for r in raw.get("precedence") or []:
        a, b = _index(raw, r["a"]), _index(raw, r["b"])
        if a == b:
            raise ValueError(f"rule {r.get('label', '')!r} links a project to itself")
        out.append((a, b, r.get("lo"), r.get("hi")))
    return out


def violation(prec, starts) -> str | None:
    """A description of the first rule the schedule breaks, or None."""
    for a, b, lo, hi in prec:
        d = int(starts[b]) - int(starts[a])
        if (lo is not None and d < lo) or (hi is not None and d > hi):
            return f"sequencing: start[{b}] - start[{a}] = {d} outside [{lo}, {hi}]"
    return None


def violators(prec, starts) -> set[int]:
    """Every project in a rule the schedule breaks."""
    out: set[int] = set()
    for a, b, lo, hi in prec:
        d = int(starts[b]) - int(starts[a])
        if (lo is not None and d < lo) or (hi is not None and d > hi):
            out |= {a, b}
    return out


def _bounds(prec):
    """(x, y, lag) triples: start_y - start_x >= lag, from every rule's lo and hi side."""
    out = []
    for a, b, lo, hi in prec:
        if lo is not None:
            out.append((a, b, int(lo)))
        if hi is not None:
            out.append((b, a, -int(hi)))
    return out


def earliest_starts(allowed: list[np.ndarray], prec) -> np.ndarray | None:
    """The earliest start each project can have under the rules (each start snapped up to an allowed one), by
    propagation to a fixed point; None when the rules cannot all hold."""
    es = np.array([int(a[0]) for a in allowed], dtype=np.int64)
    trip = _bounds(prec)
    for _ in range(len(allowed) * 4 + 10):
        changed = False
        for x, y, lag in trip:
            need = es[x] + lag
            if es[y] < need:
                j = int(np.searchsorted(allowed[y], need))
                if j >= len(allowed[y]):
                    return None
                es[y] = int(allowed[y][j])
                changed = True
        if not changed:
            return es
    return None  # no fixed point: a cycle of rules that pushes starts later forever


class PrecedenceRows:
    """The rules' rows in one fixed-last-year model (built before the start blocks): start_y - start_x >= lag becomes,
    for every year t in y's options, [start_y <= t] - [start_x <= t - lag] <= 0. Each option s of y puts +1 on the rows
    with t >= s, each option s of x puts -1 on the rows with t - lag >= s, so the step encoding turns them into
    z_y,t - z_x,t-lag <= 0. Rows that can never bind (t - lag at or past x's last option) are skipped."""

    def __init__(self, b, inst, L: int, INF: float):
        opts = [a[a + z <= L] for a, z in zip(inst.allowed, inst.last_nz)]
        self.marks: list[list[tuple[int, int, float]]] = [[] for _ in range(inst.n)]  # p -> (row, threshold, coef)
        self.rows = 0
        for x, y, lag in _bounds(inst.precedence):
            ox, oy = opts[x], opts[y]
            if not len(ox) or not len(oy):
                continue  # the model build fails on that project anyway (it cannot finish by L)
            for t in range(int(oy[0]), int(oy[-1]) + 1):
                if t - lag >= ox[-1]:
                    break
                r = b.new_row(-INF, 0.0)
                self.marks[y].append((r, t, 1.0))
                self.marks[x].append((r, t - lag, -1.0))
                self.rows += 1

    def entries(self, p: int, s: int, e: dict[int, float]) -> None:
        for r, thr, coef in self.marks[p]:
            if s <= thr:
                e[r] = e.get(r, 0.0) + coef
