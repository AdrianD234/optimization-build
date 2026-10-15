"""H17: holding-cash policy variants of a CPO instance (written under private/, never published).

    python tools/make_policy_variants.py <instance name> [--root private/cpo]

Variants (same projects, funding and PV; only the holding-cash treatment changes; the final build year's balance is
never penalised or capped, as in the CPO model):
    tiers_x0.1, tiers_x0.01 : today's tier weights scaled down (backlog weight kept)
    smooth                  : 7 gentler, convex steps from 12% to 26% of funding (250 .. 12,000 per $0.01M-year)
    purePV_cap350/_cap500   : maximise PV only, with a hard cap on the closing balance ($350M / $500M)
    tiersx0.01_cap500       : light tiers inside a $500M hard cap
For each variant a reference file scores the CPO's own schedule under the variant's rules, so time-to-1% / 0.1% stay
comparable. It is a feasible target, not that variant's optimum.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from capopt import instance as I  # noqa: E402
from capopt.evaluate import evaluate  # noqa: E402


def variants(raw: dict) -> dict[str, dict]:
    F = raw["funding_S"][0]
    out = {}

    def scaled(k):
        v = copy.deepcopy(raw)
        for b in v["tiers"]["bands"]:
            b["weight"] *= k
        return v
    out["tiers_x0.1"] = scaled(0.1)
    out["tiers_x0.01"] = scaled(0.01)
    sm = copy.deepcopy(raw)
    edges = [0.12, 0.14, 0.16, 0.18, 0.20, 0.22, 0.24, 0.26]
    weights = [250, 500, 1000, 2000, 4000, 8000, 12000]
    sm["tiers"] = {"base_cap_frac": 0.12,
                   "bands": [{"from_frac": edges[i], "to_frac": edges[i + 1] if i + 1 < len(weights) else None,
                              "weight": float(weights[i])} for i in range(len(weights))]}
    out["smooth"] = sm
    for cap_m in (350, 500):
        v = copy.deepcopy(raw)
        v["weights"] = {"backlog_weight": 0.0, "pv_weight": 1.0}
        for b in v["tiers"]["bands"]:
            b["weight"] = 0.0
        v["cash_cap_units"] = cap_m * 100
        out[f"purePV_cap{cap_m}"] = v
    v = scaled(0.01)
    v["cash_cap_units"] = 500 * 100
    out["tiersx0.01_cap500"] = v
    for k, v in out.items():
        v["name"] = f"{raw['name']}__{k}"
        v["description"] = raw["description"] + f" | H17 policy variant: {k}"
    del F
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--root", default=str(ROOT / "private" / "cpo"))
    a = ap.parse_args()
    root = Path(a.root)
    raw = json.loads((root / "instances" / f"{a.name}.json").read_text(encoding="utf-8"))
    ref = json.loads((root / "solutions" / f"{a.name}_cpo_reference.json").read_text(encoding="utf-8"))
    for k, v in variants(raw).items():
        (root / "instances" / f"{v['name']}.json").write_text(json.dumps(v, separators=(",", ":")), encoding="utf-8")
        inst = I.from_dict(v)
        ev = evaluate(inst, inst.starts_from_dict(ref["starts"]))
        r = dict(ref, instance=v["name"], evaluator_objective=ev.get("objective"), evaluator_feasible=ev["feasible"],
                 source=ref["source"] + f" (CPO schedule scored under policy variant {k}; a target, not its optimum)")
        (root / "solutions" / f"{v['name']}_cpo_reference.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
        print(f"{v['name']}: CPO schedule feasible {ev['feasible']}, objective {ev.get('objective')}")
