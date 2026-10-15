"""Slow regression (run with `pytest -m slow` on the 20-thread workstation): the default pipeline must prove every
instance optimal, reproduce the optimum, and be scored identically by the pack's referee."""
import json
from pathlib import Path

import pytest

from capopt import instance as I
from capopt import solver
from capopt.bench import referee_score

pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[1]


def optimum(name):
    """Pack best-known, unless capopt has since proven a better optimum (stored in benchmarks/solutions)."""
    ours = ROOT / "benchmarks" / "solutions" / f"{name}_proven_optimal.json"
    if ours.exists():
        return json.loads(ours.read_text())["objective"]
    return I.best_known(name)["objective"]


@pytest.mark.parametrize("name,limit", [("P100_growth_nopenalty", 60), ("P100_growth_penalised", 120),
                                        ("P100_raw_nopenalty", 300), ("P100_raw_penalised", 400)])
def test_default_pipeline_proves_every_instance(name, limit):
    r = solver.solve(name, time_limit=limit)
    assert r.proven
    assert r.objective == pytest.approx(optimum(name), rel=1e-9)
    assert r.objective <= I.best_known(name)["objective"] + 1e-9 * abs(r.objective)  # never worse than the pack
    ref = referee_score(name, r.starts)
    assert ref["feasible"] and ref["objective"] == pytest.approx(r.objective, rel=1e-12)
    assert r.seconds < limit
