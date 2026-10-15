"""Run a built model with HiGHS and record an incumbent trace (wall-clock seconds, evaluator objective)."""
from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import highspy
import numpy as np

from .evaluate import objective
from .formulation import Model


def sink(t: float, obj: float, starts) -> None:
    """Append an incumbent to $CAPOPT_TRACE_FILE (if set), so a run killed by the benchmark watchdog keeps its trace."""
    path = os.environ.get("CAPOPT_TRACE_FILE")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": round(t, 3), "obj": obj, "starts": [int(x) for x in starts]}) + "\n")


@dataclass
class Trace:
    """Incumbent history on a shared clock. Every point is a schedule re-scored by the exact evaluator."""
    t0: float = field(default_factory=time.perf_counter)
    points: list[tuple[float, float, str]] = field(default_factory=list)  # (seconds, objective, source)
    best: float = float("inf")
    best_starts: np.ndarray | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def offer(self, starts: np.ndarray, obj: float, source: str) -> bool:
        with self.lock:
            if obj < self.best - 1e-9 * max(1.0, abs(self.best) if self.best < float("inf") else 1.0):
                self.best = obj
                self.best_starts = np.array(starts, dtype=np.int64)
                self.points.append((time.perf_counter() - self.t0, obj, source))
                sink(self.points[-1][0], obj, starts)
                return True
        return False

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def time_to(self, target: float, rel: float) -> float | None:
        goal = target + rel * abs(target) + 1e-9 * abs(target)
        for t, v, _ in self.points:
            if v <= goal:
                return t
        return None


@dataclass
class SolveResult:
    status: str
    seconds: float
    model_objective: float | None
    dual_bound: float | None
    starts: np.ndarray | None
    nodes: int = 0


def make_highs(model: Model, threads: int = 1, time_limit: float = 60.0, rel_gap: float = 1e-9, abs_gap: float = 1e-6,
               log: bool = False, seed: int = 0, options: dict | None = None) -> highspy.Highs:
    h = highspy.Highs()
    h.setOptionValue("output_flag", bool(log))
    h.setOptionValue("threads", int(threads))
    h.setOptionValue("time_limit", float(time_limit))
    h.setOptionValue("mip_rel_gap", float(rel_gap))
    h.setOptionValue("mip_abs_gap", float(abs_gap))
    h.setOptionValue("random_seed", int(seed))
    for k, v in (options or {}).items():
        h.setOptionValue(k, v)
    model.builder.to_highs(h)
    return h


def solve(model: Model, trace: Trace | None = None, source: str = "highs", threads: int = 1, time_limit: float = 60.0,
          rel_gap: float = 1e-9, abs_gap: float = 1e-6, start: np.ndarray | None = None, log: bool = False, seed: int = 0,
          options: dict | None = None, stop: threading.Event | None = None, cutoff: Callable[[], float] | None = None,
          user_solutions: Callable[[], np.ndarray | None] | None = None) -> SolveResult:
    """Solve `model`; every improving MIP solution is translated to a schedule and offered to `trace`.

    stop          : event checked in the interrupt callback (portfolio runs stop each other through it)
    user_solutions: polled from the user-solution callback; a returned schedule is injected into HiGHS' B&B
    """
    h = make_highs(model, threads, time_limit, rel_gap, abs_gap, log, seed, options)
    if start is not None:
        vec = model.solution_vector(start)
        if vec is not None:
            h.setSolution(_solution(vec))
    if trace is not None:
        def on_improve(e):
            sol = np.asarray(e.data_out.mip_solution)
            st = model.starts_from_solution(sol)
            trace.offer(st, objective(model.inst, st), source)
        h.cbMipImprovingSolution.subscribe(on_improve)
    if stop is not None:
        def on_interrupt(e):
            if stop.is_set():
                e.data_in.user_interrupt = True
        h.cbMipInterrupt.subscribe(on_interrupt)
    if user_solutions is not None:
        def on_user(e):
            st = user_solutions()
            if st is not None:
                vec = model.solution_vector(st)
                if vec is not None:
                    e.data_in.user_has_solution = True
                    e.data_in.user_solution = vec.tolist()
        h.cbMipUserSolution.subscribe(on_user)
    t0 = time.perf_counter()
    h.run()
    secs = time.perf_counter() - t0
    status = h.modelStatusToString(h.getModelStatus())
    info = h.getInfo()
    sol = h.getSolution()
    starts = None
    mobj = None
    if info.primal_solution_status >= 1 or sol.value_valid:
        vals = np.asarray(sol.col_value)
        if len(vals) == len(model.builder.cost):
            starts = model.starts_from_solution(vals)
            mobj = model.user(info.objective_function_value)
    res = SolveResult(status, secs, mobj, model.user(info.mip_dual_bound), starts, int(info.mip_node_count))
    return res


def _solution(vec: np.ndarray) -> highspy.HighsSolution:
    s = highspy.HighsSolution()
    s.col_value = vec.tolist()
    s.value_valid = True
    return s
