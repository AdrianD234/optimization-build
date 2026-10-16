"""Year-block Lagrangian bounds (capopt.bounds): exact strong-duality self-check and validity against known optima."""
import pytest

from capopt import instance as I
from capopt.bounds import block_bound
from capopt.formulation import last_year_model


@pytest.mark.parametrize("name", ["P100_raw_penalised", "P100_growth_penalised"])
def test_relaxed_block_bound_equals_the_lp_bound(name):
    """With X's integrality relaxed the Lagrangian at the LP duals is the LP value: checks the dual sign convention."""
    m = last_year_model(I.load(name), 18, "step", "cumulative")
    r = block_bound(m, None, integral=False)
    assert r["dropped_multipliers"] == 0
    assert r["bound"] == pytest.approx(r["lp_bound"], rel=1e-12, abs=1e-6)


@pytest.mark.parametrize("name", ["P100_raw_penalised", "P100_growth_penalised"])
def test_block_bounds_sit_between_the_lp_bound_and_the_optimum(name):
    m = last_year_model(I.load(name), 18, "step", "cumulative")
    opt = I.best_known(name)["objective"]  # its schedule ends in L = 18, so OPT(18) <= opt
    for keep in ({14, 15, 16, 17}, set(range(8, 18))):
        r = block_bound(m, keep, time_limit=5)
        assert r["lp_bound"] - 1e-9 * abs(opt) <= r["bound"] <= opt + 1e-9 * abs(opt)
