"""E58+: practitioner capability demonstrations on the private CPO scenarios, with report-ready data.

    python benchmarks/experiments/practitioner.py <command> [--force] [--scenarios cfads_P50,cfadsds_P50]

Commands (one per capability; each runs its solves one at a time, waits for a quiet machine before each, load-tags
every run, caches each run's record, and writes tidy chart data):
    windows         E58 forced start windows
    sequencing      E59 sequencing rules (precedence) between projects
    shapes          E60 benefit profile shapes (linear / log / exp, equal real totals)
    growth          E61 benefit growth vs the discount rate: calendar vs opening anchors, growth-neutral ranking
    override        E62 the cost of forcing a large, low-BCR project early (P217)
    small_moves     E62b moving small projects by up to 10 years
    warm_envelopes  E63 warm starts across funding envelopes (CFADS <-> CFADS-DS, scaled)
    time_to_gap     E64 time to certified 1% / 0.5% / 0.1% and to a 1e-5 proof
    loop            E65 the optioneering loop: pin a third, re-optimise, tweak, final proof
    existing        E64 stored evidence: the time-to-gap numbers already measured, in one table

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


# ---- shared report helpers ------------------------------------------------------------------------------------------

def project_table(inst: I.Instance, starts: dict) -> list[dict]:
    """Per project at its start in `starts`: start/end years, real spend, PV, cost PV and BCR (PV / cost PV)."""
    d = disc(inst.T)
    base = int(inst.raw.get("base_year", 0))
    nm = names(inst.raw)
    out = []
    for p, pid in enumerate(inst.ids):
        s = int(starts[pid])
        sp = inst.spend[p].astype(float) / SPEND_SCALE * 1e6
        cost = float((sp / d[s:s + len(sp)]).sum())
        pv = inst.pv_of(p, s) / PV_SCALE
        out.append({"id": pid, "name": nm.get(pid, pid), "start": base + s, "end": base + s + len(sp) - 1,
                    "duration": len(sp), "spend_real_m": float(sp.sum()) / 1e6, "pv_m": pv / 1e6,
                    "cost_pv_m": cost / 1e6, "bcr": pv / cost if cost else None})
    return out


def bcr_by_year(inst: I.Instance, starts: dict) -> list[dict]:
    """Expenditure-weighted BCR by year: each project's BCR (at its start) weighted by its spend in that year."""
    rows = {r["id"]: r for r in project_table(inst, starts)}
    base = int(inst.raw.get("base_year", 0))
    num, den = np.zeros(inst.T), np.zeros(inst.T)
    for p, pid in enumerate(inst.ids):
        s = int(starts[pid])
        sp = inst.spend[p].astype(float) / SPEND_SCALE
        num[s:s + len(sp)] += sp * rows[pid]["bcr"]
        den[s:s + len(sp)] += sp
    return [{"year": base + t, "expenditure_m": den[t], "exp_weighted_bcr": num[t] / den[t] if den[t] else None}
            for t in range(inst.T) if den[t] > 0]


def benefit_flows(inst: I.Instance, starts: dict, streams: dict[int, np.ndarray] | None = None,
                  growth: float = 0.0, anchor: str = "calendar") -> list[dict]:
    """Per year: real and discounted benefits realised, discounted spend, and cumulative PV efficiency (cumulative
    discounted benefit / cumulative discounted spend). Streams default to each project's recovered flat stream."""
    from capopt.benefits import flat_annual
    d = disc(inst.T)
    base = int(inst.raw.get("base_year", 0))
    ben, cost = np.zeros(inst.T), np.zeros(inst.T)
    for p, pid in enumerate(inst.ids):
        s = int(starts[pid])
        sp = inst.spend[p].astype(float) / SPEND_SCALE * 1e6
        cost[s:s + len(sp)] += sp
        st = streams[p] if streams and p in streams else np.full(40, flat_annual(inst.raw, p, disc=d)[0])
        for k, b in enumerate(st):
            y = s + len(sp) + k
            if y < inst.T:
                ben[y] += b * ((1 + growth) ** (y if anchor == "calendar" else k) if growth else 1.0)
    cb, cc = np.cumsum(ben / d), np.cumsum(cost / d)
    return [{"year": base + t, "benefit_real_m": ben[t] / 1e6, "benefit_pv_m": ben[t] / d[t] / 1e6,
             "spend_pv_m": cost[t] / d[t] / 1e6, "cum_benefit_pv_bn": cb[t] / 1e9, "cum_spend_pv_bn": cc[t] / 1e9,
             "cum_pv_efficiency": cb[t] / cc[t] if cc[t] else None} for t in range(inst.T)]


def base_value(sc: str, group: str = "base") -> dict:
    """The scenario's near-optimal base plan (warm from the CPO plan, certified to 0.01%), shared by every command."""
    return run(group, f"{sc}__base__value", CPO / "instances" / f"CPO233_{sc}.json", 300, cpo_plan(sc), **VALUE)


def base_plan_path(sc: str) -> Path:
    rec = base_value(sc)
    return write_plan(rec["starts"], RES / "base" / "plans" / f"{sc}__base.json")


# ---- E59: sequencing rules -------------------------------------------------------------------------------------------

def cmd_sequencing() -> None:
    group = "sequencing"
    rows = []
    for sc in _arg("--scenarios", "cfadsds_P50,cfads_P50").split(","):
        raw = base_raw(sc)
        base = base_value(sc)
        plan, pv0 = base["starts"], base["metrics"]["pv"]
        bp = base_plan_path(sc)
        nm = names(raw)
        sizes = np.array([sum(p["spend_S"]) for p in raw["projects"]], dtype=float)
        lo_q, hi_q = np.quantile(sizes, [0.3, 0.9])
        mid = [p["id"] for i, p in enumerate(raw["projects"]) if lo_q <= sizes[i] <= hi_q and p["id"] != "P217"]
        rng = np.random.default_rng(59)
        # X starts at least 4 years before Y in the base plan, so every rule below binds
        pairs = [(x, y) for x in mid for y in mid if plan[y] - plan[x] >= 4]
        x, y = pairs[int(rng.integers(len(pairs)))]
        chain = sorted(rng.choice(mid, size=4, replace=False), key=lambda p: -plan[p])  # latest first: binds
        rules = {
            "B_not_before_A_starts": [TM.after_start(y, x)],
            "B_not_before_A_finishes": [TM.after_finish(raw, y, x)],
            "B_within_2y_of_A_finishing": [TM.after_finish(raw, y, x, within=2)],
            "B_exactly_when_A_finishes": [TM.after_finish(raw, y, x, within=0)],
            "B_no_later_than_A": [TM.not_after(x, y)],
            "B_3_to_6y_after_A_starts": [TM.lag_between(y, x, lo=3, hi=6)],
            "chain_of_4_finish_to_start": [TM.after_finish(raw, a, b) for a, b in zip(chain, chain[1:])],
        }
        for arm, rr in rules.items():
            sraw = TM.with_precedence(raw, rr, name=f"CPO233_{sc}__seq_{arm}")
            path = write_instance(sraw)
            val = run(group, f"{sc}__{arm}__value", path, 300, bp, **VALUE)
            w1 = run(group, f"{sc}__{arm}__warm1", path, 120, bp, preset="1pct")
            c1 = run(group, f"{sc}__{arm}__cold1", path, 120, None, preset="1pct")
            m = val["metrics"] or {}
            involved = sorted({r["a"] for r in rr} | {r["b"] for r in rr})
            rows.append({
                "scenario": sc, "rule": arm, "rules": len(rr), "feasible": bool(val["starts"]),
                "pv_bn": m.get("pv", 0) / 1e9 if m else None,
                "pv_change_pct_of_base": 100 * (m["pv"] - pv0) / pv0 if m else None,
                "pv_change_m": (m["pv"] - pv0) / 1e6 if m else None, "value_gap": val["certified_gap"],
                "warm_status": w1["warm_status"], "warm_1pct_s": w1["seconds"] if w1["proven"] else None,
                "cold_1pct_s": c1["seconds"] if c1["proven"] else None,
                "projects_in_rules": len(involved),
                "other_projects_moved": sum(1 for p in plan if p not in involved and val["starts"]
                                            and plan[p] != val["starts"][p]),
            })
            write_json(REPORT / "sequencing" / f"{sc}__{arm}.json", {
                "scenario": sc, "rule": arm, "rules": rr,
                "projects": [{"id": p, "name": nm.get(p, p), "duration": TM.duration(raw, p),
                              "base_start": 2026 + plan[p],
                              "new_start": 2026 + val["starts"][p] if val["starts"] else None} for p in involved]})
    write_csv(REPORT / "sequencing" / "sequencing_table.csv", rows)
    print(json.dumps(rows, indent=1))


# ---- E60: benefit profile shapes -------------------------------------------------------------------------------------

SHAPES = ("linear", "log", "exp")


def _shape_subset(raw: dict, plan: dict) -> list[int]:
    """40 projects: 35 at random plus five named ones (P217, the two highest-BCR mid-size projects, two small ones)."""
    from capopt.benefits import flat_annual
    rng = np.random.default_rng(60)
    n = len(raw["projects"])
    sizes = np.array([sum(p["spend_S"]) for p in raw["projects"]], dtype=float)
    bcr = np.array([40 * flat_annual(raw, p)[0] / (sizes[p] / SPEND_SCALE * 1e6) for p in range(n)])
    ids = [p["id"] for p in raw["projects"]]
    named = {ids.index("P217")}
    mids = [p for p in np.argsort(-bcr) if np.quantile(sizes, 0.5) <= sizes[p] <= np.quantile(sizes, 0.9)]
    named |= set(int(p) for p in mids[:2])
    named |= set(int(p) for p in np.argsort(sizes)[:2])
    rest = [p for p in range(n) if p not in named]
    return sorted(named | set(int(p) for p in rng.choice(rest, size=35, replace=False)))


def cmd_shapes() -> None:
    from capopt import benefits as B
    group = "shapes"
    rows, starts_rows = [], []
    curve_rows = []
    for k in range(41):  # chart data: the three accumulation curves and annual streams for a $1bn real total
        row = {"year_after_opening": k}
        for sh in SHAPES:
            row[f"cum_share_{sh}"] = float(B.cumulative_share(sh)[k])
            row[f"annual_m_per_bn_{sh}"] = float(B.shape_stream(1e9, sh)[k - 1]) / 1e6 if k else 0.0
        curve_rows.append(row)
    write_csv(REPORT / "shapes" / "benefit_curve_shapes.csv", curve_rows)
    for sc in _arg("--scenarios", "cfadsds_P50,cfads_P50").split(","):
        raw = base_raw(sc)
        base = base_value(sc)
        bp = base_plan_path(sc)
        sub = _shape_subset(raw, base["starts"])
        ids = [p["id"] for p in raw["projects"]]
        nm = names(raw)
        tot = {p: 40 * B.flat_annual(raw, p)[0] for p in range(len(ids))}
        for scope, members in (("subset40", sub), ("all", list(range(len(ids))))):
            for sh in SHAPES:
                streams = {p: B.shape_stream(tot[p], sh) for p in members}
                rraw = B.rebuild(raw, streams, name=f"CPO233_{sc}__shape_{sh}_{scope}")
                path = write_instance(rraw)
                inst = I.load(path)
                if sh == "linear":  # identical to the base instance (the supplied stream): reuse the base plan
                    val = base
                else:
                    val = run(group, f"{sc}__{sh}_{scope}__value", path, 300, bp, **VALUE)
                c1 = run(group, f"{sc}__{sh}_{scope}__cold1", path, 120, None, preset="1pct")
                st = val["starts"]
                mv = [st[ids[p]] - base["starts"][ids[p]] for p in members]
                sub_pv = sum(inst.pv_of(p, st[ids[p]]) for p in members) / PV_SCALE
                sub_pv_base_sched = sum(inst.pv_of(p, base["starts"][ids[p]]) for p in members) / PV_SCALE
                m = metrics(inst, st)
                mb = metrics(inst, base["starts"])
                w = np.array([tot[p] for p in members])
                rows.append({
                    "scenario": sc, "scope": scope, "shape": sh, "projects_reshaped": len(members),
                    "programme_pv_bn": m["pv"] / 1e9, "programme_pv_base_schedule_bn": mb["pv"] / 1e9,
                    "reshaped_pv_bn": sub_pv / 1e9, "reshaped_pv_base_schedule_bn": sub_pv_base_sched / 1e9,
                    "reoptimisation_gain_pct": 100 * (m["pv"] - mb["pv"]) / mb["pv"],
                    "reshaped_mean_start_shift_y": float(np.mean(mv)),
                    "reshaped_benefit_weighted_shift_y": float(np.dot(w, mv) / w.sum()),
                    "reshaped_moved": int(sum(1 for v in mv if v)), "reshaped_earlier": int(sum(1 for v in mv if v < 0)),
                    "reshaped_later": int(sum(1 for v in mv if v > 0)), "bcr": m["bcr"], "value_gap": val["certified_gap"],
                    "cold_1pct_s": c1["seconds"] if c1["proven"] else None,
                })
                if scope == "subset40":
                    for p in members:
                        starts_rows.append({"scenario": sc, "shape": sh, "id": ids[p], "name": nm.get(ids[p], ids[p]),
                                            "base_start": 2026 + base["starts"][ids[p]], "start": 2026 + st[ids[p]],
                                            "real_benefit_total_m": tot[p] / 1e6,
                                            "pv_m": inst.pv_of(p, st[ids[p]]) / PV_SCALE / 1e6})
    write_csv(REPORT / "shapes" / "shapes_table.csv", rows)
    write_csv(REPORT / "shapes" / "subset_starts_by_shape.csv", starts_rows)
    print(json.dumps(rows, indent=1))


# ---- E61: benefit growth vs the discount rate --------------------------------------------------------------------------

def _pv_under(raw: dict, starts: dict, growth: float, anchor: str) -> float:
    """Programme PV ($) of a schedule with every project's flat stream grown at `growth` from `anchor`."""
    from capopt import benefits as B
    g = B.rebuild(raw, {}, growth=growth, anchor=anchor) if growth else raw
    inst = I.from_dict(g)
    return sum(inst.pv_of(p, starts[pid]) for p, pid in enumerate(inst.ids)) / PV_SCALE


def _schedule_shape(raw: dict, starts: dict) -> dict:
    inst = I.from_dict(raw)
    st = np.array([starts[p] for p in inst.ids])
    w = np.array([float(s.sum()) for s in inst.spend])
    return {"spend_weighted_mean_start": 2026 + float(np.dot(w, st) / w.sum()), "median_start": 2026 + float(np.median(st)),
            "share_starting_after_2050_pct": 100 * float(np.mean(st + 2026 > 2050)),
            "last_build_year": 2026 + int(max(s + z for s, z in zip(st, inst.last_nz)))}


def cmd_growth() -> None:
    from capopt import benefits as B
    group = "growth"
    rows, hist = [], []
    for sc in _arg("--scenarios", "cfadsds_P50").split(","):
        raw = base_raw(sc)
        pure = json.loads((CPO / "instances" / f"CPO233_{sc}_purePV.json").read_text(encoding="utf-8"))
        base = base_value(sc)
        bp = base_plan_path(sc)
        plans = {("penalty on", "none", 0.0): base}
        purerec = run(group, f"{sc}__pure__none__value", CPO / "instances" / f"CPO233_{sc}_purePV.json", 300, None,
                      **VALUE)
        plans[("penalty off", "none", 0.0)] = purerec
        for g in (0.03, 0.05):
            for anchor in ("calendar", "opening"):
                for obj, src, warm in (("penalty on", raw, bp), ("penalty off", pure, None)):
                    tag = "pen" if obj == "penalty on" else "pure"
                    rr = B.rebuild(src, {}, growth=g, anchor=anchor, name=f"CPO233_{sc}__{tag}_g{int(g * 100)}_{anchor}")
                    path = write_instance(rr)
                    plans[(obj, anchor, g)] = run(group, f"{sc}__{tag}__g{int(g * 100)}_{anchor}__value", path, 300,
                                                  warm, **VALUE)
        for (obj, anchor, g), rec in plans.items():
            st = rec["starts"]
            row = {"scenario": sc, "objective": obj, "optimised_on": "real (no growth)" if not g else f"{anchor} {g:.0%}",
                   "growth": g, "anchor": anchor, "value_gap": rec["certified_gap"], **_schedule_shape(raw, st)}
            for gg in (0.0, 0.03, 0.05):
                for an in (("calendar", "opening") if gg else ("none",)):
                    row[f"pv_bn_{an}_{int(gg * 100)}" if gg else "pv_bn_real"] = _pv_under(raw, st, gg, an) / 1e9
            rows.append(row)
            inst = I.from_dict(raw)
            for y in range(inst.T):
                n = sum(1 for p in inst.ids if st[p] == y)
                if n:
                    hist.append({"scenario": sc, "objective": obj, "optimised_on": row["optimised_on"],
                                 "start_year": 2026 + y, "projects_starting": n})
    write_csv(REPORT / "growth" / "growth_table.csv", rows)
    write_csv(REPORT / "growth" / "start_year_histogram.csv", hist)
    print(json.dumps(rows, indent=1))


# ---- E62: the cost of overriding the optimum ------------------------------------------------------------------------

def _extract(sc_pkl: str, name: str) -> Path:
    """An instance for a CPO preset not extracted yet (tools/extract_cpo.build reads the CPO pickle, read-only)."""
    p = INST / f"{name}.json"
    if p.exists():
        return p
    sys.path.insert(0, str(ROOT / "tools"))
    import extract_cpo as X
    src = Path(os.environ["CAPOPT_CPO_PRESETS"])  # the CPO's legacy/scenario_sets/presets/GPS27 folder (read-only)
    inst, sol, chk = X.build(src / sc_pkl, name)
    write_instance(inst)
    write_json(INST / f"{name}_cpo_reference.json", sol)
    return p


OVERRIDE_CASES = {  # case -> (instance stem or extraction, forced start year of the large project P217)
    "cfadsds_P50_2028": ("cfadsds_P50", 2028),
    "cfadsds_P50_2029": ("cfadsds_P50", 2029),  # the CPO notebook's own forced-start rule on CFADS-DS
    "cfads_P50_2028": ("cfads_P50", 2028),
    "s2000_P50_2026": ("s2000_P50", 2026),      # the forced case of the CPO's own deck
    "s1500_P95_2026": ("s1500_P95", 2026),      # the stored pairs closest to the user's earlier figures (PV here,
    "s1500_P95high_2026": ("s1500_P95high", 2026),  # BCR there)
}
EXTRACT = {"s1500_P95": "objective bestP95Real_LIN40_base_s1500_pm0_TOT.pkl",
           "s1500_P95high": "objective bestP95Real_LIN40_high_s1500_pm0_TOT.pkl"}


def _override_inst(stem: str) -> tuple[Path, Path]:
    if stem in EXTRACT:
        p = _extract(EXTRACT[stem], f"CPO233_{stem}")
        return p, INST / f"CPO233_{stem}_cpo_reference.json"
    return CPO / "instances" / f"CPO233_{stem}.json", cpo_plan(stem)


def cmd_override() -> None:
    group = "override"
    rows, disp_rows, bcr_rows, eff_rows, gantt_rows, cash_rows = [], [], [], [], [], []
    cases = _arg("--cases", ",".join(OVERRIDE_CASES)).split(",")
    for case in cases:
        stem, year = OVERRIDE_CASES[case]
        path, ref = _override_inst(stem)
        raw = json.loads(path.read_text(encoding="utf-8"))
        inst = I.load(path)
        b = run(group, f"{stem}__base__value", path, 600, ref, **VALUE)
        bpath = write_plan(b["starts"], RES / group / "plans" / f"{stem}__base.json")
        fraw = TM.pin(raw, {"P217": year}, calendar=True, name=f"{raw['name']}__P217_{year}")
        fpath = write_instance(fraw)
        f = run(group, f"{case}__forced__value", fpath, 600, bpath, **VALUE)
        mb, mf = b["metrics"], f["metrics"]
        loss = mb["pv"] - mf["pv"]
        tb = {r["id"]: r for r in project_table(inst, b["starts"])}
        tf = {r["id"]: r for r in project_table(inst, f["starts"])}
        rows.append({
            "case": case, "scenario": stem, "forced_project": "P217", "forced_name": names(raw).get("P217"),
            "forced_start": year, "optimal_start": 2026 + b["starts"]["P217"],
            "p217_bcr_at_optimal": tb["P217"]["bcr"], "p217_real_cost_bn": tb["P217"]["spend_real_m"] / 1e3,
            "pv_best_bn": mb["pv"] / 1e9, "pv_forced_bn": mf["pv"] / 1e9, "pv_forgone_bn": loss / 1e9,
            "pv_forgone_pct_of_best": 100 * loss / mb["pv"], "best_more_valuable_pct_of_forced": 100 * loss / mf["pv"],
            "cost_pv_best_bn": mb["cost_pv"] / 1e9, "cost_pv_forced_bn": mf["cost_pv"] / 1e9,
            "bcr_best": mb["bcr"], "bcr_forced": mf["bcr"], "bcr_drop_pct": 100 * (mb["bcr"] - mf["bcr"]) / mb["bcr"],
            "gap_best": b["certified_gap"], "gap_forced": f["certified_gap"],
            "projects_displaced": sum(1 for p in inst.ids if p != "P217" and b["starts"][p] != f["starts"][p]),
            "projects_later": sum(1 for p in inst.ids if p != "P217" and f["starts"][p] > b["starts"][p]),
            "projects_earlier": sum(1 for p in inst.ids if p != "P217" and f["starts"][p] < b["starts"][p]),
            "p217_project_pv_change_bn": (tf["P217"]["pv_m"] - tb["P217"]["pv_m"]) / 1e3,
        })
        for pid in inst.ids:
            if b["starts"][pid] != f["starts"][pid]:
                disp_rows.append({"case": case, "id": pid, "name": tb[pid]["name"], "bcr": tb[pid]["bcr"],
                                  "real_cost_m": tb[pid]["spend_real_m"], "base_start": tb[pid]["start"],
                                  "forced_start": tf[pid]["start"], "years_moved": tf[pid]["start"] - tb[pid]["start"],
                                  "pv_change_m": tf[pid]["pv_m"] - tb[pid]["pv_m"]})
        for plan, rec in (("objective best", b), ("forced", f)):
            for r in bcr_by_year(inst, rec["starts"]):
                bcr_rows.append({"case": case, "plan": plan, **r})
            for r in benefit_flows(inst, rec["starts"]):
                eff_rows.append({"case": case, "plan": plan, **r})
            for r in cash_series(inst, rec["starts"]):
                cash_rows.append({"case": case, "plan": plan, **r})
        for pid in inst.ids:
            gantt_rows.append({"case": case, "id": pid, "name": tb[pid]["name"], "bcr": tb[pid]["bcr"],
                               "base_start": tb[pid]["start"], "base_end": tb[pid]["end"],
                               "forced_start": tf[pid]["start"], "forced_end": tf[pid]["end"]})
    out = REPORT / "override"
    write_csv(out / "large_project_summary.csv", rows)
    write_csv(out / "large_project_displaced.csv", disp_rows)
    write_csv(out / "large_project_bcr_by_year.csv", bcr_rows)
    write_csv(out / "large_project_pv_efficiency_by_year.csv", eff_rows)
    write_csv(out / "large_project_cash_by_year.csv", cash_rows)
    write_csv(out / "large_project_gantt.csv", gantt_rows)
    print(json.dumps(rows, indent=1))


def cmd_cpo_pairs() -> None:
    """E62 cross-check: the CPO's own stored result pairs (objective best vs the forced start of P217), read from its
    pickles (read-only): benefit PV, cost PV (MBCM discounting), BCR and P217's start, for every funding scenario."""
    import pickle
    src = Path(os.environ["CAPOPT_CPO_PRESETS"])
    rows = []
    for f in sorted(src.glob("*_TOT.pkl")):
        stem = f.name[:-8]
        if "Orchestrator" in stem:
            continue
        o = pickle.loads(f.read_bytes())
        sp = o["spend"]
        yrs = [c for c in sp.columns if isinstance(c, (int, np.integer))]
        tot = sp.loc[[i for i in sp.index if str(i).lower().startswith("project")], yrs].sum(axis=0).to_numpy(float)
        cost = float((tot / disc(len(yrs))).sum()) * 1e6
        sched = o["schedule"].set_index("Project")
        kind = "forced" if "objective best" not in stem else "objective best"
        key = stem.replace("CFADS less debt service - ", "cfadsds ").replace("CFADS ", "cfads ")
        for w in ("Waitemata start", "objective best"):
            key = key.replace(w, "")
        rows.append({"scenario_key": " ".join(key.split()), "plan": kind, "pv_bn": o["pv_total"] / 1e9,
                     "cost_pv_bn": cost / 1e9, "bcr": o["pv_total"] / cost,
                     "p217_start": int(sched.loc["project 217", "StartFY"]), "cpo_gap": o.get("gap")})
    by = {}
    for r in rows:
        by.setdefault(r["scenario_key"], {})[r["plan"]] = r
    out = []
    for k, d in sorted(by.items()):
        if "forced" in d and "objective best" in d:
            b, f = d["objective best"], d["forced"]
            out.append({"scenario_key": k, "pv_best_bn": b["pv_bn"], "pv_forced_bn": f["pv_bn"],
                        "pv_forgone_bn": b["pv_bn"] - f["pv_bn"],
                        "pv_forgone_pct_of_best": 100 * (b["pv_bn"] - f["pv_bn"]) / b["pv_bn"],
                        "best_more_valuable_pct_of_forced": 100 * (b["pv_bn"] - f["pv_bn"]) / f["pv_bn"],
                        "bcr_best": b["bcr"], "bcr_forced": f["bcr"], "p217_start_best": b["p217_start"],
                        "p217_start_forced": f["p217_start"]})
    write_csv(REPORT / "override" / "cpo_stored_pairs.csv", out)
    print(json.dumps(out, indent=1))


def cmd_small_moves() -> None:
    """E62b: move several small projects forward or back by up to 10 years; the rest re-optimised (warm) and, where
    the moved plan stays feasible, also held fixed."""
    group = "small_moves"
    rows, gantt = [], []
    for sc in _arg("--scenarios", "cfadsds_P50,cfads_P50").split(","):
        raw = base_raw(sc)
        inst = I.load(CPO / "instances" / f"CPO233_{sc}.json")
        base = base_value(sc)
        bp = base_plan_path(sc)
        pv0 = base["metrics"]["pv"]
        tb = {r["id"]: r for r in project_table(inst, base["starts"])}
        sizes = {p: tb[p]["spend_real_m"] for p in inst.ids}
        small = [p for p in sorted(inst.ids, key=lambda p: sizes[p])][:60]
        rng = np.random.default_rng(62)
        moves = {}
        for p in rng.permutation(small):
            a = inst.allowed[inst.ids.index(p)]
            s0 = base["starts"][p]
            for k in rng.permutation([-10, -7, -5, -3, 3, 5, 7, 10]):
                if a[0] <= s0 + k <= a[-1]:
                    moves[str(p)] = int(s0 + k)
                    break
            if len(moves) == 6:
                break
        arms = {"each": [{p: s} for p, s in moves.items()], "all6": [moves]}
        for arm, sets in arms.items():
            for mv in sets:
                tag = "all6" if arm == "all6" else f"{next(iter(mv))}"
                praw = TM.pin(raw, mv, name=f"CPO233_{sc}__small_{tag}")
                path = write_instance(praw)
                rec = run(group, f"{sc}__{tag}__value", path, 300, bp, **VALUE)
                fixed = dict(base["starts"], **mv)
                mfix = metrics(I.load(path), fixed)
                rows.append({
                    "scenario": sc, "moved": tag, "projects_moved": len(mv),
                    "years_moved": "/".join(str(s - base["starts"][p]) for p, s in mv.items()),
                    "moved_real_cost_m": sum(sizes[p] for p in mv),
                    "pv_change_reoptimised_m": (rec["metrics"]["pv"] - pv0) / 1e6,
                    "pv_change_reoptimised_pct": 100 * (rec["metrics"]["pv"] - pv0) / pv0,
                    "objective_change_pct": 100 * (rec["objective"] - base["objective"]) / abs(base["objective"]),
                    "rest_fixed_feasible": mfix["feasible"],
                    "pv_change_rest_fixed_pct": 100 * (mfix["pv"] - pv0) / pv0 if mfix["feasible"] else None,
                    "other_projects_moved": sum(1 for p in inst.ids if p not in mv and rec["starts"][p] != base["starts"][p]),
                    "value_gap": rec["certified_gap"], "seconds": rec["seconds"]})
                if arm == "all6":
                    for p, s in mv.items():
                        gantt.append({"scenario": sc, "id": p, "name": tb[p]["name"], "real_cost_m": sizes[p],
                                      "bcr": tb[p]["bcr"], "baseline_start": tb[p]["start"], "baseline_end": tb[p]["end"],
                                      "rescheduled_start": 2026 + s,
                                      "rescheduled_end": 2026 + s + tb[p]["duration"] - 1})
    write_csv(REPORT / "override" / "small_moves_table.csv", rows)
    write_csv(REPORT / "override" / "small_moves_gantt.csv", gantt)
    print(json.dumps(rows, indent=1))


# ---- E63: warm starts across funding envelopes ------------------------------------------------------------------------

def _scaled(sc: str, f: float) -> Path:
    raw = base_raw(sc)
    raw["funding_S"] = [int(round(v * f)) for v in raw["funding_S"]]
    raw["name"] = f"CPO233_{sc}__env_x{f:g}"
    return write_instance(raw)


def cmd_warm_envelopes() -> None:
    group = "warm_envelopes"
    rows = []
    plans = {sc: base_plan_path(sc) for sc in ("cfads_P50", "cfadsds_P50")}
    targets = {  # target -> (instance, [source plans])
        "cfadsds_P50": (CPO / "instances" / "CPO233_cfadsds_P50.json", ["cfads_P50"]),
        "cfads_P50": (CPO / "instances" / "CPO233_cfads_P50.json", ["cfadsds_P50"]),
        "cfadsds_P50_x0.9": (_scaled("cfadsds_P50", 0.9), ["cfadsds_P50"]),
        "cfadsds_P50_x1.1": (_scaled("cfadsds_P50", 1.1), ["cfadsds_P50"]),
    }
    for tgt, (path, sources) in targets.items():
        best = None
        for src in [None] + sources:
            warm = None if src is None else plans[src]
            mode = "cold" if src is None else f"warm from {src}"
            tag = "cold" if src is None else f"warm_{src}"
            r1 = run(group, f"{tgt}__{tag}__1pct", path, 300, warm, preset="1pct")
            r05 = run(group, f"{tgt}__{tag}__05pct", path, 300, warm, preset="0.5pct")
            rb = run(group, f"{tgt}__{tag}__budget10", path, 10, warm, preset="0.5pct", rel_gap=1e-4)
            first_obj = r1["trace"][0][1] if r1["trace"] else None
            rows.append({"target": tgt, "start": mode, "warm_status": r1["warm_status"],
                         "time_to_first_feasible_s": r1["time_to_first_feasible"], "first_objective": first_obj,
                         "time_to_1pct_s": r1["seconds"] if r1["proven"] else None,
                         "time_to_0.5pct_s": r05["seconds"] if r05["proven"] else None,
                         "gap_at_10s_pct": 100 * rb["certified_gap"] if rb["certified_gap"] is not None else None,
                         "pv_at_10s_bn": rb["metrics"]["pv"] / 1e9 if rb["metrics"] else None,
                         "objective_at_10s": rb["objective"]})
            best = rb["objective"] if best is None else min(best, rb["objective"])
        for r in rows:
            if r["target"] == tgt and r["first_objective"] is not None:
                r["first_feasible_vs_best_10s_pct"] = 100 * (r["first_objective"] - best) / abs(best)
    write_csv(REPORT / "warm_envelopes" / "warm_envelopes_table.csv", rows)
    env = []
    for tgt, (path, _) in targets.items():
        inst = I.load(path)
        for t in range(inst.T):
            env.append({"target": tgt, "year": 2026 + t, "funding_m": float(inst.F[t]) / SPEND_SCALE})
    write_csv(REPORT / "warm_envelopes" / "funding_envelopes.csv", env)
    print(json.dumps(rows, indent=1))


# ---- E64: the time-to-gap table ----------------------------------------------------------------------------------------

GAPS = {"1pct": dict(preset="1pct"), "0.5pct": dict(preset="0.5pct"), "0.1pct": dict(preset="0.5pct", rel_gap=1e-3),
        "proof_1e-5": dict(rel_gap=1e-5, lns_workers=16, lns_after_rampup=True)}


def cmd_time_to_gap() -> None:
    group = "time_to_gap"
    rows = []
    for sc in _arg("--scenarios", "cfads_P50,cfads_P95,cfadsds_P50,cfadsds_P95").split(","):
        path = CPO / "instances" / f"CPO233_{sc}.json"
        for mode in ("warm", "cold"):
            for lvl, opts in GAPS.items():
                tl = 900 if lvl.startswith("proof") else 300
                r = run(group, f"{sc}__{mode}__{lvl}", path, tl, cpo_plan(sc) if mode == "warm" else None, **opts)
                rows.append({"scenario": sc, "mode": mode, "target": lvl, "seconds": r["seconds"],
                             "certified": r["proven"], "achieved_gap_pct": 100 * r["certified_gap"]
                             if r["certified_gap"] is not None else None,
                             "time_limit": tl, "load_mean": (r["load"] or {}).get("other_cores_mean")})
    write_csv(REPORT / "time_to_gap" / "time_to_gap_table.csv", rows)
    print(json.dumps(rows, indent=1))


def cmd_existing() -> None:
    """E64 (stored evidence): the time-to-gap numbers already measured, gathered into one tidy table. Certified times
    come from the presets' final table (1%, 0.5%; 3 seeds, median and range) and the E37 0.1% log (1 seed); proof times
    to a 1e-5 gap from the official KPI campaign's milestones (median of 3 seeds, warm from the CPO plan)."""
    import glob
    import statistics
    rows = []
    res = CPO / "results"
    for arm, target in (("p1", "1pct"), ("p05", "0.5pct")):
        cells: dict = {}
        for f in glob.glob(str(res / "final_presets" / f"*__{arm}__*.json")):
            d = json.loads(Path(f).read_text(encoding="utf-8"))
            sc, _, mode, _ = Path(f).stem.split("__")
            cells.setdefault((sc, mode), []).append(d["seconds"] if d["proven"] else None)
        for (sc, mode), v in sorted(cells.items()):
            ok = [x for x in v if x is not None]
            rows.append({"scenario": sc, "mode": mode, "target": target, "source": "final presets table (E37-E42)",
                         "seeds": len(v), "certified_runs": len(ok),
                         "median_s": statistics.median([x if x is not None else 300.0 for x in v]),
                         "min_s": min(ok) if ok else None, "max_s": max(ok) if len(ok) == len(v) else None,
                         "kind": "certified gap"})
    for f in sorted(set(glob.glob(str(res / "e37_p01*" / "*__p01__*.json")))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        sc, _, mode, _ = Path(f).stem.split("__")
        rows.append({"scenario": sc, "mode": mode, "target": "0.1pct", "source": f"E37 0.1% log ({Path(f).parent.name})",
                     "seeds": 1, "certified_runs": int(bool(d["proven"])),
                     "median_s": d["seconds"] if d["proven"] else None, "time_limit": d.get("time_limit"),
                     "kind": "certified gap"})
    ms = json.loads((res / "milestones.json").read_text(encoding="utf-8"))
    for sc, m in ms.items():
        rows.append({"scenario": sc, "mode": "warm", "target": "proof_1e-5", "source": "official KPI milestones",
                     "seeds": m["seeds"], "median_s": m["proof"] if np.isfinite(m["proof"]) else None,
                     "time_limit": m["limit"], "copt_logged_s": m.get("copt"), "kind": "proven (gap 1e-5)"})
    write_csv(REPORT / "time_to_gap" / "existing_time_to_gap.csv", rows)
    print(f"{len(rows)} rows")


# ---- E65: the optioneering loop ------------------------------------------------------------------------------------

def cmd_loop() -> None:
    group = "loop"
    rows = []
    for sc in _arg("--scenarios", "cfadsds_P50").split(","):
        raw = base_raw(sc)
        inst = I.load(CPO / "instances" / f"CPO233_{sc}.json")
        base = base_value(sc)
        bp = base_plan_path(sc)
        plan = base["starts"]
        rng = np.random.default_rng(65)
        pick = [str(p) for p in rng.choice(inst.ids, size=inst.n // 3, replace=False)]
        wins = {}
        for i, p in enumerate(pick):  # the decision-maker's pins: keep, nudge, pull forward or push back
            a = inst.allowed[inst.ids.index(p)]
            s0 = plan[p]
            kind = i % 4
            lo, hi = {0: (s0 - 1, s0 + 1), 1: (s0, s0), 2: (s0 - 5, s0 - 3), 3: (s0 + 2, s0 + 4)}[kind]
            lo, hi = max(lo, int(a[0])), min(hi, int(a[-1]))
            if lo > hi:
                lo = hi = s0
            wins[p] = (lo, hi)
        steps = []
        r1raw = TM.with_windows(raw, wins, name=f"CPO233_{sc}__loop_step1")
        p1 = write_instance(r1raw)
        s1w = run(group, f"{sc}__step1_pins__warm05", p1, 300, bp, preset="0.5pct")
        s1c = run(group, f"{sc}__step1_pins__cold05", p1, 300, None, preset="0.5pct")
        steps.append(("1 pin a third of the projects (warm from the base plan)", s1w, len(wins)))
        steps.append(("1' the same, cold (for contrast)", s1c, len(wins)))
        # step 2: one more tweak - bring the largest unpinned late project forward by 5 years
        free = [p for p in inst.ids if p not in wins and plan[p] >= 10]
        tweak = max(free, key=lambda p: float(inst.spend[inst.ids.index(p)].sum()))
        a = inst.allowed[inst.ids.index(tweak)]
        tw = max(int(a[0]), plan[tweak] - 5)
        wins2 = dict(wins, **{tweak: (tw, tw)})
        p2 = write_instance(TM.with_windows(raw, wins2, name=f"CPO233_{sc}__loop_step2"))
        plan1 = write_plan(s1w["starts"], RES / group / "plans" / f"{sc}__step1.json")
        s2 = run(group, f"{sc}__step2_tweak__warm05", p2, 300, plan1, preset="0.5pct")
        steps.append((f"2 one more pin ({tweak} five years earlier; warm from step 1)", s2, len(wins2)))
        plan2 = write_plan(s2["starts"], RES / group / "plans" / f"{sc}__step2.json")
        s3 = run(group, f"{sc}__step3_final__proof", p2, 900, plan2, rel_gap=1e-5, lns_workers=16,
                 lns_after_rampup=True)
        steps.append(("3 final run to optimality (gap 1e-5; warm from step 2)", s3, len(wins2)))
        pv0 = base["metrics"]["pv"]
        for name, rec, nw in steps:
            m = rec["metrics"] or {}
            rows.append({"scenario": sc, "step": name, "windows": nw, "seconds": rec["seconds"],
                         "certified": rec["proven"], "certified_gap_pct": 100 * rec["certified_gap"]
                         if rec["certified_gap"] is not None else None, "warm_status": rec["warm_status"],
                         "time_to_first_feasible_s": rec["time_to_first_feasible"],
                         "pv_bn": m.get("pv", 0) / 1e9, "pv_vs_unpinned_optimum_pct": 100 * (m["pv"] - pv0) / pv0
                         if m else None})
        write_json(REPORT / "loop" / f"{sc}__pins.json",
                   {"tweak": tweak, "windows": {p: [2026 + a, 2026 + b] for p, (a, b) in wins2.items()},
                    "names": {p: names(raw).get(p, p) for p in wins2}})
    write_csv(REPORT / "loop" / "loop_table.csv", rows)
    print(json.dumps(rows, indent=1))


COMMANDS = {"windows": cmd_windows, "sequencing": cmd_sequencing, "shapes": cmd_shapes, "growth": cmd_growth,
            "override": cmd_override, "cpo_pairs": cmd_cpo_pairs, "small_moves": cmd_small_moves, "warm_envelopes": cmd_warm_envelopes,
            "time_to_gap": cmd_time_to_gap, "loop": cmd_loop, "existing": cmd_existing}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        raise SystemExit(2)
    COMMANDS[sys.argv[1]]()
