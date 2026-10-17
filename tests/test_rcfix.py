"""E40: reduced-cost start-window fixing never excludes a schedule that beats the cutoff."""
import itertools

import numpy as np
from conftest import write_instance

from capopt import instance as I
from capopt import solver
from capopt import synthetic as S
from capopt.evaluate import evaluate
from capopt.formulation import last_year_model
from capopt.parallel import rc_fixings
from capopt.solver import lp_bound


def model_value(model, starts):
    vec = model.solution_vector(starts)
    return None if vec is None else float(np.dot(model.builder.cost, vec)) + model.builder.offset


def satisfies(starts, splits):
    return all((starts[p] <= s) if side == 0 else (starts[p] > s) for p, s, side in splits)


def test_fixings_keep_every_schedule_below_the_cutoff():
    assert sum(_check_fixings(seed) for seed in (0, 1, 4, 7)) >= 2  # real fixings happen on most of them


def _check_fixings(seed):
    inst = I.from_dict(S.tiny(6, 9, 100, seed, True, 0.8, 1.0))
    L = inst.min_last_year()
    m = last_year_model(inst, L, "step", "cumulative")
    vals = []
    for st in itertools.product(*[list(a) for a in inst.allowed]):
        st = np.array(st)
        ev = evaluate(inst, st)
        if ev["feasible"] and ev["last_build_year_index"] <= L:
            vals.append((model_value(m, st), st))
    assert vals
    lp, best = lp_bound(inst, L), min(v for v, _ in vals)
    fixed_any = False
    for frac in (0.0, 0.25, 0.5, 1.0, 2.0):
        cut = best + frac * (best - lp) + 1e-9
        splits = rc_fixings(m, cut)
        fixed_any |= bool(splits)
        for v, st in vals:
            if v < cut - 1e-6 * max(1.0, abs(cut)):
                assert satisfies(st, splits), (cut, v, st, splits)
    return fixed_any


def test_rc_fix_preset_matches_brute_force(tmp_path):
    raw = S.tiny(6, 9, 100, 4, True, 0.8, 1.0)
    path = write_instance(tmp_path, raw)
    bf, st = S.brute_force(I.from_dict(raw))
    warm = tmp_path / "warm.json"
    import json
    warm.write_text(json.dumps({"starts": I.from_dict(raw).starts_to_dict(st)}), encoding="utf-8")
    for preset, gap in (("0.5pct", 5e-3), (None, 1e-9)):
        kw = {"preset": preset} if preset else {"rel_gap": 1e-9}
        r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4, rc_fix=True, **kw)
        assert r.proven and r.dual_bound <= bf + 1e-9 * abs(bf)
        assert r.objective - bf <= gap * abs(r.objective) + 1e-6
        r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4, rc_fix=True, warm_start=str(warm), **kw)
        assert r.proven and r.dual_bound <= bf + 1e-9 * abs(bf)
