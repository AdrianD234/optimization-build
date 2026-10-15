"""Benchmark harness: run solver configurations on the pack instances, store traces, write BENCHMARK.md.

Every stored objective is re-scored with the pack's referee (benchmark_pack/reference/evaluate.py), loaded from file
so the referee itself, not our port, has the last word.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys
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
                        lambda n, tl, seed: solver.solve(n, 20, tl, 60, quick_seconds=0, leaf_cutoff=True)),
    "capopt_resplit": ("I4", "I3 + time-sliced re-splitting (10 s slices) - rejected, slower",
                       lambda n, tl, seed: solver.solve(n, 20, tl, 60, slice_seconds=10.0, quick_seconds=0,
                                                        leaf_cutoff=True)),
    "capopt_lns": ("I5", "I3 + 4 LNS workers around the shared incumbent (first 60 s)",
                   lambda n, tl, seed: solver.solve(n, 20, tl, 60, lns_workers=4, lns_seconds=60.0, quick_seconds=0,
                                                    leaf_cutoff=True)),
    "capopt_cuts": ("I6", "I3 + root cover cuts (30 rounds)",
                    lambda n, tl, seed: solver.solve(n, 20, tl, 60, cut_rounds=30, quick_seconds=0, leaf_cutoff=True)),
    "capopt_quick": ("I7", "I3 + 1.5 s direct solve in the master while the pool spawns",
                     lambda n, tl, seed: solver.solve(n, 20, tl, 60, quick_seconds=1.5, leaf_cutoff=True)),
    "capopt_quick_cuts": ("I8", "I7 + root cover cuts computed while the pool spawns",
                          lambda n, tl, seed: solver.solve(n, 20, tl, 60, quick_seconds=1.5, cut_rounds=30,
                                                                leaf_cutoff=True)),
    "capopt_quick_nocut": ("I9", "I7 without the static leaf cutoff - capopt default",
                           lambda n, tl, seed: solver.solve(n, 20, tl, 60, quick_seconds=1.5, leaf_cutoff=False)),
    "capopt_quick_cuts_nocut": ("I10", "I8 without the static leaf cutoff",
                                lambda n, tl, seed: solver.solve(n, 20, tl, 60, quick_seconds=1.5, cut_rounds=30,
                                                                 leaf_cutoff=False)),
    "mindopt_cold": ("M", "MindOpt cold, live re-run (one solve at a time under the licence lock)", None),
}
# Runs that must never be killed by the watchdog: killing MindOpt mid-solve can lock the floating-licence seat.
NEVER_KILL = {"mindopt_cold"}


def machine() -> dict[str, Any]:
    return {"platform": platform.platform(), "processor": platform.processor(), "threads": os.cpu_count()}


def _mindopt_result(name: str, time_limit: float, out_dir: Path) -> solver.Result | dict:
    from . import mindopt_runner
    t0 = time.time()
    r = mindopt_runner.solve_cold(name, time_limit, out_dir / "mindopt_cold" / "logs")
    if "skipped" in r:
        return r
    st = r.get("starts")
    return solver.Result(name, r.get("objective", float("inf")), st, r["status"] == "OPTIMAL",
                         r.get("dual_bound", float("-inf")), time.time() - t0, [tuple(x) for x in r["trace"]], {},
                         [f"MindOpt {r['status']} gap {r.get('gap')} in {r['seconds']}s (build {r['build_seconds']}s)"])


def run(config: str, name: str, time_limit: float = 900.0, seed: int = 0, out_dir: Path = RESULTS) -> dict[str, Any]:
    rung, desc, fn = CONFIGS[config]
    t = time.time()
    r = _mindopt_result(name, time_limit, out_dir) if config == "mindopt_cold" else fn(name, time_limit, seed)
    if isinstance(r, dict):  # MindOpt could not run here: record why; the report keeps the pack's recorded run
        rec = {"config": config, "rung": rung, "instance": name, "seed": seed, "skipped": r["skipped"],
               "generated": datetime.now().isoformat(timespec="seconds"), "machine": machine()}
        d = out_dir / config
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}_s{seed}.skipped.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
        return rec
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


def run_isolated(config: str, name: str, time_limit: float = 900.0, seed: int = 0, out_dir: Path = RESULTS,
                 grace: float = 180.0) -> dict[str, Any]:
    """`run` in a child process with a hard wall-clock limit (time_limit + grace). If the child has to be killed (a
    solver that ignores its time limit), the record is rebuilt from the streamed incumbent trace and marked killed."""
    d = out_dir / config
    d.mkdir(parents=True, exist_ok=True)
    trace_file = d / f"{name}_s{seed}.trace.jsonl"
    trace_file.unlink(missing_ok=True)
    env = dict(os.environ, CAPOPT_TRACE_FILE=str(trace_file))
    cmd = [sys.executable, "-m", "capopt.cli", "bench", "--configs", config, "--instances", name, "--seeds", str(seed),
           "--time-limit", str(time_limit), "--no-isolate", "--out-dir", str(out_dir)]
    t = time.time()
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL)  # the parent prints the summary line
    if config in NEVER_KILL:
        # MindOpt stops itself at MaxTime. Be patient; if it still has not finished, leave it running (killing it can
        # lock the licence seat) and record that, rather than block the benchmark forever.
        try:
            proc.wait(timeout=3 * time_limit + 600)
        except subprocess.TimeoutExpired:
            return {"config": config, "instance": name, "seed": seed, "skipped":
                    f"no result after {3 * time_limit + 600:.0f}s; process {proc.pid} left running to protect the licence"}
        skipped = d / f"{name}_s{seed}.skipped.json"
        if skipped.exists():
            return json.loads(skipped.read_text(encoding="utf-8"))
        return json.loads((d / f"{name}_s{seed}.json").read_text(encoding="utf-8"))
    try:
        proc.wait(timeout=time_limit + grace)
        rec = json.loads((d / f"{name}_s{seed}.json").read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        proc.wait()
        rec = _record_from_trace(config, name, seed, time_limit, trace_file, time.time() - t)
        (d / f"{name}_s{seed}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    trace_file.unlink(missing_ok=True)
    return rec


def _record_from_trace(config, name, seed, time_limit, trace_file: Path, secs: float) -> dict[str, Any]:
    rung, desc, _ = CONFIGS[config]
    pts = [json.loads(x) for x in trace_file.read_text(encoding="utf-8").splitlines()] if trace_file.exists() else []
    pts.sort(key=lambda r: r["t"])
    trace, run_best, starts = [], float("inf"), None
    for r in pts:
        if r["obj"] < run_best:
            run_best = r["obj"]
            trace.append((r["t"], r["obj"]))
            starts = r["starts"]
    inst = I.load(name)
    sd = inst.starts_to_dict(starts) if starts else None
    best = I.best_known(name)["objective"]
    return {"config": config, "rung": rung, "description": desc, "instance": name, "seed": seed,
            "generated": datetime.now().isoformat(timespec="seconds"), "machine": machine(), "time_limit": time_limit,
            "objective": run_best, "referee": referee_score(name, sd) if sd else {"feasible": False}, "best_known": best,
            "proven": False, "dual_bound": None, "seconds": round(secs, 2), "wall_seconds": round(secs, 2),
            "trace": trace, "time_to_1pct": time_to(trace, best, 0.01), "time_to_0.1pct": time_to(trace, best, 0.001),
            "time_to_best_known": time_to(trace, best, 1e-6), "time_to_proof": None, "last_years": {},
            "log": [f"killed by watchdog after {secs:.0f}s (solver ignored its {time_limit:.0f}s time limit)"],
            "killed": True, "starts": sd}


def time_to(trace: list, target: float, rel: float) -> float | None:
    goal = target + rel * abs(target) + 1e-9 * abs(target)
    for t, v in trace:
        if v <= goal:
            return round(t, 2)
    return None


# ---- report -------------------------------------------------------------------------------------------------------
# MindOpt's recorded pack runs are kept privately (licence terms on publishing benchmarks), under the gitignored
# private/ folder. The public report compares capopt with the HiGHS baseline (B0) only; `capopt report` also writes
# a private report that includes MindOpt when that data is present.
MINDOPT_RESULTS = ROOT / "private" / "pack_mindopt" / "results" / "benchmark_results.json"
PRIVATE_REPORT = ROOT / "private" / "pack_mindopt" / "BENCHMARK_with_mindopt.md"
BASELINE = "highs_reference"


def baseline_rows(res: dict) -> dict[str, dict[str, Any]]:
    """The HiGHS baseline (B0) per instance, as medians of its stored runs."""
    out = {}
    for name in I.INSTANCE_NAMES:
        recs = res.get(name, {}).get(BASELINE)
        if recs:
            out[name] = {k: _median([r[k] for r in recs]) for k in
                         ("time_to_1pct", "time_to_0.1pct", "time_to_best_known", "time_to_proof")}
            out[name]["seconds"] = max(r["seconds"] for r in recs)
            out[name]["status"] = "proven" if out[name]["time_to_proof"] is not None else "not proven"
    return out


def mindopt_rows(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """The pack's recorded MindOpt cold runs, per instance ({} when the private data isn't present)."""
    path = MINDOPT_RESULTS if path is None else path
    if not Path(path).exists():
        return {}
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for name, rec in d["instances"].items():
        c = rec["mindopt_cold"]
        # time to best-known is recomputed from the log trace exactly as tools/write_benchmark_md.py does
        # (the log prints 7 significant figures, hence the 1e-6 relative tolerance)
        tbk = time_to(c["trace"], rec["best_known_objective"], 1e-6)
        out[name] = {"time_to_1pct": c["time_to_within_1pct_s"], "time_to_0.1pct": c["time_to_within_0.1pct_s"],
                     "time_to_best_known": tbk,
                     "time_to_proof": c["seconds"] if c["status"] == "OPTIMAL" else None,
                     "seconds": c["seconds"], "status": f"{c['status'].lower()}, gap {c['gap']:.4%}"}
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
        for f in sorted((out_dir / cfg).glob("*_s*[0-9].json")) if (out_dir / cfg).exists() else []:
            rec = json.loads(f.read_text(encoding="utf-8"))
            res.setdefault(rec["instance"], {}).setdefault(cfg, []).append(rec)
    return res


def fmt(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:.0f}" if x >= 10 else f"{x:.1f}"


DEFAULT_CONFIG = "capopt_quick_nocut"


def _headline(ref: dict, ref_label: str, res: dict) -> list[str]:
    """capopt's default pipeline against the reference solver, one row per instance and metric."""
    if not ref or not any(DEFAULT_CONFIG in res.get(n, {}) for n in I.INSTANCE_NAMES):
        return []
    out = [f"## Headline: capopt default ({CONFIGS[DEFAULT_CONFIG][0]}) vs {ref_label}", "",
           "| Instance | Within 1% | Within 0.1% | Pack best-known | Proof of optimality |", "|---|---|---|---|---|"]
    for name in I.INSTANCE_NAMES:
        recs = res.get(name, {}).get(DEFAULT_CONFIG)
        if not recs or name not in ref:
            continue
        cells = []
        for key in ("time_to_1pct", "time_to_0.1pct", "time_to_best_known", "time_to_proof"):
            ours, theirs = _median([r[key] for r in recs]), ref[name][key]
            if ours and theirs:
                k = theirs / ours
                cells.append(f"{fmt(ours)} s vs {fmt(theirs)} s ({k:.1f}x)" if k < 10 else
                             f"{fmt(ours)} s vs {fmt(theirs)} s ({k:.0f}x)")
            elif ours:
                cells.append(f"{fmt(ours)} s ({ref_label}: not reached)")
            else:
                cells.append(f"- vs {fmt(theirs)} s")
        out.append(f"| {name} | " + " | ".join(cells) + " |")
    out += ["", "capopt also found and proved a **new optimum for P100_raw_nopenalty**: -74,119,916,766,966, 5.2e-6 better "
            "than the pack's best-known (`benchmarks/solutions/P100_raw_nopenalty_proven_optimal.json`).", ""]
    return out


def write_report(out_dir: Path = RESULTS, path: Path = REPORT, include_mindopt: bool = False) -> str:
    """The benchmark report. Public (default): capopt vs the HiGHS baseline. include_mindopt=True adds the pack's
    recorded MindOpt runs, which are private data: write that version under private/ only."""
    res = load_results(out_dir)
    mo = mindopt_rows() if include_mindopt else {}
    ref, ref_label = (mo, "MindOpt cold") if mo else (baseline_rows(res), "HiGHS baseline")
    lines = [f"# Benchmark: capopt vs {ref_label}", "",
             "Generated by `capopt report` from the stored runs in `benchmarks/results/`. Times are wall-clock seconds "
             "from the start of the run (model build included). The thresholds are relative to the pack's best-known "
             "objective, and every schedule is re-scored by the pack's referee. The HiGHS baseline (B0) is stock "
             "HiGHS 1.15.1 on a faithful port of the reference production model, run on this machine. Where several "
             "seeds ran, the table shows the median, with the range in brackets.", "",
             "| Instance | Rung | Method | Runs | Within 1% | Within 0.1% | Best-known | Proof | Final gap to best-known "
             "| Referee feasible |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name in I.INSTANCE_NAMES:
        if mo:
            m = mo[name]
            lines.append(f"| {name} | ref | MindOpt cold ({m['status']}) | 1 | {fmt(m['time_to_1pct'])} | "
                         f"{fmt(m['time_to_0.1pct'])} | {fmt(m['time_to_best_known'])} | {fmt(m['time_to_proof'])} | - | yes |")
        else:
            lines.append(f"| {name} | | | | | | | | | |")
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
            if any(r.get("killed") for r in recs):
                desc += " (killed by the watchdog: solver ignored its time limit)"
            lines.append(f"| | {rung} | {desc} | {len(recs)} | " + " | ".join(cells) +
                         f" | {'-' if g is None else f'{g:.2e}'} | {'yes' if feas else 'NO'} |")
    lines += ["", "Rungs: B0 = baseline (stock HiGHS on the reference production model); I1..In = capopt's improvement "
              "ladder (see docs/MASTER_PLAN.md). '-' = not reached within the time limit.", ""]
    best = {}
    for name in I.INSTANCE_NAMES:
        for cfg, recs in res.get(name, {}).items():
            best.setdefault(cfg, {})[name] = _median([r["time_to_proof"] for r in recs])
    if best and ref:
        lines += [f"## Speed-up vs {ref_label} (time to proof)", "", "| Method | " + " | ".join(I.INSTANCE_NAMES) + " |",
                  "|---|" + "---|" * len(I.INSTANCE_NAMES)]
        for cfg, d in best.items():
            row = []
            for name in I.INSTANCE_NAMES:
                ours, theirs = d.get(name), ref.get(name, {}).get("time_to_proof")
                if ours is not None and theirs is None and name in ref:
                    row.append(f"proved in {fmt(ours)} s ({ref_label} did not, {ref[name]['seconds']:.0f} s)")
                else:
                    row.append("-" if ours is None or theirs is None else f"{theirs / ours:.1f}x")
            lines.append(f"| {CONFIGS[cfg][0]} {cfg} | " + " | ".join(row) + " |")
        lines.append("")
    head = lines[:3]  # title, blank, method paragraph
    text = "\n".join(head + [""] + _headline(ref, ref_label, res) + ["## All runs", ""] + lines[4:])
    path.write_text(text, encoding="utf-8")
    return text
