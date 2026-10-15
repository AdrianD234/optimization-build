"""Export the 100-project benchmark instances + known solutions from a capital-programme run.

    C:/Conda/anaconda3/python.exe tools/build_pack.py <results.json>

Writes benchmark_pack/instances/*.json, benchmark_pack/solutions/*.json, benchmark_pack/data/projects.csv,
then checks (1) every PV coefficient against the run's stored per-start PV curves and (2) that the
reference evaluator reproduces the MindOpt objective value of each stored schedule.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "benchmark_pack"
sys.path.insert(0, str(PACK / "reference"))
from evaluate import evaluate, prepare  # noqa: E402

T, BASE = 60, 2026
SPEND_SCALE, PV_SCALE = 100, 10_000
INSTANCES = {  # name -> (benefit profile, objective, run solve key)
    "P100_raw_penalised": ("raw", "production", ("raw", "production")),
    "P100_growth_penalised": ("growth", "production", ("growth", "production")),
    "P100_raw_nopenalty": ("raw", "pure_pv", ("raw", "pure_pv")),
    "P100_growth_nopenalty": ("growth", "pure_pv", ("growth", "pure_pv")),
}


def disc_vec() -> list[float]:
    d = [1.0] * T
    for t in range(1, T):
        d[t] = d[t - 1] * (1.02 if t <= 30 else 1.015)
    return d


def main() -> int:
    src = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    cfg = src["config"]
    E = cfg["envelope_m"]
    F_S = [int(round(E * SPEND_SCALE))] * T
    disc = disc_vec()
    projects = []
    worst_pv_rel = 0.0
    rounding_zero_flags = 0
    for i, p in enumerate(src["projects"]):
        pid = f"P{i + 1:03d}"
        dur = p["duration"]
        spend_m = p["spend_profile_m"]
        spend_S = [int(round(a * SPEND_SCALE)) for a in spend_m]
        nonzero = [a != 0.0 for a in spend_m]
        rounding_zero_flags += sum(1 for a, s in zip(spend_m, spend_S) if a != 0.0 and s == 0)
        # allowed starts: finish inside the horizon and fundable alone from cumulative funding (as the model prunes)
        allowed = []
        for s in range(0, T - dur + 1):
            cum_f, cum_s, ok = 0.0, 0.0, True
            for t in range(s + dur):
                cum_f += E
                if t >= s:
                    cum_s += spend_m[t - s]
                    if cum_s > cum_f + 1e-9:
                        ok = False
                        break
            if ok:
                allowed.append(s)
        assert len(allowed) == p["allowed_starts"], (pid, len(allowed), p["allowed_starts"])
        b = p["annual_benefit_m"] * 1e6
        curves = {c["start_year"] - BASE: c for c in src["curves"][p["project"]]}
        pv = {"raw": [], "growth": []}
        for s in allowed:
            for prof in ("raw", "growth"):
                g = p["growth_rate"] if prof == "growth" else 0.0
                v = sum(b * (1 + g) ** t / disc[t] for t in range(s + dur, min(s + dur + 40, T)))
                ref = curves[s]["pv_raw_m" if prof == "raw" else "pv_growth_calendar_m"] * 1e6
                if ref:
                    worst_pv_rel = max(worst_pv_rel, abs(v - ref) / ref)
                pv[prof].append(int(round(v * PV_SCALE)))
        projects.append({"id": pid, "source_name": p["project"], "duration": dur, "spend_S": spend_S,
                         "spend_nonzero": nonzero, "allowed_starts": allowed, "pv": pv,
                         "cost_m": p["cost_m"], "base_bcr": p["base_bcr"], "growth_rate": p["growth_rate"],
                         "annual_benefit_m": p["annual_benefit_m"], "cost_band": p["cost_band"]})
    print(f"[check] worst relative PV-coefficient difference vs the run's stored curves: {worst_pv_rel:.2e}")
    print(f"[check] nonzero spend entries that round to 0 units: {rounding_zero_flags}")

    tiers = cfg["piecewise_tiers"]
    (PACK / "instances").mkdir(parents=True, exist_ok=True)
    (PACK / "solutions").mkdir(parents=True, exist_ok=True)
    (PACK / "data").mkdir(parents=True, exist_ok=True)
    solves = {(s["profile"], s["objective"]): s for s in src["solves"]}
    name_of = {p["source_name"]: p["id"] for p in projects}
    for name, (prof, objective, key) in INSTANCES.items():
        penalised = objective == "production"
        inst = {
            "name": name,
            "description": (f"100-project capital programme, 60-year horizon {BASE}-{BASE + T - 1}, "
                            f"{'growth' if prof == 'growth' else 'no growth'} in benefits, "
                            f"{'production objective with holding-cash penalties' if penalised else 'maximise PV only (no holding-cash penalty)'}."),
            "objective": objective,
            "sense": "minimise",
            "T": T, "base_year": BASE,
            "units": {"spend_and_cash": "1 unit = $0.01M (real)", "pv_int": "PV of benefits in $ x 10,000 (integer)"},
            "funding_S": F_S,
            "max_starts_per_year": 100,
            "weights": {"backlog_weight": cfg["backlog_weight"] if penalised else 0.0,
                        "pv_weight": cfg["pv_weight"] if penalised else 1.0},
            "tiers": {"base_cap_frac": tiers[0][0],
                      "bands": [{"from_frac": tiers[i][0], "to_frac": tiers[i + 1][0] if i + 1 < len(tiers) else None,
                                 "weight": tiers[i][1] if penalised else 0.0} for i in range(len(tiers))]},
            "projects": [{"id": p["id"], "duration": p["duration"], "spend_S": p["spend_S"], "spend_nonzero": p["spend_nonzero"],
                          "allowed_starts": p["allowed_starts"], "pv_int": p["pv"][prof]} for p in projects],
        }
        (PACK / "instances" / f"{name}.json").write_text(json.dumps(inst, separators=(",", ":")), encoding="utf-8")
        s = solves[key]
        starts = {name_of[n]: y - BASE for n, y in s["start_map"].items()}
        res = evaluate(prepare(json.loads(json.dumps(inst))), starts)
        rel = abs(res["objective"] - s["objective_value"]) / abs(s["objective_value"])
        status = {"1": "OPTIMAL", "7": "TIME LIMIT"}.get(str(s["status"]), str(s["status"]))
        sol = {"instance": name, "source": "MindOpt 2.3 via the production pipeline (greedy multi-start warm start), 20 threads",
               "status": status, "gap": s["gap"], "solve_seconds": round(s["seconds"], 1),
               "mindopt_objective": s["objective_value"], "evaluator_objective": res["objective"],
               "evaluator_vs_mindopt_rel_diff": rel, "starts": starts}
        (PACK / "solutions" / f"{name}_reference.json").write_text(json.dumps(sol, indent=1), encoding="utf-8")
        print(f"[{name}] {status} gap {s['gap']:.4%} {s['seconds']:.0f}s | MindOpt obj {s['objective_value']:.6e} "
              f"| evaluator {res['objective']:.6e} | rel diff {rel:.1e} | feasible {res['feasible']}")
    with (PACK / "data" / "projects.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "duration_yrs", "cost_m", "base_bcr", "growth_rate", "annual_benefit_m_2026", "cost_band",
                    "spend_profile_m_by_build_year"])
        for p in projects:
            src_p = next(q for q in src["projects"] if q["project"] == p["source_name"])
            w.writerow([p["id"], p["duration"], round(p["cost_m"], 4), p["base_bcr"], p["growth_rate"],
                        round(p["annual_benefit_m"], 6), p["cost_band"],
                        " ".join(f"{a:.4f}" for a in src_p["spend_profile_m"])])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
