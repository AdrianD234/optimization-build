"""Spread of early-solve metrics across seeds, per configuration (reference-campaign results).

    python benchmarks/experiments/variance_report.py <results dir>

Labels are "<instance>__<config>__seed<k>". For each config: median / min / max of the time to within 1% and 0.1% of
the reference objective, and the incumbent's distance from the reference at 30, 60 and 90 s. A miss counts as "not
reached" and is shown as ">cap".
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def at(trace, t):
    best = None
    for tt, v in trace:
        if tt <= t:
            best = v if best is None else min(best, v)
    return best


if __name__ == "__main__":
    rows = defaultdict(list)
    for f in sorted(Path(sys.argv[1]).glob("*__seed*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        cfg = r["label"].split("__")[1]
        ref = r["reference_objective"]
        rows[cfg].append({
            "t1": r["time_to_ref_within_1pct"], "t01": r["time_to_ref_within_0.1pct"],
            **{f"inc{t}": (lambda v: None if v is None else (v - ref) / abs(ref))(at(r["trace"], t)) for t in (30, 60, 90)},
            "cap": r["time_limit"]})

    def spread(vals, cap, pct=False):
        hit = [v for v in vals if v is not None]
        miss = len(vals) - len(hit)
        if not hit:
            return f"not reached in {len(vals)} runs"
        f = (lambda v: f"{v:+.2e}") if pct else (lambda v: f"{v:.1f}s")
        med = statistics.median([v if v is not None else float("inf") for v in vals])
        med_s = ">cap" if med == float("inf") else f(med)
        return f"median {med_s}, range {f(min(hit))}..{f(max(hit))}" + (f", {miss} miss" if miss else "")

    for cfg, rs in rows.items():
        print(f"{cfg} ({len(rs)} seeds)")
        print(f"   time to 1%   : {spread([r['t1'] for r in rs], rs[0]['cap'])}")
        print(f"   time to 0.1% : {spread([r['t01'] for r in rs], rs[0]['cap'])}")
        for t in (30, 60, 90):
            print(f"   incumbent @{t:>2}s: {spread([r[f'inc{t}'] for r in rs], rs[0]['cap'], pct=True)}")
