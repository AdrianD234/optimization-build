"""Every formulation must agree with the evaluator on the best-known schedules."""
import numpy as np
import pytest

from capopt import instance as I
from capopt.evaluate import evaluate
from capopt.formulation import last_year_model, reference_bigm


def check_vector(model, vec, expected, tol=1e-6):
    b = model.builder
    obj = float(np.dot(b.cost, vec)) + b.offset
    assert obj == pytest.approx(expected, rel=1e-12, abs=1e-3)
    assert np.all(vec >= np.array(b.lb) - tol) and np.all(vec <= np.array(b.ub) + tol)
    for lo, hi, ri, rv in zip(b.row_lo, b.row_hi, b.row_idx, b.row_val):
        a = float(np.dot(vec[ri], rv)) if ri else 0.0
        assert lo - tol * max(1, abs(lo)) <= a <= hi + tol * max(1, abs(hi))


@pytest.mark.parametrize("name", I.INSTANCE_NAMES)
@pytest.mark.parametrize("encoding", ["pulse", "step"])
@pytest.mark.parametrize("balance", ["recursive", "cumulative"])
def test_last_year_model_matches_evaluator(instances, best_known, name, encoding, balance):
    inst = instances[name]
    st = inst.starts_from_dict(best_known[name]["starts"])
    ev = evaluate(inst, st)
    L = ev["last_build_year_index"] if inst.penalised else None
    m = last_year_model(inst, L, encoding, balance)
    vec = m.solution_vector(st)
    assert vec is not None
    check_vector(m, vec, ev["objective"])
    assert np.array_equal(m.starts_from_solution(vec), st)


@pytest.mark.parametrize("name", I.INSTANCE_NAMES)
def test_reference_bigm_matches_evaluator(instances, best_known, name):
    inst = instances[name]
    st = inst.starts_from_dict(best_known[name]["starts"])
    m = reference_bigm(inst)
    vec = m.solution_vector(st)
    check_vector(m, vec, evaluate(inst, st)["objective"])
    assert np.array_equal(m.starts_from_solution(vec), st)


def test_last_year_model_is_much_smaller(instances):
    inst = instances["P100_raw_penalised"]
    small = last_year_model(inst, inst.min_last_year(), "pulse", "recursive").builder
    big = reference_bigm(inst).builder
    assert len(small.cost) < len(big.cost) / 3 and len(small.row_lo) < len(big.row_lo) / 100


def test_schedule_not_fitting_model_is_rejected(instances, best_known):
    inst = instances["P100_raw_penalised"]
    st = inst.starts_from_dict(best_known["P100_raw_penalised"]["starts"])
    m = last_year_model(inst, 18, "step", "cumulative")
    late = st.copy()
    late[0] = inst.allowed[0][-1]  # finishes after year 18
    assert m.solution_vector(late) is None
