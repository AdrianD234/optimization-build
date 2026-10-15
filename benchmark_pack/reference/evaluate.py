"""Reference evaluator: scores a schedule exactly as the MIP objective does.

A schedule assigns every project one start index s (0 = first model year). Rules (identical to the MIP):

* Each project starts exactly once, at one of its allowed starts; at most `max_starts_per_year` starts in a year.
* Spend: project p started at s spends spend_S[p][k] in year s + k (integer units, 1 unit = $0.01M).
* The programme is active from year 0 to the last year with any spend ("last"). Funding of funding_S[t]
  arrives in every active year; none after.
* Closing balance net[t] = net[t-1] + funding[t] - spend[t]; it may never be negative (no debt).
  In year `last` the remaining balance is released (a dividend) at no cost.
* Cost of holding cash, for every year t < last (i.e. building continues next year):
    backlog   : backlog_weight x net[t]
    tier bands: the part of net[t] above base_cap is split into bands (widths in units), each charged its weight.
* Objective (MINIMISE):  backlog + tier penalties - pv_weight x sum(pv_int[p][s]).
  The "pure_pv" instances set backlog_weight = 0, tier weights = 0 and pv_weight = 1.

Usage:
    python evaluate.py <instance.json> <schedule.json>
schedule.json: {"starts": {"<project id>": <start index>, ...}}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def evaluate(inst: dict[str, Any], starts: dict[str, int]) -> dict[str, Any]:
    T = inst["T"]
    projects = inst["projects"]
    obj = inst["objective"]
    out: dict[str, Any] = {"feasible": False}
    if set(starts) != {p["id"] for p in projects}:
        missing = sorted({p["id"] for p in projects} - set(starts))
        extra = sorted(set(starts) - {p["id"] for p in projects})
        out["reason"] = f"every project needs exactly one start (missing {missing[:5]}, unknown {extra[:5]})"
        return out
    spend = [0] * T
    active = [False] * T
    per_year = [0] * T
    pv_sum = 0
    for p in projects:
        s = int(starts[p["id"]])
        if s not in p["_allowed"]:
            out["reason"] = f"{p['id']}: start {s} not allowed"
            return out
        per_year[s] += 1
        pv_sum += p["_pv"][s]
        for k, a in enumerate(p["spend_S"]):
            spend[s + k] += a
            if p["spend_nonzero"][k]:
                active[s + k] = True
    if max(per_year) > inst["max_starts_per_year"]:
        out["reason"] = "too many starts in one year"
        return out
    last = max(t for t in range(T) if active[t])
    F = inst["funding_S"]
    env = [max(0.0, float(f)) for f in F]
    base = inst["tiers"]["base_cap_frac"]
    bands = inst["tiers"]["bands"]  # [{"from_frac", "to_frac" (null = open), "weight"}]
    net, backlog, tiers = 0.0, 0.0, 0.0
    closing = []
    for t in range(T):
        if t <= last:
            net = net + F[t] - spend[t]
        if net < -1e-9:
            out["reason"] = f"negative balance in year index {t} ({net:.2f} units)"
            return out
        if t < last:
            backlog += net
            excess = max(0.0, net - env[t] * base)
            for b in bands:
                if excess <= 0:
                    break
                width = None if b["to_frac"] is None else env[t] * (b["to_frac"] - b["from_frac"])
                take = excess if width is None else min(excess, width)
                tiers += take * b["weight"]
                excess -= take
        closing.append(net if t < last else 0.0)
        if t == last:
            net = 0.0  # released at no cost
    w = inst["weights"]
    pv_term = w["pv_weight"] * pv_sum
    out.update({
        "feasible": True,
        "objective": w["backlog_weight"] * backlog + tiers - pv_term,
        "components": {"backlog_units": backlog, "tier_units": tiers, "pv_term": pv_term, "pv_int_sum": pv_sum},
        "last_build_year_index": last,
        "closing_balance_units": closing,
        "spend_units": spend,
        "objective_type": obj,
    })
    return out


def prepare(inst: dict[str, Any]) -> dict[str, Any]:
    """Index allowed starts / PV coefficients for fast lookup (done once per instance)."""
    for p in inst["projects"]:
        p["_allowed"] = set(p["allowed_starts"])
        p["_pv"] = dict(zip(p["allowed_starts"], p["pv_int"]))
    return inst


if __name__ == "__main__":
    inst = prepare(load(sys.argv[1]))
    sched = load(sys.argv[2])
    res = evaluate(inst, {k: int(v) for k, v in sched["starts"].items()})
    res.pop("closing_balance_units", None)
    res.pop("spend_units", None)
    print(json.dumps(res, indent=2))
