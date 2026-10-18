"""Distribution layers (capopt.layers): the model rows agree with the evaluator, and solves stay exact."""
import numpy as np
import pytest
from conftest import write_instance

from capopt import instance as I
from capopt import layers as LY
from capopt import solver
from capopt import synthetic as S
from capopt.bounds import cash_floor_bound
from capopt.evaluate import evaluate
from capopt.formulation import last_year_model


def layered(seed: int, kinds=("short", "bands", "floor"), n: int = 6, T: int = 9) -> dict:
    """A tiny instance with two groups, a soft allocation layer on windows and checkpoints, loose hard bands and a PV
    floor on a second PV vector (the objective PV re-weighted per project, as dimension scoring does)."""
    raw = S.tiny(n, T, 100, seed, True, 0.8, 1.0)
    rng = np.random.default_rng(seed + 100)
    groups = {"A": [p for p in range(n) if p % 2 == 0], "B": [p for p in range(n) if p % 2 == 1]}
    shares = LY.cost_shares(raw, groups)
    lay: dict = {}
    if "short" in kinds:
        lay["shortfall"] = (LY.allocation_rows(raw, groups, shares, [(0, 1), (2, 3)], [2.0, 1.0], 0.5, 1e4 * 50,
                                               tol=0.05, lumpiness=None, norm_floor=10.0, prefix="win:")
                            + LY.allocation_rows(raw, groups, shares, [(0, 2), (0, 4)], [2.0, 1.0], 0.2, 1e4 * 50,
                                                 tol=0.0, lumpiness=None, norm_floor=10.0, prefix="cum:")
                            + LY.allocation_rows(raw, groups, shares, [(1, 3)], [1.0], 0.3, 1e4 * 50,
                                                 tol=0.0, lumpiness=None, norm_floor=10.0, prefix="endo:",
                                                 endogenous=True))
    pv_obj = None
    if "floor" in kinds:
        tot = [p["pv_int"] for p in raw["projects"]]
        w = rng.uniform(1.0, 2.0, n)
        pv_obj = [[int(v * w[p]) for v in tot[p]] for p in range(n)]
        best = sum(max(v) for v in tot)
        lay["pv_floor"] = {"pv": tot, "min": int(0.7 * best)}
    out = LY.with_layers(raw, lay, raw["name"] + "_lay", pv_obj)
    if "bands" in kinds:
        out["layers"]["bands"] = LY.hard_bands(groups, shares, [(0, 2)], tol=0.5, floor=60.0, min_share=0.0)
    return out


def model_value(model, starts):
    vec = model.solution_vector(starts)
    if vec is None:
        return None, None
    b = model.builder
    act = np.zeros(len(b.row_lo))
    for r, (idx, val) in enumerate(zip(b.row_idx, b.row_val)):
        act[r] = float(np.dot(vec[idx], val)) if idx else 0.0
    ok = np.all(act >= np.array(b.row_lo) - 1e-6 * np.maximum(1, np.abs(b.row_lo))) and \
        np.all(act <= np.array(b.row_hi) + 1e-6 * np.maximum(1, np.abs(b.row_hi)))
    return float(np.dot(b.cost, vec)) + b.offset, ok


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("encoding,balance", [("step", "cumulative"), ("pulse", "recursive")])
def test_model_equals_evaluator_with_layers(seed, encoding, balance):
    """At a schedule's own last year the model value (layer columns filled) equals the evaluator's objective, the
    vector satisfies every row, and at a later last year the value is not below it; the cash floor bounds both."""
    inst = I.from_dict(layered(seed))
    assert inst.layers is not None and inst.layers.short and inst.layers.bands
    assert any(r["gref"] >= 0 for r in inst.layers.short)  # an endogenous-base row is in the mix
    rng = np.random.default_rng(seed)
    checked = charged = 0
    for _ in range(5000):
        st = np.array([rng.choice(a) for a in inst.allowed])
        ev = evaluate(inst, st)
        if not ev["feasible"]:
            continue
        L0 = ev["last_build_year_index"]
        charged += ev["components"]["layer_penalty"] > 0
        for L in (L0, min(inst.T - 1, L0 + 2)):
            v, ok = model_value(last_year_model(inst, L, encoding, balance), st)
            assert ok
            if L == L0:
                assert v == pytest.approx(ev["objective"], rel=1e-9, abs=1e-6)
            assert v >= ev["objective"] - 1e-6 * abs(ev["objective"])
            assert cash_floor_bound(inst, L) <= v + 1e-6 * abs(v)
        checked += 1
    assert checked >= 5 and charged >= 2


def test_hard_layers_reject_schedules():
    """The PV floor and the bands make schedules infeasible in the evaluator (and the model has no vector for them)."""
    inst = I.from_dict(layered(1, kinds=("floor",)))
    rng = np.random.default_rng(0)
    reasons = set()
    for _ in range(500):
        st = np.array([rng.choice(a) for a in inst.allowed])
        ev = evaluate(inst, st)
        if not ev["feasible"]:
            reasons.add(ev["reason"].split(":")[0])
    assert "PV floor" in reasons


@pytest.mark.parametrize("seed", [0, 3])
def test_layered_solve_matches_brute_force(tmp_path, seed):
    raw = layered(seed)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    r = solver.solve(str(path), workers=2, time_limit=60, preset="0.5pct", n_leaves=4)
    assert r.proven and r.objective - bf <= 5e-3 * abs(r.objective) + 1e-6 and r.dual_bound <= bf + 1e-9 * abs(bf)


def test_allocation_pricing_is_pv_equivalent():
    """A deficit of 1% of the normaliser, beyond the free zone, in the first span costs price_pct % of PV*."""
    raw = S.tiny(4, 8, 100, 0)
    rows = LY.allocation_rows(raw, {"A": [0, 1]}, {"A": 0.5}, [(0, 1), (0, 3)], [4.0, 2.0], 0.1, 1e9, tol=0.1,
                              lumpiness=None, norm_floor=0.0)
    r0, r1 = rows
    N = r0["target"]
    w, c = r0["segments"][0]
    assert w == pytest.approx(0.1 * N)
    assert c * 0.01 * N == pytest.approx(0.001 * 1e9)
    assert [s[1] / c for s in r0["segments"]] == pytest.approx([1, 4, 12])
    assert r0["free"] == pytest.approx(0.1 * N)
    assert r1["segments"][0][1] * 0.01 * r1["target"] == pytest.approx(0.0005 * 1e9)  # weight 2/4


def test_dimension_weights():
    w = LY.dimension_weights(np.array([1.0, 2.0, 4.0, 8.0]), np.array([2.0, 2.0, 2.0, 2.0]))
    assert w.min() >= 1.0 and w.max() <= 2.0
    assert np.all(np.diff(w) >= 0)  # higher dimension PV, higher weight


def test_kernel_refuses_layers():
    from capopt.heuristics import kernel as K
    with pytest.raises(NotImplementedError):
        K.pack(I.from_dict(layered(0)))


def test_best_first_with_a_poor_warm_plan_stays_exact(tmp_path):
    """E44: best_first reorders the last years after the screen; a poor warm plan still ends at the optimum."""
    import json
    raw = layered(3)
    path = write_instance(tmp_path, raw)
    inst = I.from_dict(raw)
    bf, _ = S.brute_force(inst)
    rng = np.random.default_rng(1)
    worst, poor = -np.inf, None
    for _ in range(20000):
        st = np.array([rng.choice(a) for a in inst.allowed])
        ev = evaluate(inst, st)
        if ev["feasible"] and ev["objective"] > worst:
            worst, poor = ev["objective"], st
    warm = tmp_path / "poor.json"
    warm.write_text(json.dumps({"starts": inst.starts_to_dict(poor)}), encoding="utf-8")
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4, warm_start=str(warm), lp_screen=True,
                     best_first=True)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4, warm_start=str(warm), preset="1pct",
                     warm_triage=True)
    assert r.proven and r.objective - bf <= 1e-2 * abs(r.objective) + 1e-6 and r.dual_bound <= bf + 1e-9 * abs(bf)
    assert any("warm triage" in line for line in r.log)


def test_lumpiness_floor_is_capped():
    raw = S.tiny(4, 8, 100, 0)
    mem = [0, 1]
    big = max(max(raw["projects"][p]["spend_S"]) for p in mem)
    r = LY.allocation_rows(raw, {"A": mem}, {"A": 0.5}, [(0, 1)], [1.0], 0.1, 1e9, tol=0.0, lump_cap=0.5)[0]
    assert r["free"] == pytest.approx(min(0.5 * r["target"], big))


def test_unsolvable_node_lp_becomes_an_open_leaf(monkeypatch):
    """E44: a node LP that ends neither optimal nor infeasible (twice) is kept open with no bound, not raised."""
    import highspy

    from capopt import parallel as P

    class Stuck:
        def changeColsBounds(self, *a):
            pass

        def run(self):
            pass

        def getModelStatus(self):
            return highspy.HighsModelStatus.kNotset

        def modelStatusToString(self, s):
            return "Not Set"

    model = last_year_model(I.from_dict(layered(0)), 8, "step", "cumulative")
    monkeypatch.setattr(P, "_lp_solver", lambda m: Stuck())
    monkeypatch.setattr(P, "_highs", lambda *a, **k: Stuck())
    node = P._lp_node(model, [])
    assert node is not None and node.bound == -np.inf and node.frac == []


@pytest.mark.parametrize("seed", [0, 3])
def test_mega_first_stays_exact(tmp_path, seed):
    """E49: branching on the largest projects first changes the tree, never the answer."""
    raw = layered(seed)
    path = write_instance(tmp_path, raw)
    bf, _ = S.brute_force(I.from_dict(raw))
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=6, mega_first=2)
    assert r.proven and r.objective == pytest.approx(bf, rel=1e-9, abs=1e-6)
    r = solver.solve(str(path), workers=2, time_limit=60, preset="0.5pct", n_leaves=6, mega_first=2)
    assert r.proven and r.objective - bf <= 5e-3 * abs(r.objective) + 1e-6 and r.dual_bound <= bf + 1e-9 * abs(bf)


def test_mega_first_puts_priority_projects_first():
    from capopt import parallel as P
    inst = I.from_dict(layered(0))
    model = last_year_model(inst, inst.T - 1, "step", "cumulative")
    root = P._lp_node(model, [])
    mega = P.mega_projects(inst, 2)
    model.aux["priority"] = set(mega)
    node = P._lp_node(model, [])
    frac_mega = [c for c in root.frac if c[1] in mega]
    if frac_mega:
        assert node.frac and all(c[1] in mega for c in node.frac)
    else:
        assert node.frac == root.frac
    assert node.n_frac == root.n_frac
