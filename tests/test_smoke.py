"""Smoke tests: end-to-end solves in seconds, checked against brute force and the referee."""
import numpy as np
import pytest
from conftest import write_instance

from capopt import instance as I
from capopt import solver
from capopt import synthetic as S
from capopt.evaluate import evaluate
from capopt.formulation import last_year_model
from capopt.solve import Trace, solve

pytestmark = pytest.mark.smoke

# (n, T, funding, seed, penalised, growth, pv_weight); seeds 4 and 7 have optima that end after Lmin
TINY = [
    (5, 8, 100, 0, True, -0.04, 1e-4),
    (5, 8, 100, 1, False, -0.04, 1e-4),
    (6, 9, 100, 4, True, 0.8, 1.0),
    (6, 9, 100, 7, True, 0.8, 1.0),
]


@pytest.mark.parametrize("cfg", TINY)
def test_sequential_pipeline_matches_brute_force(tmp_path, cfg):
    raw = S.tiny(*cfg)
    path = write_instance(tmp_path, raw)
    inst = I.from_dict(raw)
    bf, _ = S.brute_force(inst)
    r = solver.solve_sequential(str(path), time_limit=30)
    assert r.proven
    assert r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    assert evaluate(inst, inst.starts_from_dict(r.starts))["objective"] == pytest.approx(bf, rel=1e-9, abs=1e-6)


@pytest.mark.parametrize("cfg", [TINY[0], TINY[2]])
def test_parallel_pipeline_matches_brute_force(tmp_path, cfg):
    raw = S.tiny(*cfg)
    path = write_instance(tmp_path, raw)
    inst = I.from_dict(raw)
    bf, _ = S.brute_force(inst)
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4)
    assert r.proven
    assert r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)


def test_reference_bigm_on_tiny_matches_brute_force(tmp_path):
    raw = S.tiny(*TINY[3])
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve_reference_highs(str(path), time_limit=60, threads=1)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)


def test_benchmark_instance_short_run_is_feasible(referee, instances):
    inst = instances["P100_growth_penalised"]
    tr = Trace()
    r = solve(last_year_model(inst, 18, "step", "cumulative"), tr, time_limit=10)
    assert tr.best < np.inf and r.starts is not None
    rinst = referee.prepare(referee.load(I.PACK / "instances" / "P100_growth_penalised.json"))
    chk = referee.evaluate(rinst, inst.starts_to_dict(tr.best_starts))
    assert chk["feasible"] and chk["objective"] == pytest.approx(tr.best, rel=1e-12)
    assert [v for _, v, _ in tr.points] == sorted([v for _, v, _ in tr.points], reverse=True)


def test_warm_start_is_accepted(instances, best_known):
    inst = instances["P100_growth_penalised"]
    st = inst.starts_from_dict(best_known["P100_growth_penalised"]["starts"])
    tr = Trace()
    solve(last_year_model(inst, 18, "pulse", "recursive"), tr, time_limit=3, start=st)
    assert tr.best <= best_known["P100_growth_penalised"]["objective"] + 1e-6 * abs(tr.best)
