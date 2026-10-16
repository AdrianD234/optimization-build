"""Smoke tests: end-to-end solves in seconds, checked against brute force and the referee."""
import numpy as np
import pytest
from conftest import write_instance

from capopt import instance as I
from capopt import solver
from capopt import synthetic as S
from capopt.evaluate import evaluate
from capopt.formulation import last_year_model
from capopt.solve import Trace, solve

pytestmark = pytest.mark.smoke

# (n, T, funding, seed, penalised, growth, pv_weight); seeds 4 and 7 have optima that end after Lmin
TINY = [
    (5, 8, 100, 0, True, -0.04, 1e-4),
    (5, 8, 100, 1, False, -0.04, 1e-4),
    (6, 9, 100, 4, True, 0.8, 1.0),
    (6, 9, 100, 7, True, 0.8, 1.0),
]


@pytest.mark.parametrize("cfg", TINY)
def test_sequential_pipeline_matches_brute_force(tmp_path, cfg):
    raw = S.tiny(*cfg)
    path = write_instance(tmp_path, raw)
    inst = I.from_dict(raw)
    bf, _ = S.brute_force(inst)
    r = solver.solve_sequential(str(path), time_limit=30)
    assert r.proven
    assert r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    assert evaluate(inst, inst.starts_from_dict(r.starts))["objective"] == pytest.approx(bf, rel=1e-9, abs=1e-6)


@pytest.mark.parametrize("cfg", [TINY[0], TINY[2]])
def test_parallel_pipeline_matches_brute_force(tmp_path, cfg):
    raw = S.tiny(*cfg)
    path = write_instance(tmp_path, raw)
    inst = I.from_dict(raw)
    bf, _ = S.brute_force(inst)
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4)
    assert r.proven
    assert r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)


def test_reference_bigm_on_tiny_matches_brute_force(tmp_path):
    raw = S.tiny(*TINY[3])
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve_reference_highs(str(path), time_limit=60, threads=1)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)


def test_benchmark_instance_short_run_is_feasible(referee, instances):
    inst = instances["P100_growth_penalised"]
    tr = Trace()
    r = solve(last_year_model(inst, 18, "step", "cumulative"), tr, time_limit=10)
    assert tr.best < np.inf and r.starts is not None
    rinst = referee.prepare(referee.load(I.PACK / "instances" / "P100_growth_penalised.json"))
    chk = referee.evaluate(rinst, inst.starts_to_dict(tr.best_starts))
    assert chk["feasible"] and chk["objective"] == pytest.approx(tr.best, rel=1e-12)
    assert [v for _, v, _ in tr.points] == sorted([v for _, v, _ in tr.points], reverse=True)


def test_warm_start_is_accepted(instances, best_known):
    inst = instances["P100_growth_penalised"]
    st = inst.starts_from_dict(best_known["P100_growth_penalised"]["starts"])
    tr = Trace()
    solve(last_year_model(inst, 18, "pulse", "recursive"), tr, time_limit=3, start=st)
    assert tr.best <= best_known["P100_growth_penalised"]["objective"] + 1e-6 * abs(tr.best)


def test_parallel_with_lns_and_resplitting_matches_brute_force(tmp_path):
    raw = S.tiny(6, 9, 100, 4, True, 0.8, 1.0)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=3, time_limit=60, n_leaves=4, slice_seconds=0.5, lns_workers=1, lns_seconds=2)
    assert r.proven
    assert r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)


def test_parallel_with_root_cuts_matches_brute_force(tmp_path):
    raw = S.tiny(6, 9, 100, 7, True, 0.8, 1.0)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4, cut_rounds=5)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)


def test_quick_try_finishes_easy_instances_before_the_pool(tmp_path):
    raw = S.tiny(5, 8, 100, 0, True)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4, quick_seconds=5, cut_rounds=3)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    assert any("quick try: Optimal" in line for line in r.log)


@pytest.mark.parametrize("cap", [1, 2])
def test_start_cap_is_respected_and_optimal(tmp_path, cap):
    raw = S.tiny(5, 8, 100, 3, True)
    raw["max_starts_per_year"] = cap
    raw["name"] += f"_cap{cap}"
    path = write_instance(tmp_path, raw)
    inst = I.from_dict(raw)
    bf, _ = S.brute_force(inst)
    for r in (solver.solve_sequential(str(path), time_limit=30),
              solver.solve(str(path), workers=2, time_limit=60, n_leaves=4),
              solver.solve_reference_highs(str(path), time_limit=60, threads=1)):
        assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
        st = inst.starts_from_dict(r.starts)
        assert np.bincount(st, minlength=inst.T).max() <= cap


def test_relative_gap_target_stops_within_the_gap(tmp_path):
    raw = S.tiny(6, 9, 100, 7, True, 0.8, 1.0)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4, rel_gap=1e-3)
    assert r.proven
    assert r.objective <= bf + 1e-3 * abs(bf) + 1e-6  # within the requested gap of the true optimum
    assert r.dual_bound <= bf + 1e-6                   # and the reported bound stays valid


# hard cash cap (policy variant H17): capped tiny instances, with and without holding penalties
CAPPED = [(6, 9, 100, 4, True, 0.8, 1.0, 60), (6, 9, 100, 7, True, 0.8, 1.0, 40), (5, 8, 100, 1, False, -0.04, 1e-4, 50)]


@pytest.mark.parametrize("cfg", CAPPED)
def test_hard_cash_cap_matches_brute_force(tmp_path, cfg):
    *args, cap = cfg
    raw = S.tiny(*args)
    raw["cash_cap_units"] = cap
    raw["name"] += f"_cap{cap}"
    path = write_instance(tmp_path, raw)
    inst = I.from_dict(raw)
    bf, arg = S.brute_force(inst)
    if arg is None:
        pytest.skip("no schedule satisfies this cap")
    for r in (solver.solve_sequential(str(path), time_limit=30),
              solver.solve(str(path), workers=2, time_limit=60, n_leaves=4),
              solver.solve_reference_highs(str(path), time_limit=60, threads=1)):
        assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
        ev = evaluate(inst, inst.starts_from_dict(r.starts))
        last = ev["last_build_year_index"]
        assert ev["feasible"] and max(ev["closing_balance_units"][:last], default=0) <= cap + 1e-6


def test_live_leaf_bounds_and_late_cutoff_stay_exact(tmp_path):
    """E1/E2: live leaf bounds, the leaf census, and the late cutoff must not change the proven optimum."""
    raw = S.tiny(6, 9, 100, 7, True, 0.8, 1.0)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=6, quick_seconds=0, cutoff_after=0.0)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    assert r.dual_bound <= bf + 1e-6
    assert any("leaf census" in line for line in r.log)


def test_strong_branching_ramp_up_and_resplit_match_brute_force(tmp_path):
    raw = S.tiny(6, 9, 100, 11, True, 0.8, 1.0)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=3, time_limit=60, n_leaves=6, slice_seconds=0.3, quick_seconds=0,
                     sb_candidates=-1)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)


def test_strong_branching_picks_a_split_that_moves_both_children(instances):
    from capopt.parallel import _branch, _lp_node
    model = last_year_model(instances["P100_growth_penalised"], 18, "step", "cumulative")
    root = _lp_node(model, [])
    kids = _branch(model, root, sb_candidates=8)
    default = _branch(model, root)

    def product(ks):
        return float(np.prod([np.inf if k is None else max(1e-6, k.bound - root.bound) for k in ks]))
    assert product(kids) >= product(default) * (1 - 1e-9)


def test_node_lp_never_prunes_on_a_time_limit(instances, monkeypatch):
    """Regression: the cached node-LP solver had a 60 s lifetime time limit; after it, every node read as infeasible."""
    from capopt import parallel as P
    model = last_year_model(instances["P100_growth_penalised"], 18, "step", "cumulative")
    P._LP_CACHE.clear()
    root = P._lp_node(model, [])
    h = P._lp_solver(model)
    h.setOptionValue("time_limit", 0.0)  # simulate the exhausted lifetime limit
    again = P._lp_node(model, [])
    assert again is not None and again.bound == pytest.approx(root.bound, rel=1e-9)
    P._LP_CACHE.clear()


def test_big_project_lns_matches_brute_force(tmp_path):
    raw = S.tiny(6, 9, 100, 13, True, 0.8, 1.0)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=3, time_limit=60, n_leaves=4, lns_workers=2, lns_seconds=2, lns_big=True,
                     lns_alns=True)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)


def test_warm_start_from_a_solution_file(tmp_path):
    import json
    raw = S.tiny(6, 9, 100, 17, True, 0.8, 1.0)
    path = write_instance(tmp_path, raw)
    inst = I.from_dict(raw)
    bf, bf_starts = S.brute_force(inst)
    sol = tmp_path / "warm.json"
    sol.write_text(json.dumps({"starts": inst.starts_to_dict(np.asarray(bf_starts))}), encoding="utf-8")
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4, warm_start=str(sol))
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    assert any(line.startswith("warm start: objective") for line in r.log)


def test_lns_patience_hands_workers_back_and_still_proves(tmp_path):
    raw = S.tiny(6, 9, 100, 19, True, 0.8, 1.0)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=3, time_limit=60, n_leaves=4, lns_workers=2, lns_seconds=30, lns_patience=0.5)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    assert r.seconds < 30


def test_warm_start_in_a_later_last_year_is_solved_first_and_still_proves(tmp_path):
    """Regression (KPI pass 2): a warm schedule ending after L0 broke LNS on L0 and the survivors' time budget."""
    import json
    raw = S.tiny(6, 10, 100, 23, True, 0.8, 1.0)
    path = write_instance(tmp_path, raw)
    inst = I.from_dict(raw)
    bf, _ = S.brute_force(inst)
    L0 = inst.min_last_year()
    from capopt.evaluate import evaluate
    late = None
    rng = np.random.default_rng(0)
    for _ in range(4000):  # a feasible schedule whose last build year is after L0
        st = rng.integers(0, inst.T, size=inst.n)
        ev = evaluate(inst, st)
        if ev["feasible"] and ev["last_build_year_index"] > L0:
            late = st
            break
    assert late is not None
    sol = tmp_path / "late.json"
    sol.write_text(json.dumps({"starts": inst.starts_to_dict(late)}), encoding="utf-8")
    r = solver.solve(str(path), workers=3, time_limit=60, n_leaves=4, lns_workers=1, lns_seconds=2, warm_start=str(sol))
    assert any("solved first" in line for line in r.log)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    # E34: the same, with the surviving last years proven concurrently
    rc = solver.solve(str(path), workers=3, time_limit=60, n_leaves=4, lns_workers=1, lns_seconds=2,
                      warm_start=str(sol), concurrent_proofs=True, low_priority_proofs=True)
    assert any("concurrent proofs started" in line for line in rc.log), rc.log
    assert rc.proven and rc.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)


def test_lns_burst_before_the_ramp_up_still_proves(tmp_path):
    import json
    raw = S.tiny(6, 9, 100, 29, True, 0.8, 1.0)
    path = write_instance(tmp_path, raw)
    inst = I.from_dict(raw)
    bf, bf_starts = S.brute_force(inst)
    sol = tmp_path / "warm.json"
    sol.write_text(json.dumps({"starts": inst.starts_to_dict(np.asarray(bf_starts))}), encoding="utf-8")
    r = solver.solve(str(path), workers=3, time_limit=60, n_leaves=4, warm_start=str(sol), lns_first=0.5, quick_seconds=0)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    assert any("lns burst" in line for line in r.log)
