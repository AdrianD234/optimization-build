"""E37: the cash-floor bound, the threaded last-year screen and the certified-gap presets."""
import json

import numpy as np
import pytest
from conftest import write_instance

from capopt import instance as I
from capopt import solver
from capopt import synthetic as S
from capopt.bounds import cash_floor_bound
from capopt.evaluate import evaluate
from capopt.formulation import last_year_model
from capopt.solver import lp_bound, screen_last_years


def model_value(model, starts):
    vec = model.solution_vector(starts)
    return None if vec is None else float(np.dot(model.builder.cost, vec)) + model.builder.offset


@pytest.mark.parametrize("seed", range(4))
def test_cash_floor_bounds_every_schedule_of_every_last_year(seed):
    """floor(L) <= the model value at L of every schedule that fits L (= its objective when L is its last year)."""
    inst = I.from_dict(S.tiny(6, 9, 100, seed, True, 0.8, 1.0))
    rng = np.random.default_rng(seed)
    floors = {L: cash_floor_bound(inst, L) for L in range(inst.T)}
    checked = 0
    for _ in range(300):
        st = np.array([rng.choice(a) for a in inst.allowed])
        ev = evaluate(inst, st)
        if not ev["feasible"]:
            continue
        for L in range(ev["last_build_year_index"], inst.T):
            v = model_value(last_year_model(inst, L, "step", "cumulative"), st)
            assert floors[L] <= v + 1e-6 * max(1.0, abs(v))
            checked += 1
    assert checked > 20


@pytest.mark.parametrize("name", ["P100_raw_penalised", "P100_growth_penalised", "P100_raw_nopenalty"])
def test_cash_floor_never_beats_the_lp_bound(instances, name):
    """The floor relaxes the LP (its balance floor and best-PV credit are implied by the LP's rows)."""
    inst = instances[name]
    for L in range(inst.min_last_year(), inst.T, 3):
        f, lp = cash_floor_bound(inst, L), lp_bound(inst, L)
        assert f <= lp + 1e-9 * abs(lp)


def test_cash_floor_is_infinite_when_a_project_cannot_finish(instances):
    inst = instances["P100_raw_penalised"]
    late = max(int(a[0]) + int(z) for a, z in zip(inst.allowed, inst.last_nz))
    assert cash_floor_bound(inst, late - 1) == np.inf


def test_screen_matches_the_lp_where_the_floor_cannot_prune(instances, best_known):
    name = "P100_raw_penalised"
    inst, bk = instances[name], best_known[name]["objective"]
    Ls = list(range(inst.min_last_year(), inst.T))
    log: list[str] = []
    out = screen_last_years(inst, Ls, bk, threads=4, log=log)
    for L in Ls:
        f = cash_floor_bound(inst, L)
        if f >= bk:
            assert out[L] == f
        else:
            assert out[L] == pytest.approx(max(f, lp_bound(inst, L)), rel=1e-12)
    assert "screen:" in log[0]


def test_preset_certifies_a_good_warm_schedule_without_a_mip(tmp_path, best_known):
    name = "P100_growth_penalised"
    warm = tmp_path / "warm.json"
    warm.write_text(json.dumps({"starts": best_known[name]["starts"]}), encoding="utf-8")
    r = solver.solve(name, workers=2, time_limit=60, preset="1pct", warm_start=str(warm))
    bk = best_known[name]["objective"]
    assert r.proven and r.objective == pytest.approx(bk, rel=1e-12)
    assert r.dual_bound <= bk + 1e-9 * abs(bk)
    assert (r.objective - r.dual_bound) / abs(r.objective) <= 1e-2
    assert any("certifies the warm schedule" in line for line in r.log)


@pytest.mark.parametrize("preset,gap", [("1pct", 1e-2), ("0.5pct", 5e-3)])
@pytest.mark.parametrize("cfg", [(6, 9, 100, 4, True, 0.8, 1.0), (6, 9, 100, 7, True, 0.8, 1.0)])
def test_cold_preset_result_is_within_its_gap_and_the_bound_is_valid(tmp_path, preset, gap, cfg):
    raw = S.tiny(*cfg)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=2, time_limit=60, preset=preset, n_leaves=4)
    assert r.proven
    assert r.dual_bound <= bf + 1e-9 * abs(bf)  # E37: honest bounds (pruned subtrees keep their real bound)
    assert r.objective - r.dual_bound <= gap * abs(r.objective) + 1e-9
    assert (r.objective - bf) <= gap * abs(r.objective) + 1e-9


def test_explicit_arguments_override_the_preset(tmp_path):
    raw = S.tiny(5, 8, 100, 0, True, -0.04, 1e-4)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=2, time_limit=60, preset="1pct", rel_gap=1e-9, n_leaves=4)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError, match="unknown preset"):
        solver.solve("P100_growth_penalised", preset="fast")


def test_perturb_projects_changes_exactly_k_projects(instances):
    raw = instances["P100_raw_penalised"].raw
    new, ids = S.perturb_projects(raw, 3, 5, delay=2)
    changed = [a["id"] for a, b in zip(raw["projects"], new["projects"]) if a != b]
    assert sorted(changed) == sorted(ids) and len(ids) == 5
    for p in new["projects"]:
        assert len(p["pv_int"]) == len(p["allowed_starts"]) >= 1
    I.from_dict(new)  # still a valid instance


def test_repair_keeps_a_previous_plan_after_an_edit(instances, best_known):
    """E38: a start that is no longer allowed and a spend jump that drives the balance negative are both repaired,
    touching only a few projects; a feasible plan comes back unchanged."""
    name = "P100_growth_penalised"
    inst = instances[name]
    st = inst.starts_from_dict(best_known[name]["starts"])
    assert np.array_equal(solver.repair_schedule(inst, st), st)
    raw = json.loads(json.dumps(inst.raw))
    early = min(range(inst.n), key=lambda p: (st[p], -int(inst.spend[p].sum())))  # a big project starting first
    pr = raw["projects"][early]
    pr["spend_S"] = [3 * a for a in pr["spend_S"]]
    k = list(pr["allowed_starts"]).index(int(st[early]))  # its current start is no longer allowed
    pr["allowed_starts"], pr["pv_int"] = pr["allowed_starts"][k + 1:], pr["pv_int"][k + 1:]
    edited = I.from_dict(raw)
    assert not evaluate(edited, st)["feasible"]
    log: list[str] = []
    fixed = solver.repair_schedule(edited, st, log=log)
    assert fixed is not None and evaluate(edited, fixed)["feasible"]
    assert (fixed != st).sum() <= inst.n // 2
    assert "repaired" in log[-1]
