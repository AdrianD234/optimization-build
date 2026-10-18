"""E55: MIR cuts on the layers' shortfall rows are valid and keep solves exact."""
import numpy as np
import pytest
from conftest import write_instance
from test_layers import layered

from capopt import instance as I
from capopt import solver
from capopt import synthetic as S
from capopt.evaluate import evaluate
from capopt.formulation import last_year_model
from capopt.layers import mir_loop, shortfall_mir
from capopt.solve import new_highs


def _lp_point(model):
    h = new_highs(1)
    h.setOptionValue("output_flag", False)
    h.setOptionValue("solve_relaxation", True)
    model.builder.to_highs(h)
    h.run()
    return np.asarray(h.getSolution().col_value)


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("encoding,balance", [("step", "cumulative"), ("pulse", "recursive")])
def test_shortfall_mir_cuts_hold_at_every_feasible_schedule(seed, encoding, balance):
    inst = I.from_dict(layered(seed, kinds=("short",)))
    rng = np.random.default_rng(seed)
    L = inst.T - 1
    model = last_year_model(inst, L, encoding, balance)
    _, rows = mir_loop(model, 3)
    cuts = rows + shortfall_mir(model, _lp_point(model), min_violation=-np.inf)  # unviolated ones too
    assert cuts
    checked = 0
    for _ in range(3000):
        st = np.array([rng.choice(a) for a in inst.allowed])
        if not evaluate(inst, st)["feasible"]:
            continue
        vec = model.solution_vector(st)
        if vec is None:
            continue
        for lo, hi, cols, vals in cuts:
            act = float(np.dot(vec[cols], vals))
            assert lo - 1e-6 * max(1.0, abs(lo)) <= act <= hi
        checked += 1
    assert checked >= 5


@pytest.mark.parametrize("seed", [0, 3])
def test_layer_cuts_keep_solves_exact(tmp_path, seed):
    raw = layered(seed)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=6, layer_cuts=3)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    r = solver.solve(str(path), workers=2, time_limit=60, preset="0.5pct", n_leaves=6, layer_cuts=3)
    assert r.proven and r.objective - bf <= 5e-3 * abs(r.objective) + 1e-6 and r.dual_bound <= bf + 1e-9 * abs(bf)
