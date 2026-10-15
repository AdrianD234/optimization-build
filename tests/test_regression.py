"""Slow regression (run with `pytest -m slow` on the 20-thread workstation): the full pipeline must prove the penalised
instances optimal and reproduce the best-known objectives."""
import pytest

from capopt import instance as I
from capopt import solver
from capopt.bench import referee_score

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("name,limit", [("P100_growth_penalised", 120), ("P100_raw_penalised", 400)])
def test_penalised_instances_proven_optimal(name, limit):
    r = solver.solve(name, time_limit=limit)
    bk = I.best_known(name)["objective"]
    assert r.proven
    assert r.objective == pytest.approx(bk, rel=1e-9)
    ref = referee_score(name, r.starts)
    assert ref["feasible"] and ref["objective"] == pytest.approx(r.objective, rel=1e-12)
    assert r.seconds < limit
