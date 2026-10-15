"""Experiment 7 (stage a, single thread): H11 construction + H10 weighted local search on CPO scenarios.

    python benchmarks/experiments/exp07_h11h10.py [seconds=60] [seeds=3] [scenario ...]

For each scenario and seed: build alpha-point schedules with cash repair (H11), then run the weighted local search (H10)
in 1-second chunks, each continuing from the best feasible schedule so far. Reports the best schedule's distance from
the CPO reference objective at 2/5/10/30/60 s. The bar for this stage: single-thread LNS reached 0.88% at 90 s, and
plain HiGHS reached 2.5% at 60 s (s2000_P50high). Results go to private/cpo/results/exp07/ (CPO data stays private).
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

from capopt import instance as I
from capopt.formulation import last_year_model
from capopt.heuristics import fjls
from capopt.heuristics import kernel as K
from capopt.solve import new_highs

ROOT = Path(__file__).resolve().parents[2]
CPO = ROOT / "private" / "cpo"
SCREEN = ["CPO233_s2000_P50high", "CPO233_s2000_P50", "CPO233_cfadsds_P50"]
CHECKPOINTS = (2, 5, 10, 30, 60)

if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    names = sys.argv[3:] or SCREEN
    out = CPO / "results" / "exp07"
    out.mkdir(parents=True, exist_ok=True)
    for nm in names:
        inst = I.load(CPO / "instances" / f"{nm}.json")
        ref = json.loads((CPO / "solutions" / f"{nm}_cpo_reference.json").read_text())["evaluator_objective"]
        L = inst.min_last_year()
        t0 = time.time()
        m = last_year_model(inst, L, "pulse", "cumulative")
        h = new_highs(1)
        h.setOptionValue("output_flag", False)
        h.setOptionValue("solve_relaxation", True)
        m.builder.to_highs(h)
        h.run()
        prep = fjls.prepare(inst, L, np.asarray(h.getSolution().col_value), m)
        pk = prep["pk"]
        fjls.run(prep, 0, 1000)  # compile (numba cache) outside the timed region
        setup = time.time() - t0
        rows = []
        for seed in range(seeds):
            t1 = time.time()
            best, st = fjls.run(prep, seed, 20_000)  # construction + a short burst
            first = (time.time() - t1, best)
            trace = [first]
            rng = np.random.default_rng(seed)
            # calibrate iterations per second once
            t2 = time.time()
            fjls.fj_search(st, 50_000, seed, float(pk.bw + pk.band_w.max()) * 10.0, 1.5, 2000, 1e-7,
                           pk.spend.astype(np.float64), pk.nz, pk.dur, pk.lo, pk.hi, pk.pv, pk.F, pk.base, pk.band_w,
                           pk.band_width, pk.bw, pk.pvw, prep["cap"], prep["L"])
            per_sec = 50_000 / max(1e-3, time.time() - t2)
            while time.time() - t1 < secs:
                v, st2 = fjls.fj_search(st if np.isfinite(best) else st, int(per_sec), int(rng.integers(1 << 30)),
                                        float(pk.bw + pk.band_w.max()) * 10.0, 1.5, 2000, 1e-7,
                                        pk.spend.astype(np.float64), pk.nz, pk.dur, pk.lo, pk.hi, pk.pv, pk.F, pk.base,
                                        pk.band_w, pk.band_width, pk.bw, pk.pvw, prep["cap"], prep["L"])
                if v < best:
                    best, st = v, st2
                trace.append((time.time() - t1, best))
            # verify the final schedule with the exact evaluator path
            exact = K.cost(K.pack(inst), st, inst.penalised) if np.isfinite(best) else np.inf
            assert not np.isfinite(best) or abs(exact - best) <= 1e-9 * abs(best), (exact, best)
            gaps = {c: next(((b - ref) / abs(ref) for t, b in reversed(trace) if t <= c), None) for c in CHECKPOINTS}
            rows.append({"seed": seed, "first_seconds": round(first[0], 3),
                         "first_gap": None if not np.isfinite(first[1]) else (first[1] - ref) / abs(ref),
                         "gap_at": gaps, "iters_per_sec": per_sec})
            fmt = " ".join(f"{c}s:{'-' if g is None or not np.isfinite(g) else f'{g:+.2e}'}" for c, g in gaps.items())
            fg = rows[-1]["first_gap"]
            fg_s = "infeasible" if fg is None else f"{fg:+.2e}"
            print(f"[{nm} seed {seed}] first {fg_s} at {first[0]:.2f}s | {fmt} | {per_sec / 1e3:.0f}k iters/s", flush=True)
        (out / f"{nm}.json").write_text(json.dumps({"setup_seconds": setup, "runs": rows}, indent=1), encoding="utf-8")
