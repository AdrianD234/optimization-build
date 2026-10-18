"""E44+: distribution layers (capopt.layers) on the private CPO scenarios, timed with the certified-gap presets.

    python benchmarks/experiments/cpo_layers.py pvstar <scenario> [...]           PV* from the 0.5pct preset (cached)
    python benchmarks/experiments/cpo_layers.py build <scenario> <variant> [...]  write a layered instance
    python benchmarks/experiments/cpo_layers.py spec <name> --variants v1,v2 --scenarios s1,s2 [--presets 1pct,0.5pct]
           [--arms {"triage": {"warm_triage": true}}]
           [--modes warm,cold] [--seeds 0,1] [--time-limit 120]
    python benchmarks/experiments/cpo_layers.py summary <name> [<name> ...]     times + layer metrics per cell
    python benchmarks/experiments/cpo_layers.py frontier <name> --scenario sc --variants v1,v2,... [--preset 1pct]
           [--first cold|warm] [--options {...}]   a chained sweep: each point warm-starts from the previous one

Variants ("+" joins layers; "base" = no layer):
    dim-<D>-f<NN>      dimension scoring: objective PV = w_p x PV_D (CPO notebook weights), Total PV >= NN% of PV*
    cls-x<P>[-opts]    activity class, soft: first four 3-year windows, weights half per decade, free 20% or the lumpiness
                       floor (largest single-year spend of a member, capped at half the target), slopes 1:4:12; a 1%
                       shortfall in window 1 costs P% of PV* (of the score at the PV* plan when combined with dim-)
    clshard            the CPO baseline: hard +-10% of realised window spend + $25M, first four windows, min share 2%
    reg-<cost|blend|pop>-x<P>[-opts]  regional: cumulative checkpoints at years 10/20/30, weights 4:2:1, free 20% or the lumpiness
                       floor; target share = cost share, or 50/50 population and cost share (National excluded)

Options (-tolNN, -lumpwin, -lumpnone, -carveN, -endo): see layer_opts. The metrics measure every plan against the same
default rows (20% free, single-year lumpiness floor, nothing carved), whatever the variant optimised.

Warm runs start from the CPO's stored (Total) plan: the realistic "turn a layer on" workflow. Real data and results
stay under private/ (gitignored); only aggregates go into the docs.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from capopt import instance as I  # noqa: E402
from capopt import layers as LY  # noqa: E402
from capopt.evaluate import evaluate  # noqa: E402

CPO = Path(os.environ.get("CAPOPT_CPO", ROOT / "private" / "cpo"))
ATTR = CPO / "attributes"
OUT = CPO / "instances" / "layers"
# OneDrive's sync holds about 1 core busy on this machine since 2026-10-18, so the presets campaigns' 1-core gate never
# opens: wait for at most 2 outside cores, don't rerun (load tags still record the outside load per run)
QUIET = {"quiet": {"max_cores": 2.0, "window_s": 30.0, "max_wait_s": 1800.0, "min_commit_gb": 8.0},
         "rerun_contended": 0}
D5 = ["HSP", "INC", "ENV", "ECO", "RES"]
CLASS_SPANS = [(0, 2), (3, 5), (6, 8), (9, 11)]
# the user's time decay: 4:2:1 over the 10/20/30-year checkpoints, i.e. half per decade (about 7%/yr), applied to the
# 3-year class windows as 0.5^(3w/10)
CLASS_WEIGHTS = [0.5 ** (3 * w / 10) for w in range(4)]
REG_SPANS = [(0, 9), (0, 19), (0, 29)]
REG_WEIGHTS = [4.0, 2.0, 1.0]
# E51 (MULTI_OBJECTIVE §3): 5-yearly cumulative checkpoints, same decay (half per decade), so spend cannot bunch just
# before years 10 / 20 / 30
REG_SPANS5 = [(0, c - 1) for c in (5, 10, 15, 20, 25, 30)]
REG_WEIGHTS5 = [0.5 ** ((c - 5) / 10) for c in (5, 10, 15, 20, 25, 30)]
NORM_FLOOR = 2_500.0  # $25M in $0.01M units
# the chosen defaults (EXPERIMENTS.md "Distribution layers: recommended defaults"): class at x = 0.01%; region on the
# user's early-horizon population profile (E56): 100% population share at cumulative checkpoints 5 and 10 only, +-10%
# free, x = 0.01% (as fast as the blend, cheaper than cost share, near-exact population fairness in the first decade);
# the blend and cost-share bases stay variants; dimension scoring is opt-in per run (dim-INC-fNN, f from the frontier)
DEFAULT_VARIANT = "cls-x0.01+reg-pop-x0.01-at5_10-tol10"


def _arg(flag: str, default: str) -> str:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def base_raw(sc: str) -> dict:
    return json.loads((CPO / "instances" / f"CPO233_{sc}.json").read_text(encoding="utf-8"))


def attrs(sc: str) -> dict:
    return json.loads((ATTR / f"CPO233_{sc}.json").read_text(encoding="utf-8"))


def ref_plan(sc: str) -> dict:
    return json.loads((CPO / "solutions" / f"CPO233_{sc}_cpo_reference.json").read_text(encoding="utf-8"))


def pv_of(raw: dict, starts: dict, vec: str = "pv_int", dim: str | None = None, A: dict | None = None) -> float:
    tot = 0
    for i, p in enumerate(raw["projects"]):
        j = p["allowed_starts"].index(int(starts[p["id"]]))
        tot += A["projects"][i]["pv_dim"][dim][j] if dim else p[vec][j]
    return float(tot)


def pvstar(sc: str) -> dict:
    """PV* = Total PV of the 0.5pct preset's plan for the base scenario (warm from the CPO plan). The preset certifies
    the objective (carry - 1e-4 PV) within 0.5%, so PV* is within about 0.4-0.5% of the best achievable PV."""
    cache = ATTR / "pvstar.json"
    data = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    if sc in data:
        return data[sc]
    from capopt import solver
    ref = CPO / "solutions" / f"CPO233_{sc}_cpo_reference.json"
    r = solver.solve(str(CPO / "instances" / f"CPO233_{sc}.json"), preset="0.5pct", warm_start=str(ref))
    raw = base_raw(sc)
    data[sc] = {"pv_int": pv_of(raw, r.starts), "objective": r.objective, "dual_bound": r.dual_bound,
                "proven": r.proven, "seconds": r.seconds, "starts": r.starts}
    cache.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data[sc]


def mega(raw: dict, n: int) -> set[int]:
    """The n most expensive projects (the CPO's carve_top_n)."""
    cost = [sum(p["spend_S"]) for p in raw["projects"]]
    return set(sorted(range(len(cost)), key=lambda i: -cost[i])[:n])


def groups_of(raw: dict, A: dict, key: str, drop: tuple[str, ...] = (), exclude: set[int] = frozenset()
              ) -> dict[str, list[int]]:
    g: dict[str, list[int]] = {}
    for i, p in enumerate(A["projects"]):
        if p[key] not in drop and i not in exclude:
            g.setdefault(p[key], []).append(i)
    return g


def shares_of_total(raw: dict, groups: dict[str, list[int]]) -> dict[str, float]:
    """Each group's cost as a share of the whole programme's cost: with projects carved out (or National dropped) the
    shares sum to the covered fraction, so targets scale the funding base down to the covered spend."""
    total = float(sum(sum(p["spend_S"]) for p in raw["projects"]))
    return {k: sum(sum(raw["projects"][p]["spend_S"]) for p in mem) / total for k, mem in groups.items()}


def layer_opts(bits: list[str], tol: float) -> tuple[dict, set]:
    """Variant options after the price: -tolNN (free tolerance %), -lumpwin (the approved floor: the largest spend one
    member can put in the span, uncapped), -lumpnone, -carveN (the N largest projects leave the targets), -endo (target
    = share of what the covered projects actually spend, instead of the fixed entitlement). Region only: -cp5
    (5-yearly checkpoints), -wcX (worst-case term: the worst region's normalised shortfall, 1% of it costs X% of PV*),
    -atY[_Y2] (checkpoints at those years only, e.g. at5_10: the user's early-horizon population profile, E56)."""
    kw: dict = {"tol": tol}
    carve: set = set()
    for b in bits:
        if b == "cp5" or b.startswith("wc") or b.startswith("at"):
            continue  # region-only options, read by build_variant
        if b.startswith("tol"):
            kw["tol"] = float(b[3:]) / 100.0
        elif b == "lumpwin":
            kw.update(lumpiness="window", lump_cap=float("inf"))
        elif b == "lumpnone":
            kw["lumpiness"] = None
        elif b == "endo":
            kw["endogenous"] = True
        elif b.startswith("carve"):
            carve = {int(b[5:])}
        else:
            raise ValueError(f"unknown layer option {b!r}")
    return kw, carve


def region_shares(raw: dict, A: dict, basis: str, exclude: set[int] = frozenset()
                  ) -> tuple[dict[str, list[int]], dict[str, float]]:
    """Regional target shares of the funding base: the governed regions' (National excluded, carved projects excluded)
    share of programme cost (pool fraction), split by cost share or by a 50/50 blend of population and cost share."""
    allg = groups_of(raw, A, "region", exclude=exclude)
    cs_all = shares_of_total(raw, allg)
    gov = {k: v for k, v in allg.items() if k != "National"}
    pool = sum(cs_all[k] for k in gov)
    cs = {k: cs_all[k] / pool for k in gov}
    if basis == "cost":
        sh = cs
    elif basis == "pop":  # E56: the user's population-only profile (early checkpoints only, see -atY)
        pop = A["population_thousands"]
        tp = sum(pop[k] for k in gov)
        sh = {k: pop[k] / tp for k in gov}
    else:
        pop = A["population_thousands"]
        tp = sum(pop[k] for k in gov)
        sh = {k: 0.5 * pop[k] / tp + 0.5 * cs[k] for k in gov}
    return gov, {k: pool * v for k, v in sh.items()}


def dim_weights(A: dict, d: str) -> np.ndarray:
    pv0 = np.array([p["pv0_dim"][d] for p in A["projects"]])
    oth = np.array([np.mean([p["pv0_dim"][e] for e in D5 if e != d]) for p in A["projects"]])
    return LY.dimension_weights(pv0, oth)


def weighted_dim(raw: dict, starts: dict, A: dict, d: str, w: np.ndarray) -> float:
    """The dimension score (the dimension-scoring objective's PV): sum_p w_p PV_d(p, start_p)."""
    return float(sum(w[i] * A["projects"][i]["pv_dim"][d][p["allowed_starts"].index(int(starts[p["id"]]))]
                     for i, p in enumerate(raw["projects"])))


def build_variant(sc: str, variant: str) -> tuple[dict, Path]:
    raw = base_raw(sc)
    A = attrs(sc)
    ps = pvstar(sc)
    pv_star_obj = raw["weights"]["pv_weight"] * ps["pv_int"]
    for part in variant.split("+"):  # prices are a share of the PV the objective maximises (the score under dim-)
        if part.startswith("dim-"):
            d = part.split("-")[1]
            pv_star_obj = raw["weights"]["pv_weight"] * weighted_dim(raw, ps["starts"], A, d, dim_weights(A, d))
    layers: dict = {}
    pv_obj = None
    for part in [] if variant == "base" else variant.split("+"):
        bits = part.split("-")
        if bits[0] == "dim":
            d, f = bits[1], float(bits[2][1:]) / 100.0
            w = dim_weights(A, d)
            pv_obj = [[int(round(w[i] * v)) for v in p["pv_dim"][d]] for i, p in enumerate(A["projects"])]
            layers["pv_floor"] = {"pv": [p["pv_int"] for p in raw["projects"]], "min": int(math.ceil(f * ps["pv_int"]))}
        elif bits[0] == "cls":
            kw, carve = layer_opts(bits[2:], 0.20)
            g = groups_of(raw, A, "activity_class", exclude=mega(raw, next(iter(carve))) if carve else set())
            layers.setdefault("shortfall", []).extend(LY.allocation_rows(
                raw, g, shares_of_total(raw, g), CLASS_SPANS, CLASS_WEIGHTS, float(bits[1][1:]), pv_star_obj,
                norm_floor=NORM_FLOOR, prefix="class:", **kw))
        elif bits[0] == "clshard":
            g = groups_of(raw, A, "activity_class")
            layers.setdefault("bands", []).extend(LY.hard_bands(g, LY.cost_shares(raw, g), CLASS_SPANS, tol=0.10,
                                                                floor=NORM_FLOOR, min_share=0.02, prefix="class:"))
        elif bits[0] == "reg":
            kw, carve = layer_opts(bits[3:], 0.20)
            g, sh = region_shares(raw, A, bits[1], exclude=mega(raw, next(iter(carve))) if carve else set())
            spans, weights = (REG_SPANS5, REG_WEIGHTS5) if "cp5" in bits else (REG_SPANS, REG_WEIGHTS)
            at = [b for b in bits[3:] if b.startswith("at")]
            if at:  # E56: cumulative checkpoints at the given years only (e.g. at5_10), decay half per decade
                ys = [int(y) for y in at[0][2:].split("_")]
                spans, weights = [(0, y - 1) for y in ys], [0.5 ** ((y - ys[0]) / 10) for y in ys]
            rows = LY.allocation_rows(raw, g, sh, spans, weights, float(bits[2][1:]), pv_star_obj,
                                      norm_floor=NORM_FLOOR, prefix="region:", **kw)
            layers.setdefault("shortfall", []).extend(rows)
            for b in bits[3:]:
                if b.startswith("wc"):  # E51: worst-case term over the same rows
                    layers.setdefault("worst", []).append(LY.worst_case_block(rows, float(b[2:]), pv_star_obj,
                                                                              "region-worst"))
        else:
            raise ValueError(f"unknown layer {part!r}")
    new = LY.with_layers(raw, layers, f"CPO233_{sc}__{variant}", pv_obj) if (layers or pv_obj) else dict(raw)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"CPO233_{sc}__{variant}.json"
    path.write_text(json.dumps(new, separators=(",", ":")), encoding="utf-8")
    # reference file: the CPO's stored plan scored under the layered objective (the warm start of "warm" runs)
    inst = I.from_dict(new)
    plan = ref_plan(sc)
    ev = evaluate(inst, inst.starts_from_dict(plan["starts"]))
    refp = OUT / f"CPO233_{sc}__{variant}_ref.json"
    refp.write_text(json.dumps({"starts": plan["starts"], "source": "CPO stored Total plan",
                                "evaluator_objective": ev["objective"] if ev["feasible"] else plan["evaluator_objective"],
                                "feasible_under_layers": ev["feasible"]}), encoding="utf-8")
    return new, path


def build_polish(sc: str, variant: str, starts: dict, delta: float = 0.0) -> tuple[Path, Path]:
    """Lexicographic polish (stage 2) of a dimension-scoring plan: maximise Total PV (the base objective, carry kept)
    subject to dimension score >= (1 - delta) x the stage-1 plan's score. Other layers of the variant are kept. Returns
    the instance path and a warm-start file holding the stage-1 plan (feasible by construction at delta >= 0)."""
    raw = base_raw(sc)
    A = attrs(sc)
    d = next(b.split("-")[1] for b in variant.split("+") if b.startswith("dim-"))
    w = dim_weights(A, d)
    score = weighted_dim(raw, starts, A, d, w)
    layered = json.loads((OUT / f"CPO233_{sc}__{variant}.json").read_text(encoding="utf-8"))
    lay = {k: v for k, v in layered.get("layers", {}).items() if k != "pv_floor"}
    lay["pv_floor"] = {"pv": [[int(round(w[i] * v)) for v in A["projects"][i]["pv_dim"][d]]
                              for i in range(len(raw["projects"]))],
                       "min": int(math.floor((1 - delta) * score)) - len(raw["projects"])}  # rounding slack
    new = LY.with_layers(raw, lay, f"CPO233_{sc}__{variant}__polish")
    path = OUT / f"CPO233_{sc}__{variant}__polish.json"
    path.write_text(json.dumps(new, separators=(",", ":")), encoding="utf-8")
    ev = evaluate(I.from_dict(new), I.from_dict(new).starts_from_dict(starts))
    warm = path.with_name(path.stem + "_ref.json")
    warm.write_text(json.dumps({"starts": starts, "source": "stage-1 plan", "evaluator_objective": ev["objective"],
                                "feasible_under_layers": ev["feasible"]}), encoding="utf-8")
    return path, warm


# ------------------------------------------------------------------------------------------------------------ metrics

def metrics(sc: str, starts: dict) -> dict:
    """Layer-independent metrics of a plan: Total PV vs PV*, PV of every dimension, class shortfall per window and
    regional shortfall per checkpoint (beyond each layer's free zone, as % of target), and hard-band violations."""
    raw = base_raw(sc)
    A = attrs(sc)
    ps = pvstar(sc)
    inst = I.from_dict(raw)
    st = inst.starts_from_dict(starts)
    out = {"pv_retained": pv_of(raw, starts) / ps["pv_int"]}
    for d in D5:
        out[f"pv_{d}"] = pv_of(raw, starts, dim=d, A=A) / pv_of(raw, ps["starts"], dim=d, A=A)
        w = dim_weights(A, d)
        out[f"score_{d}"] = weighted_dim(raw, starts, A, d, w) / weighted_dim(raw, ps["starts"], A, d, w)
    spend = np.zeros((inst.n, inst.T))
    for p, s in enumerate(st):
        spend[p, s:s + len(inst.spend[p])] = inst.spend[p]

    def short(groups, shares, spans, weights, tol):
        """Deficits beyond the free zone, from the same rows the layer builds (price irrelevant here)."""
        rows = LY.allocation_rows(raw, groups, shares, spans, weights, 1.0, 1.0, tol=tol, norm_floor=NORM_FLOOR)
        res = [{"worst_pct": 0.0, "total_M": 0.0} for _ in spans]
        for r in rows:
            w = int(r["label"].rsplit("|w", 1)[1])
            a, b = r["years"]
            d = max(0.0, r["target"] - r["free"] - spend[r["members"], a:b + 1].sum())
            res[w]["worst_pct"] = max(res[w]["worst_pct"], 100 * d / r["norm"])
            res[w]["total_M"] += d / 100
        return res
    cg = groups_of(raw, A, "activity_class")
    csh = LY.cost_shares(raw, cg)
    out["class"] = short(cg, csh, CLASS_SPANS, CLASS_WEIGHTS, 0.20)
    viol = 0
    for k, mem in cg.items():
        if csh[k] < 0.02:
            continue
        for a, b in CLASS_SPANS:
            sp, tot = spend[mem, a:b + 1].sum(), spend[:, a:b + 1].sum()
            viol += abs(sp - csh[k] * tot) > 0.10 * csh[k] * tot + NORM_FLOOR + 1
    out["class_band_violations"] = int(viol)
    for basis in ("cost", "blend"):
        g, sh = region_shares(raw, A, basis)
        out[f"region_{basis}"] = short(g, sh, REG_SPANS, REG_WEIGHTS, 0.20)
        out[f"region_{basis}_5y"] = short(g, sh, REG_SPANS5, REG_WEIGHTS5, 0.20)  # bunching check (E51)
    g, sh = region_shares(raw, A, "pop")  # E56 yardstick: population share at years 5 and 10, 20% free
    out["region_pop"] = short(g, sh, [(0, 4), (0, 9)], [1.0, 0.5 ** 0.5], 0.20)
    return out


# ------------------------------------------------------------------------------------------------------- campaigns

def spec(name: str) -> Path:
    variants = _arg("--variants", "base").split(",")
    scenarios = _arg("--scenarios", "s2000_P50,cfadsds_P50").split(",")
    presets = _arg("--presets", "1pct,0.5pct").split(",")
    modes = _arg("--modes", "warm,cold").split(",")
    seeds = [int(s) for s in _arg("--seeds", "0").split(",")]
    tl = float(_arg("--time-limit", "120"))
    arms = json.loads(_arg("--arms", '{"": {}}'))  # arm name -> extra solve options; labels carry "<variant>@<arm>"
    paths = {(sc, v): build_variant(sc, v)[1] for sc in scenarios for v in variants}
    runs = []
    for seed in seeds:
        for sc in scenarios:
            for v in variants:
                for mode in modes:
                    for pr, (arm, extra) in [(p, a) for p in presets for a in arms.items()]:
                        path = paths[(sc, v)]
                        ref = path.with_name(path.stem + "_ref.json")
                        label = f"{sc}__{v}{'@' + arm if arm else ''}__{pr}__{mode}__seed{seed}"
                        if (CPO / "results" / name / f"{label}.json").exists():
                            continue  # done in an earlier launch of this campaign
                        o = {"preset": pr, "seed": seed, **extra}
                        if mode == "warm":
                            o["warm_start"] = str(ref)
                        runs.append({"instance": str(path), "reference": str(ref), "time_limit": tl, "options": o,
                                     "label": label})
    (CPO / "campaigns").mkdir(exist_ok=True)
    out = CPO / "campaigns" / f"{name}.spec.json"
    if "--append" in sys.argv and out.exists():  # several variant x arm blocks in one campaign
        runs = json.loads(out.read_text(encoding="utf-8"))["runs"] + runs
    out.write_text(json.dumps({"name": name, "time_limit": tl, "runner": "capopt.campaign:reference_runner",
                               "out_dir": str(CPO / "results" / name), "runs": runs, **QUIET}, indent=1),
                   encoding="utf-8")
    print(f"{out}: {len(runs)} runs")
    return out


def frontier_spec(name: str) -> Path:
    """Stage C: a chained sweep. Runs go in the order given and each warm-starts from the previous run's result (its
    record carries "starts"); the first starts from `--first` (cold, or warm from the CPO plan). Sweeping in the
    direction that loosens the problem (a lower PV floor, a higher price) makes the previous plan a free start
    (MULTI_OBJECTIVE_LAYERS §4)."""
    variants = _arg("--variants", "").split(",")
    sc = _arg("--scenario", "cfadsds_P50")
    pr = _arg("--preset", "1pct")
    tl = float(_arg("--time-limit", "180"))
    first = _arg("--first", "cold")
    extra = json.loads(_arg("--options", "{}"))
    out_dir = CPO / "results" / name
    runs, prev = [], None
    for v in variants:
        path = build_variant(sc, v)[1]
        ref = path.with_name(path.stem + "_ref.json")
        label = f"{sc}__{v}__{pr}__chain__seed0"
        o = {"preset": pr, "seed": 0, **extra}
        if prev is not None:
            o["warm_start"] = str(out_dir / f"{prev}.json")
        elif first == "warm":
            o["warm_start"] = str(ref)
        runs.append({"instance": str(path), "reference": str(ref), "time_limit": tl, "options": o, "label": label})
        prev = label
    out = CPO / "campaigns" / f"{name}.spec.json"
    out.write_text(json.dumps({"name": name, "time_limit": tl, "runner": "capopt.campaign:reference_runner",
                               "out_dir": str(out_dir), "runs": runs, **QUIET}, indent=1), encoding="utf-8")
    print(f"{out}: {len(runs)} runs")
    return out


def _records(names: list[str]):
    for name in names:
        for f in sorted((CPO / "results" / name).glob("*__seed*.json")):
            rec = json.loads(f.read_text(encoding="utf-8"))
            sc, v, pr, mode, seed = f.stem.split("__")
            yield (sc, v, pr, mode, seed), rec


def summary(names: list[str]) -> None:
    cells: dict[tuple, list[dict]] = {}
    for (sc, v, pr, mode, _), rec in _records(names):
        cells.setdefault((sc, v, mode, pr), []).append(rec)
    print(f"{'scenario':12s} {'variant':44s} {'mode':4s} {'preset':6s} {'n':>2s} {'med s':>7s} {'range':>13s} "
          f"{'PV ret':>7s} {'dimPV / score':>23s} {'cls w1/w2 %':>12s} {'bandv':>5s} {'reg cost c1/c2 %':>17s} "
          f"{'reg blend c1/c2 %':>17s} cont")
    for (sc, v, mode, pr), recs in sorted(cells.items()):
        ts = [r["seconds"] if r["proven"] else r["time_limit"] for r in recs]
        bad = sum(not r["proven"] for r in recs)
        cont = sum(bool(r.get("load") and r["load"]["contended"]) for r in recs)
        m = [metrics(sc, r["starts"]) for r in recs if r.get("starts")]
        med = lambda xs: statistics.median(xs) if xs else float("nan")  # noqa: E731
        dims = [d for d in D5 if f"dim-{d}" in v]
        dim = (f"{dims[0]} {med([x[f'pv_{dims[0]}'] for x in m]) * 100 - 100:+.2f}% "
               f"sc {med([x[f'score_{dims[0]}'] for x in m]) * 100 - 100:+.2f}%") if dims else ""
        def wp(key, i):
            return med([x[key][i]["worst_pct"] for x in m])
        print(f"{sc:12s} {v:44s} {mode:4s} {pr:6s} {len(ts):2d} {'>' if bad * 2 >= len(ts) else ' '}{med(ts):6.1f} "
              f"{min(ts):6.1f}-{max(ts):6.1f}{'*' if bad else ' '} {med([x['pv_retained'] for x in m]) * 100:6.2f}% "
              f"{dim:>23s} {wp('class', 0):5.1f}/{wp('class', 1):5.1f} {med([x['class_band_violations'] for x in m]):5.0f} "
              f"{wp('region_cost', 0):8.1f}/{wp('region_cost', 1):7.1f} {wp('region_blend', 0):8.1f}/{wp('region_blend', 1):7.1f} "
              f"{cont}")


def compact(names: list[str]) -> None:
    """One line per run: seconds (> = not certified), certified gap, PV retained, INC/HSP score, and the class and
    regional shortfalls (worst %, first two windows / checkpoints) against the default rows."""
    print("scenario | variant | mode | preset | s | gap | PV | score | class w1/w2 | reg cost c10/c20 | reg blend c10/c20 | "
          "blend worst 5-yearly | pop y5/y10")
    for (sc, v, pr, mode, _), r in _records(names):
        if not r.get("starts"):
            print(f"{sc} | {v} | {mode} | {pr} | >{r['time_limit']:.0f} | - | no schedule")
            continue
        m = metrics(sc, r["starts"])
        d = [x for x in D5 if f"dim-{x}" in v]
        sc_txt = f"{d[0]} {m['score_' + d[0]] * 100 - 100:+.1f}%" if d else ""
        gap = (r["objective"] - r["dual_bound"]) / abs(r["objective"])

        def two(k):
            return f"{m[k][0]['worst_pct']:.0f}/{m[k][1]['worst_pct']:.0f}"
        t = f"{r['seconds']:.1f}" if r["proven"] else f">{r['time_limit']:.0f}"
        w5 = max(x["worst_pct"] for x in m["region_blend_5y"])
        print(f"{sc} | {v} | {mode} | {pr} | {t} | {gap * 100:.2f}% | {m['pv_retained'] * 100:.2f}% | {sc_txt} | "
              f"{two('class')} | {two('region_cost')} | {two('region_blend')} | {w5:.0f} | {two('region_pop')}")


def main() -> int:
    cmd = sys.argv[1]
    if cmd == "pvstar":
        for sc in sys.argv[2:]:
            p = pvstar(sc)
            print(sc, {k: v for k, v in p.items() if k != "starts"})
    elif cmd == "build":
        _, path = build_variant(sys.argv[2], sys.argv[3])
        print(path)
    elif cmd == "spec":
        spec(sys.argv[2])
    elif cmd == "frontier":
        frontier_spec(sys.argv[2])
    elif cmd == "compact":
        compact([a for a in sys.argv[2:] if not a.startswith("--")])
    elif cmd == "summary":
        summary([a for a in sys.argv[2:] if not a.startswith("--")])
    elif cmd == "metrics":
        print(json.dumps(metrics(sys.argv[2], json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))["starts"]),
                         indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
