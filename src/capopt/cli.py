"""Command line: capopt solve | bench | report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import bench, solver
from . import instance as I


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="capopt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("solve", help="solve one instance and write the schedule")
    s.add_argument("instance", help="instance name (e.g. P100_raw_penalised) or path to an instance JSON")
    s.add_argument("--out", default=None, help="schedule JSON to write ({'starts': {...}})")
    s.add_argument("--workers", type=int, default=None)
    s.add_argument("--time-limit", type=float, default=900.0)
    s.add_argument("--leaves", type=int, default=60)
    b = sub.add_parser("bench", help="run benchmark configurations and store traces")
    b.add_argument("--configs", default=",".join(bench.CONFIGS))
    b.add_argument("--instances", default=",".join(I.INSTANCE_NAMES))
    b.add_argument("--seeds", default="0")
    b.add_argument("--time-limit", type=float, default=900.0)
    b.add_argument("--no-isolate", action="store_true", help="run in this process (no watchdog subprocess)")
    b.add_argument("--out-dir", default=str(bench.RESULTS))
    sub.add_parser("report", help="write benchmarks/BENCHMARK.md from stored runs")
    a = ap.parse_args(argv)
    if a.cmd == "solve":
        r = solver.solve(a.instance, a.workers, a.time_limit, a.leaves)
        print(json.dumps({"instance": r.instance, "objective": r.objective, "proven": r.proven,
                          "dual_bound": r.dual_bound, "seconds": round(r.seconds, 2), "log": r.log}, indent=1))
        if a.out and r.starts:
            Path(a.out).write_text(json.dumps({"starts": r.starts}, indent=1), encoding="utf-8")
        return 0 if r.starts else 1
    if a.cmd == "bench":
        for cfg in a.configs.split(","):
            for name in a.instances.split(","):
                for seed in (int(x) for x in a.seeds.split(",")):
                    out = Path(a.out_dir)
                    rec = (bench.run(cfg, name, a.time_limit, seed, out) if a.no_isolate
                           else bench.run_isolated(cfg, name, a.time_limit, seed, out))
                    print(f"[{cfg}] {name} s{seed}: obj {rec['objective']:.6e} proven {rec['proven']} "
                          f"1% {rec['time_to_1pct']} 0.1% {rec['time_to_0.1pct']} bk {rec['time_to_best_known']} "
                          f"proof {rec['time_to_proof']} ({rec['seconds']}s)", flush=True)
        return 0
    if a.cmd == "report":
        print(bench.write_report())
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
