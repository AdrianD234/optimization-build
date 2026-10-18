"""Timing controls (capopt.timing): forced start windows and sequencing rules stay exact under every solve path."""
import numpy as np
import pytest
from conftest import write_instance

from capopt import instance as I
from capopt import solver
from capopt import synthetic as S
from capopt import timing as TM
from capopt.evaluate import evaluate


def test_window_restricts_allowed_starts_and_keeps_pv_aligned():
    raw = S.tiny(5, 9, 100, 0, True, 0.8, 1.0)
    out = TM.with_windows(raw, {"S002": (2, 4), 0: (3, 3)})
    p0, p1 = out["projects"][0], out["projects"][1]
    assert p0["allowed_starts"] == [3] and p0["pv_int"] == [raw["projects"][0]["pv_int"][3]]
    assert p1["allowed_starts"] == [2, 3, 4] and p1["pv_int"] == raw["projects"][1]["pv_int"][2:5]
    assert out["timing"]["windows"] == {"S001": [3, 3], "S002": [2, 4]}
    assert raw["projects"][0]["allowed_starts"][0] == 0  # the input is not modified
    cal = TM.with_windows(raw, {"S002": (2028, 2030)}, calendar=True)  # base_year 2026
    assert cal["projects"][1]["allowed_starts"] == [2, 3, 4]


def test_window_without_an_allowed_start_is_an_error():
    raw = S.tiny(4, 8, 100, 0)
    with pytest.raises(ValueError):
        TM.with_windows(raw, {"S001": (20, 25)})
    with pytest.raises(ValueError):
        TM.with_windows(raw, {"S001": (4, 2)})
    with pytest.raises(KeyError):
        TM.pin(raw, {"nope": 1})


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("preset", [None, "1pct"])
def test_windowed_solve_matches_brute_force(tmp_path, seed, preset):
    """A window (and a pin, a = b) is respected by the pipeline, and the result is the brute-force optimum of the
    windowed instance (proof mode) or within the preset's certified gap of it."""
    raw = S.tiny(6, 9, 100, seed, True, 0.8, 1.0)
    win = TM.with_windows(raw, {"S001": (3, 5), "S004": (1, 1)}, name=raw["name"] + "_win")
    bf, _ = S.brute_force(I.from_dict(win))
    path = write_instance(tmp_path, win)
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4, **({"preset": preset} if preset else {}))
    st = r.starts
    assert 3 <= st["S001"] <= 5 and st["S004"] == 1
    gap = 1e-2 if preset else 1e-9
    assert r.proven and r.objective - bf <= gap * abs(r.objective) + 1e-9
    assert r.dual_bound <= bf + 1e-9 * abs(bf)
    free, _ = S.brute_force(I.from_dict(raw))
    assert bf >= free - 1e-9 * abs(free)  # a window can only cost


def test_warm_start_outside_a_window_is_repaired(tmp_path):
    """A previous plan that breaks a new window is moved into it (nearest allowed start) and re-optimised."""
    import json
    raw = S.tiny(6, 9, 100, 1, True, 0.8, 1.0)
    free_val, free_st = S.brute_force(I.from_dict(raw))
    p = int(np.argmax([len(a) for a in I.from_dict(raw).allowed]))
    s0 = int(free_st[p])
    a = s0 + 2 if s0 + 2 <= I.from_dict(raw).allowed[p][-1] else s0 - 2
    win = TM.pin(raw, {p: a}, name=raw["name"] + "_pin")
    path = write_instance(tmp_path, win)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"starts": I.from_dict(raw).starts_to_dict(free_st)}), encoding="utf-8")
    r = solver.solve(str(path), workers=2, time_limit=60, n_leaves=4, preset="1pct", warm_start=str(plan))
    bf, _ = S.brute_force(I.from_dict(win))
    assert r.starts[raw["projects"][p]["id"]] == a
    assert r.proven and r.objective - bf <= 1e-2 * abs(r.objective) + 1e-9


# ---- sequencing rules ------------------------------------------------------------------------------------------------

def _rules(raw: dict, kind: str) -> list[dict]:
    """One rule of each type between S001 (A) and S002 (B), plus a second pair for the general lag."""
    return {
        "after_start": [TM.after_start("S001", "S002")],
        "after_start_lag": [TM.after_start("S001", "S002", lag=2)],
        "after_finish": [TM.after_finish(raw, "S001", "S002")],
        "finish_within": [TM.after_finish(raw, "S001", "S002", within=1)],
        "finish_exact": [TM.after_finish(raw, "S001", "S002", within=0)],
        "not_after": [TM.not_after("S001", "S002")],
        "general": [TM.lag_between("S001", "S002", lo=-1, hi=2), TM.lag_between("S003", "S004", lo=1)],
        "chain": [TM.after_finish(raw, "S001", "S002"), TM.after_start("S002", "S003", lag=1)],
    }[kind]


KINDS = ["after_start", "after_start_lag", "after_finish", "finish_within", "finish_exact", "not_after", "general",
         "chain"]


def _rows_ok(model, vec) -> bool:
    b = model.builder
    for idx, val, lo, hi in zip(b.row_idx, b.row_val, b.row_lo, b.row_hi):
        a = float(np.dot(vec[idx], val)) if idx else 0.0
        if a < lo - 1e-6 * max(1, abs(lo)) or a > hi + 1e-6 * max(1, abs(hi)):
            return False
    return True


def _broken(inst, st) -> bool:
    r = evaluate(inst, st)
    return not r["feasible"] and r["reason"].startswith("sequencing")


def test_rule_builders_resolve_durations_and_reject_nonsense():
    raw = S.tiny(4, 8, 100, 0)
    d = raw["projects"][0]["duration"]
    assert TM.after_finish(raw, "S001", "S002")["lo"] == d
    r = TM.after_finish(raw, "S001", "S002", lag=1, within=2)
    assert (r["a"], r["b"], r["lo"], r["hi"]) == ("S001", "S002", d + 1, d + 3)
    assert TM.not_after("S001", "S002")["hi"] == 0 and TM.not_after("S001", "S002")["lo"] is None
    with pytest.raises(ValueError):
        TM.lag_between("S001", "S002")
    with pytest.raises(ValueError):
        TM.lag_between("S001", "S002", lo=3, hi=1)
    with pytest.raises(KeyError):
        TM.with_precedence(raw, [TM.after_start("S001", "nope")])
    with pytest.raises(ValueError):
        I.from_dict(TM.with_precedence(raw, [TM.after_start("S001", "S001")]))


@pytest.mark.parametrize("kind", KINDS)
def test_evaluator_enforces_each_rule_type(kind):
    raw = S.tiny(5, 9, 100, 0, True, 0.8, 1.0)
    inst = I.from_dict(TM.with_precedence(raw, _rules(raw, kind)))
    rng = np.random.default_rng(0)
    hits = 0
    for _ in range(300):
        st = np.array([rng.choice(a) for a in inst.allowed])
        ok = all((lo is None or st[b] - st[a] >= lo) and (hi is None or st[b] - st[a] <= hi)
                 for a, b, lo, hi in inst.precedence)
        assert _broken(inst, st) == (not ok)
        hits += not ok
    assert hits > 0


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("encoding,balance", [("step", "cumulative"), ("pulse", "recursive")])
def test_model_rows_equal_the_rules_and_keep_the_decomposition(kind, encoding, balance):
    """Every schedule that keeps the rules has a vector satisfying the rule rows at its own last year (value =
    evaluator) and at a later one (value >= evaluator); a schedule that breaks a rule violates the rows."""
    from capopt.formulation import last_year_model
    raw = S.tiny(5, 9, 100, 2, True, 0.8, 1.0)
    inst = I.from_dict(TM.with_precedence(raw, _rules(raw, kind)))
    free = I.from_dict(raw)
    rng = np.random.default_rng(1)
    seen = broken = 0
    for _ in range(400):
        st = np.array([rng.choice(a) for a in inst.allowed])
        ev0 = evaluate(free, st)
        if not ev0["feasible"]:
            continue
        L0 = ev0["last_build_year_index"]
        ev = evaluate(inst, st)
        for L in (L0, min(inst.T - 1, L0 + 2)):
            m = last_year_model(inst, L, encoding, balance)
            mf = last_year_model(free, L, encoding, balance)
            assert len(m.builder.cost) == len(mf.builder.cost)  # rule rows add no columns
            if ev["feasible"]:
                vec = m.solution_vector(st)
                assert vec is not None and _rows_ok(m, vec)
                val = float(np.dot(m.builder.cost, vec)) + m.builder.offset
                assert val >= ev["objective"] - 1e-6 * abs(ev["objective"])
                if L == L0:
                    assert abs(val - ev["objective"]) <= 1e-6 * abs(ev["objective"])
                    seen += 1
            else:
                vec = mf.solution_vector(st)
                assert vec is not None and _rows_ok(mf, vec) and not _rows_ok(m, vec)
                broken += 1
    assert seen > 5 and broken > 5


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("preset", [None, "1pct"])
def test_sequenced_solve_matches_brute_force(tmp_path, kind, preset):
    raw = S.tiny(6, 9, 100, 3, True, 0.8, 1.0)
    rr = TM.with_precedence(raw, _rules(raw, kind), name=f"{raw['name']}_{kind}")
    inst = I.from_dict(rr)
    bf, arg = S.brute_force(inst)
    assert arg is not None
    r = solver.solve(str(write_instance(tmp_path, rr)), workers=2, time_limit=60, n_leaves=4,
                     **({"preset": preset} if preset else {}))
    st = inst.starts_from_dict(r.starts)
    assert TM.violation(inst.precedence, st) is None
    gap = 1e-2 if preset else 1e-9
    assert r.proven and r.objective - bf <= gap * abs(r.objective) + 1e-9
    assert r.dual_bound <= bf + 1e-9 * abs(bf)


def test_earliest_starts_follow_chains_and_detect_impossible_rules():
    raw = S.tiny(5, 12, 100, 0)
    inst = I.from_dict(TM.with_precedence(raw, [TM.after_finish(raw, "S001", "S002"),
                                                TM.after_finish(raw, "S002", "S003")]))
    es = TM.earliest_starts(inst.allowed, inst.precedence)
    d = [len(s) for s in inst.spend]
    assert es[1] == d[0] and es[2] == d[0] + d[1]
    assert inst.min_last_year() >= es[2] + inst.last_nz[2]
    cyc = I.from_dict(TM.with_precedence(raw, [TM.after_start("S001", "S002", 1), TM.after_start("S002", "S001", 1)]))
    assert TM.earliest_starts(cyc.allowed, cyc.precedence) is None


def test_infeasible_rules_give_no_schedule(tmp_path):
    raw = S.tiny(4, 8, 100, 0, True, 0.8, 1.0)
    rr = TM.with_precedence(raw, [TM.lag_between("S001", "S002", lo=20)], name=raw["name"] + "_imp")
    r = solver.solve(str(write_instance(tmp_path, rr)), workers=2, time_limit=30, n_leaves=4, preset="1pct")
    assert r.starts is None


def test_warm_start_that_breaks_a_new_rule_is_repaired(tmp_path):
    import json
    raw = S.tiny(6, 9, 100, 4, True, 0.8, 1.0)
    free = I.from_dict(raw)
    _, st = S.brute_force(free)
    a, b = int(np.argmax(st)), int(np.argmin(st))  # B (the earliest start) must now follow A (the latest)
    rr = TM.with_precedence(raw, [TM.after_start(a, b)], name=raw["name"] + "_rep")
    inst = I.from_dict(rr)
    assert TM.violation(inst.precedence, st) is not None
    bf, arg = S.brute_force(inst)
    assert arg is not None
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"starts": free.starts_to_dict(st)}), encoding="utf-8")
    r = solver.solve(str(write_instance(tmp_path, rr)), workers=2, time_limit=60, n_leaves=4, preset="1pct",
                     warm_start=str(plan))
    assert r.proven and TM.violation(inst.precedence, inst.starts_from_dict(r.starts)) is None
    assert r.objective - bf <= 1e-2 * abs(r.objective) + 1e-9
    assert any("warm start repaired" in line for line in r.log)
