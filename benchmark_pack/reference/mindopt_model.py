"""Reference MIP (MindOpt) for the benchmark instances - the formulation the original pipeline solves.

Variables : x[p,s] binary (project p starts at s); y[t] binary (programme active); fund[t]; net[t] >= 0;
            dividend[t] >= 0; backlog[t] >= 0; band[t,i] >= 0 (cash held in penalty band i).
Constraints: sum_s x[p,s] = 1; y[t] >= y[t+1]; y[0] = 1; y[t] >= x[p,s] for every year p spends in;
            fund[t] = F[t] y[t]; net[t] = net[t-1] + fund[t] - spend[t] - dividend[t];
            if y[t+1]=1: net[t] <= base_cap[t] + sum_i band[t,i];  dividend[t] = 0;  backlog[t] = net[t]
            if y[t+1]=0: backlog[t] = 0.  Last year: bands = 0, backlog = 0. <= 100 starts per year.
Objective (min): backlog_weight * sum backlog + sum_i w_i * sum_t band[t,i] - pv_weight * sum pv_int[p,s] x[p,s]

Requires mindoptpy and a MindOpt licence (floating licence: ONE solve at a time, keep all solves in one process).
"""
from __future__ import annotations

import os
import re
import sys
import time
from typing import Any

from mindoptpy import MDO, Model, quicksum


def build(inst: dict[str, Any], threads: int | None = None) -> dict[str, Any]:
    T = inst["T"]
    F = [float(f) for f in inst["funding_S"]]
    env = [max(0.0, f) for f in F]
    tiers = inst["tiers"]
    base_frac = tiers["base_cap_frac"]
    bands = tiers["bands"]
    w = inst["weights"]
    m = Model(inst["name"])
    threads = threads or (os.cpu_count() or 1)
    for k, v in (("NumThreads", threads), ("OutputFlag", 1), ("LogToConsole", 1), ("MIP/AutoConfiguration", 1)):
        m.setParam(k, v)
    if threads > 1:
        m.setParam("MIP/RootParallelism", min(256, threads))
    net_ub, acc = [], 0.0
    for t in range(T):
        acc += max(0.0, F[t])
        net_ub.append(acc)
    x: dict[tuple[str, int], Any] = {}
    spend_terms: list[list[tuple[Any, int]]] = [[] for _ in range(T)]
    for p in inst["projects"]:
        for s, pv in zip(p["allowed_starts"], p["pv_int"]):
            var = m.addVar(vtype="B", lb=0.0, ub=1.0, name=f"x[{p['id']}|{s}]")
            x[(p["id"], s)] = var
            for k, a in enumerate(p["spend_S"]):
                if p["spend_nonzero"][k]:
                    spend_terms[s + k].append((var, a))
    y = [m.addVar(vtype="B", lb=0.0, ub=1.0, name=f"active[{t}]") for t in range(T)]
    for t in range(T - 1):
        m.addConstr(y[t] >= y[t + 1], name=f"active_noninc[{t}]")
    m.addConstr(y[0] == 1.0, name="active_start")
    spend_expr = [quicksum(a * v for v, a in spend_terms[t]) if spend_terms[t] else 0.0 for t in range(T)]
    for t in range(T):
        for k, (v, _a) in enumerate(spend_terms[t]):
            m.addConstr(y[t] >= v, name=f"active_link[{t}|{k}]")
    fund = []
    for t in range(T):
        f = m.addVar(vtype="C", lb=min(0.0, F[t]), ub=max(0.0, F[t]), name=f"fund[{t}]")
        m.addConstr(f == F[t] * y[t], name=f"fund_fixed[{t}]")
        fund.append(f)
    div = [m.addVar(vtype="C", lb=0.0, ub=net_ub[t], name=f"dividend[{t}]") for t in range(T)]
    net = [m.addVar(vtype="C", lb=0.0, ub=net_ub[t], name=f"net[{t}]") for t in range(T)]
    m.addConstr(net[0] == fund[0] - spend_expr[0] - div[0], name="net0")
    for t in range(1, T):
        m.addConstr(net[t] == net[t - 1] + fund[t] - spend_expr[t] - div[t], name=f"net[{t}]")
    band_vars: list[list[Any]] = []
    tier_pen = 0.0
    for t in range(T):
        row = []
        for i, b in enumerate(bands):
            ub = net_ub[t] if b["to_frac"] is None else env[t] * (b["to_frac"] - b["from_frac"])
            v = m.addVar(vtype="C", lb=0.0, ub=ub, name=f"band{i}[{t}]")
            row.append(v)
            tier_pen = tier_pen + float(b["weight"]) * v
        band_vars.append(row)
        if t < T - 1:
            m.addGenConstrIndicator(y[t + 1], True, net[t] - env[t] * base_frac - quicksum(row), MDO.LESS_EQUAL, 0.0, name=f"cap[{t}]")
        else:
            for v in row:
                m.addConstr(v == 0.0, name=f"band_last[{t}]")
    for t in range(T - 1):
        m.addGenConstrIndicator(y[t + 1], True, div[t], MDO.LESS_EQUAL, 0.0, name=f"div_zero[{t}]")
    backlog = [m.addVar(vtype="C", lb=0.0, ub=net_ub[t], name=f"backlog[{t}]") for t in range(T)]
    for t in range(T):
        if t < T - 1:
            m.addGenConstrIndicator(y[t + 1], True, backlog[t] - net[t], MDO.EQUAL, 0.0, name=f"backlog_eq[{t}]")
            m.addGenConstrIndicator(y[t + 1], False, backlog[t], MDO.LESS_EQUAL, 0.0, name=f"backlog_zero[{t}]")
        else:
            m.addConstr(backlog[t] == 0.0, name="backlog_last")
    for p in inst["projects"]:
        m.addConstr(quicksum(x[(p["id"], s)] for s in p["allowed_starts"]) == 1.0, name=f"start_once[{p['id']}]")
    for t in range(T):
        cols = [x[(p["id"], t)] for p in inst["projects"] if (p["id"], t) in x]
        if cols:
            m.addConstr(quicksum(cols) <= inst["max_starts_per_year"], name=f"cap_starts[{t}]")
    pv_expr = quicksum(int(pv) * x[(p["id"], s)] for p in inst["projects"] for s, pv in zip(p["allowed_starts"], p["pv_int"]) if int(pv))
    obj = w["backlog_weight"] * quicksum(backlog) + tier_pen - w["pv_weight"] * pv_expr
    m.setObjective(1.0 * obj, MDO.MINIMIZE)
    return {"m": m, "x": x, "y": y, "fund": fund, "net": net, "div": div, "backlog": backlog, "bands": band_vars,
            "inst": inst}


def set_warm_start(model: dict[str, Any], starts: dict[str, int], evaluation: dict[str, Any]) -> None:
    """Load a complete MIP start (binaries and the matching continuous completion)."""
    inst = model["inst"]
    T = inst["T"]
    last = evaluation["last_build_year_index"]
    closing = evaluation["closing_balance_units"]
    env = [max(0.0, float(f)) for f in inst["funding_S"]]
    base = inst["tiers"]["base_cap_frac"]
    for (pid, s), v in model["x"].items():
        v.Start = 1.0 if starts[pid] == s else 0.0
    net_run = 0.0
    for t in range(T):
        model["y"][t].Start = 1.0 if t <= last else 0.0
        f = float(inst["funding_S"][t]) if t <= last else 0.0
        model["fund"][t].Start = f
        net_run += f - evaluation["spend_units"][t]
        d = net_run if t == last else 0.0
        model["div"][t].Start = d
        net_run -= d
        model["net"][t].Start = net_run if t <= last else 0.0
        model["backlog"][t].Start = closing[t] if t < last else 0.0
        excess = max(0.0, closing[t] - env[t] * base) if t < last else 0.0
        for i, b in enumerate(inst["tiers"]["bands"]):
            width = excess if b["to_frac"] is None else min(excess, env[t] * (b["to_frac"] - b["from_frac"]))
            model["bands"][t][i].Start = width
            excess -= width


class CaptureStdout:
    """MindOpt prints from C: redirect file descriptors 1/2 to a log file (contextlib cannot capture it)."""

    def __init__(self, path: str) -> None:
        self.path = path

    def __enter__(self):
        sys.stdout.flush(); sys.stderr.flush()
        self.fh = open(self.path, "a", encoding="utf-8")
        self.saved = (os.dup(1), os.dup(2))
        os.dup2(self.fh.fileno(), 1)
        os.dup2(self.fh.fileno(), 2)
        return self

    def __exit__(self, *exc):
        sys.stdout.flush(); sys.stderr.flush()
        os.dup2(self.saved[0], 1)
        os.dup2(self.saved[1], 2)
        os.close(self.saved[0]); os.close(self.saved[1])
        self.fh.close()


ROW = re.compile(r"^\s*\d+\s+\d+\s+(\S+)\s+(\S+)\s+[\d.]+%\s+(\d+)s\s*$")


def incumbent_trace(log_text: str) -> list[tuple[int, float]]:
    """(seconds, incumbent) pairs from MindOpt's branch-and-bound table."""
    out = []
    for line in log_text.splitlines():
        mm = ROW.match(line)
        if mm:
            try:
                out.append((int(mm.group(3)), float(mm.group(2))))
            except ValueError:
                pass
    return out


def solve(model: dict[str, Any], time_limit: float, log_path: str) -> dict[str, Any]:
    m = model["m"]
    m.setParam("MaxTime", float(time_limit))
    m.setParam("MIP/GapRel", 0.0)
    m.setParam("MIP/GapAbs", 0.0)
    t0 = time.perf_counter()
    with CaptureStdout(log_path):
        m.optimize()
    secs = time.perf_counter() - t0
    has = bool(getattr(m, "HasSolution", 0))
    status = str(m.Status)
    res = {"status": {"1": "OPTIMAL", "7": "TIME LIMIT"}.get(status, status), "seconds": round(secs, 1), "has_solution": has}
    if has:
        res["objective"] = float(m.ObjVal)
        res["dual_bound"] = float(m.DualObjVal)
        res["gap"] = abs(res["objective"] - res["dual_bound"]) / max(1.0, abs(res["objective"]))
        res["starts"] = {pid: s for (pid, s), v in model["x"].items() if v.X > 0.5}
    return res
