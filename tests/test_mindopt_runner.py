"""MindOpt's floating licence allows one solve at a time: the runner must serialise solves across processes, retry
the licence with backoff, never kill a solve, and skip cleanly when MindOpt is unavailable."""
import multiprocessing as mp
import time

import pytest

from capopt import bench, mindopt_runner
from capopt.mindopt_runner import LicenceLock, LicenceUnavailable, acquire_licence


def _hold_lock(path, seconds, ready):
    with LicenceLock(path):
        ready.set()
        time.sleep(seconds)


def test_lock_is_exclusive_across_processes(tmp_path):
    path = tmp_path / "mindopt.lock"
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    holder = ctx.Process(target=_hold_lock, args=(str(path), 3.0, ready))
    holder.start()
    assert ready.wait(30)
    with pytest.raises(TimeoutError):
        LicenceLock(path, timeout=0.5, poll=0.1).acquire()
    holder.join()
    with LicenceLock(path, timeout=5):  # released when the holder exits
        pass


def test_lock_is_released_if_the_holder_dies(tmp_path):
    path = tmp_path / "mindopt.lock"
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    holder = ctx.Process(target=_hold_lock, args=(str(path), 60.0, ready))
    holder.start()
    assert ready.wait(30)
    holder.kill()  # a crashed benchmark process must not leave a stale lock
    holder.join()
    with LicenceLock(path, timeout=10):
        pass


def test_same_process_reacquire_after_release(tmp_path):
    lock = LicenceLock(tmp_path / "l.lock", timeout=1)
    with lock:
        pass
    with lock:
        pass


def test_licence_retry_with_exponential_backoff():
    calls, waits = [], []

    def probe():
        calls.append(1)
        if len(calls) < 4:
            raise RuntimeError("licence in use")

    k = acquire_licence(probe, attempts=6, first_wait=10, max_wait=25, sleep=waits.append, log=lambda m: None)
    assert k == 4 and waits == [10, 20, 25]


def test_licence_gives_up_cleanly():
    def probe():
        raise RuntimeError("licence in use")

    with pytest.raises(LicenceUnavailable):
        acquire_licence(probe, attempts=3, first_wait=1, sleep=lambda s: None, log=lambda m: None)


def test_solve_cold_skips_when_mindopt_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mindopt_runner, "available", lambda: False)
    assert "not installed" in mindopt_runner.solve_cold("P100_raw_penalised", 10, tmp_path)["skipped"]


def test_solve_cold_skips_when_licence_busy(monkeypatch, tmp_path):
    monkeypatch.setattr(mindopt_runner, "available", lambda: True)
    monkeypatch.setattr(mindopt_runner, "LOCK_PATH", tmp_path / "l.lock")

    def busy(**kw):
        raise LicenceUnavailable("seat held elsewhere")

    monkeypatch.setattr(mindopt_runner, "acquire_licence", busy)
    assert "seat held" in mindopt_runner.solve_cold("P100_raw_penalised", 10, tmp_path)["skipped"]


def test_bench_records_skip_and_report_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(mindopt_runner, "available", lambda: False)
    rec = bench.run("mindopt_cold", "P100_raw_penalised", 10, 0, tmp_path)
    assert "skipped" in rec and (tmp_path / "mindopt_cold" / "P100_raw_penalised_s0.skipped.json").exists()
    text = bench.write_report(tmp_path, tmp_path / "B.md")
    assert "MindOpt cold (optimal" in text  # the pack's recorded run is still reported


def test_mindopt_runs_are_never_watchdog_killed():
    assert "mindopt_cold" in bench.NEVER_KILL
