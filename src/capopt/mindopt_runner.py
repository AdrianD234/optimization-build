"""Live MindOpt runs for the benchmark, within MindOpt's licence limits.

MindOpt here uses a *floating licence: one solve at a time* (benchmark_pack/README.md, HANDOFF.md). It needs:

1. **Machine-wide serialisation.** Every MindOpt solve holds an OS-level exclusive file lock (`LicenceLock`). Solves
   from different processes (two `capopt bench` invocations, a test run, a notebook) therefore queue instead of
   fighting over the seat. The OS releases the lock if the holder dies, so a crash cannot leave a stale lock.
2. **Patient licence acquisition.** Even with the lock, the seat can be held elsewhere (another machine, or a seat
   still locked after a killed process). `acquire_licence` probes it with retries and exponential backoff, the same
   approach as the pack's `run_benchmark.wait_for_licence`, and raises `LicenceUnavailable` once it gives up.
3. **Never killing a MindOpt solve.** Killing a process mid-solve can lock the seat for a while. MindOpt stops
   through its own `MaxTime`, and the benchmark watchdog is told not to kill these runs (see bench.CONFIGS: never_kill).
4. **Graceful skip.** No `mindoptpy` or no licence gives a `skipped` record with the reason; the report then falls
   back to the pack's recorded MindOpt run, which was measured on this machine.
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import instance as I

LOCK_PATH = Path(os.environ.get("CAPOPT_MINDOPT_LOCK", Path(tempfile.gettempdir()) / "capopt_mindopt.lock"))


class LicenceUnavailable(RuntimeError):
    pass


def available() -> bool:
    return importlib.util.find_spec("mindoptpy") is not None


class LicenceLock:
    """Exclusive, cross-process lock on a file (msvcrt on Windows, fcntl elsewhere); released by the OS if the holder
    dies."""

    def __init__(self, path: Path | None = None, timeout: float | None = None, poll: float = 0.5) -> None:
        # resolve the default at call time, so tests (and callers) can point LOCK_PATH elsewhere
        self.path, self.timeout, self.poll = Path(LOCK_PATH if path is None else path), timeout, poll
        self.fh = None

    def _try(self) -> bool:
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+b")
        if os.name == "nt" and self.fh.seek(0, 2) == 0:
            self.fh.write(b"0")
            self.fh.flush()
        t0 = time.monotonic()
        while not self._try():
            if self.timeout is not None and time.monotonic() - t0 >= self.timeout:
                self.fh.close()
                self.fh = None
                raise TimeoutError(f"MindOpt licence lock {self.path} is held by another process")
            time.sleep(self.poll)

    def release(self) -> None:
        if self.fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        finally:
            self.fh.close()
            self.fh = None

    def __enter__(self) -> LicenceLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def acquire_licence(probe: Callable[[], None] | None = None, attempts: int = 8, first_wait: float = 15.0,
                    max_wait: float = 300.0, sleep: Callable[[float], None] = time.sleep,
                    log: Callable[[str], None] = print) -> int:
    """Probe the floating licence until it is free. Returns the attempt that succeeded. The default probe creates and
    disposes an empty MindOpt model."""
    if probe is None:
        def probe() -> None:
            from mindoptpy import Model
            Model("capopt_licence_probe").dispose()
    wait = first_wait
    last: Exception | None = None
    for k in range(1, attempts + 1):
        try:
            probe()
            log(f"[mindopt] licence acquired (attempt {k})")
            return k
        except Exception as exc:  # noqa: BLE001 - mindoptpy raises its own error types
            last = exc
            if k == attempts:
                break
            log(f"[mindopt] licence busy (attempt {k}/{attempts}): {exc}; retrying in {wait:.0f}s")
            sleep(wait)
            wait = min(max_wait, wait * 2)
    raise LicenceUnavailable(f"MindOpt licence unavailable after {attempts} attempts: {last}")


@contextmanager
def licensed(lock_timeout: float | None = None, **licence_kwargs):
    """Hold the machine-wide lock *and* a confirmed licence for the duration of one solve."""
    with LicenceLock(timeout=lock_timeout):
        acquire_licence(**licence_kwargs)
        yield


def _reference_module():
    spec = importlib.util.spec_from_file_location("mindopt_model", I.PACK / "reference" / "mindopt_model.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def solve_cold(name: str, time_limit: float, log_dir: Path, lock_timeout: float | None = None,
               licence_attempts: int = 8, rel_gap: float = 0.0) -> dict[str, Any]:
    """One cold MindOpt solve of the pack's reference model (instance name or JSON path), under the lock and a
    confirmed licence, stopping at relative gap `rel_gap` (the pack's reference code uses 0; COPT's CPO runs used
    1e-5). Returns {"skipped": reason} when MindOpt cannot run here."""
    if not available():
        return {"skipped": "mindoptpy is not installed"}
    try:
        with licensed(lock_timeout=lock_timeout, attempts=licence_attempts):
            mm = _reference_module()
            t0 = time.perf_counter()
            model = mm.build(I.load(name).raw)
            build_s = time.perf_counter() - t0
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{Path(str(name)).stem}_mindopt_cold.log"
            log_path.unlink(missing_ok=True)
            m = model["m"]
            try:
                m.setParam("MaxTime", float(time_limit))  # MindOpt stops itself at MaxTime; never killed
                m.setParam("MIP/GapRel", float(rel_gap))
                m.setParam("MIP/GapAbs", 0.0)
                t1 = time.perf_counter()
                with mm.CaptureStdout(str(log_path)):
                    m.optimize()
                secs = time.perf_counter() - t1
                status = str(m.Status)
                res = {"status": {"1": "OPTIMAL", "7": "TIME LIMIT"}.get(status, status), "seconds": round(secs, 2),
                       "has_solution": bool(getattr(m, "HasSolution", 0)), "rel_gap_target": rel_gap}
                if res["has_solution"]:
                    res["objective"] = float(m.ObjVal)
                    res["dual_bound"] = float(m.DualObjVal)
                    res["gap"] = abs(res["objective"] - res["dual_bound"]) / max(1.0, abs(res["objective"]))
                    res["starts"] = {pid: int(s) for (pid, s), v in model["x"].items() if v.X > 0.5}
            finally:
                m.dispose()  # always hand the seat back
            res["build_seconds"] = round(build_s, 2)
            res["trace"] = [(t + build_s, v) for t, v in mm.incumbent_trace(log_path.read_text("utf-8", errors="ignore"))]
            return res
    except LicenceUnavailable as exc:
        return {"skipped": str(exc)}
    except TimeoutError as exc:
        return {"skipped": str(exc)}


def mindopt_python() -> str | None:
    """A Python interpreter that can import mindoptpy: $CAPOPT_MINDOPT_PYTHON, this interpreter, or a known conda
    install. MindOpt can live in a different environment from capopt (here: the Anaconda Python next to
    C:/mindopt/2.3.0), so MindOpt runs execute in a subprocess of that interpreter."""
    import subprocess
    import sys
    cands = [os.environ.get("CAPOPT_MINDOPT_PYTHON"), sys.executable, "C:/Conda/anaconda3/python.exe",
             str(Path.home() / "anaconda3" / "python.exe"), str(Path.home() / "miniconda3" / "python.exe")]
    for py in [c for c in cands if c]:
        if not Path(py).exists():
            continue
        r = subprocess.run([py, "-c", "import mindoptpy"], capture_output=True, timeout=60)
        if r.returncode == 0:
            return py
    return None
