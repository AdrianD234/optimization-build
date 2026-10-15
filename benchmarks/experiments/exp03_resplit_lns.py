"""Experiment 3: time-sliced re-splitting and LNS workers on top of split-and-solve.

    python benchmarks/experiments/exp03_resplit_lns.py <instance> <slice|none> <lns_workers> <lns_seconds> [leaves]
"""
import json
import sys

from capopt import instance as I
from capopt.solver import solve

if __name__ == "__main__":
    name = sys.argv[1]
    sl = None if sys.argv[2] == "none" else float(sys.argv[2])
    lw, ls = int(sys.argv[3]), float(sys.argv[4])
    leaves = int(sys.argv[5]) if len(sys.argv) > 5 else 60
    best = I.best_known(name)["objective"]
    r = solve(name, 20, 600, leaves, slice_seconds=sl, lns_workers=lw, lns_seconds=ls)

    def tt(rel):
        g = best + rel * abs(best) + 1e-9 * abs(best)
        return next((round(t, 1) for t, v in r.trace if v <= g), None)

    print(json.dumps({"instance": name, "slice": sl, "lns_workers": lw, "lns_seconds": ls, "leaves": leaves,
                      "obj": r.objective, "proven": r.proven, "secs": round(r.seconds, 1), "t1": tt(0.01),
                      "t01": tt(0.001), "tbk": tt(1e-6), "log": r.log}), flush=True)
