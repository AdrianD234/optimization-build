"""Build capopt instances from Capital Programme Optimiser (CPO) result pickles.

    python tools/extract_cpo.py <path to CPO legacy/scenario_sets/presets/GPS27> [--out private/cpo]

The CPO repository is read-only reference material: nothing from it is imported or run. Each CPO result pickle is
self-contained (cost profiles, benefit kernels, the funding path, weights and tiers, plus the CPO's solved schedule).
This script turns a chosen set of them into instances in the benchmark-pack format (see benchmark_pack/FORMULATION.md)
and the CPO's schedules into reference solutions. It then checks the conversion:

* every project's PV at its CPO start must match the CPO's stored per-project PV;
* the CPO schedule must be feasible under capopt's evaluator, and its total PV must match the CPO's stored pv_total.

The output holds real programme data: it is written under private/ (gitignored) and must never be published.
Units follow the pack: spend and cash in $0.01M integers, PV as integer $ x 10,000.
"""
from __future__ import annotations

import argparse
import json
import pickle
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from capopt import instance as I  # noqa: E402
from capopt.evaluate import evaluate  # noqa: E402

SPEND_SCALE, PV_SCALE = 100, 10_000
TIERS_DEFAULT = [(0.12, 1000.0), (0.15, 4000.0), (0.20, 12000.0)]
MAX_STARTS = 100  # the CPO notebook model's cap on project starts per year (binds in 2026 on the envelope runs)

# instance name -> CPO GPS27 preset file ("objective best" = pure optimisation, nothing pinned; Total dimension)
SCENARIOS = {
    "CPO233_cfads_P50": "CFADS objective bestP50_Real_LIN40_base_cfads_TOT.pkl",
    "CPO233_cfads_P95": "CFADS objective bestP95_Real_LIN40_base_cfads_TOT.pkl",
    "CPO233_s1750_P50": "objective bestP50Real_LIN40_base_s1500_pm250_TOT.pkl",
    "CPO233_s1750_P95": "objective bestP95Real_LIN40_base_s1500_pm250_TOT.pkl",
    "CPO233_cfadsds_P50": "CFADS less debt service - objective bestP50_Real_LIN40_base_cfads_ds_TOT.pkl",
    "CPO233_s1500_P50": "objective bestP50Real_LIN40_base_s1500_pm0_TOT.pkl",
    "CPO233_s2000_P50": "objective bestP50Real_LIN40_base_s2000_pm0_TOT.pkl",
    # further scenarios with a COPT solve time in the CPO notebook's log (benefit "high" = benefits x 1.2)
    "CPO233_s1500_P50high": "objective bestP50Real_LIN40_high_s1500_pm0_TOT.pkl",
    "CPO233_s2000_P50high": "objective bestP50Real_LIN40_high_s2000_pm0_TOT.pkl",
    "CPO233_cfadsds_P50high": "CFADS less debt service - objective bestP50_Real_LIN40_high_cfads_ds_TOT.pkl",
    "CPO233_cfadsds_P95": "CFADS less debt service - objective bestP95_Real_LIN40_base_cfads_ds_TOT.pkl",
    "CPO233_cfadsds_P95high": "CFADS less debt service - objective bestP95_Real_LIN40_high_cfads_ds_TOT.pkl",
}


def discount(T: int) -> np.ndarray:
    """MBCM discount factors: 2% p.a. for years 1-30, 1.5% after (year 0 = base year)."""
    d = np.ones(T)
    for t in range(1, T):
        d[t] = d[t - 1] * (1.02 if t <= 30 else 1.015)
    return d


def project_number(name: str) -> int:
    m = re.search(r"(\d+)$", name)
    return int(m.group(1)) if m else 0


def build(pkl: Path, name: str, objective: str = "production") -> tuple[dict, dict, dict]:
    o = pickle.loads(pkl.read_bytes())
    costs = o["inputs"]["costs_input_df_M"]
    kernel = o["inputs"]["benefits_kernel_df"]
    sched = o["schedule"].set_index("Project")
    years = [int(y) for y in costs.columns]
    T, base = len(years), years[0]
    if "AvailableFunding" in o["cash_flow"]:
        funding_m = np.clip(o["cash_flow"]["AvailableFunding"].to_numpy(float), 0.0, None)
    else:
        funding_m = o["envelope"]["Envelope"].to_numpy(float)
    meta = o.get("meta", {})
    tiers = [tuple(t) for t in meta.get("net_cap_tiers", TIERS_DEFAULT)]
    penalised = objective == "production"
    disc = discount(T)
    cum_f = np.cumsum(funding_m)
    names = sorted(costs.index, key=project_number)
    projects, ref_starts, pv_checks = [], {}, []
    for i, proj in enumerate(names):
        pid = f"P{i + 1:03d}"
        dur = int(sched.loc[proj, "Dur"])
        prof = costs.loc[proj].to_numpy(float)
        assert not np.any(prof[dur:] != 0), f"{proj}: spend beyond its duration"
        prof = prof[:dur]
        b = kernel.loc[(proj, "Total")].to_numpy(float)
        allowed, pv = [], []
        for s in range(0, T - dur + 1):
            # fundable on its own: cumulative funding covers this project's cumulative spend in every build year
            cum_s = np.cumsum(prof)
            if np.any(cum_s > cum_f[s:s + dur] + 1e-9):
                continue
            k = np.arange(len(b))
            yrs = s + dur + k
            ok = yrs < T
            v = float(np.sum(b[ok] / disc[yrs[ok]]))
            allowed.append(s)
            pv.append(int(round(v * PV_SCALE)))
        start = int(sched.loc[proj, "StartFY"]) - base
        ref_starts[pid] = start
        stored = float(o["pv_by_project_and_dimension"].loc[(proj, "Total"), "PV"])
        ours = pv[allowed.index(start)] / PV_SCALE if start in allowed else float("nan")
        pv_checks.append(abs(ours - stored) / max(1.0, abs(stored)))
        projects.append({"id": pid, "source_name": proj, "duration": dur,
                         "spend_S": [int(round(a * SPEND_SCALE)) for a in prof],
                         "spend_nonzero": [bool(a != 0.0) for a in prof],
                         "allowed_starts": allowed, "pv_int": pv})
    inst = {
        "name": name if penalised else f"{name}_purePV",
        "description": f"CPO GPS27 preset '{pkl.stem}': {len(projects)} projects, {T}-year horizon {base}-{base + T - 1}, "
                       + ("production objective (holding-cash tiers)" if penalised else "maximise PV only"),
        "objective": "production" if penalised else "pure_pv",
        "sense": "minimise",
        "T": T, "base_year": base,
        "units": {"spend_and_cash": "1 unit = $0.01M (real)", "pv_int": "PV of benefits in $ x 10,000 (integer)"},
        "funding_S": [int(round(f * SPEND_SCALE)) for f in funding_m],
        "max_starts_per_year": MAX_STARTS,
        "weights": {"backlog_weight": float(meta.get("backlog_weight", 1.0)) if penalised else 0.0,
                    "pv_weight": float(meta.get("pv_weight", 1e-4)) if penalised else 1.0},
        "tiers": {"base_cap_frac": tiers[0][0],
                  "bands": [{"from_frac": tiers[j][0], "to_frac": tiers[j + 1][0] if j + 1 < len(tiers) else None,
                             "weight": tiers[j][1] if penalised else 0.0} for j in range(len(tiers))]},
        "projects": [{k: p[k] for k in ("id", "duration", "spend_S", "spend_nonzero", "allowed_starts", "pv_int")}
                     for p in projects],
        "source": {"cpo_preset": pkl.name, "project_names": {p["id"]: p["source_name"] for p in projects}},
    }
    ev = evaluate(I.from_dict(inst), I.from_dict(inst).starts_from_dict(ref_starts))
    sol = {"instance": inst["name"], "source": f"CPO stored result ({pkl.name})", "cpo_status": o.get("status"),
           "cpo_gap": o.get("gap"), "cpo_pv_total": o.get("pv_total"), "cpo_created_at": o.get("created_at"),
           "cpo_threads": meta.get("threads"), "evaluator_feasible": ev["feasible"],
           "evaluator_objective": ev.get("objective"), "evaluator_pv": ev.get("components", {}).get("pv_int_sum", 0) / PV_SCALE,
           "last_build_year": base + ev["last_build_year_index"] if ev["feasible"] else None, "starts": ref_starts}
    checks = {"projects": len(projects), "T": T, "worst_pv_rel_diff": max(pv_checks),
              "pv_total_rel_diff": abs(sol["evaluator_pv"] - (o.get("pv_total") or 0)) / max(1.0, o.get("pv_total") or 1),
              "reference_feasible": ev["feasible"], "reason": ev.get("reason")}
    return inst, sol, checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("presets", help="CPO legacy/scenario_sets/presets/GPS27 folder (read-only)")
    ap.add_argument("--out", default=str(ROOT / "private" / "cpo"))
    ap.add_argument("--pure-pv", action="store_true", help="also write maximise-PV-only variants")
    a = ap.parse_args()
    out = Path(a.out)
    if not str(out.resolve()).startswith(str((ROOT / "private").resolve())):
        print("refusing to write CPO-derived data outside private/ (it must never be published)")
        return 2
    (out / "instances").mkdir(parents=True, exist_ok=True)
    (out / "solutions").mkdir(parents=True, exist_ok=True)
    for name, fname in SCENARIOS.items():
        pkl = Path(a.presets) / fname
        for objective in (("production", "pure_pv") if a.pure_pv else ("production",)):
            inst, sol, chk = build(pkl, name, objective)
            (out / "instances" / f"{inst['name']}.json").write_text(json.dumps(inst, separators=(",", ":")), encoding="utf-8")
            if objective == "production":
                (out / "solutions" / f"{inst['name']}_cpo_reference.json").write_text(json.dumps(sol, indent=1),
                                                                                   encoding="utf-8")
            print(f"[{inst['name']}] {chk['projects']} projects, T={chk['T']} | PV coeff worst rel diff "
                  f"{chk['worst_pv_rel_diff']:.1e} | PV total rel diff {chk['pv_total_rel_diff']:.1e} | CPO schedule "
                  f"feasible: {chk['reference_feasible']} {chk['reason'] or ''}| CPO gap {sol['cpo_gap']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
