"""Command line: capopt solve | bench | report | campaign | status | watch."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import bench, campaign, solver
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
    c = sub.add_parser("campaign", help="long benchmark campaigns with heartbeat (run in foreground, or launch detached)")
    c.add_argument("mode", choices=["run", "launch"], help="run: here, in the foreground; launch: detached + verified")
    c.add_argument("--spec", required=True, help="campaign spec JSON (see capopt/campaign.py)")
    c.add_argument("--root", default=str(campaign.RESULTS), help="folder holding campaign folders")
    c.add_argument("--heartbeat", type=float, default=campaign.HEARTBEAT_S, help="heartbeat interval (s)")
    c.add_argument("--verify", type=float, default=campaign.NOT_STARTED_S, help="launch: seconds to wait for a heartbeat")
    for cmd, helptext in (("status", "campaign progress; exit 1 if STALLED / NOT_STARTED / FAILED"),
                          ("watch", "print one line per campaign state change (arm with an agent's Monitor tool)")):
        w = sub.add_parser(cmd, help=helptext)
        w.add_argument("campaign", help="campaign name (under --root) or campaign folder")
        w.add_argument("--root", default=str(campaign.RESULTS))
        w.add_argument("--stale", type=float, default=campaign.STALE_S, help="seconds without heartbeat => STALLED")
        if cmd == "watch":
            w.add_argument("--interval", type=float, default=30.0)
    a = ap.parse_args(argv)
    if a.cmd == "campaign":
        if a.mode == "run":
            spec = campaign.load_spec(a.spec)
            data = campaign.run(spec, campaign.campaign_dir(spec["name"], Path(a.root)), a.heartbeat)
            return 0 if data["state"] == "complete" else 1
        try:
            info = campaign.launch(a.spec, Path(a.root), a.verify, a.heartbeat)
        except campaign.LaunchError as exc:
            print(f"LAUNCH FAILED: {exc}", file=sys.stderr, flush=True)
            return 1
        print(f"LAUNCHED {info['campaign']}: {info['total']} runs, runner pid {info['pid']}, heartbeat verified after "
              f"{info['verified_after']:.1f}s. Check with: capopt status {info['campaign']}  |  "
              f"capopt watch {info['campaign']}", flush=True)
        return 0
    if a.cmd in ("status", "watch"):
        cdir = Path(a.campaign) if Path(a.campaign).is_dir() else campaign.campaign_dir(a.campaign, Path(a.root))
        if a.cmd == "status":
            st = campaign.status(cdir, stale_s=a.stale)
            print(campaign.describe(st), flush=True)
            return 0 if st["state"] in campaign.OK_STATES else (2 if st["state"] == "NOT_LAUNCHED" else 1)
        return campaign.watch(cdir, a.interval, emit=lambda line: print(line, flush=True), stale_s=a.stale)
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
                    if "skipped" in rec:
                        print(f"[{cfg}] {name} s{seed}: skipped ({rec['skipped']})", flush=True)
                        continue
                    print(f"[{cfg}] {name} s{seed}: obj {rec['objective']:.6e} proven {rec['proven']} "
                          f"1% {rec['time_to_1pct']} 0.1% {rec['time_to_0.1pct']} bk {rec['time_to_best_known']} "
                          f"proof {rec['time_to_proof']} ({rec['seconds']}s)", flush=True)
        return 0
    if a.cmd == "report":
        print(bench.write_report())  # public: capopt vs the HiGHS baseline
        if bench.mindopt_rows():     # private copy with the pack's MindOpt runs, when that data is present
            bench.write_report(path=bench.PRIVATE_REPORT, include_mindopt=True)
            print(f"(private report with MindOpt written to {bench.PRIVATE_REPORT})")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
