"""Cash-safe, LP-guided construction: a fast warm start for the fixed-last-year model.

Rounding the LP relaxation directly gives schedules that run the cash balance negative: the LP spreads a project over
several start years, and rounding breaks the cumulative balance. Instead, a serial schedule-generation scheme (SGS)
places projects one at a time, in the order of their LP target start, each at the earliest year >= its target that
keeps every cumulative balance non-negative (cumulative spend <= cumulative funding in every year up to L) and
respects the start-count cap. Placing a project only ever adds spend, so a placement never breaks an earlier one.
If no year from the target on fits, the project takes the feasible year closest below the target. Failing that, it
takes its latest start and the schedule is reported infeasible.
"""
from __future__ import annotations

import numpy as np
from numba import njit

from ..instance import Instance
from ..solve import new_highs
from . import kernel as K


@njit(cache=True)
def sgs(order, target, spend, dur, lo, hi, cumF, cap, L):
    n = order.shape[0]
    T = cumF.shape[0]
    spend_y = np.zeros(T)
    count = np.zeros(T, dtype=np.int64)
    starts = np.empty(n, dtype=np.int64)
    feasible = True
    for k in range(n):
        p = order[k]
        chosen = -1
        # candidate order: target, target+1, ..., hi, then target-1, ..., lo
        m = hi[p] - lo[p] + 1
        for c in range(m):
            if target[p] + c <= hi[p]:
                s = target[p] + c
            else:
                s = target[p] - (c - (hi[p] - target[p]))
                if s < lo[p]:
                    break
            if s < lo[p] or s > hi[p] or count[s] >= cap:
                continue
            ok = True
            run = 0.0
            for t in range(L + 1):
                add = spend[p, t - s] if s <= t < s + dur[p] else 0.0
                run += spend_y[t] + add
                if run > cumF[t] + 1e-9:
                    ok = False
                    break
            if ok:
                chosen = s
                break
        if chosen < 0:
            chosen = hi[p]
            feasible = False
        starts[p] = chosen
        count[chosen] += 1
        for j in range(dur[p]):
            spend_y[chosen + j] += spend[p, j]
    return starts, feasible


def lp_targets(inst: Instance, L: int) -> tuple[np.ndarray, np.ndarray]:
    """Per project: the LP's median start (first option whose cumulative LP mass reaches 1/2) and its mean start."""
    from ..formulation import last_year_model
    m = last_year_model(inst, L, "pulse", "cumulative")
    h = new_highs(1)
    h.setOptionValue("output_flag", False)
    h.setOptionValue("solve_relaxation", True)
    m.builder.to_highs(h)
    h.run()
    x = np.asarray(h.getSolution().col_value)
    med = np.empty(inst.n, dtype=np.int64)
    mean = np.empty(inst.n)
    for p, (opts, cols) in enumerate(zip(m.options, m.start_cols)):
        v = np.clip(x[cols], 0.0, None)
        v = v / v.sum() if v.sum() > 0 else np.ones_like(v) / len(v)
        med[p] = opts[int(np.searchsorted(np.cumsum(v), 0.5 - 1e-9))]
        mean[p] = float(np.dot(opts, v))
    return med, mean


def construct(inst: Instance, L: int, targets: np.ndarray, order: np.ndarray) -> tuple[np.ndarray, bool]:
    pk = K.pack(inst, L)
    cumF = np.cumsum(inst.F.astype(np.float64))
    tgt = np.clip(np.asarray(targets, dtype=np.int64), pk.lo, pk.hi)
    return sgs(np.asarray(order, dtype=np.int64), tgt, pk.spend.astype(np.float64), pk.dur, pk.lo, pk.hi, cumF,
               int(inst.max_starts), int(L))
