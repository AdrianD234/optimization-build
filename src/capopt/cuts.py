"""Cover cuts on the cumulative-budget rows (Phase 4: bound strengthening).

Let C_p(k) be project p's cumulative spend over its first k+1 build years (0 for k < 0, its total for k >= duration)
and y[p,s] = "p has started by year s" (= sum of x[p,s'] for s' <= s, or the step variable z directly). Starting by s
means p has spent at least C_p(t-s) by year t, so the balance rule (cumulative spend <= cumulative funding F_t) gives,
for any set of pairs (p, s_p) with at most one pair per project:

    sum_p C_p(t - s_p) > F_t   =>   sum_p y[p, s_p] <= |set| - 1          (GUB cover inequality)

Separation (per year t, given an LP point y*): choose at most one s_p per project to make sum (1 - y*[p,s_p]) < 1 while
the weights cover F_t. Heuristic: per project take its heaviest certainly-started pair (y* = 1, cost 0), then add the
cheapest fractional upgrades per unit of extra weight until F_t is exceeded; finally drop the lightest pairs while the
rest still covers (a minimal cover gives the stronger inequality). Cuts are added as rows before the MIP solve: HiGHS
has no user-cut callback.
"""
from __future__ import annotations

from dataclasses import dataclass

import highspy
import numpy as np

from .formulation import Model


@dataclass
class Cover:
    t: int
    pairs: list[tuple[int, int]]  # (project, s_p)

    def violation(self, ystar: dict[tuple[int, int], float]) -> float:
        return sum(ystar[pp] for pp in self.pairs) - (len(self.pairs) - 1)


def started_by(model: Model, x: np.ndarray) -> dict[tuple[int, int], float]:
    """y*[p,s] for every project and option year, from a primal vector of either encoding."""
    out = {}
    for p, (opts, cols) in enumerate(zip(model.options, model.start_cols)):
        v = x[cols]
        cum = np.cumsum(v) if model.encoding == "pulse" else np.append(v, 1.0)
        for j, s in enumerate(opts):
            out[(p, int(s))] = float(min(1.0, cum[j]))
    return out


def cum_spend(model: Model) -> list[np.ndarray]:
    return [np.cumsum(sp) for sp in model.inst.spend]


def separate(model: Model, x: np.ndarray, max_cuts: int = 50, min_violation: float = 1e-4) -> list[Cover]:
    inst = model.inst
    L = model.L if model.L is not None else inst.T - 1
    cumF = np.cumsum(inst.F)
    cs = cum_spend(model)
    ystar = started_by(model, x)
    cuts: list[Cover] = []
    for t in range(L):
        cap = float(cumF[t])
        items: list[tuple[int, int, float, float]] = []  # chosen (project, s, cost = 1 - y*, weight)
        upgrades = []
        for p, opts in enumerate(model.options):
            base, frac = None, []
            for s in opts:
                k = t - int(s)
                if k < 0:
                    break
                w = float(cs[p][min(k, len(cs[p]) - 1)])
                c = 1.0 - ystar[(p, int(s))]
                if w <= 0 or c >= 1 - 1e-9:
                    continue
                if c <= 1e-9:
                    if base is None or w > base[3]:
                        base = (p, int(s), 0.0, w)
                else:
                    frac.append((p, int(s), c, w))
            bw = base[3] if base else 0.0
            ups = [(c / (w - bw), (p, s, c, w)) for p, s, c, w in frac if w > bw]
            if base:
                items.append(base)
            if ups:
                upgrades.append(min(ups))
        W = sum(it[3] for it in items)
        cost = 0.0
        upgrades.sort()
        for _, (p, s, c, w) in upgrades:
            if W > cap or cost + c >= 1 - min_violation:
                break
            old = next((it for it in items if it[0] == p), None)
            if old:
                items.remove(old)
                W -= old[3]
            items.append((p, s, c, w))
            W += w
            cost += c
        if W <= cap or cost >= 1 - min_violation:
            continue
        # minimal cover: drop the lightest items while the rest still exceeds the funding
        items.sort(key=lambda it: it[3])
        kept = list(items)
        for it in items:
            if W - it[3] > cap:
                kept.remove(it)
                W -= it[3]
        cov = Cover(t, [(p, s) for p, s, _, _ in kept])
        if cov.violation(ystar) > min_violation:
            cuts.append(cov)
    cuts.sort(key=lambda c: -c.violation(ystar))
    return cuts[:max_cuts]


def cut_row(model: Model, cov: Cover) -> tuple[float, float, list[int], list[float]]:
    """(lo, hi, cols, vals) of the cover inequality in the model's own variables."""
    coef: dict[int, float] = {}
    rhs = float(len(cov.pairs) - 1)
    for p, s in cov.pairs:
        opts, cols = model.options[p], model.start_cols[p]
        j = int(np.searchsorted(opts, s))
        if model.encoding == "pulse":
            for jj in range(j + 1):
                coef[cols[jj]] = coef.get(cols[jj], 0.0) + 1.0
        elif j < len(cols):
            coef[cols[j]] = coef.get(cols[j], 0.0) + 1.0
        else:  # last option: "started by the last option" is always 1
            rhs -= 1.0
    return -highspy.kHighsInf, rhs, list(coef), list(coef.values())


def root_loop(model: Model, rounds: int = 20, max_cuts: int = 50) -> tuple[list[float], list[Cover]]:
    """LP cut loop: returns the LP bound after each round and the cuts added to model.builder."""
    from .formulation import MipBuilder  # noqa: F401  (rows are appended to the model's builder)
    bounds, added = [], []
    for _ in range(rounds + 1):
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("solve_relaxation", True)
        model.builder.to_highs(h)
        h.run()
        bounds.append(model.user(h.getInfo().objective_function_value))
        x = np.asarray(h.getSolution().col_value)
        cuts = separate(model, x, max_cuts)
        if not cuts or len(bounds) > rounds:
            break
        for c in cuts:
            lo, hi, cols, vals = cut_row(model, c)
            model.builder.row(lo, hi, cols, vals)
        added += cuts
    return bounds, added
