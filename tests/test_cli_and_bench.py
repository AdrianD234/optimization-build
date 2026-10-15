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


def _record(cfg, name, proof, best_known=-7408929413.924332):
    return {"config": cfg, "instance": name, "time_to_1pct": 3.0, "time_to_0.1pct": 3.0, "time_to_best_known": 70.0,
            "time_to_proof": proof, "seconds": proof or 900.0, "best_known": best_known,
            "referee": {"feasible": True, "objective": best_known}}


def test_public_report_compares_with_the_highs_baseline_only(tmp_path):
    for cfg, proof in (("capopt_parallel", 80.0), ("highs_reference", 320.0)):
        (tmp_path / cfg).mkdir()
        (tmp_path / cfg / "P100_raw_penalised_s0.json").write_text(json.dumps(_record(cfg, "P100_raw_penalised", proof)))
    text = bench.write_report(tmp_path, tmp_path / "B.md")
    assert "vs HiGHS baseline" in text and "| I3 |" in text and "4.0x" in text
    assert "MindOpt cold" not in text  # MindOpt's recorded runs are private


def test_private_report_includes_mindopt_when_present(tmp_path, monkeypatch):
    fake = {"instances": {n: {"best_known_objective": -1.0, "mindopt_cold": {
        "time_to_within_1pct_s": 5, "time_to_within_0.1pct_s": 30, "trace": [[200, -1.0]], "status": "OPTIMAL",
        "seconds": 240.0, "gap": 0.0}} for n in I.INSTANCE_NAMES}}
    f = tmp_path / "benchmark_results.json"
    f.write_text(json.dumps(fake))
    monkeypatch.setattr(bench, "MINDOPT_RESULTS", f)
    mo = bench.mindopt_rows(f)
    assert set(mo) == set(I.INSTANCE_NAMES) and mo["P100_raw_penalised"]["time_to_proof"] == 240.0
    (tmp_path / "capopt_parallel").mkdir()
    (tmp_path / "capopt_parallel" / "P100_raw_penalised_s0.json").write_text(
        json.dumps(_record("capopt_parallel", "P100_raw_penalised", 80.0)))
    text = bench.write_report(tmp_path, tmp_path / "P.md", include_mindopt=True)
    assert "MindOpt cold" in text and "3.0x" in text


def test_mindopt_rows_empty_without_private_data(tmp_path):
    assert bench.mindopt_rows(tmp_path / "missing.json") == {}


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


def test_holdout_instances_resolve_by_name_and_path(tmp_path, monkeypatch):
    raw = S.perturb("P100_growth_nopenalty", 7)
    monkeypatch.setattr(I, "HOLDOUT", tmp_path)
    assert cli.main(["holdout", "--seeds", "7", "--instances", "P100_growth_nopenalty", "--out-dir", str(tmp_path)]) == 0
    path = tmp_path / f"{raw['name']}.json"
    assert json.loads(path.read_text()) == raw and I.holdout_names() == [raw["name"]]
    assert I.path_of(raw["name"]) == path and I.name_of(str(path)) == raw["name"]
    assert I.path_of("P100_raw_penalised") == I.PACK / "instances" / "P100_raw_penalised.json"
    assert I.best_known_objective(raw["name"]) is None
    starts = I.best_known("P100_growth_nopenalty")["starts"]  # perturbing spend can make it infeasible, but it scores
    assert "feasible" in bench.referee_score(str(path), starts)


def test_holdout_report_retargets_to_best_proven(tmp_path):
    def rec(cfg, obj, proven, trace, proof):
        return {"config": cfg, "instance": "X_perturbed_s1", "objective": obj, "proven": proven, "trace": trace,
                "time_to_proof": proof, "time_limit": 900.0, "referee": {"feasible": True}}
    runs = [rec("capopt_parallel", -100.0, True, [(1.0, -99.5), (8.0, -100.0)], 10.0),
            rec("capopt_quick_nocut", -100.0, True, [(0.5, -99.0), (4.0, -100.0)], 5.0),
            rec("capopt_quick", -99.0, False, [(2.0, -99.0)], None)]
    for r in runs:
        d = tmp_path / "holdout" / r["config"]
        d.mkdir(parents=True)
        (d / "X_perturbed_s1_s0.json").write_text(json.dumps(r))
    h = bench.holdout_table(tmp_path / "holdout")
    assert h["targets"]["X_perturbed_s1"] == (-100.0, True) and not h["disagree"]
    c = h["cells"]["X_perturbed_s1"]
    assert c["capopt_parallel"]["time_to_1pct"] == 1.0 and c["capopt_parallel"]["time_to_target"] == 8.0
    assert c["capopt_quick"]["time_to_target"] is None
    s = h["summary"]
    assert s["capopt_quick_nocut"]["fastest_proofs"] == 1 and s["capopt_parallel"]["fastest_proofs"] == 0
    assert s["capopt_quick"]["sgm_time_to_proof"] == pytest.approx(900.0)  # a miss counts as the time limit
    assert bench.shifted_geomean([5.0]) == pytest.approx(5.0)
    text = "\n".join(bench.holdout_section(tmp_path / "holdout"))
    assert "Holdout: perturbed instances" in text and "**5.0**" in text and "agree across configs: yes" in text
