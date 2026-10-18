"""Benefit streams (capopt.benefits): recovery, shapes with equal totals, and the two growth anchors."""
import numpy as np
import pytest

from capopt import benefits as B
from capopt import synthetic as S


def flat_instance(seed: int = 0, T: int = 60, n: int = 4) -> tuple[dict, list[float]]:
    """A tiny instance whose pv_int comes from known flat 40-year streams (as the CPO's LIN40 benefits)."""
    raw = S.tiny(n, T, 100, seed)
    rng = np.random.default_rng(seed)
    cs = []
    for pr in raw["projects"]:
        c = float(rng.uniform(1e5, 1e7))
        pr["pv_int"] = B.pv_row(np.full(40, c), pr["duration"], pr["allowed_starts"], T)
        cs.append(c)
    return raw, cs


def test_flat_stream_is_recovered_exactly_and_rebuilds_the_same_pv():
    raw, cs = flat_instance()
    for p, c in enumerate(cs):
        got, mis = B.flat_annual(raw, p)
        assert abs(got - c) <= 1e-6 * c and mis < 1e-6
    again = B.rebuild(raw, {p: np.full(40, B.flat_annual(raw, p)[0]) for p in range(len(cs))})
    for a, b in zip(again["projects"], raw["projects"]):
        assert max(abs(x - y) for x, y in zip(a["pv_int"], b["pv_int"])) <= 2


@pytest.mark.parametrize("shape", ["linear", "log", "exp"])
def test_shapes_keep_the_total_and_order_the_timing(shape):
    s = B.shape_stream(1000.0, shape)
    assert len(s) == 40 and np.all(s > 0) and abs(s.sum() - 1000.0) < 1e-9
    C = B.cumulative_share(shape)
    assert C[0] == 0 and abs(C[-1] - 1) < 1e-12 and np.all(np.diff(C) > 0)
    if shape == "log":
        assert np.all(np.diff(s) < 0)  # front-loaded: concave accumulation
    if shape == "exp":
        assert np.all(np.diff(s) > 0)  # back-loaded: convex accumulation


def test_log_and_exp_are_mirror_images_at_the_quarter_points():
    lg, ex = B.cumulative_share("log"), B.cumulative_share("exp")
    assert abs(ex[30] - (1 - lg[10])) < 1e-6


def test_front_loaded_streams_are_worth_more_after_discounting():
    T = 80
    pv = {sh: B.pv_row(B.shape_stream(1e6, sh), 3, [0], T)[0] for sh in ("log", "linear", "exp")}
    assert pv["log"] > pv["linear"] > pv["exp"]


def test_calendar_growth_above_the_discount_rate_rewards_delay_and_opening_growth_does_not():
    """The deferral bias: with calendar-indexed growth of 3% (> the 2% discount rate) a later start is worth more,
    until the horizon truncates the stream; anchored to opening, delay only loses discounting."""
    T, stream, allowed = 90, np.full(40, 1e6), list(range(0, 20))
    cal = B.pv_row(stream, 3, allowed, T, growth=0.03, anchor="calendar")
    opn = B.pv_row(stream, 3, allowed, T, growth=0.03, anchor="opening")
    flat = B.pv_row(stream, 3, allowed, T)
    assert all(b > a for a, b in zip(cal, cal[1:]))       # rises with delay
    assert all(b < a for a, b in zip(opn, opn[1:]))       # falls with delay, like the un-grown stream
    assert all(b < a for a, b in zip(flat, flat[1:]))
    assert all(o > f for o, f in zip(opn, flat))           # growth adds value either way
    assert cal[0] > flat[0]
    low = B.pv_row(stream, 3, allowed, T, growth=0.01, anchor="calendar")  # below the discount rate: no bias
    assert all(b < a for a, b in zip(low, low[1:]))


def test_rebuild_applies_growth_to_every_project_and_records_it():
    raw, _ = flat_instance(1)
    g = B.rebuild(raw, {}, growth=0.05, anchor="opening", name="g")
    assert g["name"] == "g" and g["benefits"] == {"growth": 0.05, "anchor": "opening", "reshaped": []}
    assert all(x >= y for a, b in zip(g["projects"], raw["projects"]) for x, y in zip(a["pv_int"], b["pv_int"]))
    assert all(a["pv_int"][0] > b["pv_int"][0] for a, b in zip(g["projects"], raw["projects"]))
    with pytest.raises(ValueError):
        B.pv_row(np.ones(3), 1, [0], 10, anchor="nope")
    with pytest.raises(ValueError):
        B.cumulative_share("cubic")
