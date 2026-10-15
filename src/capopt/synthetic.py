"""Synthetic instances in the pack's JSON format: tiny ones for brute-force tests, perturbed ones for tuning."""
from __future__ import annotations

import copy
from typing import Any

import numpy as np

from . import instance as I

BANDS = [  # the pack's band structure (fractions of annual funding) and weights
    (0.1687858669967368, 0.22504782266231574, 1000.0),
    (0.22504782266231574, 0.28130977832789467, 10000.0),
    (0.28130977832789467, 0.3375717339934736, 100000.0),
    (0.3375717339934736, None, 1000000.0),
]


def tiny(n: int = 5, T: int = 8, funding: int = 100, seed: int = 0, penalised: bool = True, growth: float = -0.04,
         pv_weight: float = 1e-4) -> dict[str, Any]:
    """n projects with 1-3 year profiles over T years; total spend ~ 4.5 years of funding so the last year matters.
    growth > 0 makes later starts worth more (as in the pack's growth instances), so deferring can pay."""
    rng = np.random.default_rng(seed)
    projects = []
    budget = funding * 4.5
    for p in range(n):
        d = int(rng.integers(1, 4))
        spend = rng.integers(5, 60, size=d)
        spend = (spend * (budget / n) / spend.sum()).round().astype(int).clip(1)
        allowed = list(range(0, T - d + 1))
        bcr = rng.uniform(1.0, 6.0)
        g = growth * rng.uniform(0.5, 1.5)
        pv = [int(bcr * spend.sum() * 1e4 * (1 + g) ** s) for s in allowed]
        projects.append({"id": f"S{p + 1:03d}", "duration": d, "spend_S": spend.tolist(), "spend_nonzero": [True] * d,
                         "allowed_starts": allowed, "pv_int": pv})
    return {
        "name": f"tiny_n{n}_T{T}_s{seed}{'' if penalised else '_nopen'}", "objective": "production" if penalised else "pv",
        "sense": "minimise", "T": T, "base_year": 2026, "funding_S": [funding] * T, "max_starts_per_year": n,
        "weights": {"backlog_weight": 1.0, "pv_weight": pv_weight} if penalised else {"backlog_weight": 0.0, "pv_weight": 1.0},
        "tiers": {"base_cap_frac": BANDS[0][0],
                  "bands": [{"from_frac": lo, "to_frac": hi, "weight": w if penalised else 0.0} for lo, hi, w in BANDS]},
        "projects": projects,
    }


def perturb(name: str, seed: int, spend_noise: float = 0.1, pv_noise: float = 0.1) -> dict[str, Any]:
    """A benchmark instance with each project's spend and PV scaled by independent factors (held-out tuning sets)."""
    rng = np.random.default_rng(seed)
    raw = copy.deepcopy(I.load(name).raw)
    raw["name"] = f"{name}_perturbed_s{seed}"
    for p in raw["projects"]:
        f = float(rng.uniform(1 - spend_noise, 1 + spend_noise))
        p["spend_S"] = [int(round(a * f)) for a in p["spend_S"]]
        g = float(rng.uniform(1 - pv_noise, 1 + pv_noise))
        p["pv_int"] = [int(round(v * g)) for v in p["pv_int"]]
    return raw


def brute_force(inst: I.Instance) -> tuple[float, np.ndarray | None]:
    """Exact optimum by enumeration (tiny instances only)."""
    from .evaluate import objective
    from .heuristics import kernel as K
    pk = K.pack(inst)
    sizes = [len(a) for a in inst.allowed]
    if np.prod(sizes, dtype=float) > 2e6:
        raise ValueError("too many schedules to enumerate")
    best, arg = np.inf, None
    for idx in np.ndindex(*sizes):
        st = np.array([inst.allowed[p][j] for p, j in enumerate(idx)], dtype=np.int64)
        if np.bincount(st, minlength=inst.T).max() > inst.max_starts:
            continue
        # the compiled kernel knows no hard cash cap; capped instances use the exact evaluator instead
        v = K.cost(pk, st, inst.penalised) if inst.cash_cap is None else objective(inst, st)
        if v < best:
            best, arg = v, st
    return best, arg
