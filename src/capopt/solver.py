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
from .parallel import Node, _branch, _lp_node, solve_parallel
from .solve import new_highs


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


class _LPTree:
    """E16: a best-first LP tree for one last year L, grown one node at a time in the master's idle time, so a last
    year whose root LP bound beats the incumbent can still be pruned without its own MIP solve."""

    def __init__(self, inst: I.Instance, L: int, encoding: str, balance: str, max_expansions: int = 500):
        self.model = last_year_model(inst, L, encoding, balance)
        root = _lp_node(self.model, [])
        self.heap: list[Node] = [] if root is None else [root]
        self.expansions, self.max_expansions, self.stuck = 0, max_expansions, False

    @property
    def closed(self) -> bool:
        return not self.heap and not self.stuck

    def step(self, cutoff: float) -> None:
        import heapq
        while self.heap and self.heap[0].bound >= cutoff:
            heapq.heappop(self.heap)
        if not self.heap or self.stuck or self.expansions >= self.max_expansions:
            return
        node = heapq.heappop(self.heap)
        if not node.frac:  # an LP-integral node that beats the cutoff: only the MIP can settle this last year
            heapq.heappush(self.heap, node)
            self.stuck = True
            return
        self.expansions += 1
        for child in _branch(self.model, node):
            if child is not None and child.bound < cutoff:
                heapq.heappush(self.heap, child)


def lp_bound(inst: I.Instance, L: int) -> float:
    """LP relaxation bound of MIP(L); +inf if no schedule can end by L."""
    try:
        m = last_year_model(inst, L, "pulse", "recursive")
    except ValueError:
        return np.inf
    h = new_highs(1)
    h.setOptionValue("output_flag", False)
    h.setOptionValue("solve_relaxation", True)
    m.builder.to_highs(h)
    h.run()
    if h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
        return np.inf
    return m.user(h.getInfo().objective_function_value)


def solve(name: str, workers: int | None = None, time_limit: float = 900.0, n_leaves: int | None = 60,
          encoding: str = "step", balance: str = "cumulative", slice_seconds: float | None = None,
          lns_workers: int = 0, lns_seconds: float = 30.0, cut_rounds: int = 0, quick_seconds: float = 1.5,
          leaf_cutoff: bool = False, rel_gap: float = 1e-9, seed: int = 0, cutoff_after: float | None = None,
          sb_candidates: int = 0, lns_big: bool = True, warm_start: str | None = None,
          lns_after_rampup: bool = False, lns_patience: float | None = None, lns_alns: bool = True,
          lns_first: float = 0.0, steal_after: float | None = None, race_after: float | None = None,
          proof_options: dict | None = None) -> Result:
    """capopt's default pipeline (ladder rung I9: split-and-solve, 60 leaves, 1.5 s quick try, no static leaf cutoff).
    See docs/EXPERIMENTS.md for why each default was chosen.
    lns_big: the big-project LNS neighbourhood (E13), on by default (it only acts when lns_workers > 0).
    lns_alns: adaptive LNS neighbourhood selection (E20), on by default.
    warm_start: path to a solution JSON with "starts" (e.g. a previous plan); used as the first incumbent if feasible.
    The time counted includes loading it."""
    t0 = time.time()
    inst = I.load(name)
    log: list[str] = []
    status: dict[int, str] = {}
    if not inst.penalised and inst.cash_cap is None:  # idle cash free and uncapped: L = T-1 alone is exact
        L_order = [inst.T - 1]
        others: list[int] = []
    else:
        L0 = inst.min_last_year()
        L_order = [L0]
        others = list(range(L0 + 1, inst.T))
    lp: dict[int, float] = {}
    queue = list(others)
    trees: dict[int, _LPTree] = {}
    tree_pruned: set[int] = set()

    def idle(best: float) -> None:  # LP-bound one more last year while workers are busy
        if queue:
            L = queue.pop(0)
            lp[L] = lp_bound(inst, L)
            return
        if best == np.inf:
            return
        cut = best - rel_gap * abs(best)
        for L in others:  # E16: grow an LP tree for a last year the root LP could not prune
            if L in tree_pruned or lp.get(L, np.inf) >= cut:
                continue
            if L not in trees:
                trees[L] = _LPTree(inst, L, encoding, balance)
            tree = trees[L]
            tree.step(cut)
            if tree.closed:
                tree_pruned.add(L)
                lp[L] = max(lp[L], cut)
                log.append(f"L={L}: pruned by an LP tree of {tree.expansions} expansions in idle time")
                continue
            if tree.stuck or tree.expansions >= tree.max_expansions:
                continue
            return

    best_obj, best_starts, dual, trace = np.inf, None, np.inf, []
    if warm_start is not None:
        import json
        from pathlib import Path
        st = inst.starts_from_dict(json.loads(Path(warm_start).read_text(encoding="utf-8"))["starts"])
        ev = evaluate(inst, st)
        if ev["feasible"]:
            best_obj, best_starts = float(ev["objective"]), st
            trace.append((time.time() - t0, best_obj))
            log.append(f"warm start: objective {best_obj:.6f}")
            Lw = int(ev["last_build_year_index"])
            if others and Lw in others:  # solve the warm schedule's own last year first: LNS can work around it
                L_order = [Lw]
                others = [L for L in range(L0, inst.T) if L != Lw]
                queue = list(others)
                log.append(f"warm start ends in L={Lw}: solved first")
        else:
            log.append("warm start: infeasible for this instance, ignored")
    proven_all = True
    for L in L_order:
        r = solve_parallel(name, L, workers, n_leaves, encoding, balance, time_limit,  # relative to the shared t0
                           incumbent=best_starts, t0=t0, idle=idle, slice_seconds=slice_seconds,
                           lns_workers=lns_workers, lns_seconds=lns_seconds, cut_rounds=cut_rounds,
                           quick_seconds=quick_seconds, leaf_cutoff=leaf_cutoff, rel_gap=rel_gap, seed=seed,
                           cutoff_after=cutoff_after, sb_candidates=sb_candidates, lns_big=lns_big,
                           lns_after_rampup=lns_after_rampup, lns_patience=lns_patience, lns_alns=lns_alns,
                           lns_first=lns_first, steal_after=steal_after, race_after=race_after,
                           proof_options=proof_options)
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
    survivors = [L for L in others if lp[L] < best_obj - rel_gap * abs(best_obj)]
    for L in others:
        status[L] = "pruned (LP)" if L not in survivors else status.get(L, "open")
    for L in survivors:
        # a surviving last year is a pure proof against a good incumbent: same tree settings, cutoff from the start
        # (time_limit is relative to the shared t0; subtracting the elapsed time here used to count it twice)
        r = solve_parallel(name, L, workers, n_leaves, encoding, balance, max(time.time() - t0 + 1.0, time_limit),
                           incumbent=best_starts, t0=t0, slice_seconds=slice_seconds, leaf_cutoff=leaf_cutoff,
                           rel_gap=rel_gap, seed=seed, cutoff_after=0.0, sb_candidates=sb_candidates,
                           steal_after=steal_after, race_after=race_after, proof_options=proof_options)
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
    return Result(name, best_obj, starts, proven_all and dual >= best_obj - rel_gap * abs(best_obj), dual,
                  time.time() - t0, mono, status, log)


def solve_sequential(name: str, encoding: str = "step", balance: str = "cumulative", time_limit: float = 900.0,
                     threads: int = 1) -> Result:
    """The same decomposition with one plain HiGHS run per last year (no split-and-solve). Ladder rungs I1/I2."""
    from .solve import Trace
    from .solve import solve as highs_solve
    t0 = time.time()
    inst = I.load(name)
    tr = Trace(t0=time.perf_counter())
    L0 = inst.min_last_year() if (inst.penalised or inst.cash_cap is not None) else inst.T - 1
    m = last_year_model(inst, L0, encoding, balance)
    r = highs_solve(m, tr, threads=threads, time_limit=time_limit)
    best = tr.best
    proven = r.status == "Optimal"
    dual = r.dual_bound if r.dual_bound is not None else -np.inf
    status = {L0: "solved" if proven else "time limit"}
    log = [f"L={L0}: {r.status} {r.seconds:.1f}s nodes {r.nodes}"]
    if inst.penalised or inst.cash_cap is not None:  # LP-bound every other last year; solve the survivors
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
