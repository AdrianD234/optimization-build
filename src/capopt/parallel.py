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
import multiprocessing as mp
import os
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field

import highspy
import numpy as np

from . import instance as I
from .evaluate import objective
from .formulation import Model, last_year_model

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


def _highs(model: Model, splits: list[Split], relax: bool, time_limit: float, seed: int = 0) -> highspy.Highs | None:
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("threads", 1)
    h.setOptionValue("time_limit", float(time_limit))
    h.setOptionValue("mip_rel_gap", 1e-9)
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


def _lp_node(model: Model, splits: list[Split]) -> Node | None:
    h = _highs(model, splits, True, 60.0)
    if h is None:
        return None
    h.run()
    if h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
        return None
    x = np.asarray(h.getSolution().col_value)
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
    return Node(h.getInfo().objective_function_value, splits, cand[:8])


def ramp_up(model: Model, n_leaves: int, cutoff: float = np.inf) -> tuple[list[Node], int]:
    """Best-first LP tree until `n_leaves` open leaves remain. Returns (leaves sorted by bound, nodes pruned)."""
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
        _, p, s = node.frac[0]
        for side in (0, 1):
            child = _lp_node(model, node.splits + [(p, s, side)])
            if child is None or child.bound >= cutoff:
                pruned += 1
                continue
            heapq.heappush(heap, child)
    leaves = sorted(heap + done)
    return leaves, pruned


# ---- worker side --------------------------------------------------------------------------------------------------
_W: dict = {}


def _init(best_val, best_starts, lock, name, L, encoding, balance):
    _W.update(best_val=best_val, best_starts=best_starts, lock=lock,
              model=last_year_model(I.load(name), L, encoding, balance))


def _offer(starts: np.ndarray, obj: float) -> None:
    with _W["lock"]:
        if obj < _W["best_val"].value:
            _W["best_val"].value = obj
            _W["best_starts"][:] = [int(s) for s in starts]


def _solve_leaf(splits: list[Split], time_limit: float, seed: int, t0: float) -> dict:
    model: Model = _W["model"]
    h = _highs(model, splits, False, time_limit, seed)
    if h is None:
        return {"status": "empty", "bound": np.inf, "found": []}
    h.setOptionValue("objective_bound", float(_W["best_val"].value))
    found = []

    def on_improve(e):
        st = model.starts_from_solution(np.asarray(e.data_out.mip_solution))
        obj = objective(model.inst, st)
        found.append((time.time() - t0, obj))
        _offer(st, obj)

    def on_interrupt(e):
        if e.data_out.mip_dual_bound >= _W["best_val"].value - 1e-9 * abs(_W["best_val"].value):
            e.data_in.user_interrupt = True

    h.cbMipImprovingSolution.subscribe(on_improve)
    h.cbMipInterrupt.subscribe(on_interrupt)
    h.run()
    info = h.getInfo()
    status = h.modelStatusToString(h.getModelStatus())
    best = _W["best_val"].value
    bound = info.mip_dual_bound
    if status in ("Infeasible",) or (status != "Optimal" and bound >= best - 1e-9 * abs(best)):
        bound = np.inf if status == "Infeasible" else max(bound, best)
        status = "pruned"
    elif status == "Optimal":
        bound = max(bound, info.objective_function_value) if info.objective_function_value < np.inf else bound
    return {"status": status, "bound": float(bound), "found": found, "nodes": int(info.mip_node_count)}


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
                   slice_seconds: float | None = None, slice_growth: float = 1.5) -> ParallelResult:
    """Solve the fixed-last-year model for one L to proven optimality with split-and-solve.

    Leaves wait in a best-first queue. With `slice_seconds`, a leaf that is still open after its time slice comes back
    to the master, which re-splits it on its LP solution; the children go back into the queue with a slice
    `slice_growth` times longer. Hard subtrees get spread over idle workers, and every restart picks up the latest
    shared incumbent as its cutoff.

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
    trace: list[tuple[float, float]] = []
    if incumbent is not None:
        v = objective(inst, incumbent)
        if v < np.inf:
            best_val.value = v
            best_starts[:] = [int(s) for s in incumbent]
            trace.append((time.time() - t0, v))

    def tol() -> float:
        b = best_val.value
        return b - 1e-9 * abs(b) if b < np.inf else np.inf

    closed: list[float] = []      # dual bounds of finished subtrees
    resplits = 0
    with ProcessPoolExecutor(workers, mp_context=ctx, initializer=_init,
                             initargs=(best_val, best_starts, lock, name, L, encoding, balance)) as pool:
        warm = [pool.submit(_noop) for _ in range(workers)]  # the pool spawns while the master builds the ramp-up
        leaves, pruned = ramp_up(model, n_leaves, best_val.value)
        log.append(f"ramp-up: {len(leaves)} leaves, {pruned} pruned, {time.time() - t0:.1f}s")
        wait(warm)
        seq = 0
        heap: list[tuple[float, int, list[Split], float | None]] = []
        for leaf in leaves:
            heap.append((leaf.bound, seq, leaf.splits, slice_seconds)); seq += 1
        heapq.heapify(heap)
        running: dict = {}
        last_seen = best_val.value
        while heap or running:
            while heap and len(running) < workers:
                b, k, splits, sl = heapq.heappop(heap)
                if b >= tol():
                    closed.append(max(b, best_val.value)); continue
                remaining = time_limit - (time.time() - t0)
                if remaining <= 0:
                    closed.append(b); continue
                tl = min(remaining, sl) if sl else remaining
                fut = pool.submit(_solve_leaf, splits, max(0.5, tl), k, t0)
                running[fut] = (b, splits, sl, sl is not None and tl < remaining)
            done, _ = wait(list(running), timeout=0.05 if idle else 0.5, return_when=FIRST_COMPLETED)
            if idle is not None and not done:
                idle(best_val.value)
            if best_val.value < last_seen:
                last_seen = best_val.value
                trace.append((time.time() - t0, last_seen))
            for fut in done:
                b, splits, sl, sliced = running.pop(fut)
                r = fut.result()
                trace.extend(r["found"])
                if r["status"] == "Time limit reached" and sliced:
                    node = _lp_node(model, splits)
                    if node is None:
                        closed.append(np.inf); continue
                    nxt = sl * slice_growth
                    if not node.frac:  # LP integral yet the MIP is open: let it run to the end
                        heapq.heappush(heap, (max(b, r["bound"]), seq, splits, None)); seq += 1; continue
                    _, p, s = node.frac[0]
                    resplits += 1
                    for side in (0, 1):
                        child = _lp_node(model, splits + [(p, s, side)])
                        if child is not None:
                            heapq.heappush(heap, (child.bound, seq, child.splits, nxt)); seq += 1
                else:
                    closed.append(r["bound"])
        if best_val.value < last_seen:
            trace.append((time.time() - t0, best_val.value))
    if resplits:
        log.append(f"re-splits: {resplits}")
    best = best_val.value
    dual = min(closed + [best])
    starts = np.array(best_starts[:], dtype=np.int64) if best < np.inf else None
    proven = dual >= best - 1e-9 * abs(best)
    mono, run = [], np.inf
    for t, v in sorted(trace):
        if v < run:
            run = v
            mono.append((t, v))
    return ParallelResult(best, starts, dual, proven, time.time() - t0, mono, len(leaves), pruned, log)


def _noop() -> None:
    return None
