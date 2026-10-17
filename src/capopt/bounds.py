"""Year-block Lagrangian bounds for a fixed-last-year model (GAP_ANATOMY #3, CFADS_BOUND_STALL #4).

For min c'x s.t. lo <= Ax <= hi, x in X, and any row multipliers y whose signs match a finite row bound
(y_i > 0 needs a finite lo_i, y_i < 0 a finite hi_i), weak duality gives, for every feasible x:

    c'x >= (c - A_R' y_R)'x + sum_{i in R} (y_i > 0 ? y_i lo_i : y_i hi_i)

where R is any set of relaxed rows. Minimising the right-hand side over X (integrality, the kept rows, column bounds)
is a valid lower bound on the model's optimum. capopt keeps the per-project structure and the year rows of a block of
years, relaxes the other year rows priced at the LP duals, and solves that smaller MIP. Its dual bound is valid even
when the MIP stops at a time limit. The LP duals make the bound at least the LP bound (relaxing X's integrality
gives the LP value back by strong duality); integrality inside the block is what it adds.
"""
from __future__ import annotations

import time

import highspy
import numpy as np

from .formulation import INF, Model
from .solve import new_highs


def _lp_duals(model: Model) -> tuple[np.ndarray, float]:
    h = new_highs(1)
    h.setOptionValue("output_flag", False)
    h.setOptionValue("solve_relaxation", True)
    model.builder.to_highs(h)
    h.run()
    if h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
        raise RuntimeError(f"LP not optimal: {h.modelStatusToString(h.getModelStatus())}")
    return np.asarray(h.getSolution().row_dual, dtype=float), h.getInfo().objective_function_value


def block_bound(model: Model, keep_years: set[int] | None, time_limit: float = 30.0, integral: bool = True,
                threads: int = 1) -> dict:
    """Lower bound on MIP(L) keeping the year rows of `keep_years` (None: keep no year row) and every row that is not
    tied to a year. Returns {"bound", "lp_bound", "status", "seconds", "kept_year_rows", "dropped_multipliers"},
    bounds in the instance's objective units. `integral=False` relaxes X (a strong-duality self-check: the bound
    then equals the LP bound)."""
    t0 = time.time()
    b = model.builder
    s = b.obj_scale
    y, lp_obj = _lp_duals(model)
    row_year = model.aux["row_year"]
    keep = set(keep_years or ())
    relaxed = [r for r, t in row_year.items() if t not in keep]
    cost = np.array(b.cost, dtype=float) * s
    const = b.offset * s
    dropped = 0
    for r in relaxed:
        yr = y[r]
        if yr > 0 and b.row_lo[r] > -INF:
            const += yr * b.row_lo[r]
        elif yr < 0 and b.row_hi[r] < INF:
            const += yr * b.row_hi[r]
        else:
            if yr != 0:
                dropped += 1  # sign without a matching finite bound: drop the multiplier (still valid)
            continue
        for c, v in zip(b.row_idx[r], b.row_val[r]):
            cost[c] -= yr * v
    relaxed_set = set(relaxed)
    kept_rows = [r for r in range(len(b.row_lo)) if r not in relaxed_set]
    h = new_highs(threads)
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(time_limit))
    h.setOptionValue("mip_rel_gap", 1e-9)
    n = len(b.cost)
    h.addVars(n, np.array(b.lb, dtype=np.float64), np.array(b.ub, dtype=np.float64))
    h.changeColsCost(n, np.arange(n, dtype=np.int32), cost)
    if integral:
        ints = np.array([i for i, v in enumerate(b.integer) if v], dtype=np.int32)
        if len(ints):
            h.changeColsIntegrality(len(ints), ints, np.full(len(ints), highspy.HighsVarType.kInteger))
    starts, idx, val = [], [], []
    for r in kept_rows:
        starts.append(len(idx)); idx.extend(b.row_idx[r]); val.extend(b.row_val[r])
    if kept_rows:
        h.addRows(len(kept_rows), np.array([b.row_lo[r] for r in kept_rows], dtype=np.float64),
                  np.array([b.row_hi[r] for r in kept_rows], dtype=np.float64), len(idx),
                  np.array(starts, dtype=np.int32), np.array(idx, dtype=np.int32), np.array(val, dtype=np.float64))
    h.changeObjectiveOffset(const)
    h.run()
    st = h.getModelStatus()
    status = h.modelStatusToString(st)
    if st == highspy.HighsModelStatus.kInfeasible:
        bound = np.inf
    elif not integral:
        bound = h.getInfo().objective_function_value if st == highspy.HighsModelStatus.kOptimal else -np.inf
    else:
        bound = h.getInfo().mip_dual_bound  # valid whether or not the block MIP finished
    return {"bound": bound / s, "lp_bound": lp_obj / s, "status": status, "seconds": round(time.time() - t0, 2),
            "kept_year_rows": len(row_year) - len(relaxed), "dropped_multipliers": dropped}


def cash_floor_bound(inst, L: int) -> float:
    """E37: an O(T) lower bound on MIP(L) that needs no LP. Cumulative spend by year t is at most what every project
    would have spent by t had it started at its earliest allowed year (spend is non-negative), so the closing balance
    is at least max(0, cumF_t - maxcumspend_t). Holding cost is non-decreasing in the balance (non-negative weights),
    so charging that floor in years 0..L-1 and crediting every project its best PV among the starts that finish by L
    bounds every schedule in MIP(L) from below. +inf when some project cannot finish by L; -inf when a sign assumption
    fails (negative spend or weights), so callers fall back to the LP. On the CPO scenarios it prunes half to nine
    tenths of the later last years at a 1% target without building their models."""
    if (inst.backlog_weight < 0 or inst.pv_weight < 0 or any(w < 0 for _, _, w in inst.bands)
            or any(int(sp.min(initial=0)) < 0 for sp in inst.spend)):
        return -np.inf
    pv = 0
    for p in range(inst.n):
        ok = inst.allowed[p] + inst.last_nz[p] <= L
        if not ok.any():
            return np.inf
        pv += int(inst.pv[p][ok].max())
    maxspend = np.zeros(inst.T)
    for p in range(inst.n):
        a = int(inst.allowed[p][0])
        sp = inst.spend[p][:max(0, inst.T - a)].astype(float)
        maxspend[a:a + len(sp)] += sp
    floor = np.maximum(0.0, np.cumsum(inst.F.astype(float)) - np.cumsum(maxspend))
    hold = 0.0
    if inst.penalised:
        for t in range(min(L, inst.T)):
            env = max(0.0, float(inst.F[t]))
            hold += inst.backlog_weight * floor[t]
            excess = max(0.0, floor[t] - env * inst.base_frac)
            for lo, hi, w in inst.bands:
                if excess <= 0:
                    break
                take = excess if hi is None else min(excess, env * (hi - lo))
                hold += take * w
                excess -= take
    return float(hold - inst.pv_weight * pv)
