"""Campaign watchdog: a long benchmark must never die or hang unnoticed (incident of 2026-10-15, docs/EXPERIMENTS.md).

Covers progress counts and ETA, STALLED on a stale heartbeat / dead runner / overdue run, NOT_STARTED, COMPLETE,
launch-and-verify (including a bad spec and a real kill of a detached runner), and the watch output.
"""
import json
import os
import signal
import subprocess
import sys
import time

import pytest
from conftest import write_instance

from capopt import campaign as C
from capopt import cli
from capopt import synthetic as S


def write_spec(tmp_path, **spec):
    p = tmp_path / f"{spec['name']}.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return p


def fake_progress(cdir, **over):
    now = time.time()
    prog = {"name": "fake", "pid": os.getpid(), "state": "running", "started_at": now - 600, "last_heartbeat": now,
            "time_limit": 900.0, "runner": "x", "total": 4, "done": 2, "failed": 0,
            "current": {"index": 2, "config": "c", "instance": "i", "seed": 0, "started_at": now - 10},
            "runs": [{"status": "done", "seconds": 100.0}, {"status": "done", "seconds": 300.0},
                     {"status": "running"}, {"status": "pending"}]}
    prog.update(over)
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / C.PROGRESS).write_text(json.dumps(prog), encoding="utf-8")
    return prog


# ---- runner, progress, ETA ----------------------------------------------------------------------------------------
def test_runner_counts_outcomes_and_continues_after_a_failed_run(tmp_path):
    spec = C.load_spec(write_spec(tmp_path, name="c1", runner="capopt.campaign:sleep_runner", time_limit=5,
                                  runs=[{"instance": "a", "seconds": 0.05}, {"instance": "b", "fail": True},
                                        {"instance": "c", "seconds": 0.05}]))
    data = C.run(spec, tmp_path / "c1", heartbeat_s=0.05)
    assert data["state"] == "complete" and data["done"] == 3 and data["failed"] == 1
    assert [r["status"] for r in data["runs"]] == ["done", "failed", "done"]
    assert "requested failure" in data["runs"][1]["error"]
    on_disk = json.loads((tmp_path / "c1" / C.PROGRESS).read_text())
    assert on_disk["done"] == 3 and on_disk["pid"] == os.getpid() and on_disk["eta_seconds"] == 0.0
    assert C.status(tmp_path / "c1")["state"] == "COMPLETE"


def test_cross_product_spec(tmp_path):
    s = C.load_spec(write_spec(tmp_path, name="x", configs=["a", "b"], instances=["i", "j", "k"], seeds=[0, 1]))
    assert len(s["runs"]) == 12 and s["runner"] == "capopt.campaign:bench_runner" and s["time_limit"] == 900.0


def test_eta_uses_median_run_time_and_current_run():
    prog = {"total": 4, "done": 2, "current": {"started_at": 1000.0},
            "runs": [{"status": "done", "seconds": 100.0}, {"status": "done", "seconds": 300.0},
                     {"status": "running"}, {"status": "pending"}]}
    # median 200 s x 2 runs left, less the 50 s the current run has used
    assert C.eta_seconds(prog, now=1050.0) == pytest.approx(350.0)
    assert C.eta_seconds(dict(prog, done=4), now=1050.0) == 0.0
    assert C.eta_seconds(dict(prog, runs=[{"status": "running"}]), now=1050.0) is None


def test_heartbeat_thread_keeps_the_file_fresh(tmp_path):
    spec = C.load_spec(write_spec(tmp_path, name="hb", runner="capopt.campaign:sleep_runner", time_limit=5,
                                  runs=[{"instance": "a", "seconds": 0.6}]))
    seen = []
    import threading
    t = threading.Thread(target=C.run, args=(spec, tmp_path / "hb"), kwargs={"heartbeat_s": 0.1})
    t.start()
    for _ in range(5):
        time.sleep(0.1)
        p = C.read_json(tmp_path / "hb" / C.PROGRESS)
        if p:
            seen.append(p["last_heartbeat"])
    t.join()
    assert len(set(seen)) >= 2  # the heartbeat moved while the run was busy


def test_solve_runner_on_tiny_synthetic_instances(tmp_path):
    runs = []
    for seed in (0, 1):
        path = write_instance(tmp_path, S.tiny(5, 8, 100, seed, True))
        runs.append({"config": "solve", "instance": str(path), "seed": 0, "workers": 2,
                     "options": {"n_leaves": 4, "quick_seconds": 5}})
    spec = C.load_spec(write_spec(tmp_path, name="tiny", runner="capopt.campaign:solve_runner", time_limit=60,
                                  runs=runs))
    data = C.run(spec, tmp_path / "tiny", heartbeat_s=1, out_dir=tmp_path / "out")
    assert data["state"] == "complete" and data["failed"] == 0
    assert all(r["proven"] for r in data["runs"])
    assert len(list((tmp_path / "out" / "solve").glob("*.json"))) == 2


# ---- status classification -----------------------------------------------------------------------------------------
def test_status_running_and_percent(tmp_path):
    fake_progress(tmp_path)
    st = C.status(tmp_path)
    assert st["state"] == "RUNNING" and st["pct"] == 50.0 and st["done"] == 2
    assert "2/4 (50%)" in C.describe(st)


def test_stale_heartbeat_is_stalled(tmp_path):
    fake_progress(tmp_path, last_heartbeat=time.time() - 400)
    st = C.status(tmp_path)
    assert st["state"] == "STALLED" and "no heartbeat" in st["reason"]


def test_dead_runner_pid_is_stalled(tmp_path):
    fake_progress(tmp_path, pid=999_999_999)  # no such process
    st = C.status(tmp_path)
    assert st["state"] == "STALLED" and "not running" in st["reason"]


def test_overdue_run_is_stalled(tmp_path):
    fake_progress(tmp_path, time_limit=10.0,
                  current={"index": 2, "config": "c", "instance": "i", "seed": 0, "started_at": time.time() - 400})
    st = C.status(tmp_path)
    assert st["state"] == "STALLED" and "past its 10s limit" in st["reason"]


def test_complete_and_failed(tmp_path):
    fake_progress(tmp_path / "a", state="complete", done=4, current=None, pid=999_999_999)
    assert C.status(tmp_path / "a")["state"] == "COMPLETE"  # a finished runner may be gone: that's fine
    fake_progress(tmp_path / "b", state="failed", error="boom")
    st = C.status(tmp_path / "b")
    assert st["state"] == "FAILED" and st["reason"] == "boom"


def test_not_launched_launching_and_not_started(tmp_path):
    assert C.status(tmp_path / "none")["state"] == "NOT_LAUNCHED"
    d = tmp_path / "l"
    d.mkdir()
    (d / C.LAUNCH).write_text(json.dumps({"launched_at": time.time() - 5}))
    assert C.status(d)["state"] == "LAUNCHING"
    (d / C.LAUNCH).write_text(json.dumps({"launched_at": time.time() - 61}))
    assert C.status(d)["state"] == "NOT_STARTED"


def test_status_cli_exit_codes(tmp_path):
    fake_progress(tmp_path / "ok")
    assert cli.main(["status", str(tmp_path / "ok")]) == 0
    fake_progress(tmp_path / "bad", last_heartbeat=time.time() - 400)
    assert cli.main(["status", str(tmp_path / "bad")]) == 1
    assert cli.main(["status", "nope", "--root", str(tmp_path)]) == 2


def test_pid_alive():
    assert C.pid_alive(os.getpid())
    assert not C.pid_alive(999_999_999) and not C.pid_alive(None) and not C.pid_alive(0)


# ---- watch ---------------------------------------------------------------------------------------------------------
def test_watch_emits_one_line_per_state_change(tmp_path):
    fake_progress(tmp_path, done=1)
    lines = []
    assert C.watch(tmp_path, interval=0, emit=lines.append, max_polls=3) == 2  # nothing changes: one STARTED line
    assert len(lines) == 2 and lines[0].startswith("STARTED") and lines[1].startswith("PROGRESS 1/4")
    lines.clear()
    fake_progress(tmp_path, last_heartbeat=time.time() - 400)
    C.watch(tmp_path, interval=0, emit=lines.append, max_polls=3)
    assert [ln.split()[0] for ln in lines] == ["STALLED"]  # reported once, not every poll
    lines.clear()
    fake_progress(tmp_path, state="complete", done=4, current=None)
    assert C.watch(tmp_path, interval=0, emit=lines.append) == 0
    assert lines[-1].startswith("COMPLETE")


# ---- launch-and-verify ---------------------------------------------------------------------------------------------
def test_launch_rejects_a_bad_spec_before_spawning(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(C.LaunchError, match="bad campaign spec"):
        C.launch(bad, tmp_path)
    empty = write_spec(tmp_path, name="empty", runs=[])
    with pytest.raises(C.LaunchError):
        C.launch(empty, tmp_path)
    no_runner = write_spec(tmp_path, name="nr", runner="capopt.campaign:does_not_exist", runs=[{"instance": "a"}])
    with pytest.raises(C.LaunchError, match="does_not_exist"):
        C.launch(no_runner, tmp_path)
    assert cli.main(["campaign", "launch", "--spec", str(bad), "--root", str(tmp_path)]) == 1


def test_launch_fails_loudly_when_the_runner_cannot_start(tmp_path, monkeypatch):
    spec = write_spec(tmp_path, name="dies", runner="capopt.campaign:sleep_runner", runs=[{"instance": "a"}])
    monkeypatch.setattr(sys, "executable", sys.executable + "_missing_interpreter")
    with pytest.raises(C.LaunchError, match="could not start the runner"):
        C.launch(spec, tmp_path, verify_s=5)


def test_launch_verify_then_kill_is_detected_as_stalled(tmp_path):
    spec = write_spec(tmp_path, name="live", runner="capopt.campaign:sleep_runner", time_limit=60,
                      runs=[{"instance": "a", "seconds": 30}, {"instance": "b", "seconds": 30}])
    info = C.launch(spec, tmp_path, verify_s=60, heartbeat_s=1)
    cdir = tmp_path / "live"
    try:
        assert C.status(cdir)["state"] == "RUNNING"
    finally:  # simulate the runner dying mid-campaign
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(info["pid"])], capture_output=True)
        else:
            os.kill(info["pid"], signal.SIGKILL)
    for _ in range(50):
        if not C.pid_alive(info["pid"]):
            break
        time.sleep(0.1)
    st = C.status(cdir)
    assert st["state"] == "STALLED" and "not running" in st["reason"]
