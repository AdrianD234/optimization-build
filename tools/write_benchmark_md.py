"""Write benchmark_pack/results/BENCHMARK.md and solutions/*_best_known.json from results/benchmark_results.json."""
from __future__ import annotations

import json
from pathlib import Path

PACK = Path(__file__).resolve().parents[1] / "benchmark_pack"
R = json.loads((PACK / "results" / "benchmark_results.json").read_text(encoding="utf-8"))


def fmt_t(v):
    return "-" if v is None else f"{v:.0f}"


lines = [
    "# Benchmark results",
    "",
    f"Measured {R['generated']} on {R['machine']['threads']} threads ({R['machine']['platform']}), MindOpt 2.3, gap target 0, "
    f"time limit {R['settings']['mindopt_time_limit_s']:.0f} s per MindOpt solve; heuristic budget "
    f"{R['settings']['heuristic_seconds']:.0f} s. Times in seconds. Objective: minimise (lower is better).",
    "",
    "| Instance | Best-known objective | Method | Status | Gap | Time to within 1% | Time to within 0.1% | Time to best-known | Total time |",
    "|---|---|---|---|---|---|---|---|---|",
]
def time_to(trace, target, rel):
    goal = target + rel * abs(target)
    return next((t for t, inc in trace if inc <= goal), None)


LOG_TOL = 1e-6  # MindOpt prints incumbents to 7 significant figures
for name, r in R["instances"].items():
    best = r["best_known_objective"]
    for k in ("mindopt_cold", "heuristic_then_mindopt"):
        r[k]["time_to_best_known_s"] = time_to(r[k]["trace"], best, LOG_TOL)
    h = r["heuristic"]
    c, w = r["mindopt_cold"], r["heuristic_then_mindopt"]
    lines.append(f"| {name} | {best:.6e} | MindOpt cold | {c['status']} | {c.get('gap', 0):.4%} | {fmt_t(c['time_to_within_1pct_s'])} | "
                 f"{fmt_t(c['time_to_within_0.1pct_s'])} | {fmt_t(c['time_to_best_known_s'])} | {c['seconds']:.0f} |")
    hcap = f"heuristic ({h['seconds']:.0f} s, {h['gap_to_best_known']:+.2%}) then MindOpt"
    shift = h["seconds"]
    tt = lambda v: "-" if v is None else f"{v + shift:.0f}"  # noqa: E731
    lines.append(f"| | | {hcap} | {w['status']} | {w.get('gap', 0):.4%} | {tt(w['time_to_within_1pct_s'])} | "
                 f"{tt(w['time_to_within_0.1pct_s'])} | {tt(w['time_to_best_known_s'])} | {w['seconds'] + shift:.0f} |")
    pr = r["pipeline_reference"]
    lines.append(f"| | | original pipeline (greedy start then MindOpt) | {pr['status']} | {pr['gap']:.4%} | | | | {pr['solve_seconds']:.0f} |")
lines += [
    "",
    "Notes",
    "* \"Time to within x%\" = first time MindOpt's incumbent was within x% of the best-known objective (from the solver log, whole seconds).",
    "  \"Time to best-known\" uses the log's precision (within 1e-6 relative); the rest of the run is proving optimality.",
    "  For the heuristic-then-MindOpt rows, times include the heuristic's run time.",
    "* MindOpt run times vary between runs of the same instance (P100_growth_nopenalty proved optimal in under 30 s in a smoke test, 132 s here).",
    "* P100_raw_nopenalty has not been proven optimal by any run (best gap 0.0007% after 900 s): many near-equal schedules make the final proof slow.",
    "* All returned schedules were re-scored with reference/evaluate.py and match the MindOpt objective.",
]
(PACK / "results" / "BENCHMARK.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

for name, r in R["instances"].items():
    cands = [("mindopt_cold", r["mindopt_cold"]), ("heuristic_then_mindopt", r["heuristic_then_mindopt"])]
    ref = json.loads((PACK / "solutions" / f"{name}_reference.json").read_text(encoding="utf-8"))
    best = min([("pipeline_reference", {"evaluator_objective": ref["evaluator_objective"], "starts": ref["starts"],
                                        "status": ref["status"], "gap": ref["gap"]})] +
               [(k, v) for k, v in cands if v.get("evaluator_objective") is not None],
               key=lambda kv: kv[1]["evaluator_objective"])
    out = {"instance": name, "objective": best[1]["evaluator_objective"], "found_by": best[0],
           "proven_optimal": any(v.get("status") == "OPTIMAL" for _, v in cands) or ref["status"] == "OPTIMAL",
           "starts": best[1]["starts"]}
    (PACK / "solutions" / f"{name}_best_known.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print((PACK / "results" / "BENCHMARK.md").read_text(encoding="utf-8"))
