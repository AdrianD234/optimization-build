"""Baseline heuristic: best of three constructions -> simulated annealing -> hill-climb polish.

Constructions: best insertion (PV per unit spend), serial fill (PV per unit spend), serial fill (longest first).
Annealing: shift-by-1..3-years and pairwise start swaps; temperature decays geometrically from 2% to 1e-6 of the
PV term. Polish: best-shift + sampled swaps until no improvement.

Same objective and feasibility rules as evaluate.py, computed incrementally for speed. Pure Python, no solver.

    python hill_climb.py <instance.json> [--seconds 30] [--seed 1] [--out schedule.json]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path
from typing import Any

INF = float("inf")


class Schedule:
    def __init__(self, inst: dict[str, Any]) -> None:
        self.T = inst["T"]
        self.F = inst["funding_S"]
        self.env = [max(0.0, float(f)) for f in self.F]
        t = inst["tiers"]
        self.base = [e * t["base_cap_frac"] for e in self.env]
        self.bands = [(b["from_frac"], b["to_frac"], b["weight"]) for b in t["bands"]]
        self.bw = inst["weights"]["backlog_weight"]
        self.pvw = inst["weights"]["pv_weight"]
        self.penalised = self.bw > 0 or any(w > 0 for _, _, w in self.bands)
        self.P = inst["projects"]
        self.n = len(self.P)
        self.spend_rows = [p["spend_S"] for p in self.P]
        self.nz_rows = [p["spend_nonzero"] for p in self.P]
        self.allowed = [p["allowed_starts"] for p in self.P]
        self.pv = [dict(zip(p["allowed_starts"], p["pv_int"])) for p in self.P]
        self.spend = [0] * self.T
        self.act = [0] * self.T
        self.starts = [None] * self.n
        self.pv_sum = 0

    # -- incremental placement -------------------------------------------------
    def place(self, i: int, s: int) -> None:
        for k, a in enumerate(self.spend_rows[i]):
            self.spend[s + k] += a
            if self.nz_rows[i][k]:
                self.act[s + k] += 1
        self.starts[i] = s
        self.pv_sum += self.pv[i][s]

    def remove(self, i: int) -> None:
        s = self.starts[i]
        for k, a in enumerate(self.spend_rows[i]):
            self.spend[s + k] -= a
            if self.nz_rows[i][k]:
                self.act[s + k] -= 1
        self.starts[i] = None
        self.pv_sum -= self.pv[i][s]

    def objective(self) -> float:
        last = -1
        for t in range(self.T - 1, -1, -1):
            if self.act[t]:
                last = t
                break
        if last < 0:
            return -self.pvw * self.pv_sum
        net, cost = 0.0, 0.0
        F, spend, base, env = self.F, self.spend, self.base, self.env
        for t in range(last + 1):
            net += F[t] - spend[t]
            if net < -1e-9:
                return INF
            if t < last and self.penalised:
                cost += self.bw * net
                excess = net - base[t]
                if excess > 0:
                    for lo, hi, w in self.bands:
                        width = excess if hi is None else min(excess, env[t] * (hi - lo))
                        cost += width * w
                        excess -= width
                        if excess <= 0:
                            break
        return cost - self.pvw * self.pv_sum


def construct(sch: Schedule, rng: random.Random) -> None:
    """Best insertion, projects in descending PV per unit of spend (ties broken randomly)."""
    order = sorted(range(sch.n), key=lambda i: (-max(sch.pv[i].values()) / max(1, sum(sch.spend_rows[i])), rng.random()))
    for i in order:
        best_s, best_v = None, INF
        for s in sch.allowed[i]:
            sch.place(i, s)
            v = sch.objective()
            sch.remove(i)
            if v < best_v:
                best_s, best_v = s, v
        if best_s is None:  # nothing feasible yet: take the latest allowed start (most funding accrued)
            best_s = sch.allowed[i][-1]
        sch.place(i, best_s)


def construct_fill(sch: Schedule, rng: random.Random, longest_first: bool = False) -> None:
    """Serial fill: each project at its EARLIEST start that keeps the balance non-negative. Order: descending PV per
    unit of spend, or longest duration first (packs the long, lumpy projects before the small fillers)."""
    if longest_first:
        order = sorted(range(sch.n), key=lambda i: (-len(sch.spend_rows[i]), rng.random()))
    else:
        order = sorted(range(sch.n), key=lambda i: (-max(sch.pv[i].values()) / max(1, sum(sch.spend_rows[i])), rng.random()))
    for i in order:
        chosen = sch.allowed[i][-1]
        for s in sch.allowed[i]:
            sch.place(i, s)
            ok = sch.objective() < INF
            sch.remove(i)
            if ok:
                chosen = s
                break
        sch.place(i, chosen)


def reset(sch: Schedule) -> None:
    for i in range(sch.n):
        if sch.starts[i] is not None:
            sch.remove(i)


def local_search(sch: Schedule, rng: random.Random, deadline: float, swap_sample: int = 1500) -> float:
    cur = sch.objective()
    improved = True
    while improved and time.perf_counter() < deadline:
        improved = False
        for i in rng.sample(range(sch.n), sch.n):  # best shift per project
            s0 = sch.starts[i]
            sch.remove(i)
            best_s, best_v = s0, cur
            for s in sch.allowed[i]:
                if s == s0:
                    continue
                sch.place(i, s)
                v = sch.objective()
                sch.remove(i)
                if v < best_v - 1e-9 * max(1.0, abs(best_v)):
                    best_s, best_v = s, v
            sch.place(i, best_s)
            if best_s != s0:
                cur, improved = best_v, True
            if time.perf_counter() >= deadline:
                return cur
        for _ in range(swap_sample):  # sampled pairwise start swaps
            i, j = rng.sample(range(sch.n), 2)
            si, sj = sch.starts[i], sch.starts[j]
            if si == sj or sj not in sch.pv[i] or si not in sch.pv[j]:
                continue
            sch.remove(i); sch.remove(j)
            sch.place(i, sj); sch.place(j, si)
            v = sch.objective()
            if v < cur - 1e-9 * max(1.0, abs(cur)):
                cur, improved = v, True
            else:
                sch.remove(i); sch.remove(j)
                sch.place(i, si); sch.place(j, sj)
    return cur


def anneal(sch: Schedule, rng: random.Random, deadline: float, t_start: float = 0.02, t_end: float = 1e-6) -> tuple[float, list]:
    cur = sch.objective()
    best, best_starts = cur, list(sch.starts)
    scale = abs(sch.pvw * sch.pv_sum) or 1.0
    t0 = time.perf_counter()
    span = max(1e-9, deadline - t0)
    moves = 0
    while True:
        now = time.perf_counter()
        if now >= deadline:
            break
        temp = scale * t_start * (t_end / t_start) ** ((now - t0) / span)
        moves += 1
        if rng.random() < 0.7:  # shift one project by a few years
            i = rng.randrange(sch.n)
            s0 = sch.starts[i]
            s1 = s0 + rng.choice((-3, -2, -1, 1, 2, 3))
            if s1 not in sch.pv[i]:
                continue
            sch.remove(i); sch.place(i, s1)
            v = sch.objective()
            if v < cur or (v < INF and rng.random() < math.exp(-(v - cur) / temp)):
                cur = v
            else:
                sch.remove(i); sch.place(i, s0)
        else:  # swap two projects' starts
            i, j = rng.sample(range(sch.n), 2)
            si, sj = sch.starts[i], sch.starts[j]
            if si == sj or sj not in sch.pv[i] or si not in sch.pv[j]:
                continue
            sch.remove(i); sch.remove(j); sch.place(i, sj); sch.place(j, si)
            v = sch.objective()
            if v < cur or (v < INF and rng.random() < math.exp(-(v - cur) / temp)):
                cur = v
            else:
                sch.remove(i); sch.remove(j); sch.place(i, si); sch.place(j, sj)
        if cur < best:
            best, best_starts = cur, list(sch.starts)
    sch._anneal_moves = moves
    return best, best_starts


def solve(inst: dict[str, Any], seconds: float = 30.0, seed: int = 1) -> dict[str, Any]:
    t0 = time.perf_counter()
    rng = random.Random(seed)
    sch = Schedule(inst)
    candidates = []
    for name, build in (("best_insertion", lambda: construct(sch, rng)),
                        ("serial_fill_pv", lambda: construct_fill(sch, rng)),
                        ("serial_fill_longest", lambda: construct_fill(sch, rng, longest_first=True))):
        reset(sch)
        build()
        candidates.append((sch.objective(), name, list(sch.starts)))
    v0, start_name, start = min(candidates, key=lambda c: c[0])
    reset(sch)
    for i, s in enumerate(start):
        sch.place(i, s)
    t_construct = time.perf_counter() - t0
    best, best_starts = anneal(sch, rng, t0 + 0.8 * seconds)
    reset(sch)
    for i, s in enumerate(best_starts):
        sch.place(i, s)
    best = min(best, local_search(sch, rng, t0 + seconds))
    if sch.objective() <= best:
        best, best_starts = sch.objective(), list(sch.starts)
    ids = [p["id"] for p in inst["projects"]]
    return {"objective": best, "starts": {ids[i]: s for i, s in enumerate(best_starts)},
            "construct_objective": v0, "construction_used": start_name, "construct_seconds": round(t_construct, 3),
            "anneal_moves": getattr(sch, "_anneal_moves", 0), "seconds": round(time.perf_counter() - t0, 3),
            "feasible": not math.isinf(best)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("instance")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    inst = json.loads(Path(a.instance).read_text(encoding="utf-8"))
    res = solve(inst, a.seconds, a.seed)
    print(json.dumps({k: v for k, v in res.items() if k != "starts"}, indent=2))
    if a.out:
        Path(a.out).write_text(json.dumps({"starts": res["starts"]}, indent=1), encoding="utf-8")
