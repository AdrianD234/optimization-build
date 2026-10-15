"""Exact scorer: the same rules and arithmetic order as benchmark_pack/reference/evaluate.py (the referee).

`evaluate` is the readable version used for final scoring; `cash_cost` is the shared year loop. Heuristics use the
numba kernel in heuristics/kernel.py, which tests check against this module.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .instance import Instance

INF = float("inf")


def spend_profile(inst: Instance, starts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    spend = np.zeros(inst.T, dtype=np.int64)
    active = np.zeros(inst.T, dtype=bool)
    for p, s in enumerate(starts):
        d = len(inst.spend[p])
        spend[s:s + d] += inst.spend[p]
        active[s:s + d] |= inst.nonzero[p]
    return spend, active


def cash_cost(inst: Instance, spend: np.ndarray, last: int) -> tuple[float, float, list[float]] | None:
    """(backlog, tier penalty, closing balances) for a spend profile with the given last build year; None if the
    balance goes negative."""
    net, backlog, tiers = 0.0, 0.0, 0.0
    closing: list[float] = []
    F = inst.F
    for t in range(inst.T):
        env = max(0.0, float(F[t]))
        if t <= last:
            net = net + int(F[t]) - int(spend[t])
        if net < -1e-9:
            return None
        if t < last:
            backlog += net
            excess = max(0.0, net - env * inst.base_frac)
            for lo, hi, w in inst.bands:
                if excess <= 0:
                    break
                take = excess if hi is None else min(excess, env * (hi - lo))
                tiers += take * w
                excess -= take
        closing.append(net if t < last else 0.0)
        if t == last:
            net = 0.0
    return backlog, tiers, closing


def evaluate(inst: Instance, starts: np.ndarray | list[int]) -> dict[str, Any]:
    starts = np.asarray(starts, dtype=np.int64)
    out: dict[str, Any] = {"feasible": False}
    if len(starts) != inst.n:
        out["reason"] = "every project needs exactly one start"
        return out
    pv_sum = 0
    for p, s in enumerate(starts):
        try:
            pv_sum += inst.pv_of(p, int(s))
        except KeyError as exc:
            out["reason"] = str(exc)
            return out
    if np.bincount(starts, minlength=inst.T).max() > inst.max_starts:
        out["reason"] = "too many starts in one year"
        return out
    spend, active = spend_profile(inst, starts)
    last = int(np.nonzero(active)[0][-1])
    cc = cash_cost(inst, spend, last)
    if cc is None:
        out["reason"] = "negative balance"
        return out
    backlog, tiers, closing = cc
    pv_term = inst.pv_weight * pv_sum
    out.update({
        "feasible": True,
        "objective": inst.backlog_weight * backlog + tiers - pv_term,
        "components": {"backlog_units": backlog, "tier_units": tiers, "pv_term": pv_term, "pv_int_sum": pv_sum},
        "last_build_year_index": last,
        "closing_balance_units": closing,
        "spend_units": spend.tolist(),
    })
    return out


def objective(inst: Instance, starts: np.ndarray | list[int]) -> float:
    r = evaluate(inst, starts)
    return r["objective"] if r["feasible"] else INF


def gap(obj: float, best: float) -> float:
    """Relative gap of a (minimisation) objective to the best-known value."""
    return (obj - best) / abs(best)
