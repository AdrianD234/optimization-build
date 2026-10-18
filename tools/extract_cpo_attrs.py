"""Attributes for the distribution layers (capopt.layers): per-dimension PVs, activity class and region per project.

    python tools/extract_cpo_attrs.py <CPO legacy folder> [--population <capsule json>] [--out private/cpo/attributes]

Reads (never writes) the Capital Programme Optimiser's files:
* the GPS27 result pickles (as tools/extract_cpo.py): each holds the benefit kernels of every dimension;
* the Costs sheet of Cost_benefit_streams.xlsx: the "Activity Class" and "Region" columns;
* optionally a population capsule JSON ("population_thousands_governed_names": region -> thousands).

For each scenario of tools/extract_cpo.py it writes <out>/CPO233_<scenario>.json, aligned with that scenario's capopt
instance (same project ids and allowed starts): per project its class, region, and for each dimension (HSP, INC, ENV,
ECO, RES, TOT) the PV at every allowed start ("pv_dim", integer $ x 10,000, discounted exactly as the Total PV) and
at a year-0 start ("pv0_dim", the CPO notebook's PV0 for dimension weights). Checks: the dimension PVs add up to the
instance's Total PV, and the TOT kernel reproduces the instance's pv_int. Real data: written under private/ only.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from extract_cpo import PV_SCALE, SCENARIOS, discount  # noqa: E402

DIMS = {"HSP": "Healthy and safe people", "INC": "Inclusive Access", "ENV": "Environmental Sustainability",
        "ECO": "Economic Prosperity", "RES": "Resilience and Security", "TOT": "Total"}


def clean(name: str) -> str:
    return " ".join(str(name).split()).lower()


def pv_at(b: np.ndarray, s: int, dur: int, disc: np.ndarray) -> float:
    k = np.arange(len(b))
    yrs = s + dur + k
    ok = yrs < len(disc)
    return float(np.sum(b[ok] / disc[yrs[ok]]))


def build(pkl: Path, inst: dict, costs) -> tuple[dict, dict]:
    o = pickle.loads(pkl.read_bytes())
    kernel = o["inputs"]["benefits_kernel_df"]
    T = int(inst["T"])
    disc = discount(T)
    names = inst["source"]["project_names"]
    rows, worst_tot, worst_sum = [], 0.0, 0.0
    for p in inst["projects"]:
        src = names[p["id"]]
        dur = int(p["duration"])
        rec = costs[clean(src)]
        pv_dim, pv0 = {}, {}
        for code, dim in DIMS.items():
            b = kernel.loc[(src, dim)].to_numpy(float)
            pv_dim[code] = [int(round(pv_at(b, int(s), dur, disc) * PV_SCALE)) for s in p["allowed_starts"]]
            pv0[code] = pv_at(b, 0, dur, disc)
        tot = np.array(pv_dim["TOT"], dtype=float)
        ref = np.array(p["pv_int"], dtype=float)
        worst_tot = max(worst_tot, float(np.max(np.abs(tot - ref) / np.maximum(1.0, ref))))
        parts = sum(np.array(pv_dim[c], dtype=float) for c in DIMS if c != "TOT")
        worst_sum = max(worst_sum, float(np.max(np.abs(parts - ref) / np.maximum(1.0, ref))))
        rows.append({"id": p["id"], "activity_class": rec["Activity Class"], "region": rec["Region"],
                     "pv_dim": pv_dim, "pv0_dim": pv0})
    return {"instance": inst["name"], "dims": DIMS, "projects": rows}, {"tot_vs_pv_int": worst_tot,
                                                                         "dims_sum_vs_pv_int": worst_sum}


def main() -> int:
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("legacy", help="CPO legacy folder (read-only): scenario_sets/presets/GPS27, Cost_benefit_streams.xlsx")
    ap.add_argument("--population", help="population capsule JSON (region -> thousands)")
    ap.add_argument("--out", default=str(ROOT / "private" / "cpo" / "attributes"))
    a = ap.parse_args()
    out = Path(a.out)
    if not str(out.resolve()).startswith(str((ROOT / "private").resolve())):
        print("refusing to write CPO-derived data outside private/ (it must never be published)")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    legacy = Path(a.legacy)
    df = pd.read_excel(legacy / "Cost_benefit_streams.xlsx", sheet_name="Costs", engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]
    costs: dict[str, dict] = {}
    for _, r in df.iterrows():
        costs.setdefault(clean(r["Project"]), {"Activity Class": str(r["Activity Class"]).strip(),
                                               "Region": str(r["Region"]).strip()})
    pop = None
    if a.population:
        cap = json.loads(Path(a.population).read_text(encoding="utf-8"))
        pop = {k: float(v) for k, v in cap["population_thousands_governed_names"].items()}
    for name, fname in SCENARIOS.items():
        inst_path = ROOT / "private" / "cpo" / "instances" / f"{name}.json"
        if not inst_path.exists():
            continue
        inst = json.loads(inst_path.read_text(encoding="utf-8"))
        rec, chk = build(legacy / "scenario_sets" / "presets" / "GPS27" / fname, inst, costs)
        rec["population_thousands"] = pop
        (out / f"{name}.json").write_text(json.dumps(rec, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        print(f"[{name}] TOT kernel vs pv_int worst rel diff {chk['tot_vs_pv_int']:.1e} | sum of dimensions vs pv_int "
              f"{chk['dims_sum_vs_pv_int']:.1e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
