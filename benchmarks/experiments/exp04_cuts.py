"""Experiment 4: does the root cover-cut loop speed up HiGHS' branch-and-bound? (single thread, A/B)

    python benchmarks/experiments/exp04_cuts.py <instance> <L|none> <encoding> <rounds> [time_limit]
"""
import json
import sys
import time

from capopt import instance as I
from capopt.cuts import root_loop
from capopt.formulation import last_year_model
from capopt.solve import Trace, solve

if __name__ == "__main__":
    name, L, enc, rounds = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    tl = float(sys.argv[5]) if len(sys.argv) > 5 else 300
    best = I.best_known(name)["objective"]
    t0 = time.perf_counter()
    tr = Trace(t0=t0)
    m = last_year_model(I.load(name), None if L == "none" else int(L), enc, "cumulative")
    ncuts = len(root_loop(m, rounds, 60)[1]) if rounds else 0
    r = solve(m, tr, threads=1, time_limit=tl - (time.perf_counter() - t0))
    print(json.dumps({"instance": name, "enc": enc, "rounds": rounds, "cuts": ncuts, "status": r.status,
                      "secs": round(time.perf_counter() - t0, 1), "nodes": r.nodes, "bound": r.dual_bound,
                      "t01": tr.time_to(best, 0.001), "tbk": tr.time_to(best, 1e-6)}), flush=True)
