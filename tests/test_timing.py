"""Timing controls (capopt.timing): forced start windows and sequencing rules stay exact under every solve path."""
import numpy as np
import pytest
from conftest import write_instance

from capopt import instance as I
from capopt import solver
from capopt import synthetic as S
from capopt import timing as TM


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
