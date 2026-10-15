"""CLI and benchmark-report plumbing."""
import json

import pytest
from conftest import write_instance

from capopt import bench, cli
from capopt import instance as I
from capopt import synthetic as S
from capopt.evaluate import evaluate


@pytest.mark.smoke
def test_cli_solve_writes_valid_schedule(tmp_path):
    raw = S.tiny(5, 8, 100, 0, True)
    path = write_instance(tmp_path, raw)
    out = tmp_path / "sched.json"
    assert cli.main(["solve", str(path), "--out", str(out), "--workers", "2", "--time-limit", "60", "--leaves", "4"]) == 0
    sched = json.loads(out.read_text())["starts"]
    inst = I.from_dict(raw)
    assert evaluate(inst, inst.starts_from_dict(sched))["feasible"]


def test_time_to():
    trace = [(1.0, -90.0), (2.0, -99.5), (5.0, -100.0)]
    assert bench.time_to(trace, -100.0, 0.01) == 2.0
    assert bench.time_to(trace, -100.0, 0.001) == 5.0
    assert bench.time_to(trace, -101.0, 1e-6) is None


def test_median_counts_misses():
    assert bench._median([1.0, 2.0, 3.0]) == 2.0
    assert bench._median([1.0, None, None]) is None
    assert bench._median([1.0, 3.0, None]) == 3.0
    assert bench._median([]) is None


def test_mindopt_rows_and_report(tmp_path):
    mo = bench.mindopt_rows()
    assert set(mo) == set(I.INSTANCE_NAMES)
    assert mo["P100_raw_penalised"]["time_to_proof"] == pytest.approx(231.5)
    rec = {"config": "capopt_parallel", "instance": "P100_raw_penalised", "time_to_1pct": 3.0, "time_to_0.1pct": 3.0,
           "time_to_best_known": 70.0, "time_to_proof": 80.0, "best_known": -7408929413.924332,
           "referee": {"feasible": True, "objective": -7408929413.924332}}
    (tmp_path / "capopt_parallel").mkdir()
    (tmp_path / "capopt_parallel" / "P100_raw_penalised_s0.json").write_text(json.dumps(rec))
    text = bench.write_report(tmp_path, tmp_path / "B.md")
    assert "MindOpt cold" in text and "| I3 |" in text and "2.9x" in text


def test_referee_score_rejects_bad_schedule():
    bk = I.best_known("P100_raw_penalised")["starts"]
    assert bench.referee_score("P100_raw_penalised", bk)["feasible"]
    bad = {k: 0 for k in bk}
    assert not bench.referee_score("P100_raw_penalised", bad)["feasible"]


def test_watchdog_record_rebuilt_from_streamed_trace(tmp_path, monkeypatch):
    from capopt.solve import sink
    name = "P100_raw_penalised"
    bk = I.best_known(name)
    inst = I.load(name)
    trace = tmp_path / "t.jsonl"
    monkeypatch.setenv("CAPOPT_TRACE_FILE", str(trace))
    st = inst.starts_from_dict(bk["starts"])
    sink(5.0, bk["objective"] * 0.995, st)  # within 1% (not really this schedule's value; only the times matter here)
    sink(9.0, bk["objective"], st)
    sink(7.0, bk["objective"] * 0.5, st)  # out of order and worse: ignored
    rec = bench._record_from_trace("highs_reference", name, 0, 10.0, trace, 190.0)
    assert rec["killed"] and not rec["proven"] and rec["time_to_proof"] is None
    assert rec["time_to_1pct"] == 5.0 and rec["time_to_best_known"] == 9.0
    assert rec["referee"]["feasible"]
