"""The compiled kernel's objective must equal the evaluator; heuristics must return feasible, non-worse schedules."""
import numpy as np
import pytest

from capopt import instance as I
from capopt.evaluate import objective
from capopt.heuristics import kernel as K


@pytest.mark.parametrize("name", I.INSTANCE_NAMES)
def test_kernel_cost_equals_evaluator(instances, best_known, name):
    inst = instances[name]
    pk = K.pack(inst)
    rng = np.random.default_rng(7)
    base = inst.starts_from_dict(best_known[name]["starts"])
    assert K.cost(pk, base, inst.penalised) == objective(inst, base)
    for _ in range(300):
        st = base.copy()
        for p in rng.choice(inst.n, size=4, replace=False):
            st[p] = rng.integers(pk.lo[p], pk.hi[p] + 1)
        a, b = K.cost(pk, st, inst.penalised), objective(inst, st)
        assert (a == b == np.inf) or a == pytest.approx(b, rel=1e-12, abs=1e-6)


def test_pack_respects_last_year(instances):
    inst = instances["P100_raw_penalised"]
    pk = K.pack(inst, 18)
    assert np.all(pk.hi + inst.last_nz <= 18)
    assert np.all(pk.lo <= pk.hi)


@pytest.mark.parametrize("name", ["P100_growth_penalised", "P100_raw_nopenalty"])
def test_anneal_and_descend_never_worsen(instances, best_known, name):
    inst = instances[name]
    pk = K.pack(inst)
    start = inst.starts_from_dict(best_known[name]["starts"])
    v0 = K.cost(pk, start, inst.penalised)
    free = np.ones(inst.n, dtype=np.bool_)
    best, st, _ = K.anneal(start.copy(), free, 20000, 1e-4, 1e-8, 3, *K.args(pk, inst.penalised))
    assert best <= v0 and objective(inst, st) == pytest.approx(best, rel=1e-12)
    v, st2 = K.descend(st, *K.args(pk, inst.penalised))
    assert v <= best and objective(inst, st2) == pytest.approx(v, rel=1e-12)
