"""Benchmark: MindOpt cold vs (heuristic -> MindOpt warm start) on every instance.

    python run_benchmark.py [--time-limit 900] [--heuristic-seconds 30] [--instances P100_raw_penalised,...]

All solves run in ONE process (MindOpt floating licence = one solve at a time). Writes
../results/benchmark_results.json, ../results/BENCHMARK.md and ../results/logs/*.log.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK = HERE.parent
sys.path.insert(0, str(HERE))
import hill_climb  # noqa: E402
import mindopt_model as mm  # noqa: E402
from evaluate import evaluate, prepare  # noqa: E402

ALL = ["P100_raw_penalised", "P100_growth_penalised", "P100_raw_nopenalty", "P100_growth_nopenalty"]


def load_instance(name: str) -> dict:
    return json.loads((PACK / "instances" / f"{name}.json").read_text(encoding="utf-8"))


def wait_for_licence(attempts: int = 48, wait_s: int = 150) -> None:
    from mindoptpy import Model
    for k in range(1, attempts + 1):
        try:
            probe = Model("licence_probe")
            probe.dispose()
            print(f"[licence] acquired (attempt {k})", flush=True)
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[licence] attempt {k} failed: {exc}; retrying in {wait_s}s", flush=True)
            time.sleep(wait_s)
    raise RuntimeError("MindOpt licence unavailable")


def time_to(trace: list[tuple[int, float]], target: float, rel: float) -> int | None:
    goal = target + rel * abs(target)
    for t, inc in trace:
        if inc <= goal + 1e-9 * abs(target):
            return t
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--time-limit", type=float, default=900.0)
    ap.add_argument("--heuristic-seconds", type=float, default=30.0)
    ap.add_argument("--instances", default=",".join(ALL))
    a = ap.parse_args()
    out_dir = PACK / "results"
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)
    wait_for_licence()
    results = {"generated": datetime.now().isoformat(timespec="seconds"),
               "machine": {"platform": platform.platform(), "processor": platform.processor(), "threads": os.cpu_count()},
               "settings": {"mindopt_time_limit_s": a.time_limit, "heuristic_seconds": a.heuristic_seconds,
                            "gap_target": 0.0}, "instances": {}}
    for name in a.instances.split(","):
        inst = load_instance(name)
        ref = json.loads((PACK / "solutions" / f"{name}_reference.json").read_text(encoding="utf-8"))
        ev_inst = prepare(load_instance(name))
        rec: dict = {"pipeline_reference": {k: ref[k] for k in ("status", "gap", "solve_seconds", "evaluator_objective")}}
        # A. MindOpt cold
        t_build = time.perf_counter()
        model = mm.build(load_instance(name))
        build_s = time.perf_counter() - t_build
        log_a = out_dir / "logs" / f"{name}_mindopt_cold.log"
        log_a.unlink(missing_ok=True)
        cold = mm.solve(model, a.time_limit, str(log_a))
        cold["build_seconds"] = round(build_s, 2)
        cold["trace"] = mm.incumbent_trace(log_a.read_text(encoding="utf-8", errors="ignore"))
        if cold.get("starts"):
            chk = evaluate(ev_inst, cold["starts"])
            cold["evaluator_objective"] = chk.get("objective")
            cold["evaluator_feasible"] = chk["feasible"]
        model["m"].dispose()
        print(f"[{name}] cold: {cold['status']} obj {cold.get('objective')} gap {cold.get('gap')} {cold['seconds']}s", flush=True)
        # B. heuristic -> MindOpt warm start
        h = hill_climb.solve(load_instance(name), seconds=a.heuristic_seconds, seed=1)
        h_eval = evaluate(ev_inst, h["starts"])
        model = mm.build(load_instance(name))
        mm.set_warm_start(model, h["starts"], h_eval)
        log_b = out_dir / "logs" / f"{name}_heuristic_then_mindopt.log"
        log_b.unlink(missing_ok=True)
        warm = mm.solve(model, a.time_limit, str(log_b))
        warm["trace"] = mm.incumbent_trace(log_b.read_text(encoding="utf-8", errors="ignore"))
        if warm.get("starts"):
            chk = evaluate(ev_inst, warm["starts"])
            warm["evaluator_objective"] = chk.get("objective")
            warm["evaluator_feasible"] = chk["feasible"]
        model["m"].dispose()
        print(f"[{name}] heuristic {h['objective']:.6e} in {h['seconds']}s -> warm: {warm['status']} obj {warm.get('objective')} "
              f"{warm['seconds']}s", flush=True)
        best = min(v for v in (ref["evaluator_objective"], cold.get("evaluator_objective"), warm.get("evaluator_objective"))
                   if v is not None)
        for r in (cold, warm):
            r["time_to_within_1pct_s"] = time_to(r["trace"], best, 0.01)
            r["time_to_within_0.1pct_s"] = time_to(r["trace"], best, 0.001)
            r["time_to_best_known_s"] = time_to(r["trace"], best, 1e-6)  # log prints 7 significant figures
        rec.update({"best_known_objective": best,
                    "heuristic": {"objective": h["objective"], "gap_to_best_known": (h["objective"] - best) / abs(best),
                                  "seconds": h["seconds"], "construction_used": h["construction_used"],
                                  "anneal_moves": h["anneal_moves"], "starts": h["starts"]},
                    "mindopt_cold": cold, "heuristic_then_mindopt": warm})
        results["instances"][name] = rec
        (out_dir / "benchmark_results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("[done]", out_dir / "benchmark_results.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
