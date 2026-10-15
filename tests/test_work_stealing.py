"""E27 work stealing: a stolen leaf is handed back open (never closed), with a valid bound, and the solver's answer
does not change when leaves are stolen aggressively."""
import multiprocessing as mp
import re
import time

import numpy as np
import pytest

from capopt import instance as I
from capopt import parallel as P
from capopt import solver
from capopt.formulation import last_year_model


def _worker_state(name: str, L: int, workers: int = 2):
    ctx = mp.get_context("spawn")
    best_val = ctx.Value("d", np.inf, lock=False)
    best_starts = ctx.Array("q", I.load(name).n, lock=False)
    live = ctx.Array("d", [-np.inf] * workers, lock=False)
    steal = ctx.Array("b", [0] * workers, lock=False)
    P._init(best_val, best_starts, ctx.Lock(), ctx.Value("b", 0, lock=False), name, L, "step", "cumulative",
            None, 1e-9, live, steal)
    return best_val, live, steal


def test_a_flagged_leaf_comes_back_stolen_with_a_valid_bound(instances, best_known):
    name, L = "P100_growth_penalised", 18
    root = P._lp_node(last_year_model(instances[name], L, "step", "cumulative"), [])
    _, live, steal = _worker_state(name, L)
    steal[0] = 1  # the master asked for this slot back before the leaf even started
    r = P._solve_leaf([], 30.0, 0, time.time(), False, 0, root.bound)  # as dispatched: start bound = its LP bound
    P._W.clear()
    assert r["status"] == "stolen"  # open: the master must re-split it, never close it
    opt = best_known[name]["objective"]  # its schedule fits L = 18, so OPT(L = 18) <= opt
    assert root.bound <= r["bound"] <= opt + 1e-6 * abs(opt)  # valid: never above the subtree's optimum


def test_an_unflagged_leaf_is_not_reported_stolen(instances):
    _, live, steal = _worker_state("P100_growth_penalised", 18)
    r = P._solve_leaf([], 0.5, 0, time.time(), False, 1, -np.inf)  # slot 1, flag never set
    P._W.clear()
    assert r["status"] != "stolen"


def test_stealing_happens_and_the_proven_optimum_is_unchanged(best_known):
    """Steals must actually occur here (a probe saw 11), and the proof must land on the known optimum."""
    name = "P100_growth_penalised"
    r = solver.solve(name, workers=6, time_limit=120, n_leaves=6, quick_seconds=0, steal_after=2.0)
    util = [line for line in r.log if "tree utilization" in line]
    steals = sum(int(re.search(r"steals: (\d+)", line).group(1)) for line in util)
    assert steals > 0, util
    assert r.proven and r.objective == pytest.approx(best_known[name]["objective"], rel=1e-9)


def test_racing_happens_and_the_proven_optimum_is_unchanged(best_known):
    """E28: idle workers race copies of the weakest open leaf; the proof must land on the known optimum."""
    name = "P100_growth_penalised"
    r = solver.solve(name, workers=6, time_limit=120, n_leaves=6, quick_seconds=0, race_after=0.5)
    util = [line for line in r.log if "tree utilization" in line]
    races = sum(int(re.search(r"races: (\d+)", line).group(1)) for line in util)
    assert races > 0, util
    assert r.proven and r.objective == pytest.approx(best_known[name]["objective"], rel=1e-9)
