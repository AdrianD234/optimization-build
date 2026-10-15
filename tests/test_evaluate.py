"""Parity: capopt's evaluator must agree exactly with the pack's referee."""
import json

import numpy as np
import pytest

from capopt import instance as I
from capopt.evaluate import evaluate, gap


def ref_eval(referee, name, starts):
    inst = referee.prepare(referee.load(I.PACK / "instances" / f"{name}.json"))
    return referee.evaluate(inst, starts)


@pytest.mark.parametrize("name", I.INSTANCE_NAMES)
@pytest.mark.parametrize("kind", ["best_known", "reference"])
def test_pack_solutions_match_referee(referee, instances, name, kind):
    sol = json.loads((I.PACK / "solutions" / f"{name}_{kind}.json").read_text(encoding="utf-8"))
    inst = instances[name]
    ours = evaluate(inst, inst.starts_from_dict(sol["starts"]))
    theirs = ref_eval(referee, name, sol["starts"])
    assert ours["feasible"] and theirs["feasible"]
    assert ours["objective"] == theirs["objective"]
    assert ours["last_build_year_index"] == theirs["last_build_year_index"]
    assert ours["closing_balance_units"] == theirs["closing_balance_units"]


@pytest.mark.parametrize("name", I.INSTANCE_NAMES)
def test_best_known_objective_recorded_correctly(instances, best_known, name):
    inst = instances[name]
    bk = best_known[name]
    r = evaluate(inst, inst.starts_from_dict(bk["starts"]))
    assert r["objective"] == pytest.approx(bk["objective"], rel=1e-12)


@pytest.mark.parametrize("name", I.INSTANCE_NAMES)
def test_random_and_perturbed_schedules_match_referee(referee, instances, best_known, name):
    inst = instances[name]
    rinst = referee.prepare(referee.load(I.PACK / "instances" / f"{name}.json"))
    rng = np.random.default_rng(42)
    base = inst.starts_from_dict(best_known[name]["starts"])
    n_feasible = 0
    for k in range(1000):
        if k % 2:  # uniformly random allowed starts (mostly infeasible)
            st = np.array([rng.choice(a) for a in inst.allowed])
        else:  # small perturbation of the best-known schedule (often feasible)
            st = base.copy()
            for p in rng.choice(inst.n, size=3, replace=False):
                st[p] = int(np.clip(st[p] + rng.integers(-2, 3), inst.allowed[p][0], inst.allowed[p][-1]))
        ours = evaluate(inst, st)
        theirs = referee.evaluate(rinst, inst.starts_to_dict(st))
        assert ours["feasible"] == theirs["feasible"]
        if ours["feasible"]:
            n_feasible += 1
            assert ours["objective"] == theirs["objective"]
    assert n_feasible > 20


def test_infeasible_cases(instances):
    inst = instances["P100_raw_penalised"]
    r = evaluate(inst, np.zeros(inst.n, dtype=np.int64))  # everything at once: the balance goes negative
    assert not r["feasible"] and "negative" in r["reason"]
    assert not evaluate(inst, np.full(inst.n, 59))["feasible"]  # outside every allowed window
    assert not evaluate(inst, np.zeros(5, dtype=np.int64))["feasible"]  # wrong length


def test_gap():
    assert gap(-99.0, -100.0) == pytest.approx(0.01)
    assert gap(-100.0, -100.0) == 0.0
