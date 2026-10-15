"""Cover cuts must be valid (never cut off the best-known schedule) and objective scaling must be exact."""
import math

import numpy as np
import pytest

from capopt import instance as I
from capopt.cuts import root_loop, separate
from capopt.formulation import last_year_model, reference_bigm
from capopt.solver import lp_bound


@pytest.mark.parametrize("name", I.INSTANCE_NAMES)
@pytest.mark.parametrize("encoding", ["pulse", "step"])
def test_cover_cuts_are_valid_and_monotone(instances, best_known, name, encoding):
    inst = instances[name]
    m = last_year_model(inst, 18 if inst.penalised else None, encoding, "cumulative")
    bounds, cuts = root_loop(m, rounds=6, max_cuts=40)
    assert all(b2 >= b1 - 1e-6 * abs(b1) for b1, b2 in zip(bounds, bounds[1:]))
    assert bounds[-1] <= best_known[name]["objective"] + 1e-6 * abs(bounds[-1])
    vec = m.solution_vector(inst.starts_from_dict(best_known[name]["starts"]))
    b = m.builder
    for hi, ri, rv in zip(b.row_hi, b.row_idx, b.row_val):
        assert float(np.dot(vec[ri], rv)) <= hi + 1e-6 * max(1.0, abs(hi))
    if name == "P100_raw_penalised":
        assert len(cuts) > 0 and bounds[-1] > bounds[0]


def test_separation_finds_nothing_at_an_integer_point(instances, best_known):
    inst = instances["P100_raw_penalised"]
    m = last_year_model(inst, 18, "pulse", "cumulative")
    vec = m.solution_vector(inst.starts_from_dict(best_known["P100_raw_penalised"]["starts"]))
    assert separate(m, vec) == []


@pytest.mark.parametrize("name", I.INSTANCE_NAMES)
def test_objective_scaling_is_a_power_of_two_and_lp_solves(instances, best_known, name):
    inst = instances[name]
    for m in (last_year_model(inst, 18 if inst.penalised else None, "step", "cumulative"), reference_bigm(inst)):
        k = -math.log2(m.builder.obj_scale)
        assert k == int(k) and k >= 0
        assert max(abs(c) for c in m.builder.cost) * m.builder.obj_scale <= 2.0 ** 28
        assert m.user(m.solver_units(123.25)) == 123.25
    b = lp_bound(inst, 18 if inst.penalised else inst.T - 1)
    assert np.isfinite(b) and b <= best_known[name]["objective"] + 1e-9 * abs(b)
