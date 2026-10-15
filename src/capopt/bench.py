"""Benchmark harness: run solver configurations on the pack instances, store traces, write BENCHMARK.md.

Every stored objective is re-scored with the pack's referee (benchmark_pack/reference/evaluate.py), loaded from file
so the referee itself, not our port, has the last word.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from . import instance as I
from . import solver

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "benchmarks" / "results"
REPORT = ROOT / "benchmarks" / "BENCHMARK.md"


def _referee():
    spec = importlib.util.spec_from_file_location("referee", I.PACK / "reference" / "evaluate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def referee_score(name: str, starts: dict[str, int]) -> dict[str, Any]:
    ref = _referee()
    inst = ref.prepare(ref.load(I.PACK / "instances" / f"{name}.json"))
    r = ref.evaluate(inst, starts)
    return {"feasible": r["feasible"], "objective": r.get("objective"), "reason": r.get("reason")}


# name -> (rung label, description, runner(instance, time_limit, seed) -> solver.Result)
CONFIGS: dict[str, tuple[str, str, Callable[..., solver.Result]]] = {
    "highs_reference": ("B0", "stock HiGHS on the faithful port of the MindOpt model",
                        lambda n, tl, seed: solver.solve_reference_highs(n, tl)),
    "fixedL_pulse_1t": ("I1", "fixed-last-year decomposition, pulse encoding, 1 thread",
                        lambda n, tl, seed: solver.solve_sequential(n, "pulse", "recursive", tl)),
    "fixedL_step_1t": ("I2", "+ step encoding, cumulative balance rows, 1 thread",
                       lambda n, tl, seed: solver.solve_sequential(n, "step", "cumulative", tl)),
    "capopt_parallel": ("I3", "+ parallel split-and-solve, 20 workers, 60 leaves",
                        lambda n, tl, seed: solver.solve(n, 20, tl, 60)),
}


def machine() -> dict[str, Any]:
    return {"platform": platform.platform(), "processor": platform.processor(), "threads": os.cpu_count()}


def run(config: str, name: str, time_limit: float = 900.0, seed: int = 0, out_dir: Path = RESULTS) -> dict[str, Any]:
    rung, desc, fn = CONFIGS[config]
    t = time.time()
    r = fn(name, time_limit, seed)
    best = I.best_known(name)["objective"]
    ref = referee_score(name, r.starts) if r.starts else {"feasible": False}
    rec = {
        "config": config, "rung": rung, "description": desc, "instance": name, "seed": seed,
        "generated": datetime.now().isoformat(timespec="seconds"), "machine": machine(), "time_limit": time_limit,
        "objective": r.objective, "referee": ref, "best_known": best, "proven": r.proven, "dual_bound": r.dual_bound,
        "seconds": round(r.seconds, 2), "wall_seconds": round(time.time() - t, 2), "trace": r.trace,
        "time_to_1pct": time_to(r.trace, best, 0.01), "time_to_0.1pct": time_to(r.trace, best, 0.001),
        "time_to_best_known": time_to(r.trace, best, 1e-6),
        "time_to_proof": round(r.seconds, 2) if r.proven else None,
        "last_years": {str(k): v for k, v in r.last_years.items()}, "log": r.log, "starts": r.starts,
    }
    d = out_dir / config
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}_s{seed}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def time_to(trace: list, target: float, rel: float) -> float | None:
    goal = target + rel * abs(target) + 1e-9 * abs(target)
    for t, v in trace:
        if v <= goal:
            return round(t, 2)
    return None


# ---- report -------------------------------------------------------------------------------------------------------
def mindopt_rows() -> dict[str, dict[str, Any]]:
    d = json.loads((I.PACK / "results" / "benchmark_results.json").read_text(encoding="utf-8"))
    out = {}
    for name, rec in d["instances"].items():
        c = rec["mindopt_cold"]
        out[name] = {"time_to_1pct": c["time_to_within_1pct_s"], "time_to_0.1pct": c["time_to_within_0.1pct_s"],
                     "time_to_best_known": c["time_to_best_known_s"],
                     "time_to_proof": c["seconds"] if c["status"] == "OPTIMAL" else None,
                     "seconds": c["seconds"], "status": c["status"], "gap": c["gap"]}
    return out


def _median(xs: list[float | None]) -> float | None:
    """Median with misses (None) counted as +inf: None if the median run did not reach the target."""
    if not xs:
        return None
    vals = sorted(float("inf") if x is None else x for x in xs)
    k = len(vals) // 2
    med = vals[k] if len(vals) % 2 else (vals[k - 1] + vals[k]) / 2
    return None if med == float("inf") else med


def load_results(out_dir: Path = RESULTS) -> dict[str, dict[str, list[dict]]]:
    res: dict[str, dict[str, list[dict]]] = {}
    for cfg in CONFIGS:
        for f in sorted((out_dir / cfg).glob("*.json")) if (out_dir / cfg).exists() else []:
            rec = json.loads(f.read_text(encoding="utf-8"))
            res.setdefault(rec["instance"], {}).setdefault(cfg, []).append(rec)
    return res


def fmt(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:.0f}" if x >= 10 else f"{x:.1f}"


def write_report(out_dir: Path = RESULTS, path: Path = REPORT) -> str:
    mo = mindopt_rows()
    res = load_results(out_dir)
    lines = ["# Benchmark: capopt vs MindOpt", "",
             "Generated by `capopt report` from the stored runs in `benchmarks/results/`. Times are wall-clock seconds "
             "from the start of the run (model build included). The thresholds are relative to the pack's best-known "
             "objective, and every schedule is re-scored by the pack's referee. MindOpt cold is the pack's recorded run "
             "(MindOpt 2.3, 20 threads), measured on this same machine. Where several seeds ran, the table shows the "
             "median, with the range in brackets.", "",
             "| Instance | Rung | Method | Runs | Within 1% | Within 0.1% | Best-known | Proof | Final gap to best-known "
             "| Referee feasible |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name in I.INSTANCE_NAMES:
        m = mo[name]
        lines.append(f"| {name} | ref | MindOpt cold ({m['status'].lower()}, gap {m['gap']:.4%}) | 1 | {fmt(m['time_to_1pct'])} | "
                     f"{fmt(m['time_to_0.1pct'])} | {fmt(m['time_to_best_known'])} | {fmt(m['time_to_proof'])} | - | yes |")
        for cfg, recs in res.get(name, {}).items():
            rung, desc, _ = CONFIGS[cfg]
            cells = []
            for key in ("time_to_1pct", "time_to_0.1pct", "time_to_best_known", "time_to_proof"):
                vals = [r[key] for r in recs]
                med = _median(vals)
                hit = [v for v in vals if v is not None]
                rng = f" ({fmt(min(hit))}-{fmt(max(hit))})" if len(recs) > 1 and hit else ""
                cells.append(fmt(med) + rng)
            gaps = [(r["referee"]["objective"] - r["best_known"]) / abs(r["best_known"]) if r["referee"].get("feasible") else None
                    for r in recs]
            g = max((x for x in gaps if x is not None), default=None)
            feas = all(r["referee"].get("feasible") for r in recs)
            lines.append(f"| | {rung} | {desc} | {len(recs)} | " + " | ".join(cells) +
                         f" | {'-' if g is None else f'{g:.2e}'} | {'yes' if feas else 'NO'} |")
    lines += ["", "Rungs: B0 = baseline (stock HiGHS, same model as MindOpt); I1..In = capopt's improvement ladder "
              "(see docs/MASTER_PLAN.md). '-' = not reached within the time limit.", ""]
    best = {}
    for name in I.INSTANCE_NAMES:
        for cfg, recs in res.get(name, {}).items():
            best.setdefault(cfg, {})[name] = _median([r["time_to_proof"] for r in recs])
    if best:
        lines += ["## Speed-up vs MindOpt cold (time to proof)", "", "| Method | " + " | ".join(I.INSTANCE_NAMES) + " |",
                  "|---|" + "---|" * len(I.INSTANCE_NAMES)]
        for cfg, d in best.items():
            row = []
            for name in I.INSTANCE_NAMES:
                ours, theirs = d.get(name), mo[name]["time_to_proof"]
                row.append("-" if ours is None or theirs is None else f"{theirs / ours:.1f}x")
            lines.append(f"| {CONFIGS[cfg][0]} {cfg} | " + " | ".join(row) + " |")
        lines.append("")
    text = "\n".join(lines)
    path.write_text(text, encoding="utf-8")
    return text
