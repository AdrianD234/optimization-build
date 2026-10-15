"""Parallel split-and-solve: capopt's own parallel branch-and-bound layer on top of (single-threaded) HiGHS.

HiGHS' MIP search is sequential, so on a 20-thread machine most of the hardware idles. We split the model into
disjoint subproblems ("leaves") with a best-first ramp-up tree whose nodes are LP relaxations, then solve leaves in
worker processes. Workers share one incumbent (value + schedule) through shared memory:

* each leaf starts with HiGHS' `objective_bound` = the shared incumbent (nodes that cannot beat it are pruned), and
* the interrupt callback stops a leaf as soon as its dual bound reaches the shared incumbent.

A branching decision is `(p, s, side)`: project p starts <= s (side 0) or > s (side 1). Decisions are translated to
column bounds for either encoding, so leaves can use any formulation.
"""
from __future__ import annotations

import heapq
import json
import multiprocessing as mp
import os
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

import highspy
import numpy as np

from . import instance as I
from .evaluate import evaluate, objective
from .formulation import Model, last_year_model
from .solve import new_highs, sink, sink_bound

Split = tuple[int, int, int]  # (project, start year, side): side 0 => start <= s, side 1 => start > s


def split_bounds(model: Model, splits: list[Split]) -> tuple[list[int], list[float], list[float]] | None:
    """Column bound changes for a list of decisions; None if the decisions leave a project with no option."""
    lo_opt = [0] * model.inst.n
    hi_opt = [len(o) - 1 for o in model.options]
    for p, s, side in splits:
        j = int(np.searchsorted(model.options[p], s, side="right")) - 1  # last option <= s
        if side == 0:
            hi_opt[p] = min(hi_opt[p], j)
        else:
            lo_opt[p] = max(lo_opt[p], j + 1)
    cols, lbs, ubs = [], [], []
    for p in {sp[0] for sp in splits}:
        lo, hi = lo_opt[p], hi_opt[p]
        if lo > hi:
            return None
        cs = model.start_cols[p]
        if model.encoding == "pulse":
            for j, c in enumerate(cs):
                if j < lo or j > hi:
                    cols.append(c); lbs.append(0.0); ubs.append(0.0)
        else:  # step: z_j = started by option j; z_j = 0 for j < lo, z_j = 1 for j >= hi (last option implicit)
            for j, c in enumerate(cs):
                if j < lo:
                    cols.append(c); lbs.append(0.0); ubs.append(0.0)
                elif j >= hi:
                    cols.append(c); lbs.append(1.0); ubs.append(1.0)
    return cols, lbs, ubs


def _highs(model: Model, splits: list[Split], relax: bool, time_limit: float, seed: int = 0,
           rel_gap: float = 1e-9) -> highspy.Highs | None:
    h = new_highs(1)
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(time_limit))
    h.setOptionValue("mip_rel_gap", float(rel_gap))
    h.setOptionValue("mip_abs_gap", 1e-6)
    h.setOptionValue("random_seed", int(seed))
    model.builder.to_highs(h)
    if relax:
        h.setOptionValue("solve_relaxation", True)
    bc = split_bounds(model, splits)
    if bc is None:
        return None
    cols, lbs, ubs = bc
    if cols:
        h.changeColsBounds(len(cols), np.array(cols, dtype=np.int32), np.array(lbs), np.array(ubs))
    return h


@dataclass(order=True)
class Node:
    bound: float
    splits: list[Split] = field(compare=False)
    frac: list[tuple[float, int, int]] = field(compare=False, default_factory=list)  # (score, p, s) candidates
    n_frac: int = field(compare=False, default=0)  # projects fractional in the LP (all of them, not just the top 32)


_LP_CACHE: dict[int, tuple[highspy.Highs, int]] = {}


def _lp_solver(model: Model) -> highspy.Highs:
    """One LP-relaxation HiGHS instance per model, reused across tree nodes: a node only changes column bounds, so
    dual simplex warm-starts from the previous basis instead of rebuilding the model and solving it cold. On the
    233-project CPO models this took the 60-leaf ramp-up from ~40 s to a few seconds. Rebuilt when the model gains
    rows (root cuts). No time limit: HiGHS counts `time_limit` over the object's whole life, so a 60 s cap made every
    node LP after the first minute of tree work return "time limit", which used to read as infeasible (pruned)."""
    key = id(model)
    cached = _LP_CACHE.get(key)
    if cached is None or cached[1] != len(model.builder.row_lo):
        h = _highs(model, [], True, np.inf)
        _LP_CACHE[key] = (h, len(model.builder.row_lo))
        cached = _LP_CACHE[key]
    return cached[0]


def _lp_node(model: Model, splits: list[Split]) -> Node | None:
    bc = split_bounds(model, splits)
    if bc is None:
        return None
    h = _lp_solver(model)
    cols, lbs, ubs = bc
    idx = np.array(cols, dtype=np.int32)
    if cols:
        h.changeColsBounds(len(cols), idx, np.array(lbs), np.array(ubs))
    try:
        h.run()
        status = h.getModelStatus()
        if status not in (highspy.HighsModelStatus.kOptimal, highspy.HighsModelStatus.kInfeasible):
            # never prune on anything but proven infeasibility: retry once from a cold, fresh solver
            cold = _highs(model, splits, True, np.inf)
            cold.run()
            status = cold.getModelStatus()
            if status not in (highspy.HighsModelStatus.kOptimal, highspy.HighsModelStatus.kInfeasible):
                raise RuntimeError(f"node LP ended with status {cold.modelStatusToString(status)}")
            h_used = cold
        else:
            h_used = h
        ok = status == highspy.HighsModelStatus.kOptimal
        x = np.asarray(h_used.getSolution().col_value) if ok else None
        obj = h_used.getInfo().objective_function_value if ok else None
    finally:  # restore the model's own bounds for the next node
        if cols:
            h.changeColsBounds(len(cols), idx, np.array([model.builder.lb[c] for c in cols]),
                               np.array([model.builder.ub[c] for c in cols]))
    if not ok:
        return None  # proven infeasible
    cand = []
    inst = model.inst
    for p, (opts, cols) in enumerate(zip(model.options, model.start_cols)):
        vals = x[cols]
        # cumulative "started by option j" probabilities
        cum = np.cumsum(vals) if model.encoding == "pulse" else vals
        weight = float(inst.spend[p].sum())
        for j in range(len(cols) - (1 if model.encoding == "pulse" else 0)):
            f = min(cum[j], 1.0 - cum[j])
            if f > 1e-6:
                cand.append((f * weight, p, int(opts[j])))
    cand.sort(reverse=True)
    return Node(model.user(obj), splits, cand[:32], len({p for _, p, _ in cand}))


def _branch(model: Model, node: Node, sb_candidates: int = 0, pool=None,
            cutoff: float = np.inf) -> tuple[Node | None, Node | None]:
    """The two children of `node`. Default: split on the top-scored fractional candidate (fractionality x spend).
    E11 (sb_candidates=k): strong branching. Solve both child LPs of the top k candidates and keep the split with
    the best product of bound gains (an infeasible child scores infinite: that split is a fixing). The gap anatomy study
    measured 219 LP nodes to a 1e-4 gap this way, against 849 with the default rule.
    E12 (pool given): the candidates' child LPs run on the worker pool, one task per candidate."""
    if sb_candidates <= 1:
        _, p, s = node.frac[0]
        return _lp_node(model, node.splits + [(p, s, 0)]), _lp_node(model, node.splits + [(p, s, 1)])
    cands = [(p, s) for _, p, s in node.frac[:sb_candidates]]
    if pool is None:
        results = [(_lp_node(model, node.splits + [(p, s, 0)]), _lp_node(model, node.splits + [(p, s, 1)]))
                   for p, s in cands]
    else:
        results = [f.result() for f in [pool.submit(_sb_task, node.splits, p, s) for p, s in cands]]
    return _sb_pick(node, results, cutoff)


def _sb_pick(node: Node, results, cutoff: float = np.inf) -> tuple[Node | None, Node | None]:
    """The candidate (pair of child nodes) with the best product of bound gains, first one wins ties.
    E22: each gain is capped at the node's distance to the cutoff. A child past the cutoff is pruned however far
    past it lands; uncapped, such overshoots inflated the product and picked weak splits on the CFADS scenarios
    (docs/research/CFADS_BOUND_STALL.md)."""
    eps = 1e-6 + 1e-12 * abs(node.bound)
    cap = max(eps, cutoff - node.bound) if np.isfinite(cutoff) else np.inf
    best, best_score = None, -1.0
    for kids in results:
        gains = [cap if c is None else min(cap, max(eps, c.bound - node.bound)) for c in kids]
        score = gains[0] * gains[1]
        if score > best_score:
            best, best_score = kids, score
        if score == np.inf:
            break
    return best


def ramp_up(model: Model, n_leaves: int, cutoff: float = np.inf, sb_candidates: int = 0,
            pool=None, width: int = 1) -> tuple[list[Node], int]:
    """Best-first LP tree until `n_leaves` open leaves remain. Returns (leaves sorted by bound, nodes pruned).
    With strong branching on a pool, the `width` best open nodes are expanded together so the pool stays busy."""
    if pool is not None and sb_candidates > 1 and width > 1:
        return _ramp_up_batched(model, n_leaves, cutoff, sb_candidates, pool, width)
    root = _lp_node(model, [])
    if root is None:
        return [], 0
    heap = [root]
    done: list[Node] = []
    pruned = 0
    while heap and len(heap) + len(done) < n_leaves:
        node = heapq.heappop(heap)
        if node.bound >= cutoff:
            pruned += 1
            continue
        if not node.frac:  # LP integral: nothing to split on; keep as a leaf
            done.append(node)
            continue
        for child in _branch(model, node, sb_candidates, pool, cutoff):
            if child is None or child.bound >= cutoff:
                pruned += 1
                continue
            heapq.heappush(heap, child)
    leaves = sorted(heap + done)
    return leaves, pruned


def _ramp_up_batched(model: Model, n_leaves: int, cutoff: float, k: int, pool, width: int) -> tuple[list[Node], int]:
    root = _lp_node(model, [])
    if root is None:
        return [], 0
    heap = [root]
    done: list[Node] = []
    pruned = 0
    while heap and len(heap) + len(done) < n_leaves:
        batch: list[Node] = []
        while heap and len(batch) < width and len(heap) + len(done) + 2 * len(batch) < n_leaves:
            node = heapq.heappop(heap)
            if node.bound >= cutoff:
                pruned += 1
            elif not node.frac:
                done.append(node)
            else:
                batch.append(node)
        futs = [[pool.submit(_sb_task, n.splits, p, s) for _, p, s in n.frac[:k]] for n in batch]
        for node, fl in zip(batch, futs):
            for child in _sb_pick(node, [f.result() for f in fl], cutoff):
                if child is None or child.bound >= cutoff:
                    pruned += 1
                    continue
                heapq.heappush(heap, child)
    return sorted(heap + done), pruned


# ---- worker side --------------------------------------------------------------------------------------------------
_W: dict = {}


def _init(best_val, best_starts, lock, stop, name, L, encoding, balance, cuts_path=None, rel_gap=1e-9, live=None,
          steal=None):
    model = last_year_model(I.load(name), L, encoding, balance)
    _W.update(best_val=best_val, best_starts=best_starts, lock=lock, stop=stop, model=model, cuts_path=cuts_path,
              cuts_loaded=False, rel_gap=rel_gap, live=live, steal=steal)


def _load_cuts() -> None:
    """Append the master's root cuts (written to cuts_path before any task that needs them is submitted)."""
    if _W["cuts_loaded"] or not _W["cuts_path"]:
        return
    path = Path(_W["cuts_path"])
    if path.exists():
        for lo, hi, cols, vals in json.loads(path.read_text(encoding="utf-8")):
            _W["model"].builder.row(lo, hi, cols, vals)
    _W["cuts_loaded"] = True


def _sb_task(splits: list[Split], p: int, s: int) -> tuple[Node | None, Node | None]:
    """E12: one strong-branching candidate's two child LPs, on a worker (same model and cuts as the master)."""
    _load_cuts()
    model: Model = _W["model"]
    return _lp_node(model, splits + [(p, s, 0)]), _lp_node(model, splits + [(p, s, 1)])


def _offer(starts: np.ndarray, obj: float) -> None:
    with _W["lock"]:
        if obj < _W["best_val"].value:
            _W["best_val"].value = obj
            _W["best_starts"][:] = [int(s) for s in starts]


def _solve_leaf(splits: list[Split], time_limit: float, seed: int, t0: float, leaf_cutoff: bool = True,
                slot: int = -1, start_bound: float = -np.inf) -> dict:
    _load_cuts()
    model: Model = _W["model"]
    h = _highs(model, splits, False, time_limit, seed, _W["rel_gap"])
    if h is None:
        return {"status": "empty", "bound": np.inf, "found": []}
    if leaf_cutoff:  # start the leaf with the shared incumbent as HiGHS' objective bound (exp05: can hurt)
        h.setOptionValue("objective_bound", model.solver_units(float(_W["best_val"].value)))
    found = []

    def on_improve(e):
        st = model.starts_from_solution(np.asarray(e.data_out.mip_solution))
        obj = objective(model.inst, st)
        found.append((time.time() - t0, obj))
        _offer(st, obj)

    live = _W.get("live")
    if live is not None and slot >= 0:
        live[slot] = start_bound
    steal = _W.get("steal") if slot >= 0 else None  # E27: the master sets steal[slot] to take this leaf back

    def on_interrupt(e):
        db = model.user(e.data_out.mip_dual_bound)
        if live is not None and slot >= 0 and db > live[slot]:
            live[slot] = db  # E1: the master reads this live bound for the global gap and the leaf census
        if db >= _W["best_val"].value - _W["rel_gap"] * abs(_W["best_val"].value):
            e.data_in.user_interrupt = True
        elif steal is not None and steal[slot]:
            e.data_in.user_interrupt = True

    h.cbMipImprovingSolution.subscribe(on_improve)
    h.cbMipInterrupt.subscribe(on_interrupt)
    h.run()
    info = h.getInfo()
    status = h.modelStatusToString(h.getModelStatus())
    best = _W["best_val"].value
    bound = model.user(info.mip_dual_bound)
    if status in ("Infeasible",) or (status != "Optimal" and bound >= best - _W["rel_gap"] * abs(best)):
        bound = np.inf if status == "Infeasible" else max(bound, best)
        status = "pruned"
    elif status == "Optimal":
        obj = model.user(info.objective_function_value)
        bound = max(bound, obj) if obj < np.inf else bound
    elif steal is not None and steal[slot]:
        status = "stolen"  # an open subtree handed back to the master: it must be re-split, never closed
    bound = max(bound, start_bound)  # a subtree's bound never falls below the bound it started with
    return {"status": status, "bound": float(bound), "found": found, "nodes": int(info.mip_node_count)}


def _fix_others(starts: np.ndarray, free: np.ndarray) -> list[Split]:
    """Decisions that pin every non-free project to its start in `starts`."""
    out: list[Split] = []
    for p in np.nonzero(~free)[0]:
        s = int(starts[p])
        out += [(int(p), s, 0), (int(p), s - 1, 1)]
    return out


def _lns_task(seconds: float, seed: int, t0: float, sub_limit: float = 3.0, big: bool = False,
              patience: float | None = None, alns: bool = False) -> dict:
    """Large-neighbourhood search around the shared incumbent: free a time window or a random set of projects, fix the
    rest, solve the sub-MIP exactly with the incumbent as cutoff. Neighbourhood sizes adapt to sub-MIP outcomes.
    big (E13): add a big-project neighbourhood. Free 1-3 of the largest projects (top 10% by spend) plus a random
    set of others, with a 3x longer sub-MIP limit. The gap anatomy study found the optimum moves a few big, long
    projects by 1-19 years, which the window and hot-spot neighbourhoods rarely allow.
    patience (E18): return to the tree once the shared incumbent has not improved for this many seconds.
    alns (E20): pick neighbourhood kinds by adaptive weights (a win scores 10, an exhausted neighbourhood 1, a timeout
    0; weights move 20% toward the latest score, floor 0.2) instead of uniformly. Per-kind stats are returned either way."""
    _load_cuts()
    model: Model = _W["model"]
    inst = model.inst
    rng = np.random.default_rng(seed)
    deadline = time.time() + seconds
    size = 25
    found, tries, wins = [], 0, 0
    big_ids = np.argsort([-float(sp.sum()) for sp in inst.spend])[:max(5, inst.n // 10)]
    opt_sets = [set(int(x) for x in o) for o in model.options]
    base_kinds = 4 if inst.penalised else 3
    n_kinds = base_kinds + (1 if big else 0)
    weights = np.ones(n_kinds)
    k_tries, k_wins, k_secs = np.zeros(n_kinds, int), np.zeros(n_kinds, int), np.zeros(n_kinds)
    last_val, last_t = _W["best_val"].value, time.time()
    while time.time() < deadline and not _W["stop"].value:
        if _W["best_val"].value < last_val:
            last_val, last_t = _W["best_val"].value, time.time()
        elif patience is not None and time.time() - last_t > patience:
            break
        if _W["best_val"].value == np.inf:
            time.sleep(0.2)
            continue
        with _W["lock"]:
            cur = np.array(_W["best_starts"][:], dtype=np.int64)
            cur_val = _W["best_val"].value
        kind = int(rng.choice(n_kinds, p=weights / weights.sum())) if alns else int(rng.integers(n_kinds))
        limit = sub_limit
        if kind == base_kinds:  # E13: big projects plus a random set of others
            free = np.zeros(inst.n, dtype=bool)
            free[rng.choice(inst.n, size=min(size, inst.n), replace=False)] = True
            free[rng.choice(big_ids, size=int(rng.integers(1, 4)), replace=False)] = True
            limit = 3 * sub_limit
        elif kind == 3:  # cash hot spot: projects active around a year whose cash sits in the expensive tiers
            ev = evaluate(inst, cur)
            if ev["feasible"]:
                closing = np.asarray(ev["closing_balance_units"][:ev["last_build_year_index"]], dtype=float)
                excess = np.maximum(0.0, closing - inst.base_frac * np.maximum(0.0, inst.F[:len(closing)]))
                w = excess + 1e-9 * closing.max(initial=1.0) + 1e-12
                a = int(rng.choice(len(w), p=w / w.sum())) if len(w) else 0
            else:
                a = int(rng.integers(0, int(cur.max()) + 1))
            ends = cur + inst.last_nz
            r = int(rng.integers(1, 4))
            free = (cur <= a + r) & (ends >= a - r)
        elif kind == 0:  # time window of starts
            a = int(rng.integers(0, max(1, int(cur.max()) - 2)))
            free = (cur >= a) & (cur <= a + int(rng.integers(2, 6)))
        elif kind == 1:  # random subset
            free = np.zeros(inst.n, dtype=bool)
            free[rng.choice(inst.n, size=min(size, inst.n), replace=False)] = True
        else:  # projects active in a year window (they share that window's cash)
            a = int(rng.integers(0, int(cur.max()) + 1))
            ends = cur + inst.last_nz
            free = (cur <= a + 2) & (ends >= a)
        if kind != base_kinds and (free.sum() < 5 or free.sum() > size + 15):
            idx = np.nonzero(free)[0] if free.sum() > size + 15 else np.arange(inst.n)
            free = np.zeros(inst.n, dtype=bool)
            free[rng.choice(idx, size=min(size, len(idx)), replace=False)] = True
        # projects whose incumbent start is not an option of this last year's model must be free, or fixing fails
        free = free | np.array([int(cur[q]) not in opt_sets[q] for q in range(inst.n)])
        h = _highs(model, _fix_others(cur, free), False, min(limit, max(0.1, deadline - time.time())), int(seed + tries))
        tries += 1
        if h is None:
            continue
        h.setOptionValue("objective_bound", model.solver_units(float(cur_val)))
        h.setSolution(_vec_solution(model, cur))
        ts = time.time()
        h.run()
        status = h.modelStatusToString(h.getModelStatus())
        sol = h.getSolution()
        won = False
        if sol.value_valid:
            st = model.starts_from_solution(np.asarray(sol.col_value))
            obj = objective(inst, st)
            if obj < cur_val - 1e-9 * abs(cur_val):
                _offer(st, obj)
                found.append((time.time() - t0, obj))
                wins += 1
                won = True
        k_tries[kind] += 1
        k_wins[kind] += won
        k_secs[kind] += time.time() - ts
        score = 10.0 if won else (1.0 if status == "Optimal" else 0.0)
        weights[kind] = max(0.2, 0.8 * weights[kind] + 0.2 * score)
        size = min(45, size + 2) if status == "Optimal" else max(12, size - 3)
    return {"tries": tries, "wins": wins, "found": found,
            "kinds": [(int(a), int(b), float(c)) for a, b, c in zip(k_tries, k_wins, k_secs)]}


def _vec_solution(model: Model, starts: np.ndarray) -> highspy.HighsSolution:
    sol = highspy.HighsSolution()
    vec = model.solution_vector(starts)
    if vec is not None:
        sol.col_value = vec.tolist()
        sol.value_valid = True
    return sol


# ---- master -------------------------------------------------------------------------------------------------------
@dataclass
class ParallelResult:
    objective: float
    starts: np.ndarray | None
    dual_bound: float
    proven: bool
    seconds: float
    trace: list[tuple[float, float]]
    leaves: int
    pruned_at_rampup: int
    log: list[str]


def solve_parallel(name: str, L: int, workers: int | None = None, n_leaves: int | None = None, encoding: str = "step",
                   balance: str = "cumulative", time_limit: float = 900.0, incumbent: np.ndarray | None = None,
                   t0: float | None = None, idle: Callable[[float], None] | None = None,
                   slice_seconds: float | None = None, slice_growth: float = 1.5, lns_workers: int = 0,
                   lns_seconds: float = 30.0, cut_rounds: int = 0, quick_seconds: float = 0.0,
                   leaf_cutoff: bool = True, rel_gap: float = 1e-9, seed: int = 0,
                   cutoff_after: float | None = None, sb_candidates: int = 0, lns_big: bool = False,
                   lns_after_rampup: bool = False, lns_patience: float | None = None,
                   lns_alns: bool = False, lns_first: float = 0.0,
                   steal_after: float | None = None) -> ParallelResult:
    """Solve the fixed-last-year model for one L to proven optimality with split-and-solve.

    Leaves wait in a best-first queue. With `slice_seconds`, a leaf that is still open after its time slice comes back
    to the master, which re-splits it on its LP solution; the children go back into the queue with a slice
    `slice_growth` times longer. Hard subtrees get spread over idle workers, and every restart picks up the latest
    shared incumbent as its cutoff.

    lns_workers: that many workers run LNS around the shared incumbent for `lns_seconds` before joining the tree.
    cut_rounds: rounds of the root cover-cut loop (cuts.py), run once by the master and shipped to every worker.
    quick_seconds: while the worker pool spawns, the master tries to solve the whole model directly for this long, so
        an easy instance is finished before the parallel machinery is even up.
    The pool starts spawning first; the quick try, the cut loop and the ramp-up all overlap process start-up.
    cutoff_after (E2): leaves launched this many seconds after the start get the shared incumbent as HiGHS'
        objective_bound even when leaf_cutoff is False (search without a cutoff early, prove with one late).
    E1: each running leaf has a slot in a shared array where its worker writes its live dual bound, so the global bound
        (and the streamed gap) is exact rather than frozen at launch values; re-split children inherit the parent's
        bound; the log ends with a census of the leaves whose bounds were worst.
    lns_big (E13): LNS also uses the big-project neighbourhood (see _lns_task).
    lns_after_rampup (E15): submit the LNS workers only after the ramp-up, so strong branching has the whole pool (useful
        with a good warm start, when early LNS matters less).
    lns_patience (E18): an LNS worker joins the tree once the incumbent has not improved for this many seconds.
    steal_after (E27): work stealing. When the queue is empty and at most half the tree workers are busy, the running
        leaf with the weakest live bound that has run at least this many seconds is interrupted and re-split, so idle
        workers get its subtrees instead of waiting for a slice to expire. None = off. The log reports tree
        utilization either way.
    lns_first (E24): with an incumbent in hand, every worker runs LNS for this many seconds before the ramp-up.
    lns_alns (E20): adaptive neighbourhood selection in LNS (see _lns_task). The log totals tries/wins/seconds per kind.
    sb_candidates (E11): strong branching over this many candidates in the ramp-up and re-split (0 = off; -1 = auto,
        16 when the root has more than 15 fractional projects, else 4). E12: the
        child LPs run on the pool whenever at least 2 workers are outside LNS; otherwise on the master.
    leaf_cutoff: pass the shared incumbent to each leaf as HiGHS' `objective_bound`. Leaves are pruned by the interrupt
        callback either way; exp05 found the static bound can steer HiGHS' search badly.

    idle: called with the current incumbent value whenever the master is waiting on workers (one small unit of work per
    call), e.g. LP-bounding the other last years."""
    t0 = time.time() if t0 is None else t0
    workers = workers or max(1, (os.cpu_count() or 2))
    n_leaves = n_leaves or 3 * workers
    inst = I.load(name)
    model = last_year_model(inst, L, encoding, balance)
    log: list[str] = []
    ctx = mp.get_context("spawn")
    lock = ctx.Lock()
    best_val = ctx.Value("d", np.inf, lock=False)
    best_starts = ctx.Array("q", inst.n, lock=False)
    stop = ctx.Value("b", 0, lock=False)
    live = ctx.Array("d", [-np.inf] * workers, lock=False)  # E1: live dual bound per worker slot
    steal = ctx.Array("b", [0] * workers, lock=False)  # E27: per-slot request to hand a running leaf back
    free_slots = list(range(workers))
    census: list[tuple[float, float, int, str, int]] = []
    lns_kinds: list[list] = []  # (final bound, seconds, nodes, status, split count)
    trace: list[tuple[float, float]] = []
    if incumbent is not None:
        v = objective(inst, incumbent)
        if v < np.inf:
            best_val.value = v
            best_starts[:] = [int(s) for s in incumbent]
            trace.append((time.time() - t0, v))

    def tol() -> float:
        b = best_val.value
        return b - rel_gap * abs(b) if b < np.inf else np.inf

    closed: list[float] = []      # dual bounds of finished subtrees
    resplits = 0
    leaves: list[Node] = []
    pruned = 0
    fd, tmp = tempfile.mkstemp(suffix=".cuts.json")
    os.close(fd)
    cuts_path = Path(tmp)
    cuts_path.unlink()  # workers read it only after the master has written it
    pool = ProcessPoolExecutor(workers, mp_context=ctx, initializer=_init,
                               initargs=(best_val, best_starts, lock, stop, name, L, encoding, balance,
                                         str(cuts_path) if cut_rounds else None, rel_gap, live, steal))
    try:
        warm = [pool.submit(_noop) for _ in range(workers)]  # start spawning now; the master works meanwhile
        if quick_seconds > 0:
            from .solve import Trace
            from .solve import solve as highs_solve
            qt = Trace()
            q0 = time.time() - t0
            r = highs_solve(model, qt, threads=1, time_limit=quick_seconds, start=incumbent, rel_gap=rel_gap, seed=seed)
            if qt.best < best_val.value:
                best_val.value = qt.best
                best_starts[:] = [int(x) for x in qt.best_starts]
            trace.extend((q0 + pt, v) for pt, v, _ in qt.points)
            log.append(f"quick try: {r.status} in {r.seconds:.2f}s")
            if r.status == "Optimal":
                closed.append(best_val.value if r.dual_bound is None else max(r.dual_bound, best_val.value))
                return _finish(best_val, best_starts, closed, trace, t0, leaves, pruned, log, resplits, rel_gap)
        # LNS starts now, from the quick try's incumbent, while the master still builds cuts and the ramp-up tree
        # (on the 233-project CPO models the ramp-up alone takes ~40 s; LNS reaches 1% within ~8 s of starting)
        def start_lns() -> list:
            return [pool.submit(_lns_task, lns_seconds, 1000 + i + 7919 * seed, t0, 3.0, lns_big, lns_patience, lns_alns)
                    for i in range(lns_workers)]
        if lns_first > 0 and best_val.value < np.inf:  # E24: every worker on LNS first, then the ramp-up
            burst = [pool.submit(_lns_task, lns_first, 5000 + i + 7919 * seed, t0, 3.0, lns_big, None, lns_alns)
                     for i in range(workers)]
            wins = 0
            for f in burst:
                r = f.result()
                trace.extend(r["found"])
                wins += r["wins"]
            log.append(f"lns burst: {workers} workers x {lns_first:.0f}s, {wins} improvements, "
                       f"{time.time() - t0:.1f}s")
        lns = [] if lns_after_rampup else start_lns()
        if cut_rounds:
            from .cuts import cut_row, root_loop
            bounds, cuts = root_loop(model, cut_rounds)
            cuts_path.write_text(json.dumps([cut_row(model, c) for c in cuts]), encoding="utf-8")
            log.append(f"root cuts: {len(cuts)} in {len(bounds) - 1} rounds, LP {bounds[0]:.6e} -> {bounds[-1]:.6e}")
        free = workers if lns_after_rampup else workers - lns_workers
        sb_pool = pool if free >= 2 else None  # never queue behind LNS tasks that run for minutes
        if sb_candidates < 0:  # E23 auto: 16 candidates when the root LP smears many projects (CFADS), else 4
            root = _lp_node(model, [])
            n_frac = root.n_frac if root is not None else 0
            sb_candidates = 16 if n_frac > 15 else 4
            log.append(f"strong branching: {sb_candidates} candidates ({n_frac} fractional projects at the root)")
        width = max(1, -(-2 * free // max(1, sb_candidates)))  # E23: about 2x the free workers in tasks per round
        leaves, pruned = ramp_up(model, n_leaves, best_val.value, sb_candidates, sb_pool, width)
        log.append(f"ramp-up: {len(leaves)} leaves, {pruned} pruned, {time.time() - t0:.1f}s")
        if lns_after_rampup:
            lns = start_lns()
        wait(warm)
        seq = 0
        heap: list[tuple[float, int, list[Split], float | None]] = []
        for leaf in leaves:
            heap.append((leaf.bound, seq, leaf.splits, slice_seconds)); seq += 1
        heapq.heapify(heap)
        running: dict = {}
        last_seen = best_val.value
        last_bound, bound_trace = -np.inf, []
        busy_s = cap_s = low_s = 0.0  # utilization: busy tree-worker seconds, available ones, time under half busy
        t_prev = time.time()
        stealing: set[int] = set()
        steals = 0
        while heap or running:
            for f in [f for f in lns if f.done()]:
                lns.remove(f)
                trace.extend(f.result()["found"])
                log.append(f"lns: {f.result()['tries']} sub-MIPs, {f.result()['wins']} improvements")
                lns_kinds.append(f.result().get("kinds", []))
            while heap and len(running) < workers - len(lns):
                b, k, splits, sl = heapq.heappop(heap)
                if b >= tol():
                    closed.append(max(b, best_val.value)); continue
                remaining = time_limit - (time.time() - t0)
                if remaining <= 0:
                    closed.append(b); continue
                tl = min(remaining, sl) if sl else remaining
                slot = free_slots.pop() if free_slots else -1
                if slot >= 0:
                    steal[slot] = 0
                cut = leaf_cutoff or (cutoff_after is not None and time.time() - t0 >= cutoff_after)
                fut = pool.submit(_solve_leaf, splits, max(0.5, tl), k + 7919 * seed, t0, cut, slot, b)
                running[fut] = (b, splits, sl, sl is not None and tl < remaining, slot, time.time())
            tree_workers = workers - len(lns)
            if (steal_after is not None and not heap and running and not stealing and best_val.value < np.inf
                    and len(running) <= tree_workers // 2):  # E27: idle workers and nothing queued: take a leaf back
                # (never before an incumbent exists: leaves are the main source of schedules then)
                now = time.time()
                cands = [(max(rv[0], live[rv[4]]), rv[4]) for rv in running.values()  # pinned (sl = inf): never
                         if rv[4] >= 0 and rv[2] != np.inf and now - rv[5] >= steal_after]
                if cands:
                    victim = min(cands)[1]
                    steal[victim] = 1
                    stealing.add(victim)
            done, _ = wait(list(running) + lns, timeout=0.05 if idle else 0.5, return_when=FIRST_COMPLETED)
            done = {f for f in done if f in running}
            now = time.time()
            dt, t_prev = now - t_prev, now
            busy_s += dt * len(running)
            cap_s += dt * tree_workers
            low_s += dt if len(running) < tree_workers / 2 else 0.0
            min_closed = min(closed) if closed else np.inf
            if idle is not None and not done:
                idle(best_val.value)
            if best_val.value < last_seen:
                last_seen = best_val.value
                trace.append((time.time() - t0, last_seen))
                sink(trace[-1][0], last_seen, best_starts[:])
            # proven bound now: finished subtrees, queued subtrees (LP bounds), running subtrees (their start bounds)
            bound_now = min([min_closed] + [hb[0] for hb in heap[:1]]
                            + [max(rv[0], live[rv[4]]) if rv[4] >= 0 else rv[0] for rv in running.values()]
                            + [best_val.value])
            if bound_now > last_bound + 1e-12 * abs(bound_now):
                last_bound = bound_now
                bound_trace.append((time.time() - t0, bound_now))
                sink_bound(bound_trace[-1][0], bound_now)
            for fut in done:
                b, splits, sl, sliced, slot, started = running.pop(fut)
                if slot >= 0:
                    live[slot] = -np.inf
                    free_slots.append(slot)
                    stealing.discard(slot)
                r = fut.result()
                census.append((r["bound"], time.time() - started, r.get("nodes", 0), r["status"], len(splits)))
                trace.extend(r["found"])
                stolen = r["status"] == "stolen"
                steals += stolen
                # an unexpected interrupt is reopened too: only finished, pruned or globally timed-out leaves close
                reopen = stolen or r["status"] == "Interrupted by user"
                if (r["status"] == "Time limit reached" and sliced) or reopen:
                    node = _lp_node(model, splits)
                    if node is None:
                        closed.append(np.inf); continue
                    nxt = sl * slice_growth if sl else None
                    if not node.frac:  # LP integral yet the MIP is open: let it run to the end, pinned (sl = inf)
                        # so it can never be stolen again (E27 audit: otherwise it can be restarted forever)
                        heapq.heappush(heap, (max(b, r["bound"]), seq, splits, np.inf)); seq += 1; continue
                    resplits += 1
                    for child in _branch(model, node, sb_candidates, sb_pool, tol()):
                        if child is not None:  # E1: a child keeps at least its parent's proven bound
                            heapq.heappush(heap, (max(child.bound, r["bound"]), seq, child.splits, nxt)); seq += 1
                else:
                    closed.append(r["bound"])
        stop.value = 1
        for f in lns:  # the tree finished first: stop and collect the LNS workers
            r = f.result()
            trace.extend(r["found"])
            log.append(f"lns: {r['tries']} sub-MIPs, {r['wins']} improvements")
            lns_kinds.append(r.get("kinds", []))
        if lns_kinds and any(lns_kinds):  # E20: which neighbourhood kinds won
            tot = np.sum([np.array(k, dtype=float) for k in lns_kinds if k], axis=0)
            log.append("lns kinds (tries/wins/s): " + "; ".join(
                f"k{i} {int(a)}/{int(b)}/{c:.0f}" for i, (a, b, c) in enumerate(tot)))
        if best_val.value < last_seen:
            trace.append((time.time() - t0, best_val.value))
        if cap_s > 0:
            log.append(f"tree utilization: {busy_s / cap_s:.0%} of tree-worker time busy; under half busy for "
                       f"{low_s:.0f}s; steals: {steals}")
        if census:  # E1: which leaves held the bound (lowest final bounds first)
            worst = sorted(census)[:5]
            log.append("leaf census (worst bounds): " + "; ".join(
                f"bound {bd:.6e} after {sec:.0f}s, {nd} nodes, {st}, depth {dp}" for bd, sec, nd, st, dp in worst))
        return _finish(best_val, best_starts, closed, trace, t0, leaves, pruned, log, resplits, rel_gap)
    finally:
        stop.value = 1
        pool.shutdown(wait=False, cancel_futures=True)  # don't wait for idle workers to exit: the result is final
        cuts_path.unlink(missing_ok=True)


def _finish(best_val, best_starts, closed, trace, t0, leaves, pruned, log, resplits, rel_gap=1e-9) -> ParallelResult:
    if resplits:
        log.append(f"re-splits: {resplits}")
    best = best_val.value
    dual = min(closed + [best])
    starts = np.array(best_starts[:], dtype=np.int64) if best < np.inf else None
    proven = dual >= best - rel_gap * abs(best)
    mono, run = [], np.inf
    for t, v in sorted(trace):
        if v < run:
            run = v
            mono.append((t, v))
    return ParallelResult(best, starts, dual, proven, time.time() - t0, mono, len(leaves), pruned, log)


def _noop() -> None:
    return None
