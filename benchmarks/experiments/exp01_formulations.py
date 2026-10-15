"""Experiment 1: which encoding/balance form of the fixed-last-year model does HiGHS solve fastest?

    python benchmarks/experiments/exp01_formulations.py <instance> <L|none> <encoding> <balance> [time_limit]
"""
import json
import sys

from capopt import instance as I
from capopt.formulation import last_year_model
from capopt.solve import Trace, solve

name, L, enc, bal = sys.argv[1:5]
tl = float(sys.argv[5]) if len(sys.argv) > 5 else 120
inst = I.load(name)
best = I.best_known(name)["objective"]
tr = Trace()
m = last_year_model(inst, None if L == "none" else int(L), enc, bal)
r = solve(m, tr, threads=1, time_limit=tl)
print(json.dumps({"instance": name, "L": L, "enc": enc, "bal": bal, "status": r.status, "secs": round(r.seconds, 1),
                  "nodes": r.nodes, "obj": r.model_objective, "bound": r.dual_bound, "best_known": best,
                  "t1": tr.time_to(best, 0.01), "t01": tr.time_to(best, 0.001), "tbk": tr.time_to(best, 1e-6)}))
