"""capopt's solve pipeline.

Penalised objective
    1. L0 = Lmin (cumulative funding first covers total spend).
    2. Solve MIP(L0) to proven optimality with parallel split-and-solve.
    3. While the workers run, the master LP-bounds every other L. A last year whose LP bound is not below the
       incumbent cannot contain a better schedule. Any L that survives gets its own split-and-solve run.
    OPT = min over L of MIP(L) (see formulation.py), so the result is proven when every L is solved or pruned.
No-penalty objective
    L = T-1 alone is exact: one split-and-solve run.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import highspy
import numpy as np

from . import instance as I
from .evaluate import evaluate
from .formulation import last_year_model
from .parallel import solve_parallel


@dataclass
class Result:
    instance: str
    objective: float
    starts: dict[str, int] | None
    proven: bool
    dual_bound: float
    seconds: float
    trace: list[tuple[float, float]]
    last_years: dict[int, str] = field(default_factory=dict)  # L -> "solved" | "pruned (LP)" | "infeasible"
    log: list[str] = field(default_factory=list)


def lp_bound(inst: I.Instance, L: int) -> float:
    """LP relaxation bound of MIP(L); +inf if no schedule can end by L."""
    try:
        m = last_year_model(inst, L, "pulse", "recursive")
    except ValueError:
        return np.inf
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("solve_relaxation", True)
    m.builder.to_highs(h)
    h.run()
    if h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
        return np.inf
    return m.user(h.getInfo().objective_function_value)


def solve(name: str, workers: int | None = None, time_limit: float = 900.0, n_leaves: int | None = None,
          encoding: str = "step", balance: str = "cumulative", slice_seconds: float | None = None,
          lns_workers: int = 0, lns_seconds: float = 30.0) -> Result:
    t0 = time.time()
    inst = I.load(name)
    log: list[str] = []
    status: dict[int, str] = {}
    if not inst.penalised:
        L_order = [inst.T - 1]
        others: list[int] = []
    else:
        L0 = inst.min_last_year()
        L_order = [L0]
        others = list(range(L0 + 1, inst.T))
    lp: dict[int, float] = {}
    queue = list(others)

    def idle(best: float) -> None:  # LP-bound one more last year while workers are busy
        if queue:
            L = queue.pop(0)
            lp[L] = lp_bound(inst, L)

    best_obj, best_starts, dual, trace = np.inf, None, np.inf, []
    proven_all = True
    for L in L_order:
        r = solve_parallel(name, L, workers, n_leaves, encoding, balance, time_limit - (time.time() - t0),
                           incumbent=best_starts, t0=t0, idle=idle, slice_seconds=slice_seconds,
                           lns_workers=lns_workers, lns_seconds=lns_seconds)
        log += [f"L={L}: " + s for s in r.log] + [f"L={L}: obj {r.objective:.6f} proven {r.proven} {r.seconds:.1f}s"]
        status[L] = "solved" if r.proven else "time limit"
        proven_all &= r.proven
        trace += r.trace
        if r.objective < best_obj:
            best_obj, best_starts = r.objective, r.starts
        dual = min(dual, r.dual_bound)
    # finish bounding the remaining last years, then solve any that could still hold a better schedule
    while queue:
        idle(best_obj)
    survivors = [L for L in others if lp[L] < best_obj - 1e-9 * abs(best_obj)]
    for L in others:
        status[L] = "pruned (LP)" if L not in survivors else status.get(L, "open")
    for L in survivors:
        r = solve_parallel(name, L, workers, n_leaves, encoding, balance, max(1.0, time_limit - (time.time() - t0)),
                           incumbent=best_starts, t0=t0, slice_seconds=slice_seconds)
        log.append(f"L={L}: obj {r.objective:.6f} proven {r.proven} {r.seconds:.1f}s")
        status[L] = "solved" if r.proven else "time limit"
        proven_all &= r.proven
        trace += r.trace
        if r.objective < best_obj:
            best_obj, best_starts = r.objective, r.starts
        dual = min(dual, r.dual_bound)
    if others:
        log.append(f"LP-pruned last years: {len(others) - len(survivors)} of {len(others)}")
        dual = min([dual] + [lp[L] for L in others if L not in survivors and lp[L] < dual])
    trace.sort()
    mono, run = [], np.inf
    for t, v in trace:
        if v < run:
            run = v
            mono.append((t, v))
    starts = None
    if best_starts is not None:
        ev = evaluate(inst, best_starts)
        assert ev["feasible"] and abs(ev["objective"] - best_obj) <= 1e-9 * abs(best_obj)
        starts = inst.starts_to_dict(best_starts)
    return Result(name, best_obj, starts, proven_all and dual >= best_obj - 1e-9 * abs(best_obj), dual,
                  time.time() - t0, mono, status, log)


def solve_sequential(name: str, encoding: str = "step", balance: str = "cumulative", time_limit: float = 900.0,
                     threads: int = 1) -> Result:
    """The same decomposition with one plain HiGHS run per last year (no split-and-solve). Ladder rungs I1/I2."""
    from .solve import Trace
    from .solve import solve as highs_solve
    t0 = time.time()
    inst = I.load(name)
    tr = Trace(t0=time.perf_counter())
    L0 = inst.min_last_year() if inst.penalised else inst.T - 1
    m = last_year_model(inst, L0, encoding, balance)
    r = highs_solve(m, tr, threads=threads, time_limit=time_limit)
    best = tr.best
    proven = r.status == "Optimal"
    dual = r.dual_bound if r.dual_bound is not None else -np.inf
    status = {L0: "solved" if proven else "time limit"}
    log = [f"L={L0}: {r.status} {r.seconds:.1f}s nodes {r.nodes}"]
    if inst.penalised:  # LP-bound every other last year; solve the survivors one after another
        for L in range(L0 + 1, inst.T):
            if lp_bound(inst, L) >= tr.best - 1e-9 * abs(tr.best):
                status[L] = "pruned (LP)"
                continue
            rL = highs_solve(last_year_model(inst, L, encoding, balance), tr, threads=threads,
                             time_limit=max(1.0, time_limit - (time.time() - t0)))
            status[L] = "solved" if rL.status == "Optimal" else "time limit"
            proven &= rL.status == "Optimal"
            if rL.dual_bound is not None:
                dual = min(dual, rL.dual_bound)
            log.append(f"L={L}: {rL.status} {rL.seconds:.1f}s nodes {rL.nodes}")
        best = tr.best
    trace = [(t, v) for t, v, _ in tr.points]
    starts = inst.starts_to_dict(tr.best_starts) if tr.best_starts is not None else None
    return Result(name, best, starts, bool(proven), dual, time.time() - t0, trace, status, log)


def solve_reference_highs(name: str, time_limit: float = 900.0, threads: int = 0) -> Result:
    """Baseline B0: stock HiGHS on the faithful port of the MindOpt reference model."""
    from .formulation import reference_bigm
    from .solve import Trace
    from .solve import solve as highs_solve
    t0 = time.time()
    inst = I.load(name)
    tr = Trace(t0=time.perf_counter())
    m = reference_bigm(inst)
    r = highs_solve(m, tr, threads=threads, time_limit=time_limit)
    trace = [(t, v) for t, v, _ in tr.points]
    starts = inst.starts_to_dict(tr.best_starts) if tr.best_starts is not None else None
    dual = r.dual_bound if r.dual_bound is not None else -np.inf
    return Result(name, tr.best, starts, r.status == "Optimal", dual, time.time() - t0, trace, {},
                  [f"{r.status} {r.seconds:.1f}s nodes {r.nodes}"])
