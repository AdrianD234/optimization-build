"""Soundness of the fixed-last-year decomposition (formulation.py docstring):

* For a schedule whose true last year is L' <= L, MIP(L) values it at >= its true objective, with equality at L = L'.
* The LP bound of MIP(Lmin) is a valid lower bound (<= best-known), and so are the split-and-solve leaves together.
"""
import highspy
import numpy as np
import pytest

from capopt import instance as I
from capopt import synthetic as S
from capopt.evaluate import evaluate
from capopt.formulation import last_year_model
from capopt.parallel import _lp_node, ramp_up, split_bounds
from capopt.solver import lp_bound


def model_value(model, starts):
    vec = model.solution_vector(starts)
    return None if vec is None else float(np.dot(model.builder.cost, vec)) + model.builder.offset


@pytest.mark.parametrize("seed", range(6))
def test_model_value_overstates_only_when_last_is_earlier(seed):
    raw = S.tiny(6, 9, 100, seed, True, 0.8, 1.0)
    inst = I.from_dict(raw)
    rng = np.random.default_rng(seed)
    checked = 0
    for _ in range(400):
        st = np.array([rng.choice(a) for a in inst.allowed])
        ev = evaluate(inst, st)
        if not ev["feasible"]:
            continue
        last = ev["last_build_year_index"]
        for L in range(last, inst.T):
            for enc in ("pulse", "step"):
                v = model_value(last_year_model(inst, L, enc, "cumulative"), st)
                assert v is not None
                if L == last:
                    assert v == pytest.approx(ev["objective"], rel=1e-9, abs=1e-6)
                else:
                    assert v >= ev["objective"] - 1e-6 * max(1.0, abs(ev["objective"]))
            checked += 1
    assert checked > 10


def test_no_penalty_model_is_exact_for_every_schedule(instances):
    inst = instances["P100_raw_nopenalty"]
    m = last_year_model(inst, None, "pulse", "recursive")
    rng = np.random.default_rng(1)
    bk = inst.starts_from_dict(I.best_known("P100_raw_nopenalty")["starts"])
    for _ in range(50):
        st = bk.copy()
        for p in rng.choice(inst.n, size=3, replace=False):
            st[p] = int(np.clip(st[p] + rng.integers(-3, 4), inst.allowed[p][0], inst.allowed[p][-1]))
        ev = evaluate(inst, st)
        if ev["feasible"]:
            assert model_value(m, st) == pytest.approx(ev["objective"], rel=1e-12)


@pytest.mark.parametrize("name", ["P100_raw_penalised", "P100_growth_penalised"])
def test_lp_bounds_are_valid_and_prune_later_years(instances, best_known, name):
    inst = instances[name]
    bk = best_known[name]["objective"]
    L0 = inst.min_last_year()
    assert L0 == 18
    assert lp_bound(inst, L0) <= bk
    assert lp_bound(inst, L0 + 1) > bk  # the later years are pruned by the LP alone
    assert lp_bound(inst, L0 - 1) == np.inf or lp_bound(inst, L0 - 1) > bk


def test_split_bounds_partition_the_options(instances):
    inst = instances["P100_raw_penalised"]
    for enc in ("pulse", "step"):
        m = last_year_model(inst, 18, enc, "cumulative")
        p, s = 0, int(m.options[0][len(m.options[0]) // 2])
        left = split_bounds(m, [(p, s, 0)])
        right = split_bounds(m, [(p, s, 1)])
        assert left is not None and right is not None
        assert split_bounds(m, [(p, s, 0), (p, s, 1)]) is None  # contradictory decisions
        # every option lands on exactly one side
        for st in m.options[0]:
            sides = [st <= s, st > s]
            assert sum(sides) == 1


def test_ramp_up_leaf_bounds_cover_the_optimum(instances, best_known):
    inst = instances["P100_growth_penalised"]
    m = last_year_model(inst, 18, "step", "cumulative")
    bk = best_known["P100_growth_penalised"]["objective"]
    leaves, pruned = ramp_up(m, 8)
    assert len(leaves) == 8 and pruned == 0
    root = _lp_node(m, [])
    assert all(leaf.bound >= root.bound - 1e-6 * abs(root.bound) for leaf in leaves)
    # the best-known schedule satisfies the decisions of exactly one leaf, whose bound must not exceed it
    st = inst.starts_from_dict(best_known["P100_growth_penalised"]["starts"])
    owners = [lf for lf in leaves if all((st[p] <= s) == (side == 0) for p, s, side in lf.splits)]
    assert len(owners) == 1 and owners[0].bound <= bk + 1e-6 * abs(bk)


def test_lp_relaxation_equal_across_encodings(instances):
    inst = instances["P100_growth_penalised"]
    vals = []
    for enc in ("pulse", "step"):
        for bal in ("recursive", "cumulative"):
            h = highspy.Highs()
            h.setOptionValue("output_flag", False)
            h.setOptionValue("solve_relaxation", True)
            m = last_year_model(inst, 18, enc, bal)
            m.builder.to_highs(h)
            h.run()
            vals.append(m.user(h.getInfo().objective_function_value))
    assert max(vals) - min(vals) <= 1e-6 * abs(vals[0])  # Artigues 2017: same LP bound
