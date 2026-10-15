"""MIP formulations for HiGHS.

reference_bigm(inst)
    Faithful port of the MindOpt reference model (benchmark_pack/reference/mindopt_model.py). HiGHS has no indicator
    constraints, so each indicator becomes a big-M row with M = the cumulative funding bound the reference uses.
    This is the "HiGHS baseline": same model, different solver.

last_year_model(inst, L, ...)
    capopt's decomposition. Fix the last build year to L: every project must finish by L, funding arrives in years
    0..L, holding cash is charged in years 0..L-1. No activity binaries, no big-M. For any schedule whose true last
    year is L' <= L the model's value is >= its true objective (extra funded years only add holding cost), with
    equality at L' = L, so min over L of the per-L optima is the true optimum. For the no-penalty objective holding
    cash is free, so L = T-1 alone is exact.

Two encodings of the start decision are available:
    pulse : x[p,j] = 1 if p starts at its j-th option (sum_j x = 1)       - the reference encoding
    step  : z[p,j] = 1 if p has started by its j-th option (z monotone)  - aggregated/"step" time-indexed encoding;
            same LP bound, but branching on z splits "start <= s" vs "start > s", which is far better balanced.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import highspy
import numpy as np

from .evaluate import evaluate
from .instance import Instance

INF = highspy.kHighsInf


@dataclass
class MipBuilder:
    cost: list[float] = field(default_factory=list)
    lb: list[float] = field(default_factory=list)
    ub: list[float] = field(default_factory=list)
    integer: list[bool] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    row_lo: list[float] = field(default_factory=list)
    row_hi: list[float] = field(default_factory=list)
    row_idx: list[list[int]] = field(default_factory=list)
    row_val: list[list[float]] = field(default_factory=list)
    offset: float = 0.0
    obj_scale: float = 1.0  # HiGHS sees cost * obj_scale (a power of two, so scaling is exact)

    def auto_scale(self, max_abs: float = 2.0 ** 28) -> float:
        """Scale the objective down by a power of two when its largest coefficient exceeds `max_abs`. The no-penalty
        instances have PV coefficients near 2.4e12, which break HiGHS' dual simplex ("excessive dual values")."""
        big = max((abs(c) for c in self.cost), default=0.0)
        k = 0
        while big * 2.0 ** -k > max_abs:
            k += 1
        self.obj_scale = 2.0 ** -k
        return self.obj_scale

    def var(self, cost: float, lb: float, ub: float, integer: bool = False, name: str = "") -> int:
        self.cost.append(cost); self.lb.append(lb); self.ub.append(ub); self.integer.append(integer)
        self.names.append(name)
        return len(self.cost) - 1

    def new_row(self, lo: float, hi: float) -> int:
        self.row_lo.append(lo); self.row_hi.append(hi); self.row_idx.append([]); self.row_val.append([])
        return len(self.row_lo) - 1

    def add(self, row: int, col: int, val: float) -> None:
        if val != 0:
            self.row_idx[row].append(col); self.row_val[row].append(float(val))

    def row(self, lo: float, hi: float, idx: list[int], vals: list[float]) -> int:
        r = self.new_row(lo, hi)
        for c, v in zip(idx, vals):
            self.add(r, c, v)
        return r

    def to_highs(self, h: highspy.Highs | None = None) -> highspy.Highs:
        h = h or highspy.Highs()
        n = len(self.cost)
        h.addVars(n, np.array(self.lb, dtype=np.float64), np.array(self.ub, dtype=np.float64))
        h.changeColsCost(n, np.arange(n, dtype=np.int32), np.array(self.cost, dtype=np.float64) * self.obj_scale)
        ints = np.array([i for i, b in enumerate(self.integer) if b], dtype=np.int32)
        if len(ints):
            h.changeColsIntegrality(len(ints), ints, np.full(len(ints), highspy.HighsVarType.kInteger))
        starts, idx, val = [], [], []
        for ri, rv in zip(self.row_idx, self.row_val):
            starts.append(len(idx)); idx.extend(ri); val.extend(rv)
        h.addRows(len(self.row_lo), np.array(self.row_lo, dtype=np.float64), np.array(self.row_hi, dtype=np.float64),
                  len(idx), np.array(starts, dtype=np.int32), np.array(idx, dtype=np.int32), np.array(val, dtype=np.float64))
        if self.offset:
            h.changeObjectiveOffset(self.offset * self.obj_scale)
        return h


@dataclass
class Model:
    """A built MIP plus what is needed to translate between schedules and its variables."""
    inst: Instance
    kind: str
    builder: MipBuilder
    L: int | None
    encoding: str
    options: list[np.ndarray]                 # per project: start years available in this model
    start_cols: list[list[int]]               # per project: column per option (pulse) or per option but last (step)
    aux: dict = field(default_factory=dict)   # named continuous columns, e.g. net[t], band[t][i]

    def user(self, v: float) -> float:
        """A HiGHS objective value (scaled) in the instance's objective units."""
        return v / self.builder.obj_scale

    def solver_units(self, v: float) -> float:
        return v * self.builder.obj_scale

    def starts_from_solution(self, col_value: np.ndarray) -> np.ndarray:
        out = np.empty(self.inst.n, dtype=np.int64)
        for p, (opts, cols) in enumerate(zip(self.options, self.start_cols)):
            vals = col_value[cols]
            if self.encoding == "pulse":
                out[p] = opts[int(np.argmax(vals))]
            else:  # step: first option whose "started by" indicator is 1 (the last option is implicit)
                hit = np.nonzero(vals > 0.5)[0]
                out[p] = opts[int(hit[0])] if len(hit) else opts[-1]
        return out

    def solution_vector(self, starts: np.ndarray) -> np.ndarray | None:
        """Full primal vector for a schedule (for MIP starts); None if the schedule does not fit this model."""
        b = self.builder
        x = np.zeros(len(b.cost))
        for p, (opts, cols) in enumerate(zip(self.options, self.start_cols)):
            j = np.searchsorted(opts, starts[p])
            if j >= len(opts) or opts[j] != starts[p]:
                return None
            if self.encoding == "pulse":
                x[cols[j]] = 1.0
            else:
                for jj in range(j, len(cols)):
                    x[cols[jj]] = 1.0
        ev = evaluate(self.inst, starts)
        if not ev["feasible"]:
            return None
        fill = self.aux.get("fill")
        if fill is not None:
            fill(x, starts, ev)
        return x


def _holding_weights(inst: Instance) -> bool:
    return inst.penalised


def _add_start_block(b: MipBuilder, encoding: str, p_id: str, opts: np.ndarray, costs: np.ndarray,
                     entries: list[dict[int, float]], integer: bool = True) -> list[int]:
    """Add one project's start decision. `costs[j]` / `entries[j]` (row -> coefficient) describe choosing option j.
    Returns the columns created."""
    k = len(opts)
    if encoding == "pulse":
        cols = [b.var(float(costs[j]), 0.0, 1.0, integer, f"x[{p_id}|{opts[j]}]") for j in range(k)]
        for j, c in enumerate(cols):
            for r, a in entries[j].items():
                b.add(r, c, a)
        b.row(1.0, 1.0, cols, [1.0] * k)
        return cols
    # step: x_j = z_j - z_{j-1}, z_{-1} = 0, z_{k-1} = 1 (constant). Option j's data enters z_j with (a_j - a_{j+1}).
    b.offset += float(costs[k - 1])
    for r, a in entries[k - 1].items():
        b.row_lo[r] -= a if b.row_lo[r] > -INF else 0.0
        b.row_hi[r] -= a if b.row_hi[r] < INF else 0.0
    cols = []
    for j in range(k - 1):
        c = b.var(float(costs[j] - costs[j + 1]), 0.0, 1.0, integer, f"z[{p_id}|{opts[j]}]")
        rows = set(entries[j]) | set(entries[j + 1])
        for r in rows:
            b.add(r, c, entries[j].get(r, 0.0) - entries[j + 1].get(r, 0.0))
        cols.append(c)
    for j in range(1, len(cols)):
        b.row(-INF, 0.0, [cols[j - 1], cols[j]], [1.0, -1.0])
    return cols


def last_year_model(inst: Instance, L: int | None = None, encoding: str = "step", balance: str = "recursive") -> Model:
    """Fixed-last-year model (see module docstring). L=None means T-1 (the exact model for no-penalty instances)."""
    L = inst.T - 1 if L is None else int(L)
    hold = _holding_weights(inst)
    b = MipBuilder()
    # at most max_starts projects start in any year (binds on the 233-project CPO instances; never on the pack's)
    start_rows = [b.new_row(-INF, float(inst.max_starts)) for _ in range(L + 1)] if inst.n > inst.max_starts else []
    F = inst.F.astype(float)
    cumF = np.cumsum(F)
    env = np.maximum(0.0, F)
    base = env * inst.base_frac
    years = range(L) if hold else range(0)  # years with holding cost: 0..L-1
    # Balance rows. recursive: net[t] - net[t-1] + spend_t(x) = F_t with net >= 0.
    #               cumulative: cumspend_t(x) <= cumF_t, and net is substituted out of the cost and band rows.
    net_cols: list[int] = []
    bal_rows: list[int] = []
    band_cols: list[list[int]] = []
    band_rows: list[int] = []
    x_cost_years = np.zeros(inst.T)  # per-year cost coefficient on cumulative spend (cumulative form)
    if balance == "recursive":
        for t in range(L + 1):
            net_cols.append(b.var(inst.backlog_weight if t < L and hold else 0.0, 0.0, float(cumF[t]), name=f"net[{t}]"))
        for t in range(L + 1):
            r = b.new_row(F[t], F[t])
            b.add(r, net_cols[t], 1.0)
            if t:
                b.add(r, net_cols[t - 1], -1.0)
            bal_rows.append(r)
    else:
        for t in range(L + 1):
            bal_rows.append(b.new_row(-INF, float(cumF[t])))
        if hold:
            b.offset += inst.backlog_weight * float(cumF[:L].sum())
            x_cost_years[:L] = -inst.backlog_weight
    for t in years:
        row_cols = []
        for i, (lo, hi, w) in enumerate(inst.bands):
            ub = float(cumF[t]) if hi is None else float(env[t] * (hi - lo))
            row_cols.append(b.var(w, 0.0, ub, name=f"band{i}[{t}]"))
        band_cols.append(row_cols)
        if balance == "recursive":
            r = b.row(-INF, float(base[t]), [net_cols[t]] + row_cols, [1.0] + [-1.0] * len(row_cols))
        else:  # cumF - cumspend - sum band <= base  <=>  -cumspend - sum band <= base - cumF
            r = b.row(-INF, float(base[t] - cumF[t]), row_cols, [-1.0] * len(row_cols))
        band_rows.append(r)
    options, start_cols = [], []
    for p in range(inst.n):
        ok = inst.allowed[p] + inst.last_nz[p] <= L
        opts = inst.allowed[p][ok]
        if not len(opts):
            raise ValueError(f"{inst.ids[p]} cannot finish by year {L}")
        if np.any(np.diff(opts) != 1):
            raise NotImplementedError("non-contiguous allowed starts")
        costs = -inst.pv_weight * inst.pv[p][ok].astype(float)
        sp = inst.spend[p].astype(float)
        cs = np.cumsum(sp)
        entries = []
        for jj, s in enumerate(opts):
            e: dict[int, float] = {}
            if balance == "recursive":
                for k, a in enumerate(sp):
                    if a and s + k <= L:
                        e[bal_rows[s + k]] = a
            else:
                for t in range(s, L + 1):
                    a = cs[min(t - s, len(cs) - 1)]
                    if a:
                        e[bal_rows[t]] = a
                        if hold and t < L:
                            e[band_rows[t]] = -a
                costs[jj] += float((x_cost_years[s:L + 1] * np.array([cs[min(t - s, len(cs) - 1)] for t in range(s, L + 1)])).sum())
            if start_rows:
                e[start_rows[s]] = 1.0
            entries.append(e)
        start_cols.append(_add_start_block(b, encoding, inst.ids[p], opts, costs, entries))
        options.append(opts)

    def fill(x: np.ndarray, starts: np.ndarray, ev: dict) -> None:
        closing = ev["closing_balance_units"]
        spend = ev["spend_units"]
        run = 0.0
        for t in range(L + 1):
            run += F[t] - spend[t]
            if net_cols:
                x[net_cols[t]] = run
            if t < len(band_cols):
                excess = max(0.0, run - base[t])
                for i, (lo, hi, _w) in enumerate(inst.bands):
                    take = excess if hi is None else min(excess, env[t] * (hi - lo))
                    x[band_cols[t][i]] = take
                    excess -= take
        del closing

    b.auto_scale()
    return Model(inst, "last_year", b, L, encoding, options, start_cols,
                 {"net": net_cols, "bands": band_cols, "fill": fill})


def reference_bigm(inst: Instance) -> Model:
    """The MindOpt reference model with indicators replaced by big-M rows (pulse encoding, all allowed starts)."""
    T = inst.T
    b = MipBuilder()
    F = inst.F.astype(float)
    env = np.maximum(0.0, F)
    net_ub = np.cumsum(env)
    nb = len(inst.bands)
    y = [b.var(0.0, 1.0 if t == 0 else 0.0, 1.0, True, f"active[{t}]") for t in range(T)]
    fund = [b.var(0.0, min(0.0, F[t]), max(0.0, F[t]), name=f"fund[{t}]") for t in range(T)]
    div = [b.var(0.0, 0.0, float(net_ub[t]), name=f"dividend[{t}]") for t in range(T)]
    net = [b.var(0.0, 0.0, float(net_ub[t]), name=f"net[{t}]") for t in range(T)]
    backlog = [b.var(inst.backlog_weight, 0.0, float(net_ub[t]), name=f"backlog[{t}]") for t in range(T)]
    bands = [[b.var(w, 0.0, float(net_ub[t]) if hi is None else float(env[t] * (hi - lo)), name=f"band{i}[{t}]")
              for i, (lo, hi, w) in enumerate(inst.bands)] for t in range(T)]
    for t in range(T - 1):
        b.row(0.0, INF, [y[t], y[t + 1]], [1.0, -1.0])
    for t in range(T):
        b.row(0.0, 0.0, [fund[t], y[t]], [1.0, -F[t]])
    # net[t] - net[t-1] - fund[t] + spend_t(x) + div[t] = 0
    net_rows = []
    for t in range(T):
        r = b.row(0.0, 0.0, [net[t], fund[t], div[t]] + ([net[t - 1]] if t else []), [1.0, -1.0, 1.0] + ([-1.0] if t else []))
        net_rows.append(r)
    for t in range(T):
        M = float(net_ub[t])
        if t < T - 1:
            # y[t+1]=1 => net - base - sum band <= 0
            b.row(-INF, float(env[t] * inst.base_frac) + M, [net[t], y[t + 1]] + bands[t], [1.0, M] + [-1.0] * nb)
            b.row(-INF, M, [div[t], y[t + 1]], [1.0, M])                      # y=1 => div <= 0
            b.row(-M, INF, [backlog[t], net[t], y[t + 1]], [1.0, -1.0, -M])     # y=1 => backlog >= net
            b.row(-INF, M, [backlog[t], net[t], y[t + 1]], [1.0, -1.0, M])      # y=1 => backlog <= net
            b.row(-INF, 0.0, [backlog[t], y[t + 1]], [1.0, -M])                 # y=0 => backlog <= 0
        else:
            for c in bands[t]:
                b.ub[c] = 0.0
            b.ub[backlog[t]] = 0.0
    options, start_cols = [], []
    for p in range(inst.n):
        opts = inst.allowed[p]
        cols = [b.var(-inst.pv_weight * float(v), 0.0, 1.0, True, f"x[{inst.ids[p]}|{s}]") for s, v in zip(opts, inst.pv[p])]
        for c, s in zip(cols, opts):
            for k, a in enumerate(inst.spend[p]):
                if inst.nonzero[p][k]:
                    b.add(net_rows[s + k], c, float(a))
                    b.row(0.0, INF, [y[s + k], c], [1.0, -1.0])  # active link
        b.row(1.0, 1.0, cols, [1.0] * len(cols))
        options.append(opts); start_cols.append(cols)
    if inst.n > inst.max_starts:  # start-count cap per year (as in the reference model)
        for t in range(T):
            cols = [c for opts, cs in zip(options, start_cols) for s, c in zip(opts, cs) if s == t]
            if len(cols) > inst.max_starts:
                b.row(-INF, float(inst.max_starts), cols, [1.0] * len(cols))

    def fill(x: np.ndarray, starts: np.ndarray, ev: dict) -> None:
        last = ev["last_build_year_index"]
        closing = ev["closing_balance_units"]
        run = 0.0
        for t in range(T):
            x[y[t]] = 1.0 if t <= last else 0.0
            f = F[t] if t <= last else 0.0
            x[fund[t]] = f
            run += f - ev["spend_units"][t]
            d = run if t == last else 0.0
            x[div[t]] = d
            run -= d
            x[net[t]] = run if t <= last else 0.0
            x[backlog[t]] = closing[t] if t < last else 0.0
            excess = max(0.0, closing[t] - env[t] * inst.base_frac) if t < last else 0.0
            for i, (lo, hi, _w) in enumerate(inst.bands):
                take = excess if hi is None else min(excess, env[t] * (hi - lo))
                x[bands[t][i]] = take
                excess -= take

    b.auto_scale()
    return Model(inst, "reference_bigm", b, None, "pulse", options, start_cols, {"fill": fill})
