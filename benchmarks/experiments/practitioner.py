"""E58+: practitioner capability demonstrations on the private CPO scenarios, with report-ready data.

    python benchmarks/experiments/practitioner.py <command> [--force] [--scenarios cfads_P50,cfadsds_P50]

Commands (one per capability; each runs its solves one at a time, waits for a quiet machine before each, load-tags
every run, caches each run's record, and writes tidy chart data):
    windows     E58 forced start windows

Runs go to private/cpo/results/prac/<command>/<label>.json; instances to private/cpo/instances/prac/; chart data and
tables to private/report_data/<command>/. Real data (project names included) stays under private/ (gitignored); the
docs carry aggregates only. A cached run is reused unless --force.

Definitions used in every output:
    PV           present value of benefits, $ (the instance's pv_int / 1e4; MBCM discounting, base year 2026)
    cost PV      present value of the capital spend, $, with the same discount factors
    BCR          PV / cost PV (programme or project)
    certified gap (objective - proven bound) / |objective| of the run's own result
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from capopt import campaign as C  # noqa: E402
from capopt import instance as I  # noqa: E402
from capopt import solver  # noqa: E402
from capopt import timing as TM  # noqa: E402
from capopt.evaluate import evaluate  # noqa: E402

CPO = Path(os.environ.get("CAPOPT_CPO", ROOT / "private" / "cpo"))
REPORT = Path(os.environ.get("CAPOPT_REPORT", ROOT / "private" / "report_data"))
INST = CPO / "instances" / "prac"
RES = CPO / "results" / "prac"
# OneDrive's sync holds about 1-1.5 cores busy on this machine (since 2026-10-18): wait for at most 2 outside cores
GATE = {"max_cores": 2.0, "window_s": 30.0, "max_wait_s": 1800.0, "min_commit_gb": 8.0}
SPEND_SCALE, PV_SCALE = 100.0, 1e4  # $0.01M units -> $M; pv_int -> $
VALUE = dict(preset="0.5pct", rel_gap=1e-4)  # "value" runs: near-optimal PVs to compare (achieved gap reported)
FORCE = "--force" in sys.argv


def _arg(flag: str, default: str) -> str:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def disc(T: int) -> np.ndarray:
    """MBCM discount factors (tools/extract_cpo.py): 2% p.a. for years 1-30, 1.5% after; year 0 = 2026."""
    d = np.ones(T)
    for t in range(1, T):
        d[t] = d[t - 1] * (1.02 if t <= 30 else 1.015)
    return d


def base_raw(sc: str) -> dict:
    return json.loads((CPO / "instances" / f"CPO233_{sc}.json").read_text(encoding="utf-8"))


def cpo_plan(sc: str) -> Path:
    return CPO / "solutions" / f"CPO233_{sc}_cpo_reference.json"


def names(raw: dict) -> dict[str, str]:
    return raw.get("source", {}).get("project_names", {})


def write_instance(raw: dict) -> Path:
    INST.mkdir(parents=True, exist_ok=True)
    p = INST / f"{raw['name']}.json"
    p.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
    return p


def write_plan(starts: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"starts": starts}), encoding="utf-8")
    return path


def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8", newline="\n")
    return path


def write_csv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0]) if rows else []
    for r in rows[1:]:
        cols += [k for k in r if k not in cols]

    def fmt(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.6g}"
        s = str(v)
        return f'"{s}"' if ("," in s or '"' in s) else s
    lines = [",".join(cols)] + [",".join(fmt(r.get(c)) for c in cols) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


# ---- schedule metrics ------------------------------------------------------------------------------------------------

def metrics(inst: I.Instance, starts) -> dict:
    """Programme PV, cost PV and BCR of a schedule (the objective's own PV vector), plus its last build year."""
    st = inst.starts_from_dict(starts) if isinstance(starts, dict) else np.asarray(starts, dtype=np.int64)
    ev = evaluate(inst, st)
    if not ev["feasible"]:
        return {"feasible": False, "reason": ev.get("reason")}
    d = disc(inst.T)
    spend = np.asarray(ev["spend_units"], dtype=float) / SPEND_SCALE * 1e6  # $
    pv = ev["components"]["pv_int_sum"] / PV_SCALE
    cost = float((spend / d).sum())
    return {"feasible": True, "objective": ev["objective"], "pv": pv, "cost_pv": cost, "bcr": pv / cost,
            "last_build_year": int(inst.raw.get("base_year", 0)) + ev["last_build_year_index"],
            "holding_units": ev["components"]["backlog_units"] + ev["components"]["tier_units"]}


def cash_series(inst: I.Instance, starts) -> list[dict]:
    """Per year: funding envelope, expenditure and closing balance ($M real) for a feasible schedule."""
    st = inst.starts_from_dict(starts) if isinstance(starts, dict) else np.asarray(starts, dtype=np.int64)
    ev = evaluate(inst, st)
    base = int(inst.raw.get("base_year", 0))
    last = ev["last_build_year_index"]
    out = []
    for t in range(inst.T):
        f = float(inst.F[t]) / SPEND_SCALE if t <= last else 0.0
        out.append({"year": base + t, "funding_envelope_m": float(inst.F[t]) / SPEND_SCALE, "funding_drawn_m": f,
                    "expenditure_m": ev["spend_units"][t] / SPEND_SCALE,
                    "closing_balance_m": ev["closing_balance_units"][t] / SPEND_SCALE})
    return out


# ---- runs ------------------------------------------------------------------------------------------------------------

def run(group: str, label: str, inst_path: Path, time_limit: float = 120.0, warm: Path | None = None,
        **opts) -> dict:
    """One solve on a quiet machine, recorded (and cached) as RES/group/label.json."""
    out = RES / group / f"{label}.json"
    if out.exists() and not FORCE:
        return json.loads(out.read_text(encoding="utf-8"))
    waited = C.wait_quiet(**GATE)
    t_start = time.time()
    r = solver.solve(str(inst_path), None, time_limit, warm_start=None if warm is None else str(warm), **opts)
    load = C.load_tag(t_start, time.time(), mean_limit=GATE["max_cores"])
    inst = I.load(inst_path)
    gap = (r.objective - r.dual_bound) / abs(r.objective) if r.starts and np.isfinite(r.dual_bound) else None
    warm_status = None
    if warm is not None:
        txt = " | ".join(r.log)
        warm_status = ("rejected" if "infeasible for this instance, ignored" in txt
                       else "repaired" if "warm start repaired" in txt else "feasible")
    rec = {"group": group, "label": label, "instance": str(inst_path), "warm_start": None if warm is None else str(warm),
           "warm_status": warm_status, "options": opts, "time_limit": time_limit, "seconds": round(r.seconds, 2),
           "objective": r.objective, "dual_bound": r.dual_bound, "proven": r.proven, "certified_gap": gap,
           "time_to_first_feasible": round(r.trace[0][0], 2) if r.trace else None,
           "waited_s": round(waited, 1), "load": load, "trace": r.trace, "log": r.log, "starts": r.starts,
           "metrics": metrics(inst, r.starts) if r.starts else None, "t_start": t_start}
    write_json(out, rec)
    m = rec["metrics"] or {}
    print(f"[{group}] {label}: {r.seconds:.1f}s proven={r.proven} gap={gap if gap is None else f'{gap:.3%}'} "
          f"PV={m.get('pv', float('nan')) / 1e9:.3f}bn warm={warm_status} load={load and load['other_cores_mean']}",
          flush=True)
    return rec


def plan_of(rec: dict, path: Path) -> Path:
    return write_plan(rec["starts"], path)


# ---- E58: forced start windows ---------------------------------------------------------------------------------------

def _window_sets(raw: dict, plan: dict, rng: np.random.Generator) -> dict[str, dict]:
    """The window arms of E58 (start-year indices). 10 mid-size projects, chosen at random among those whose allowed
    starts leave room for a 5-year window 5-9 years before their base start."""
    sizes = np.array([sum(p["spend_S"]) for p in raw["projects"]], dtype=float)
    lo_q, hi_q = np.quantile(sizes, [0.2, 0.9])
    cand = [i for i, p in enumerate(raw["projects"])
            if lo_q <= sizes[i] <= hi_q and p["id"] != "P217" and plan[p["id"]] - 9 >= p["allowed_starts"][0]]
    pick = sorted(int(i) for i in rng.choice(cand, size=10, replace=False))
    ids = [raw["projects"][i]["id"] for i in pick]
    return {
        "P217_2030_2035": {"P217": (4, 9)},
        "P217_pin_2030": {"P217": (4, 4)},
        "ten_around_base": {p: (plan[p] - 2, plan[p] + 2) for p in ids},
        "ten_earlier": {p: (plan[p] - 9, plan[p] - 5) for p in ids},
        "ten_pinned_earlier": {p: (plan[p] - 7, plan[p] - 7) for p in ids},
    }


def cmd_windows() -> None:
    group = "windows"
    rows, cash_rows = [], []
    for sc in _arg("--scenarios", "cfads_P50,cfadsds_P50").split(","):
        raw = base_raw(sc)
        base = run(group, f"{sc}__base__value", CPO / "instances" / f"CPO233_{sc}.json", 300, cpo_plan(sc), **VALUE)
        base_plan = plan_of(base, RES / group / "plans" / f"{sc}__base.json")
        pv0 = base["metrics"]["pv"]
        nm = names(raw)
        sets = _window_sets(raw, base["starts"], np.random.default_rng(58))
        rows.append({"scenario": sc, "arm": "none", "windows": 0, "width_years": "", "pv_bn": pv0 / 1e9,
                     "pv_change_pct_of_base": 0.0, "bcr": base["metrics"]["bcr"], "value_gap": base["certified_gap"]})
        for t in cash_series(I.load(CPO / "instances" / f"CPO233_{sc}.json"), base["starts"]):
            cash_rows.append({"scenario": sc, "plan": "base", **t})
        for arm, win in sets.items():
            ok = {k: v for k, v in win.items()}
            wraw = TM.with_windows(raw, ok, name=f"CPO233_{sc}__win_{arm}")
            path = write_instance(wraw)
            val = run(group, f"{sc}__{arm}__value", path, 300, base_plan, **VALUE)
            w1 = run(group, f"{sc}__{arm}__warm1", path, 120, base_plan, preset="1pct")
            w05 = run(group, f"{sc}__{arm}__warm05", path, 120, base_plan, preset="0.5pct")
            c1 = run(group, f"{sc}__{arm}__cold1", path, 120, None, preset="1pct")
            widths = sorted({b - a + 1 for a, b in win.values()})
            moved = {p: (base["starts"][p], val["starts"][p]) for p in win} if val["starts"] else {}
            rows.append({
                "scenario": sc, "arm": arm, "windows": len(win), "width_years": "/".join(map(str, widths)),
                "pv_bn": val["metrics"]["pv"] / 1e9 if val["metrics"] else None,
                "pv_change_pct_of_base": 100 * (val["metrics"]["pv"] - pv0) / pv0 if val["metrics"] else None,
                "bcr": val["metrics"]["bcr"] if val["metrics"] else None, "value_gap": val["certified_gap"],
                "warm_status": w1["warm_status"], "warm_1pct_s": w1["seconds"] if w1["proven"] else None,
                "warm_0.5pct_s": w05["seconds"] if w05["proven"] else None,
                "cold_1pct_s": c1["seconds"] if c1["proven"] else None,
                "projects_moved": sum(1 for p, (a, b) in moved.items() if a != b),
                "other_projects_moved": sum(1 for p in base["starts"] if p not in win and val["starts"]
                                            and base["starts"][p] != val["starts"][p]),
            })
            write_json(REPORT / "windows" / f"{sc}__{arm}__windows.json",
                       {"scenario": sc, "arm": arm,
                        "windows": [{"id": p, "name": nm.get(p, p), "window": [2026 + a, 2026 + b],
                                     "base_start": 2026 + base["starts"][p],
                                     "new_start": 2026 + val["starts"][p] if val["starts"] else None}
                                    for p, (a, b) in win.items()]})
            if val["starts"]:
                for t in cash_series(I.load(path), val["starts"]):
                    cash_rows.append({"scenario": sc, "plan": arm, **t})
    write_csv(REPORT / "windows" / "windows_table.csv", rows)
    write_csv(REPORT / "windows" / "cash_by_year.csv", cash_rows)
    print(json.dumps(rows, indent=1))


COMMANDS = {"windows": cmd_windows}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        raise SystemExit(2)
    COMMANDS[sys.argv[1]]()
