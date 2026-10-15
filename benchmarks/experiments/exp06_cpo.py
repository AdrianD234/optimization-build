"""Experiment 6: does capopt port to the Capital Programme Optimiser's real 233-project data?

    python benchmarks/experiments/exp06_cpo.py <instance name> <config> [time_limit]

Instances come from tools/extract_cpo.py (private/cpo/instances, gitignored: real programme data). Configs:
    capopt     : the default pipeline (ladder rung I9, 20 workers)
    highs_base : stock HiGHS on the faithful reference model (baseline B0)
The record (trace, objective, proof, schedule) is written to private/cpo/results/<instance>__<config>.json. Times to
reach the CPO's stored objective (COPT, relative gap 1e-5) are computed against that schedule's evaluator objective.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from capopt import solver
from capopt.bench import machine, time_to

ROOT = Path(__file__).resolve().parents[2]
CPO = ROOT / "private" / "cpo"

if __name__ == "__main__":
    name, config = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3]) if len(sys.argv) > 3 else 900.0
    path = CPO / "instances" / f"{name}.json"
    ref_name = name.removesuffix("_purePV")
    ref = json.loads((CPO / "solutions" / f"{ref_name}_cpo_reference.json").read_text(encoding="utf-8"))
    t0 = time.time()
    if config == "capopt":
        r = solver.solve(str(path), time_limit=tl)
    elif config == "highs_base":
        r = solver.solve_reference_highs(str(path), time_limit=tl)
    else:
        raise SystemExit(f"unknown config {config}")
    target = ref["evaluator_objective"] if not name.endswith("_purePV") else -ref["evaluator_pv"]
    rec = {"instance": name, "config": config, "generated": datetime.now().isoformat(timespec="seconds"),
           "machine": machine(), "time_limit": tl, "objective": r.objective, "proven": r.proven,
           "dual_bound": r.dual_bound, "seconds": round(r.seconds, 2), "wall_seconds": round(time.time() - t0, 2),
           "cpo_reference_objective": target, "cpo_gap": ref["cpo_gap"],
           "vs_cpo_rel": (r.objective - target) / abs(target),
           "time_to_cpo_within_1pct": time_to(r.trace, target, 0.01),
           "time_to_cpo_within_0.1pct": time_to(r.trace, target, 0.001),
           "time_to_cpo_objective": time_to(r.trace, target, 0.0),
           "time_to_proof": round(r.seconds, 2) if r.proven else None,
           "trace": r.trace, "log": r.log, "last_years": {str(k): v for k, v in r.last_years.items()}, "starts": r.starts}
    out = CPO / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}__{config}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(json.dumps({k: rec[k] for k in ("instance", "config", "objective", "proven", "seconds", "vs_cpo_rel",
                                          "time_to_cpo_within_1pct", "time_to_cpo_within_0.1pct",
                                          "time_to_cpo_objective", "time_to_proof")}), flush=True)
