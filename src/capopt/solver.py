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

import os
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


def _warm_starts(inst: I.Instance, starts: dict) -> np.ndarray:
    """Start years from a plan's {id: year} map; projects the plan does not know start at their earliest option."""
    return np.array([int(starts.get(pid, a[0])) for pid, a in zip(inst.ids, inst.allowed)], dtype=np.int64)


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


def screen_last_years(inst: I.Instance, Ls: list[int], cutoff: float = np.inf, threads: int | None = None,
                      log: list[str] | None = None) -> dict[int, float]:
    """E37: a lower bound on MIP(L) for every L in Ls. The O(T) cash floor (bounds.cash_floor_bound) settles each L it
    prunes at `cutoff`; the rest get their LP bound, solved in threads (HiGHS releases the GIL, so 12 LPs take about
    0.5 s instead of 3.4 s)."""
    from concurrent.futures import ThreadPoolExecutor

    from .bounds import cash_floor_bound
    t0 = time.time()
    out = {L: cash_floor_bound(inst, L) for L in Ls}
    need = [L for L in Ls if out[L] < cutoff]
    if need:
        new_highs(1)  # set the process-wide scheduler once, before threads create their own instances
        with ThreadPoolExecutor(min(len(need), threads or os.cpu_count() or 1)) as ex:
            for L, b in zip(need, ex.map(lambda L: lp_bound(inst, L), need)):
                out[L] = max(out[L], b)
    if log is not None:
        log.append(f"screen: {len(Ls)} last years, {len(Ls) - len(need)} settled by the cash floor, {len(need)} LPs, "
                   f"{time.time() - t0:.2f}s")
    return out


def repair_schedule(inst: I.Instance, starts: np.ndarray, time_limit: float = 10.0,
                    log: list[str] | None = None) -> np.ndarray | None:
    """E38: make a previous plan feasible for an edited instance, changing as little as possible. Starts that are no
    longer allowed move to the nearest allowed year. If the balance still goes negative (spend went up), a sub-MIP
    re-times only the projects that are active around the first negative year, plus the moved ones, with everything
    else fixed; the window widens until the sub-MIP finds a schedule. None if that fails within the time limit."""
    from .parallel import _fix_others, _highs
    t0 = time.time()
    st = np.array([int(a[np.argmin(np.abs(a - int(s)))]) for a, s in zip(inst.allowed, starts)], dtype=np.int64)
    moved = st != np.asarray(starts)
    if evaluate(inst, st)["feasible"]:
        if log is not None and moved.any():
            log.append(f"warm start repaired: {int(moved.sum())} starts moved to the nearest allowed year")
        return st
    spend = np.zeros(inst.T)
    for p, s0 in enumerate(st):
        sp = inst.spend[p][:max(0, inst.T - s0)]
        spend[s0:s0 + len(sp)] += sp
    net = np.cumsum(inst.F.astype(float) - spend)
    bad = np.nonzero(net < 0)[0]
    year = int(bad[0]) if len(bad) else int(st.max())
    ends = st + inst.last_nz
    Lp = max(inst.min_last_year(), int(ends.max()))
    # the plan's own last year first (a later one charges holding cost for extra years and skews the objective)
    for L, r in [(Lp, 2), (Lp, 4), (Lp, 8), (Lp, inst.T), (min(inst.T - 1, Lp + 2), inst.T)]:
        free = moved | ((st <= year + r) & (ends >= year - r))
        model = last_year_model(inst, L, "step", "cumulative")
        h = _highs(model, _fix_others(st, free), False, max(0.5, min(5.0, time_limit - (time.time() - t0))))
        if h is None:
            continue
        h.setOptionValue("mip_rel_gap", 1e-4)  # E38: polish, not just repair (1e-2 lost 0.5-1% of the plan's value)
        h.run()
        sol = h.getSolution()
        if sol.value_valid:
            cand = model.starts_from_solution(np.asarray(sol.col_value))
            if evaluate(inst, cand)["feasible"]:
                if log is not None:
                    log.append(f"warm start repaired: {int(free.sum())} projects re-timed around year {year} "
                               f"in {time.time() - t0:.1f}s")
                return cand
        if time.time() - t0 > time_limit:
            break
    return None


PRESETS: dict[str, dict] = {}  # E37: filled below the pipeline; see docs/EXPERIMENTS.md E37


def solve(name: str, workers: int | None = None, time_limit: float = 900.0, n_leaves: int | None = 60,
          encoding: str = "step", balance: str = "cumulative", slice_seconds: float | None = None,
          lns_workers: int = 0, lns_seconds: float = 30.0, cut_rounds: int = 0, quick_seconds: float = 1.5,
          leaf_cutoff: bool = False, rel_gap: float = 1e-9, seed: int = 0, cutoff_after: float | None = None,
          sb_candidates: int = 0, lns_big: bool = True, warm_start: str | None = None,
          lns_after_rampup: bool = False, lns_patience: float | None = None, lns_alns: bool = True,
          lns_first: float = 0.0, steal_after: float | None = None, race_after: float | None = None,
          proof_options: dict | None = None, concurrent_proofs: bool = True,
          low_priority_proofs: bool = True, lp_screen: bool = False, preset: str | None = None,
          repair_warm: bool = True, rc_fix: bool = False, lns_rins: bool = False,
          primal_options: dict | None = None, lns_wait_incumbent: bool = False, lns_max_size: int = 45,
          root_extend: bool = False) -> Result:
    """capopt's default pipeline (ladder rung I9: split-and-solve, 60 leaves, 1.5 s quick try, no static leaf cutoff).
    See docs/EXPERIMENTS.md for why each default was chosen.
    lns_big: the big-project LNS neighbourhood (E13), on by default (it only acts when lns_workers > 0).
    lns_alns: adaptive LNS neighbourhood selection (E20), on by default.
    warm_start: path to a solution JSON with "starts" (e.g. a previous plan); used as the first incumbent if feasible.
    The time counted includes loading it.
    concurrent_proofs (E34): with a warm incumbent, LP-bound every other last year first and start the proofs of the
    ones the LP cannot prune at once, in background threads with their own worker pools, alongside the main last year
    (whose tree leaves workers idle in its tail). Sound: a last year proven against the warm incumbent stays proven when
    the main solve improves the incumbent, and a better incumbent can only shrink the set of survivors.
    low_priority_proofs (E36): run those concurrent proofs below normal OS priority, so they use only idle cores.
    lp_screen (E37): with a warm incumbent, bound every last year up front (cash floor, then threaded LPs) and return
    at once when that already certifies the incumbent within rel_gap; last years the screen prunes are never solved.
    repair_warm (E38): a warm schedule that is infeasible for this instance (an edited model) is repaired with
    repair_schedule instead of being dropped; projects missing from it start at their earliest allowed year.
    rc_fix (E40): tighten start windows by reduced-cost fixing at the target cutoff before each tree (rc_fixings).
    lns_rins (E39): LNS also frees the projects where the incumbent and the root LP disagree.
    root_extend (E43): a leaf still in HiGHS's root node at the end of its slice gets a longer slice, not a re-split.
    lns_max_size (E39): cap on the projects one LNS neighbourhood frees (45 before E39).
    lns_wait_incumbent (E39): on a cold start the LNS workers join the tree until the first schedule appears.
    primal_options (E39): HiGHS options for the leaves that search without the incumbent cutoff.
    preset (E37): a named option set from PRESETS ("1pct", "0.5pct") that stops at a certified gap. Arguments left at
    this function's defaults take the preset's values; any other value passed explicitly wins over the preset."""
    if preset is not None:
        if preset not in PRESETS:
            raise ValueError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
        import inspect
        given = {k: v for k, v in locals().items() if k in inspect.signature(solve).parameters}
        defaults = {k: p.default for k, p in inspect.signature(solve).parameters.items()}
        opts = {k: v for k, v in given.items() if k not in ("name", "preset") and v != defaults[k]}
        return solve(name, **{**PRESETS[preset], **opts})
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
        for L, ev in cancels.items():  # E38: a concurrent proof the improved incumbent makes unnecessary
            if not ev.is_set() and lp.get(L, -np.inf) >= best - rel_gap * abs(best):
                ev.set()
                log.append(f"L={L}: concurrent proof cancelled, LP-pruned by the improved incumbent at "
                           f"{time.time() - t0:.1f}s")
        if queue:
            L = queue.pop(0)
            from .bounds import cash_floor_bound
            floor = cash_floor_bound(inst, L)  # E37: no model build or LP when the cash floor already prunes L
            lp[L] = floor if floor >= best - rel_gap * abs(best) else max(floor, lp_bound(inst, L))
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
        st = _warm_starts(inst, json.loads(Path(warm_start).read_text(encoding="utf-8"))["starts"])
        ev = evaluate(inst, st)
        if not ev["feasible"] and repair_warm:  # E38: an edited instance: repair the previous plan, don't drop it
            fixed = repair_schedule(inst, st, log=log)
            if fixed is not None:
                st, ev = fixed, evaluate(inst, fixed)
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
    if lp_screen and best_starts is not None:  # E37: bound every last year now; maybe the warm schedule is enough
        cut = best_obj - rel_gap * abs(best_obj)
        lp.update(screen_last_years(inst, L_order + others, cut, workers, log))
        queue = []
        if min(lp.values()) >= cut:
            log.append("the screen certifies the warm schedule: no MIP solved")
            status = {L: "pruned (LP)" for L in lp}
            return Result(name, float(best_obj), inst.starts_to_dict(best_starts), True, float(min(lp.values())),
                          time.time() - t0, trace, status, log)
        for L in [L for L in L_order if lp[L] >= cut]:  # the warm schedule's own last year is settled already:
            L_order.remove(L)                              # it joins the pruned ones (status, bound) at the end
            others.append(L)
            log.append(f"L={L}: pruned by the screen")
        if not L_order:  # solve the most promising surviving last year instead
            L_order = [min((L for L in others if lp[L] < cut), key=lambda L: lp[L])]
            others.remove(L_order[0])
    proven_all = True
    background: dict = {}  # E34: L -> future of its concurrent proof
    cancels: dict = {}  # E38: L -> event that stops its concurrent proof
    if concurrent_proofs and best_starts is not None and others:
        from concurrent.futures import ThreadPoolExecutor
        while queue:  # LP-bound every other last year now (seconds), so the survivors are known up front
            idle(best_obj)
        early = [L for L in others if lp[L] < best_obj - rel_gap * abs(best_obj)]
        if early:
            import threading
            tpe = ThreadPoolExecutor(len(early))
            for L in early:
                cancels[L] = threading.Event()
                background[L] = tpe.submit(
                    solve_parallel, name, L, workers, n_leaves, encoding, balance, time_limit,
                    incumbent=best_starts, t0=t0, slice_seconds=slice_seconds, leaf_cutoff=leaf_cutoff,
                    rel_gap=rel_gap, seed=seed, cutoff_after=0.0, sb_candidates=sb_candidates,
                    steal_after=steal_after, race_after=race_after, proof_options=proof_options,
                    low_priority=low_priority_proofs, cancel=cancels[L], rc_fix=rc_fix)
            tpe.shutdown(wait=False)
            log.append(f"concurrent proofs started for L={early} at {time.time() - t0:.1f}s")
            others_idle = [L for L in others if L not in background]  # idle LP trees skip the ones being proven
            others[:] = others_idle + list(background)  # keep the full list for the final status table
            for L in background:
                tree_pruned.add(L)  # not pruned: exclude from idle trees; resolved from the background result below
    for L in L_order:
        r = solve_parallel(name, L, workers, n_leaves, encoding, balance, time_limit,  # relative to the shared t0
                           incumbent=best_starts, t0=t0, idle=idle, slice_seconds=slice_seconds,
                           lns_workers=lns_workers, lns_seconds=lns_seconds, cut_rounds=cut_rounds,
                           quick_seconds=quick_seconds, leaf_cutoff=leaf_cutoff, rel_gap=rel_gap, seed=seed,
                           cutoff_after=cutoff_after, sb_candidates=sb_candidates, lns_big=lns_big,
                           lns_after_rampup=lns_after_rampup, lns_patience=lns_patience, lns_alns=lns_alns,
                           lns_first=lns_first, steal_after=steal_after, race_after=race_after,
                           proof_options=proof_options, rc_fix=rc_fix, lns_rins=lns_rins,
                           primal_options=primal_options, lns_wait_incumbent=lns_wait_incumbent,
                           lns_max_size=lns_max_size, root_extend=root_extend)
        log += [f"L={L}: " + s for s in r.log] + [f"L={L}: obj {r.objective:.6f} proven {r.proven} {r.seconds:.1f}s"]
        status[L] = "solved" if r.proven else "time limit"
        proven_all &= r.proven
        trace += r.trace
        if r.objective < best_obj:
            best_obj, best_starts = r.objective, r.starts
        dual = min(dual, r.dual_bound)
    # finish bounding the remaining last years, then solve any that could still hold a better schedule
    if lp_screen and queue:  # E37: the rest in threads, floor first (a cold start has no cutoff for the screen early)
        lp.update(screen_last_years(inst, queue, best_obj - rel_gap * abs(best_obj), workers, log))
        queue = []
    while queue:
        idle(best_obj)
    idle_cancel_check = [L for L, ev in cancels.items() if not ev.is_set()
                         and lp.get(L, -np.inf) >= best_obj - rel_gap * abs(best_obj)]
    for L in idle_cancel_check:  # E38: the final incumbent prunes it: stop it instead of waiting for it
        cancels[L].set()
        log.append(f"L={L}: concurrent proof cancelled, LP-pruned by the final incumbent")
    for L, fut in background.items():  # E34: collect the concurrent proofs
        r = fut.result()
        tree_pruned.discard(L)
        if cancels[L].is_set():  # E38: settled by its LP bound; whatever the cancelled proof reached is not needed
            status[L] = "pruned (LP)"
            dual = min(dual, lp[L])
            trace += r.trace
            if r.objective < best_obj:
                best_obj, best_starts = r.objective, r.starts
            continue
        log += [f"L={L}: " + s for s in r.log] + [f"L={L}: obj {r.objective:.6f} proven {r.proven} {r.seconds:.1f}s"]
        status[L] = "solved" if r.proven else "time limit"
        proven_all &= r.proven
        trace += r.trace
        if r.objective < best_obj:
            best_obj, best_starts = r.objective, r.starts
        dual = min(dual, r.dual_bound)
    survivors = [L for L in others if L not in background and lp[L] < best_obj - rel_gap * abs(best_obj)]
    for L in others:
        if L not in background:
            status[L] = "pruned (LP)" if L not in survivors else status.get(L, "open")
    for L in survivors:
        if time.time() - t0 >= time_limit:  # E37: out of time: report it open (its LP bound) instead of spawning a pool
            status[L] = "time limit"
            proven_all = False
            dual = min(dual, lp[L])
            continue
        # a surviving last year is a pure proof against a good incumbent: same tree settings, cutoff from the start
        # (time_limit is relative to the shared t0; subtracting the elapsed time here used to count it twice)
        r = solve_parallel(name, L, workers, n_leaves, encoding, balance, max(time.time() - t0 + 1.0, time_limit),
                           incumbent=best_starts, t0=t0, slice_seconds=slice_seconds, leaf_cutoff=leaf_cutoff,
                           rel_gap=rel_gap, seed=seed, cutoff_after=0.0, sb_candidates=sb_candidates,
                           steal_after=steal_after, race_after=race_after, proof_options=proof_options,
                           rc_fix=rc_fix)
        log.append(f"L={L}: obj {r.objective:.6f} proven {r.proven} {r.seconds:.1f}s")
        status[L] = "solved" if r.proven else "time limit"
        proven_all &= r.proven
        trace += r.trace
        if r.objective < best_obj:
            best_obj, best_starts = r.objective, r.starts
        dual = min(dual, r.dual_bound)
    if others:
        log.append(f"LP-pruned last years: {len(others) - len(survivors) - len(background)} of {len(others)}")
        dual = min([dual] + [lp[L] for L in others if L not in survivors and L not in background and lp[L] < dual])
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
    # plain Python types: a numpy bound (the cash floor) used to make `proven` a numpy bool, which json cannot write
    return Result(name, float(best_obj), starts, bool(proven_all and dual >= best_obj - rel_gap * abs(best_obj)),
                  float(dual), time.time() - t0, mono, status, log)


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


# E37 presets: stop at a certified gap (incumbent vs proven bound over every last year) as fast as possible.
PRESETS.update({
    # E39/E42: 12 LNS workers (cold is LNS-limited: -11% pooled over 5 seeds), started after the ramp-up so warm
    # strong branching has all workers (-10% warm 0.5% on the envelope scenarios; cold has no incumbent to search yet)
    "1pct": dict(rel_gap=1e-2, lp_screen=True, quick_seconds=0.0, n_leaves=24, sb_candidates=4, lns_workers=12,
                 lns_seconds=1800.0, lns_after_rampup=True, cutoff_after=10.0, slice_seconds=10.0),
    "0.5pct": dict(rel_gap=5e-3, lp_screen=True, quick_seconds=0.0, n_leaves=24, sb_candidates=4, lns_workers=12,
                   lns_seconds=1800.0, lns_after_rampup=True, cutoff_after=10.0, slice_seconds=10.0),
})
