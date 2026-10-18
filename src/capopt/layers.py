"""Distribution layers: soft (and, for baselines, hard) linear extensions of the scheduling model.

An instance may carry a "layers" section. Every layer is linear in the start choice and adds only continuous columns,
so the fixed-last-year decomposition, the cash-floor bound, ALNS and the certified-gap presets carry over unchanged:
spend in a window does not depend on the last build year, so MIP(L) still equals the true objective at a schedule's own
L and over-estimates it at a later one. Three generic primitives, which `evaluate` scores exactly and
`formulation.last_year_model` builds as rows:

pv_floor   {"pv": [[int per allowed start] per project], "min": int}
           A hard floor on a second PV vector: sum_p pv[p][start_p] >= min (the epsilon constraint of dimension
           scoring, where the objective's pv_int is the dimension-weighted PV and this vector is the Total PV).
shortfall  [{"label", "members": [project index], "years": [a, b], "target": units, "free": units,
             "segments": [[width units or null, cost per unit], ...]}]
           A convex piecewise-linear penalty on how far the members' spend in build years a..b falls short of a fixed
           target: deficit = max(0, target - free - spend), charged segment by segment at increasing costs. Fixed
           targets (an entitlement base, not realised spend) keep it linear and shortfall-only keeps it one-sided.
bands      [{"label", "members", "years": [a, b], "share": s, "tol": t, "floor": units}]
           Hard two-sided bands around a share of *realised* spend in a..b (the CPO's activity-class baseline):
           |spend_S - s * spend_all| <= t * s * spend_all + floor. Linear, since both sides are linear in the starts.

Builders (`dimension_weights`, `allocation_rows`, `hard_bands`, `with_layers`) turn attributes (dimension PVs, classes,
regions, shares) into these primitives; `report` summarises a schedule against them. Units follow the instance: spend in
$0.01M, PV as integer $ x 10,000, penalty costs in objective units.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import numpy as np

PV_ROW_SCALE = 1e-10  # pv_int ($ x 1e4) -> $M in the floor row, so its coefficients sit near the spend rows'
BAND_TOL_UNITS = 1.0  # evaluator slack on hard bands ($0.01M): MIP rows hold to about 1e-7 of their scale
PV_FLOOR_RTOL = 1e-7  # evaluator slack on the PV floor, relative (about $10k on $95B)


@dataclass
class Layers:
    groups: list[np.ndarray]                         # member index arrays (deduplicated)
    group_of: list[list[int]]                        # per project: the groups it belongs to
    short: list[dict] = field(default_factory=list)  # g, a, b, lo (= target - free), widths, costs, label
    bands: list[dict] = field(default_factory=list)  # g, a, b, share, tol, floor, label
    pv_floor: list[np.ndarray] | None = None
    pv_min: float = 0.0
    worst: list[dict] = field(default_factory=list)  # cost, rows [g, a, b, lo, norm], label

    @property
    def empty(self) -> bool:
        return not self.short and not self.bands and self.pv_floor is None and not self.worst


def parse(raw: dict, n: int, T: int) -> Layers | None:
    if not raw:
        return None
    key_to_g: dict[tuple, int] = {}
    groups: list[np.ndarray] = []

    def gid(members) -> int:
        k = tuple(sorted(int(m) for m in members))
        if k not in key_to_g:
            key_to_g[k] = len(groups)
            groups.append(np.array(k, dtype=np.int64))
        return key_to_g[k]

    lay = Layers(groups, [])
    for r in raw.get("shortfall", []):
        lo = float(r["target"]) - float(r.get("free", 0.0))
        ref_share = float(r.get("ref_share", 0.0))
        if lo <= 0 and ref_share <= 0:
            continue  # the free tolerance covers the whole target: never charged
        segs = r["segments"]
        costs = np.array([float(c) for _, c in segs])
        if np.any(costs < 0) or np.any(np.diff(costs) < 0):
            raise ValueError(f"shortfall {r.get('label')}: segment costs must be non-negative and non-decreasing")
        widths = np.array([np.inf if w is None else float(w) for w, _ in segs])
        a, b = int(r["years"][0]), min(int(r["years"][1]), T - 1)
        lay.short.append({"g": gid(r["members"]), "a": a, "b": b, "lo": lo, "widths": widths, "costs": costs,
                          "label": r.get("label", ""), "ref_share": ref_share,
                          "gref": gid(r["ref_members"]) if ref_share > 0 else -1})
    for r in raw.get("bands", []):
        a, b = int(r["years"][0]), min(int(r["years"][1]), T - 1)
        lay.bands.append({"g": gid(r["members"]), "a": a, "b": b, "share": float(r["share"]), "tol": float(r["tol"]),
                          "floor": float(r.get("floor", 0.0)), "label": r.get("label", "")})
    for blk in raw.get("worst", []):
        rows = [{"g": gid(r["members"]), "a": int(r["years"][0]), "b": min(int(r["years"][1]), T - 1),
                 "lo": float(r["lo"]), "norm": float(r["norm"])} for r in blk["rows"]]
        if float(blk["cost"]) < 0:
            raise ValueError("worst-case cost must be non-negative")
        lay.worst.append({"cost": float(blk["cost"]), "rows": rows, "label": blk.get("label", "")})
    if raw.get("pv_floor"):
        lay.pv_floor = [np.asarray(v, dtype=np.int64) for v in raw["pv_floor"]["pv"]]
        lay.pv_min = float(raw["pv_floor"]["min"])
    lay.group_of = [[] for _ in range(n)]
    for g, mem in enumerate(groups):
        for p in mem:
            lay.group_of[int(p)].append(g)
    return None if lay.empty else lay


def window_spend(cs: np.ndarray, s: int, a: int, b: int) -> float:
    """Spend in build years a..b of a project with cumulative profile `cs` started in year s."""
    hi, lo = b - s, a - s - 1
    if hi < 0:
        return 0.0
    top = cs[min(hi, len(cs) - 1)]
    return float(top - (cs[min(lo, len(cs) - 1)] if lo >= 0 else 0.0))


def penalty_of(deficit: float, widths: np.ndarray, costs: np.ndarray) -> float:
    out, left = 0.0, deficit
    for w, c in zip(widths, costs):
        if left <= 0:
            break
        take = min(left, w)
        out += take * c
        left -= take
    return out


def group_spend(inst, lay: Layers, starts: np.ndarray) -> np.ndarray:
    """[groups, T] spend by build year of each group's members."""
    out = np.zeros((len(lay.groups), inst.T))
    for p, s in enumerate(starts):
        gs = lay.group_of[p]
        if gs:
            sp = inst.spend[p]
            for g in gs:
                out[g, s:s + len(sp)] += sp
    return out


def score(inst, starts: np.ndarray, spend_all: np.ndarray, detail: bool = False) -> dict[str, Any]:
    """{"feasible", "penalty", "reason"} for a schedule (plus per-row "rows" with detail=True)."""
    lay: Layers = inst.layers
    out: dict[str, Any] = {"feasible": True, "penalty": 0.0}
    if lay.pv_floor is not None:
        tot = sum(int(lay.pv_floor[p][int(np.searchsorted(inst.allowed[p], s))]) for p, s in enumerate(starts))
        out["pv_floor_value"] = tot
        if tot < lay.pv_min * (1 - PV_FLOOR_RTOL):
            return {"feasible": False, "penalty": 0.0, "reason": f"PV floor: {tot} < {lay.pv_min:.0f}"}
    gs = np.cumsum(group_spend(inst, lay, starts), axis=1) if lay.groups else None
    cum_all = np.cumsum(spend_all.astype(float))

    def span(c: np.ndarray, a: int, b: int) -> float:
        return float(c[b] - (c[a - 1] if a > 0 else 0.0))

    rows = []
    for r in lay.bands:
        sp, tot = span(gs[r["g"]], r["a"], r["b"]), span(cum_all, r["a"], r["b"])
        lo = (1 - r["tol"]) * r["share"] * tot - r["floor"]
        hi = (1 + r["tol"]) * r["share"] * tot + r["floor"]
        if detail:
            rows.append({"kind": "band", "label": r["label"], "spend": sp, "lo": lo, "hi": hi})
        if sp < lo - BAND_TOL_UNITS or sp > hi + BAND_TOL_UNITS:
            return {"feasible": False, "penalty": 0.0, "reason": f"band {r['label']}: {sp:.0f} outside [{lo:.0f}, {hi:.0f}]"}
    pen = 0.0
    for r in lay.short:
        sp = span(gs[r["g"]], r["a"], r["b"])
        lo = r["lo"] + (r["ref_share"] * span(gs[r["gref"]], r["a"], r["b"]) if r["gref"] >= 0 else 0.0)
        d = max(0.0, lo - sp)
        c = penalty_of(d, r["widths"], r["costs"]) if d > 0 else 0.0
        pen += c
        if detail:
            rows.append({"kind": "shortfall", "label": r["label"], "spend": sp, "lo": lo, "deficit": d, "cost": c})
    for blk in lay.worst:
        m = max([0.0] + [(r["lo"] - span(gs[r["g"]], r["a"], r["b"])) / r["norm"] for r in blk["rows"]])
        pen += blk["cost"] * m
        if detail:
            rows.append({"kind": "worst", "label": blk["label"], "max_norm_shortfall": m, "cost": blk["cost"] * m})
    out["penalty"] = pen
    if detail:
        out["rows"] = rows
    return out


class ModelRows:
    """The layer rows of one fixed-last-year model: created before the start blocks, then each option's coefficients
    go into that option's row map (so both encodings handle them like the balance rows)."""

    def __init__(self, b, inst, INF: float):
        self.inst, self.lay = inst, inst.layers
        self.cs = [np.cumsum(sp.astype(float)) for sp in inst.spend]
        self.short_rows: list[tuple[int, dict, list[int]]] = []
        for r in self.lay.short:
            row = b.new_row(r["lo"], INF)
            cols = [b.var(float(c), 0.0, float(w), name=f"short[{r['label']}]{k}")
                    for k, (w, c) in enumerate(zip(r["widths"], r["costs"]))]
            for c in cols:
                b.add(row, c, 1.0)
            self.short_rows.append((row, r, cols))
        self.band_rows = []
        for r in self.lay.bands:
            lo_row = b.new_row(-r["floor"], INF)    # spend_S - (1 - tol) s spend_all >= -floor
            hi_row = b.new_row(-INF, r["floor"])    # spend_S - (1 + tol) s spend_all <= floor
            self.band_rows.append((lo_row, hi_row, r, set(int(m) for m in self.lay.groups[r["g"]])))
        self.pv_row = b.new_row(self.lay.pv_min * PV_ROW_SCALE, INF) if self.lay.pv_floor is not None else None
        self.worst_rows: list[tuple[int, list[tuple[int, dict]]]] = []  # (M column, [(row, r)])
        for k, blk in enumerate(self.lay.worst):  # spend_S / norm + M >= lo / norm for every row; M >= 0 costs `cost`
            m = b.var(blk["cost"], 0.0, INF, name=f"worst[{blk['label'] or k}]")
            rs = []
            for r in blk["rows"]:
                row = b.new_row(r["lo"] / r["norm"], INF)
                b.add(row, m, 1.0)
                rs.append((row, r))
            self.worst_rows.append((m, rs))

    def entries(self, p: int, j_allowed: int, s: int, e: dict[int, float]) -> None:
        """Add option (project p, allowed index j_allowed, start s) to the row map e."""
        cs = self.cs[p]
        for g in self.lay.group_of[p]:
            for row, r, _ in self.short_rows:  # spend_S - ref_share x spend_ref + u >= target - free
                coef = (1.0 if r["g"] == g else 0.0) - (r["ref_share"] if r["gref"] == g else 0.0)
                if coef:
                    v = window_spend(cs, s, r["a"], r["b"])
                    if v:
                        e[row] = e.get(row, 0.0) + coef * v
        for lo_row, hi_row, r, mem in self.band_rows:
            v = window_spend(cs, s, r["a"], r["b"])
            if v:
                inside = 1.0 if p in mem else 0.0
                e[lo_row] = e.get(lo_row, 0.0) + v * (inside - (1 - r["tol"]) * r["share"])
                e[hi_row] = e.get(hi_row, 0.0) + v * (inside - (1 + r["tol"]) * r["share"])
        if self.pv_row is not None:
            v = float(self.lay.pv_floor[p][j_allowed]) * PV_ROW_SCALE
            if v:
                e[self.pv_row] = v
        groups = self.lay.group_of[p]
        for _, rs in self.worst_rows:
            for row, r in rs:
                if r["g"] in groups:
                    v = window_spend(cs, s, r["a"], r["b"])
                    if v:
                        e[row] = e.get(row, 0.0) + v / r["norm"]

    def fill(self, x: np.ndarray, starts: np.ndarray) -> None:
        gs = np.cumsum(group_spend(self.inst, self.lay, starts), axis=1)
        for _, r, cols in self.short_rows:
            c = gs[r["g"]]
            sp = float(c[r["b"]] - (c[r["a"] - 1] if r["a"] > 0 else 0.0))
            lo = r["lo"]
            if r["gref"] >= 0:
                cr = gs[r["gref"]]
                lo += r["ref_share"] * float(cr[r["b"]] - (cr[r["a"] - 1] if r["a"] > 0 else 0.0))
            left = max(0.0, lo - sp)
            for col, w in zip(cols, r["widths"]):
                take = min(left, w)
                x[col] = take
                left -= take
        for m, rs in self.worst_rows:
            x[m] = max([0.0] + [(r["lo"] - float(gs[r["g"]][r["b"]] - (gs[r["g"]][r["a"] - 1] if r["a"] > 0 else 0.0)))
                                / r["norm"] for _, r in rs])


# --------------------------------------------------------------------------------------------------------------------
# Builders: attributes -> primitives (no private data in here; the CPO attributes come from tools/extract_cpo_attrs.py)
# --------------------------------------------------------------------------------------------------------------------

def with_layers(raw: dict, layers: dict, name: str, pv_int: list[list[int]] | None = None) -> dict:
    """A copy of an instance dict with a layers section (merged into any it has) and optionally a new objective PV."""
    out = copy.deepcopy(raw)
    out["name"] = name
    cur = out.setdefault("layers", {})
    for k, v in layers.items():
        if isinstance(v, list):
            cur[k] = cur.get(k, []) + v
        else:
            cur[k] = v
    if pv_int is not None:
        for p, v in zip(out["projects"], pv_int):
            assert len(v) == len(p["allowed_starts"])
            p["pv_int"] = [int(a) for a in v]
    return out


def max_window_spend(raw: dict, members, a: int, b: int) -> float:
    """The largest spend one member can put into build years a..b over its allowed starts (the lumpiness floor)."""
    best = 0.0
    for p in members:
        pr = raw["projects"][p]
        cs = np.cumsum(np.asarray(pr["spend_S"], dtype=float))
        for s in pr["allowed_starts"]:
            best = max(best, window_spend(cs, int(s), a, b))
    return best


def funding_base(raw: dict, spans: list[tuple[int, int]]) -> list[float]:
    """Fixed entitlement base per span: funding that falls in it, with cumulative funding capped at the programme's
    total cost (surplus funding after the programme can be paid for is not an entitlement to spend)."""
    F = np.asarray(raw["funding_S"], dtype=float)
    total = float(sum(sum(p["spend_S"]) for p in raw["projects"]))
    cum = np.minimum(np.cumsum(np.maximum(F, 0.0)), total)
    return [float(cum[min(b, len(F) - 1)] - (cum[a - 1] if a > 0 else 0.0)) for a, b in spans]


def allocation_rows(raw: dict, groups: dict[str, list[int]], shares: dict[str, float],
                    spans: list[tuple[int, int]], weights: list[float], price_pct: float, pv_star_obj: float,
                    tol: float = 0.20, lumpiness: str | None = "year", lump_cap: float = 0.5,
                    slopes: tuple[float, ...] = (1.0, 4.0, 12.0),
                    breaks: tuple[float, ...] = (0.10, 0.20), norm_floor: float = 2_500.0, min_share: float = 0.0,
                    prefix: str = "", cap_attainable: bool = True, endogenous: bool = False) -> list[dict]:
    """Shortfall rows of the generic allocation layer for subsets `groups` with target `shares` over `spans` (build
    year ranges: windows, or cumulative checkpoints (0, c - 1)).

    Target = share x fixed base (`funding_base`). Free within max(tol x target, lump), lump = the lumpiness floor:
    "year" = the largest single-year spend of a member (a one-year shift of a start moves the span's spend by at most
    that), "window" = the most one member can put in the span; capped at lump_cap x target (single members can
    out-spend a small subset's whole target, which would switch its row off); None = no floor. Beyond that the deficit is
    charged at `slopes` (relative), with breakpoints at `breaks` x the normaliser N = max(target, norm_floor). Pricing
    in PV-equivalent units: a deficit of 1% of N in a span of weight weights[0] (on the first slope) costs `price_pct` %
    of `pv_star_obj` (the objective units
    of total PV, pv_weight x PV*). Spans after the first scale by weights[w] / weights[0].
    cap_attainable: the target never exceeds what the subset's own pipeline can spend in the span (the sum over members
    of the most each can put there): a population-based target can exceed a region's whole pipeline, and a penalty no
    schedule can avoid only shifts the objective and blurs its relative gap. Rows carry "norm" (N) for reporting.
    endogenous: the target is the subset's share of what the covered projects (all groups' members) actually spend in
    the span, s / sum(shares) x spend_covered, instead of the fixed entitlement; it separates misallocation from
    under-spending (MULTI_OBJECTIVE_LAYERS §3). Still linear. The free zone and the price stay sized on the fixed
    target."""
    base = funding_base(raw, spans)
    covered = sorted({p for mem in groups.values() for p in mem})
    tot_share = sum(shares[k] for k in groups)
    rows = []
    for name, mem in groups.items():
        s = shares[name]
        if s < min_share or s <= 0:
            continue
        for w, ((a, b), B) in enumerate(zip(spans, base)):
            target = s * B
            if cap_attainable:
                target = min(target, sum(max_window_spend(raw, [p], a, b) for p in mem))
            if target <= 0:
                continue
            free = tol * target
            if lumpiness == "window":
                free = max(free, min(lump_cap * target, max_window_spend(raw, mem, a, b)))
            elif lumpiness == "year":
                free = max(free, min(lump_cap * target, max(max(raw["projects"][p]["spend_S"]) for p in mem)))
            N = max(target, norm_floor)
            unit = (weights[w] / weights[0]) * (price_pct / 100.0) * pv_star_obj / (0.01 * N)
            widths = [bk * N for bk in breaks]
            widths = [widths[0]] + [hi - lo for lo, hi in zip(widths, widths[1:])] + [None]
            if endogenous:
                extra = {"target": 0.0, "ref_members": covered, "ref_share": s / tot_share}
            else:
                extra = {"target": target}
            rows.append({"label": f"{prefix}{name}|w{w}", "members": list(mem), "years": [a, b],
                         **extra, "free": free, "norm": N,
                         "segments": [[wd, unit * sl] for wd, sl in zip(widths, slopes)]})
    return rows


def worst_case_block(rows: list[dict], price_pct: float, pv_star_obj: float, label: str = "worst") -> dict:
    """A worst-case term over allocation rows (MULTI_OBJECTIVE §3: so small subsets count): M >= every row's
    normalised shortfall beyond its free zone, (target - free - spend) / N, and M is charged so that a worst
    shortfall of 1% of N costs `price_pct` % of `pv_star_obj`. Endogenous-base rows are skipped (fixed targets only)."""
    return {"label": label, "cost": price_pct / 100.0 * pv_star_obj / 0.01,
            "rows": [{"members": r["members"], "years": r["years"], "lo": r["target"] - r["free"], "norm": r["norm"]}
                     for r in rows if "ref_members" not in r]}


def hard_bands(groups: dict[str, list[int]], shares: dict[str, float], spans: list[tuple[int, int]],
               tol: float = 0.10, floor: float = 2_500.0, min_share: float = 0.02, prefix: str = "") -> list[dict]:
    """The CPO's hard activity-class bands: share of realised span spend within +-tol, plus an absolute floor."""
    return [{"label": f"{prefix}{name}|w{w}", "members": list(mem), "years": [a, b], "share": shares[name],
             "tol": tol, "floor": floor}
            for name, mem in groups.items() if shares[name] >= min_share
            for w, (a, b) in enumerate(spans)]


def cost_shares(raw: dict, groups: dict[str, list[int]]) -> dict[str, float]:
    tot = {k: float(sum(sum(raw["projects"][p]["spend_S"]) for p in mem)) for k, mem in groups.items()}
    s = sum(tot.values())
    return {k: v / s for k, v in tot.items()}


def dimension_weights(pv0_dim: np.ndarray, pv0_others: np.ndarray, exp_ratio: float = 1.0, exp_rank: float = 0.5,
                      clip: tuple[float, float] = (1.0, 2.0)) -> np.ndarray:
    """Per-project weights for dimension scoring: (PV0_dim / mean PV0 of the other dimensions)^exp_ratio x
    (percentile rank of PV0_dim)^exp_rank, normalised to mean 1, clipped to `clip`."""
    pv0_dim = np.asarray(pv0_dim, dtype=float)
    ratio = pv0_dim / np.maximum(np.asarray(pv0_others, dtype=float), 1e-12)
    order = np.argsort(np.argsort(pv0_dim, kind="stable"), kind="stable")
    rank = (order + 1) / len(pv0_dim)
    w = np.power(np.maximum(ratio, 0.0), exp_ratio) * np.power(rank, exp_rank)
    w = w / max(w.mean(), 1e-12)
    return np.clip(w, *clip)


def report(inst, starts) -> dict[str, Any]:
    """A schedule against an instance's layers: feasibility, penalty, PV floor value and the per-row detail."""
    from .evaluate import spend_profile
    starts = np.asarray(starts, dtype=np.int64)
    spend, _ = spend_profile(inst, starts)
    return score(inst, starts, spend, detail=True)


# --------------------------------------------------------------------------------------------------------------------
# E55: MIR cuts on the shortfall rows (the dual side of the Jensen / lumpiness gap, MULTI_OBJECTIVE_LAYERS §2)
# --------------------------------------------------------------------------------------------------------------------

def _pulse_values(model, x: np.ndarray) -> list[np.ndarray]:
    """Per project, the value of 'starts at option j' from a model vector (pulse columns, or step differences)."""
    out = []
    for cols in model.start_cols:
        v = np.asarray(x[cols], dtype=float)
        if model.encoding == "pulse":
            out.append(v)
        else:  # z_j = started by option j for j < K-1, z_{K-1} = 1
            out.append(np.diff(np.concatenate(([0.0], v, [1.0]))))
    return out


def _to_model_row(model, terms: dict[int, dict[int, float]], ucols: list[int], ucoef: float,
                  rhs: float) -> tuple[float, float, list[int], list[float]]:
    """sum_p sum_j c_pj [p starts at option j] + ucoef sum(u) >= rhs, in the model's own columns."""
    coef: dict[int, float] = {}
    for p, cj in terms.items():
        cols, K = model.start_cols[p], len(model.options[p])
        c = np.array([cj.get(j, 0.0) for j in range(K)])
        if model.encoding == "pulse":
            for j in range(K):
                if c[j]:
                    coef[cols[j]] = coef.get(cols[j], 0.0) + float(c[j])
        else:  # sum_j c_j (z_j - z_{j-1}) = sum_{j<K-1} (c_j - c_{j+1}) z_j + c_{K-1}
            for j in range(K - 1):
                d = float(c[j] - c[j + 1])
                if d:
                    coef[cols[j]] = coef.get(cols[j], 0.0) + d
            rhs -= float(c[K - 1])
    for u in ucols:
        coef[u] = coef.get(u, 0.0) + ucoef
    return float(rhs), np.inf, list(coef), list(coef.values())


def shortfall_mir(model, x: np.ndarray, max_cuts: int = 100, min_violation: float = 1e-4) -> list[tuple]:
    """Most violated MIR cut per fixed-target shortfall row at the LP point x. For a row
    sum_{p,j} a_pj [p starts at j] + sum_k u_k >= D (a >= 0, u >= 0) and a divisor d > 0, with f = frac(D/d) > 0,
        sum (floor(a/d) + min(1, frac(a/d)/f)) [p starts at j] + sum_k u_k / (d f) >= ceil(D/d)
    is valid (MIR of the covering knapsack). Divisors tried: the largest member spends in the row and D/k."""
    lay = model.inst.layers
    if lay is None or not lay.short:
        return []
    names = model.builder.names
    xp = _pulse_values(model, x)
    cs = [np.cumsum(sp.astype(float)) for sp in model.inst.spend]
    cuts = []
    for r in lay.short:
        D = r["lo"]
        if r["gref"] >= 0 or D <= 0:
            continue
        ucols = [i for i, nm in enumerate(names) if nm.startswith(f"short[{r['label']}]")]
        uval = float(sum(x[c] for c in ucols))
        aj = {int(p): [window_spend(cs[p], int(s), r["a"], r["b"]) for s in model.options[p]]
              for p in lay.groups[r["g"]]}
        cands = sorted({a for v in aj.values() for a in v if a > 0}, reverse=True)[:12]
        cands += [D / k for k in (1, 2, 3, 4, 6, 8)]
        best = None
        for d in cands:
            q = D / d
            f = q - np.floor(q)
            if f < 1e-6 or f > 1 - 1e-6:
                continue
            g = {p: [np.floor(a / d) + min(1.0, (a / d - np.floor(a / d)) / f) for a in v] for p, v in aj.items()}
            lhs = sum(float(np.dot(g[p], xp[p])) for p in g) + uval / (d * f)
            viol = float(np.ceil(q)) - lhs
            if viol > min_violation and (best is None or viol > best[0]):
                best = (viol, g, 1.0 / (d * f), float(np.ceil(q)))
        if best:
            viol, g, ucoef, rhs = best
            terms = {p: {j: c for j, c in enumerate(v) if c} for p, v in g.items()}
            cuts.append((viol, _to_model_row(model, terms, ucols, ucoef, rhs)))
    cuts.sort(key=lambda c: -c[0])
    return [c for _, c in cuts[:max_cuts]]


def mir_loop(model, rounds: int = 5) -> tuple[list[float], list[tuple]]:
    """Root LP loop: separate shortfall MIR cuts, add them as rows, re-solve. Returns the LP bound per round and the
    rows added (lo, hi, cols, vals) for shipping to the workers."""
    from .solve import new_highs
    bounds, added = [], []
    for _ in range(rounds + 1):
        h = new_highs(1)
        h.setOptionValue("output_flag", False)
        h.setOptionValue("solve_relaxation", True)
        model.builder.to_highs(h)
        h.run()
        bounds.append(model.user(h.getInfo().objective_function_value))
        if len(bounds) > rounds:
            break
        rows = shortfall_mir(model, np.asarray(h.getSolution().col_value))
        if not rows:
            break
        for lo, hi, cols, vals in rows:
            model.builder.row(lo, hi, cols, vals)
        added += rows
    return bounds, added
