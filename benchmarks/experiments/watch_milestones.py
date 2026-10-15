"""Live milestones for a reference campaign: one line per event, for an agent's Monitor tool.

    python benchmarks/experiments/watch_milestones.py <campaign dir> [--every 15]

Prints campaign state changes (from capopt.campaign.status: STALLED / COMPLETE / FAILED ...) and, for the run in
progress, the first time its streamed incumbent gets within 1% and 0.1% of the run's reference objective and when it
matches it. Exits when the campaign is COMPLETE or FAILED.
"""
import json
import sys
import time
from pathlib import Path

from capopt import campaign

if __name__ == "__main__":
    cdir = Path(sys.argv[1])
    every = float(sys.argv[sys.argv.index("--every") + 1]) if "--every" in sys.argv else 15.0
    spec = json.loads((cdir / "spec.json").read_text(encoding="utf-8")) if (cdir / "spec.json").exists() else None
    probe_every = float(sys.argv[sys.argv.index("--probe") + 1]) if "--probe" in sys.argv else 60.0
    seen: set = set()
    last_state = None
    last_probe = time.time()
    while True:
        st = campaign.status(cdir)
        if spec is None and (cdir / "spec.json").exists():
            spec = json.loads((cdir / "spec.json").read_text(encoding="utf-8"))
        if st["state"] != last_state or st["state"] == "STALLED":
            print(f"[campaign] {st['state']} {st.get('done', 0)}/{st.get('total', '?')} {st.get('reason', '')}", flush=True)
            last_state = st["state"]
        if st["state"] in ("COMPLETE", "FAILED", "NOT_STARTED"):
            break
        cur = st.get("current")
        if spec and cur is not None:
            run = spec["runs"][cur["index"]] if "index" in cur else next(
                (r for r in spec["runs"] if r.get("label") == cur.get("label")), None)
            if run:
                ref = json.loads(Path(run["reference"]).read_text(encoding="utf-8"))["evaluator_objective"]
                tf = Path(spec["out_dir"]) / f"{run['label']}.trace.jsonl"
                if tf.exists() and time.time() - last_probe >= probe_every:
                    last_probe = time.time()
                    objs, bounds = [], []
                    for line in tf.read_text(encoding="utf-8").splitlines():
                        try:
                            pt = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        (objs if "obj" in pt else bounds).append(pt)
                    if objs:
                        inc = min(q["obj"] for q in objs)
                        bnd = max((q["bound"] for q in bounds), default=None)
                        gap = f"{(inc - bnd) / abs(inc):.3e}" if bnd is not None else "n/a"
                        print(f"[probe {run['label']}] t={max(q['t'] for q in objs + bounds):.0f}s incumbent "
                              f"{(inc - ref) / abs(ref):+.3e} vs CPO | MIP gap {gap}", flush=True)
                if tf.exists():
                    for line in tf.read_text(encoding="utf-8").splitlines():
                        try:
                            pt = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if "obj" not in pt:  # a dual-bound line
                            continue
                        for tag, rel in (("1%", 0.01), ("0.1%", 0.001), ("reference objective", 0.0)):
                            key = (run["label"], tag)
                            if key not in seen and pt["obj"] <= ref + rel * abs(ref) + 1e-9 * abs(ref):
                                seen.add(key)
                                extra = (f" (COPT logged {run['copt_seconds']} s to gap {run['copt_gap']})"
                                         if "copt_seconds" in run else "")
                                print(f"[{run['label']}] within {tag} at {pt['t']:.1f}s{extra}", flush=True)
        time.sleep(every)
