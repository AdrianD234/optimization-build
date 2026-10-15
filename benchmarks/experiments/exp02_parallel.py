"""Experiment 2: parallel split-and-solve on the fixed-last-year model.

    python benchmarks/experiments/exp02_parallel.py <instance> <L> [workers] [leaves] [encoding] [balance]
"""
import json
import sys

from capopt import instance as I
from capopt.parallel import solve_parallel

if __name__ == "__main__":
    name, L = sys.argv[1], int(sys.argv[2])
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    nl = int(sys.argv[4]) if len(sys.argv) > 4 else 60
    enc = sys.argv[5] if len(sys.argv) > 5 else "step"
    bal = sys.argv[6] if len(sys.argv) > 6 else "cumulative"
    best = I.best_known(name)["objective"]
    r = solve_parallel(name, L, w, nl, enc, bal, time_limit=600)
    def tt(rel):
        g = best + rel * abs(best) + 1e-9 * abs(best)
        return next((round(t, 1) for t, v in r.trace if v <= g), None)
    print(json.dumps({"instance": name, "workers": w, "leaves": nl, "enc": enc, "bal": bal, "obj": r.objective,
                      "best_known": best, "proven": r.proven, "dual": r.dual_bound, "secs": round(r.seconds, 1),
                      "t1": tt(0.01), "t01": tt(0.001), "tbk": tt(1e-6), "log": r.log}))
