"""One MindOpt solve in the interpreter that has mindoptpy (called by capopt.campaign:mindopt_reference_runner).

    <mindopt python> benchmarks/experiments/run_mindopt.py <instance json> <time_limit> <rel_gap> <out json> <log dir>

It needs only numpy plus the pack's reference model, so it runs in a separate environment (e.g. Anaconda) without
capopt's own dependencies. It respects the floating licence through capopt.mindopt_runner: a machine-wide lock, a
licence probe with backoff, MaxTime instead of being killed, and the model disposed so the seat is handed back. The
returned schedule is re-scored with capopt's evaluator.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from capopt import instance as I  # noqa: E402
from capopt import mindopt_runner  # noqa: E402
from capopt.evaluate import evaluate  # noqa: E402

if __name__ == "__main__":
    inst_path, tl, gap, out_path, log_dir = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4], sys.argv[5]
    t0 = time.time()
    res = mindopt_runner.solve_cold(inst_path, tl, Path(log_dir), rel_gap=gap)
    res["wall_seconds"] = round(time.time() - t0, 2)
    if res.get("starts"):
        inst = I.load(inst_path)
        ev = evaluate(inst, inst.starts_from_dict(res["starts"]))
        res["evaluator_feasible"] = ev["feasible"]
        res["evaluator_objective"] = ev.get("objective")
    Path(out_path).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: res.get(k) for k in ("status", "seconds", "gap", "objective", "evaluator_objective",
                                              "skipped")}), flush=True)
