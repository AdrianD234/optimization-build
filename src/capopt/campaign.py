"""Long benchmark campaigns that cannot die unnoticed.

Incident (2026-10-15): a 48-run holdout benchmark was started in the background from an agent's shell. It died at
launch: no solver processes, an empty log folder, no results. Nothing noticed for about 50 minutes. This module makes
that impossible to miss.

* **Runner** (`run`): executes a list of runs one after another in the foreground of one process, one output file
  per run. It keeps `progress.json` in the campaign folder up to date: counts, the current run, per-run outcomes, an
  ETA, the runner's pid, and a heartbeat refreshed every 30 s by a background thread. Writes are atomic (temp file,
  then os.replace).
* **Launcher** (`launch`): writes a launch marker, spawns the runner fully detached (its own process group, no console,
  output to `runner.log`), then *verifies* within 60 s that the heartbeat exists. It fails loudly otherwise
  ("launch-and-verify").
* **Status** (`status`): percent done, current run, elapsed, ETA. It reports STALLED when the heartbeat is older than
  5 minutes, when the runner process is gone before the campaign is complete, or when the current run has outlived
  its own time limit plus grace. It reports NOT_STARTED when the launch marker is over 60 s old and the runner has
  never written progress.
* **Watch** (`watch`): polls status every 30 s and prints one line per state change (STARTED / PROGRESS n/N /
  STALLED / COMPLETE / FAILED). It is meant to be armed with an agent's Monitor tool, so a stall wakes someone up
  within five minutes.

Spec file (JSON):
    {"name": "holdout", "time_limit": 900,
     "runs": [{"config": "capopt_quick_nocut", "instance": "P100_raw_penalised", "seed": 0}, ...]}
or a cross product: {"name": ..., "configs": [...], "instances": [...], "seeds": [0]}.
"runner" (optional) names the function that executes one run, "module:function". The default,
capopt.campaign:bench_runner, uses bench.run_isolated (benchmark configs, watchdog, stored traces).
capopt.campaign:solve_runner solves any instance file with the default pipeline. capopt.campaign:sleep_runner exists
to test the machinery itself.
"""
from __future__ import annotations

import importlib
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "benchmarks" / "results"
HEARTBEAT_S = 30.0
STALE_S = 300.0          # no heartbeat for this long => STALLED
NOT_STARTED_S = 60.0     # launch marker this old with no progress file => NOT_STARTED
OVERDUE_GRACE_S = 300.0  # a run older than its time limit + this => STALLED (hung child)

PROGRESS = "progress.json"
LAUNCH = "launch.json"
LOG = "runner.log"


# ---- spec ----------------------------------------------------------------------------------------------------------
def load_spec(path: str | Path) -> dict[str, Any]:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if "runs" not in spec:
        spec["runs"] = [{"config": c, "instance": i, "seed": s}
                        for c in spec["configs"] for i in spec["instances"] for s in spec.get("seeds", [0])]
    if not spec.get("name") or not spec["runs"]:
        raise ValueError("a campaign spec needs a name and at least one run")
    spec.setdefault("time_limit", 900.0)
    spec.setdefault("runner", "capopt.campaign:bench_runner")
    return spec


def campaign_dir(name: str, root: Path = RESULTS) -> Path:
    return Path(root) / name


def _resolve(dotted: str) -> Callable[..., dict[str, Any]]:
    mod, fn = dotted.split(":")
    return getattr(importlib.import_module(mod), fn)


# ---- runners (one run each; return a small summary dict) -----------------------------------------------------------
def bench_runner(run: dict, time_limit: float, out_dir: Path) -> dict[str, Any]:
    from . import bench
    rec = bench.run_isolated(run["config"], run["instance"], time_limit, int(run.get("seed", 0)), out_dir)
    if "skipped" in rec:
        return {"status": "skipped", "reason": rec["skipped"]}
    return {"status": "done", "objective": rec.get("objective"), "proven": rec.get("proven"),
            "time_to_proof": rec.get("time_to_proof"), "killed": bool(rec.get("killed"))}


def solve_runner(run: dict, time_limit: float, out_dir: Path) -> dict[str, Any]:
    """Default pipeline on any instance (name or JSON path); the result goes to <out_dir>/<config>/<stem>_s<seed>.json."""
    from . import solver
    r = solver.solve(run["instance"], run.get("workers"), time_limit, **run.get("options", {}))
    d = Path(out_dir) / run.get("config", "solve")
    d.mkdir(parents=True, exist_ok=True)
    rec = {"instance": run["instance"], "objective": r.objective, "proven": r.proven, "dual_bound": r.dual_bound,
           "seconds": round(r.seconds, 2), "trace": r.trace, "log": r.log, "starts": r.starts}
    (d / f"{Path(str(run['instance'])).stem}_s{run.get('seed', 0)}.json").write_text(json.dumps(rec, indent=1),
                                                                                     encoding="utf-8")
    return {"status": "done", "objective": r.objective, "proven": r.proven, "time_to_proof": round(r.seconds, 2)}


def reference_runner(run: dict, time_limit: float, out_dir: Path) -> dict[str, Any]:
    """Default pipeline on an instance file, compared with a reference solution file.

    run = {"instance": <instance json>, "reference": <solution json with "evaluator_objective">,
           "options": {...solver.solve kwargs, e.g. "rel_gap": 1e-5}, "label": <name for the outputs>}
    Incumbents stream to <out_dir>/<label>.trace.jsonl while the solve runs (watchers read it live). The record, with
    times to within 1% / 0.1% of the reference and to the reference objective itself, goes to <out_dir>/<label>.json.
    """
    from . import solver
    from .bench import time_to
    label = run.get("label") or Path(str(run["instance"])).stem
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ref = json.loads(Path(run["reference"]).read_text(encoding="utf-8"))
    target = float(ref["evaluator_objective"])
    trace_file = out / f"{label}.trace.jsonl"
    trace_file.unlink(missing_ok=True)
    os.environ["CAPOPT_TRACE_FILE"] = str(trace_file)
    try:
        r = solver.solve(str(run["instance"]), run.get("workers"), time_limit, **run.get("options", {}))
    finally:
        os.environ.pop("CAPOPT_TRACE_FILE", None)
    rec = {"label": label, "instance": str(run["instance"]), "reference": str(run["reference"]),
           "options": run.get("options", {}), "time_limit": time_limit, "objective": r.objective, "proven": r.proven,
           "dual_bound": r.dual_bound, "seconds": round(r.seconds, 2), "reference_objective": target,
           "reference_meta": {k: ref.get(k) for k in ("source", "cpo_gap", "cpo_created_at", "cpo_threads")},
           "vs_reference_rel": (r.objective - target) / abs(target),
           "time_to_ref_within_1pct": time_to(r.trace, target, 0.01),
           "time_to_ref_within_0.1pct": time_to(r.trace, target, 0.001),
           "time_to_ref_objective": time_to(r.trace, target, 0.0),
           "time_to_proof": round(r.seconds, 2) if r.proven else None,
           "trace": r.trace, "log": r.log, "starts": r.starts}
    (out / f"{label}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return {"status": "done", "objective": r.objective, "proven": r.proven, "time_to_proof": rec["time_to_proof"],
            "vs_reference_rel": rec["vs_reference_rel"], "time_to_ref_within_0.1pct": rec["time_to_ref_within_0.1pct"]}


def mindopt_reference_runner(run: dict, time_limit: float, out_dir: Path) -> dict[str, Any]:
    """A cold MindOpt solve of the reference (big-M/indicator) model, compared with a reference solution.

    run = {"instance": <instance json>, "reference": <solution json>, "rel_gap": 1e-5, "label": ...}
    The solve runs in the interpreter that has mindoptpy (mindopt_runner.mindopt_python), under the licence lock.
    It is never killed: MindOpt stops itself at MaxTime, and only if it overruns by 3x + 10 min is it left running and
    recorded as skipped. Times in the trace are from the start of the MindOpt process: model build plus solve.
    """
    from . import mindopt_runner
    from .bench import time_to
    label = run.get("label") or Path(str(run["instance"])).stem
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    py = mindopt_runner.mindopt_python()
    if py is None:
        return {"status": "skipped", "reason": "no Python with mindoptpy found (set CAPOPT_MINDOPT_PYTHON)"}
    raw = out / f"{label}.mindopt_raw.json"
    raw.unlink(missing_ok=True)
    cmd = [py, str(ROOT / "benchmarks" / "experiments" / "run_mindopt.py"), str(run["instance"]), str(time_limit),
           str(run.get("rel_gap", 1e-5)), str(raw), str(out / "logs")]
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=3 * time_limit + 600)
    except subprocess.TimeoutExpired:
        return {"status": "skipped", "reason": f"MindOpt overran; pid {proc.pid} left running to protect the licence"}
    if not raw.exists():
        return {"status": "failed", "error": f"MindOpt run wrote no result (exit {proc.returncode})"}
    res = json.loads(raw.read_text(encoding="utf-8"))
    if "skipped" in res:
        return {"status": "skipped", "reason": res["skipped"]}
    ref = json.loads(Path(run["reference"]).read_text(encoding="utf-8"))
    target = float(ref["evaluator_objective"])
    trace = [tuple(x) for x in res.get("trace", [])]
    obj = res.get("evaluator_objective")
    rec = {"label": label, "solver": "MindOpt (reference model, cold)", "instance": str(run["instance"]),
           "rel_gap_target": run.get("rel_gap", 1e-5), "time_limit": time_limit, "status": res.get("status"),
           "objective": obj, "mindopt_objective": res.get("objective"), "dual_bound": res.get("dual_bound"),
           "gap": res.get("gap"), "seconds": res.get("seconds"), "build_seconds": res.get("build_seconds"),
           "wall_seconds": res.get("wall_seconds"), "reference_objective": target,
           "vs_reference_rel": None if obj is None else (obj - target) / abs(target),
           "time_to_ref_within_1pct": time_to(trace, target, 0.01),
           "time_to_ref_within_0.1pct": time_to(trace, target, 0.001),
           "time_to_ref_objective": time_to(trace, target, 1e-6),  # the log prints ~7 significant figures
           "time_to_gap_target": (res.get("build_seconds") or 0) + (res.get("seconds") or 0)
           if res.get("status") == "OPTIMAL" else None,
           "trace": trace, "starts": res.get("starts")}
    (out / f"{label}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return {"status": "done", "objective": obj, "proven": res.get("status") == "OPTIMAL",
            "time_to_proof": rec["time_to_gap_target"], "vs_reference_rel": rec["vs_reference_rel"]}


def sleep_runner(run: dict, time_limit: float, out_dir: Path) -> dict[str, Any]:
    """Machinery test: sleeps run['seconds']; raises if run['fail']."""
    time.sleep(float(run.get("seconds", 0.1)))
    if run.get("fail"):
        raise RuntimeError("requested failure")
    return {"status": "done"}


# ---- progress file -------------------------------------------------------------------------------------------------
def _now() -> float:
    return time.time()


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    for attempt in range(20):  # Windows: the target may be open in a reader for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, path)


def read_json(path: Path) -> dict | None:
    for _ in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, PermissionError):
            time.sleep(0.05)
    return None


def eta_seconds(prog: dict, now: float | None = None) -> float | None:
    """Median completed run time x runs left, less the time the current run has already used."""
    now = _now() if now is None else now
    secs = [r["seconds"] for r in prog["runs"] if r["status"] in ("done", "failed", "skipped") and "seconds" in r]
    left = prog["total"] - prog["done"]
    if left == 0:
        return 0.0
    if not secs:
        return None
    med = statistics.median(secs)
    cur = prog.get("current")
    used = (now - cur["started_at"]) if cur else 0.0
    return max(0.0, med * left - min(used, med))


class Progress:
    """Thread-safe owner of progress.json for one runner process."""

    def __init__(self, cdir: Path, spec: dict) -> None:
        self.path = cdir / PROGRESS
        self.lock = threading.Lock()
        t = _now()
        self.data = {
            "name": spec["name"], "pid": os.getpid(), "state": "running", "started_at": t, "started_at_iso": _iso(t),
            "last_heartbeat": t, "time_limit": float(spec["time_limit"]), "runner": spec["runner"],
            "total": len(spec["runs"]), "done": 0, "failed": 0, "current": None, "eta_seconds": None,
            "runs": [dict(run, status="pending") for run in spec["runs"]],
        }
        self.flush()

    def flush(self) -> None:
        with self.lock:
            self.data["eta_seconds"] = eta_seconds(self.data)
            write_json_atomic(self.path, self.data)

    def beat(self) -> None:
        with self.lock:
            self.data["last_heartbeat"] = _now()
        self.flush()

    def start_run(self, k: int) -> None:
        with self.lock:
            t = _now()
            self.data["runs"][k].update(status="running", started_at=t)
            r = self.data["runs"][k]
            self.data["current"] = {"index": k, "config": r.get("config"), "instance": r.get("instance"),
                                    "seed": r.get("seed", 0), "started_at": t}
            self.data["last_heartbeat"] = t
        self.flush()

    def end_run(self, k: int, outcome: dict) -> None:
        with self.lock:
            t = _now()
            r = self.data["runs"][k]
            r.update(outcome)
            r["seconds"] = round(t - r["started_at"], 2)
            r["finished_at"] = t
            self.data["done"] += 1
            self.data["failed"] += r["status"] == "failed"
            self.data["current"] = None
            self.data["last_heartbeat"] = t
        self.flush()

    def finish(self, state: str, error: str | None = None) -> None:
        with self.lock:
            self.data.update(state=state, finished_at=_now(), current=None)
            if error:
                self.data["error"] = error
        self.flush()


def run(spec: dict, cdir: Path, heartbeat_s: float = HEARTBEAT_S, out_dir: Path | None = None) -> dict:
    """Execute a campaign in this process. A failing run is recorded and the campaign moves on; a crash of the runner
    itself marks the campaign FAILED."""
    cdir.mkdir(parents=True, exist_ok=True)
    prog = Progress(cdir, spec)
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(heartbeat_s):
            prog.beat()

    hb = threading.Thread(target=heartbeat, name="campaign-heartbeat", daemon=True)
    hb.start()
    try:
        runner = _resolve(spec["runner"])
        out = Path(out_dir or spec.get("out_dir") or RESULTS)
        for k, item in enumerate(spec["runs"]):
            prog.start_run(k)
            try:
                outcome = runner(item, float(spec["time_limit"]), out)
            except Exception as exc:  # noqa: BLE001 - record and continue with the next run
                outcome = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            prog.end_run(k, outcome)
            print(f"[{prog.data['done']}/{prog.data['total']}] {item} -> {outcome}", flush=True)
        prog.finish("complete")
    except BaseException as exc:
        prog.finish("failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        stop.set()
    return prog.data


# ---- liveness ------------------------------------------------------------------------------------------------------
def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    try:  # a killed child stays a zombie until reaped, and os.kill(pid, 0) still succeeds on it
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


# ---- status --------------------------------------------------------------------------------------------------------
OK_STATES = {"LAUNCHING", "RUNNING", "COMPLETE"}


def status(cdir: Path, now: float | None = None, stale_s: float = STALE_S, not_started_s: float = NOT_STARTED_S,
           overdue_grace_s: float = OVERDUE_GRACE_S, alive: Callable[[int | None], bool] = pid_alive) -> dict:
    """Classify a campaign: NOT_LAUNCHED, LAUNCHING, NOT_STARTED, RUNNING, STALLED, COMPLETE or FAILED."""
    now = _now() if now is None else now
    prog = read_json(cdir / PROGRESS)
    launch = read_json(cdir / LAUNCH)
    if prog is None:
        if launch is None:
            return {"state": "NOT_LAUNCHED", "reason": f"no {LAUNCH} or {PROGRESS} in {cdir}"}
        age = now - launch["launched_at"]
        if age > not_started_s:
            return {"state": "NOT_STARTED", "reason": f"launched {age:.0f}s ago but the runner never wrote progress "
                    f"(see {cdir / LOG})", "launched_at": launch["launched_at"]}
        return {"state": "LAUNCHING", "reason": f"launched {age:.0f}s ago", "launched_at": launch["launched_at"]}
    out = {"name": prog["name"], "done": prog["done"], "total": prog["total"], "failed": prog["failed"],
           "pct": 100.0 * prog["done"] / max(1, prog["total"]), "elapsed": now - prog["started_at"],
           "eta": eta_seconds(prog, now), "current": prog.get("current"), "pid": prog.get("pid"),
           "heartbeat_age": now - prog["last_heartbeat"]}
    if prog["state"] == "complete":
        return dict(out, state="COMPLETE", reason=f"{prog['done']} runs, {prog['failed']} failed")
    if prog["state"] == "failed":
        return dict(out, state="FAILED", reason=prog.get("error", "runner failed"))
    reasons = []
    if out["heartbeat_age"] > stale_s:
        reasons.append(f"no heartbeat for {out['heartbeat_age']:.0f}s (limit {stale_s:.0f}s)")
    if not alive(prog.get("pid")):
        reasons.append(f"runner process {prog.get('pid')} is not running")
    cur = prog.get("current")
    if cur and now - cur["started_at"] > prog["time_limit"] + overdue_grace_s:
        reasons.append(f"current run has taken {now - cur['started_at']:.0f}s, past its {prog['time_limit']:.0f}s "
                       f"limit + {overdue_grace_s:.0f}s grace")
    if reasons:
        return dict(out, state="STALLED", reason="; ".join(reasons))
    return dict(out, state="RUNNING", reason="")


def _dur(s: float | None) -> str:
    if s is None:
        return "?"
    s = int(s)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else (f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s")


def describe(st: dict, now: float | None = None) -> str:
    now = _now() if now is None else now
    if "total" not in st:
        return f"{st['state']}: {st['reason']}"
    line = f"{st['name']}: {st['state']} {st['done']}/{st['total']} ({st['pct']:.0f}%)"
    cur = st.get("current")
    if cur:
        line += f" | current [{cur.get('config')}] {cur.get('instance')} s{cur.get('seed')} for " \
                f"{_dur(now - cur['started_at'])}"
    line += f" | elapsed {_dur(st['elapsed'])} | ETA {_dur(st['eta'])} | heartbeat {_dur(st['heartbeat_age'])} ago"
    if st.get("failed"):
        line += f" | {st['failed']} failed"
    if st.get("reason"):
        line += f" | {st['reason']}"
    return line


# ---- watch ---------------------------------------------------------------------------------------------------------
def watch(cdir: Path, interval: float = 30.0, emit: Callable[[str], None] = print, max_polls: int | None = None,
          **status_kw) -> int:
    """One line per state change. Exit 0 on COMPLETE, 1 on FAILED; keeps watching through a stall (it may recover,
    or someone relaunches)."""
    last_state, last_done, last_reason, polls = None, -1, None, 0
    while True:
        st = status(cdir, **status_kw)
        state = st["state"]
        if state == "RUNNING" and last_state not in ("RUNNING",):
            emit(f"STARTED {describe(st)}" if last_state in (None, "LAUNCHING", "NOT_LAUNCHED") else
                 f"RECOVERED {describe(st)}")
        if state in ("STALLED", "NOT_STARTED") and (state != last_state or st["reason"] != last_reason):
            emit(f"STALLED {describe(st)}")
        if state == "RUNNING" and st["done"] != last_done and st["done"] > 0:
            emit(f"PROGRESS {st['done']}/{st['total']} {describe(st)}")
        if state == "COMPLETE":
            emit(f"COMPLETE {describe(st)}")
            return 0
        if state == "FAILED":
            emit(f"FAILED {describe(st)}")
            return 1
        last_state, last_reason = state, st.get("reason")
        last_done = st.get("done", last_done)
        polls += 1
        if max_polls is not None and polls >= max_polls:
            return 2
        time.sleep(interval)


# ---- launch-and-verify ---------------------------------------------------------------------------------------------
class LaunchError(RuntimeError):
    pass


def launch(spec_path: str | Path, root: Path = RESULTS, verify_s: float = NOT_STARTED_S,
           heartbeat_s: float = HEARTBEAT_S) -> dict:
    """Start the runner fully detached and confirm its heartbeat within `verify_s` seconds; raise LaunchError if not."""
    try:  # a bad spec or runner fails here, before anything is spawned
        spec = load_spec(spec_path)
        _resolve(spec["runner"])
    except Exception as exc:  # noqa: BLE001
        raise LaunchError(f"bad campaign spec {spec_path}: {type(exc).__name__}: {exc}") from exc
    cdir = campaign_dir(spec["name"], root)
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / PROGRESS).unlink(missing_ok=True)
    spec_copy = cdir / "spec.json"
    spec_copy.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    cmd = [sys.executable, "-m", "capopt.cli", "campaign", "run", "--spec", str(spec_copy), "--root", str(root),
           "--heartbeat", str(heartbeat_s)]
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1])  # make the child import this same capopt
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    log = open(cdir / LOG, "ab")
    kw: dict[str, Any] = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "env": env,
                          "cwd": str(ROOT), "close_fds": True}
    write_json_atomic(cdir / LAUNCH, {"launched_at": _now(), "launched_at_iso": _iso(_now()), "cmd": cmd,
                                      "launcher_pid": os.getpid()})
    try:
        if os.name == "nt":
            # CREATE_NO_WINDOW, not DETACHED_PROCESS: a detached runner has no console at all, so every solver worker it
            # spawns (20 per run) gets a visible console window of its own. With a hidden console, they all share it.
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000  # CREATE_NO_WINDOW
            try:  # also leave the caller's job object, so closing the calling shell cannot take the runner down
                proc = subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kw)  # CREATE_BREAKAWAY_FROM_JOB
            except PermissionError:  # the job forbids breakaway: still detached, just inside the job
                proc = subprocess.Popen(cmd, creationflags=flags, **kw)
        else:
            proc = subprocess.Popen(cmd, start_new_session=True, **kw)
    except OSError as exc:
        raise LaunchError(f"could not start the runner ({' '.join(cmd[:3])} ...): {exc}") from exc
    finally:
        log.close()
    t0 = time.time()
    while time.time() - t0 < verify_s:
        prog = read_json(cdir / PROGRESS)
        if prog and prog.get("pid") == proc.pid and prog.get("state") == "failed":
            raise LaunchError(f"runner started but failed at once: {prog.get('error')}\n{_tail(cdir / LOG)}")
        if prog and prog.get("pid") == proc.pid and prog.get("last_heartbeat", 0) >= t0 - 1:
            return {"campaign": spec["name"], "dir": str(cdir), "pid": proc.pid, "verified_after": time.time() - t0,
                    "total": prog["total"]}
        if proc.poll() is not None:
            prog = read_json(cdir / PROGRESS)
            if prog and prog.get("pid") == proc.pid and prog.get("state") == "complete":  # finished very fast
                return {"campaign": spec["name"], "dir": str(cdir), "pid": proc.pid, "verified_after": time.time() - t0,
                        "total": prog["total"]}
            raise LaunchError(f"runner exited with code {proc.returncode} before writing a heartbeat; "
                              f"last log lines:\n{_tail(cdir / LOG)}")
        time.sleep(0.25)
    raise LaunchError(f"no heartbeat from runner pid {proc.pid} within {verify_s:.0f}s; it is still "
                      f"{'running' if proc.poll() is None else 'gone'}. Log:\n{_tail(cdir / LOG)}")


def _tail(path: Path, n: int = 15) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except FileNotFoundError:
        return "(no log)"
