"""E37+: campaign specs and summaries for the certified-gap presets on the private CPO scenarios.

    python benchmarks/experiments/cpo_presets.py spec <name> --arms '{"A": {...solve kwargs...}}' [--modes warm,cold]
           [--scenarios s2000_P50,...] [--seeds 0,1,2] [--time-limit 120]
    python benchmarks/experiments/cpo_presets.py iterate <name> --arms '{...}' [--k 1,5] [--perturb-seeds 0] [--delay 2]
    python benchmarks/experiments/cpo_presets.py summary <name> [<name> ...]

`spec` writes private/cpo/campaigns/<name>.spec.json for `capopt campaign launch` (reference_runner: the trace streams
to the results folder, every run is load-tagged). Warm runs start from the CPO's stored schedule; cold runs have no
warm start. `summary` prints, per arm x mode x scenario, the median time to the arm's certified gap over seeds (an
unproven run counts at its time limit, marked >), the worst achieved gap and the contended-run count. Real data and
results stay under private/ (gitignored); only aggregates go into the docs.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CPO = ROOT / "private" / "cpo"
TIMED = ["s2000_P50high", "s2000_P50", "cfadsds_P50", "cfadsds_P50high", "cfadsds_P95", "cfadsds_P95high"]
UNTIMED = ["cfads_P50", "cfads_P95", "s1750_P50", "s1750_P95"]
ALL = TIMED + UNTIMED
# the user shares this machine: wait for a quiet minute before each run and rerun contended runs (campaign.wait_quiet)
QUIET = {"quiet": {"max_cores": 1.0, "window_s": 30.0, "max_wait_s": 1800.0}, "rerun_contended": 2}


def _arg(flag: str, default: str) -> str:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def spec(name: str) -> Path:
    arms = json.loads(_arg("--arms", "{}"))
    modes = _arg("--modes", "warm,cold").split(",")
    scenarios = _arg("--scenarios", ",".join(ALL)).split(",")
    seeds = [int(s) for s in _arg("--seeds", "0").split(",")]
    tl = float(_arg("--time-limit", "120"))
    runs = []
    for seed in seeds:  # seed-major, so a slow period of the machine hits every arm
        for sc in scenarios:
            for mode in modes:
                for arm, opts in arms.items():
                    ref = CPO / "solutions" / f"CPO233_{sc}_cpo_reference.json"
                    o = dict(opts, seed=seed)
                    if mode == "warm":
                        o["warm_start"] = str(ref)
                    runs.append({"instance": str(CPO / "instances" / f"CPO233_{sc}.json"), "reference": str(ref),
                                 "label": f"{sc}__{arm}__{mode}__seed{seed}", "time_limit": tl, "options": o})
    out = CPO / "campaigns" / f"{name}.spec.json"
    out.write_text(json.dumps({"name": name, "time_limit": tl, "runner": "capopt.campaign:reference_runner",
                               "out_dir": str(CPO / "results" / name), "runs": runs, **QUIET}, indent=1), encoding="utf-8")
    print(f"{out}: {len(runs)} runs")
    return out


def iterate(name: str) -> Path:
    """E38: perturbed copies of each scenario (k projects' spend/PV +-20%, earliest start delayed by up to `--delay`
    years), solved warm from the unperturbed scenario's stored plan, i.e. one model iteration."""
    from capopt import synthetic
    arms = json.loads(_arg("--arms", "{}"))
    scenarios = _arg("--scenarios", ",".join(ALL)).split(",")
    ks = [int(k) for k in _arg("--k", "1,5").split(",")]
    pseeds = [int(s) for s in _arg("--perturb-seeds", "0").split(",")]
    delay = int(_arg("--delay", "2"))
    tl = float(_arg("--time-limit", "120"))
    d = CPO / "instances" / "iter"
    d.mkdir(parents=True, exist_ok=True)
    runs = []
    for ps in pseeds:
        for sc in scenarios:
            raw = json.loads((CPO / "instances" / f"CPO233_{sc}.json").read_text(encoding="utf-8"))
            ref = CPO / "solutions" / f"CPO233_{sc}_cpo_reference.json"
            for k in ks:
                new, _ = synthetic.perturb_projects(raw, 1000 * ps + k, k, delay=delay)
                f = d / f"CPO233_{sc}_k{k}_p{ps}.json"
                f.write_text(json.dumps(new), encoding="utf-8")
                for arm, opts in arms.items():
                    runs.append({"instance": str(f), "reference": str(ref), "time_limit": tl,
                                 "label": f"{sc}-k{k}-p{ps}__{arm}__iter__seed0",
                                 "options": dict(opts, seed=0, warm_start=str(ref))})
    out = CPO / "campaigns" / f"{name}.spec.json"
    out.write_text(json.dumps({"name": name, "time_limit": tl, "runner": "capopt.campaign:reference_runner",
                               "out_dir": str(CPO / "results" / name), "runs": runs, **QUIET}, indent=1), encoding="utf-8")
    print(f"{out}: {len(runs)} runs")
    return out


def summary(names: list[str]) -> None:
    rows: dict[tuple[str, str, str], list[dict]] = {}
    for name in names:
        for f in sorted((CPO / "results" / name).glob("*__seed*.json")):
            rec = json.loads(f.read_text(encoding="utf-8"))
            sc, arm, mode, _ = f.stem.split("__")
            rows.setdefault((arm, mode, sc), []).append(rec)
    print(f"{'arm':14s} {'mode':5s} {'scenario':16s} {'n':>2s} {'median s':>9s} {'range':>17s} {'max gap':>8s} cont")
    for (arm, mode, sc), recs in sorted(rows.items()):
        ts = [r["seconds"] if r["proven"] else r["time_limit"] for r in recs]
        unproven = sum(not r["proven"] for r in recs)
        gaps = [(r["objective"] - r["dual_bound"]) / abs(r["objective"]) for r in recs]
        cont = sum(bool(r.get("load") and r["load"]["contended"]) for r in recs)
        med = statistics.median(ts)
        mark = ">" if unproven * 2 >= len(ts) else " "
        print(f"{arm:14s} {mode:5s} {sc:16s} {len(ts):2d} {mark}{med:8.1f} {min(ts):7.1f}-{max(ts):7.1f}"
              f"{'*' if unproven else ' '} {max(gaps):8.5f} {cont}")


if __name__ == "__main__":
    if sys.argv[1] == "spec":
        spec(sys.argv[2])
    elif sys.argv[1] == "iterate":
        iterate(sys.argv[2])
    else:
        summary(sys.argv[2:])
