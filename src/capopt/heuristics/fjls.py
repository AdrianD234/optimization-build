"""H11 + H10: LP-guided list scheduling with cash repair, then weighted local search on the start-year vector.

Why (docs/research/HYBRID_AND_CROSSFIELD.md §0, §2.1-2.8): on the 233-project CPO data the cash constraint has
about 0.8% slack over the horizon. LP rounding is cash-infeasible, and near-optimal schedules sit far from the LP and
far from each other. Good moves often need to pass briefly through cash-infeasible states, which a search that
rejects every infeasible state (kernel.anneal / kernel.descend) cannot do.

* construct_alpha: each project gets its LP alpha-point (the first start where its LP mass reaches alpha; alpha drawn
  per project), clipped to the fixed-L window. Then a repair pass: while some year's cumulative balance is negative,
  delay by one year the project with the lowest PV loss per unit of spend it moves out of the deficit years. Then a
  cap pass the same way.
* fj_search: minimises  true objective + sum_t lam[t] * deficit[t] + mu * cap_excess,  where deficit[t] is the
  negative part of the cumulative balance in year t. Moves: shift by +-1..4, jump to a random allowed year, swap two
  projects. Improvements are accepted greedily, with a small simulated-annealing tail. After `stall` non-improving
  moves, the weights of the violated years (and of the cap) are multiplied by `bump` (Feasibility-Jump / Local-MIP
  style). The best *feasible* schedule seen is returned, scored by the exact objective (kernel._cost).
"""
from __future__ import annotations

import numpy as np
from numba import njit

from ..instance import Instance
from . import kernel as K

INF = np.inf


@njit(cache=True)
def _soft(spend_y, act_y, count, pv_sum, F, base, band_w, band_width, bw, pvw, lam, mu, cap, L, deficit):
    """Weighted cost: the exact objective's terms with cash allowed negative (penalised by lam) and the start cap
    penalised by mu. Fills `deficit` per year and returns (soft cost, total deficit, cap excess, true cost or inf)."""
    net = 0.0
    hold = 0.0
    defsum = 0.0
    for t in range(L + 1):
        net = net + F[t] - spend_y[t]
        if net < 0.0:
            deficit[t] = -net
            defsum += -net
        else:
            deficit[t] = 0.0
        if t < L and net > 0.0:
            hold += bw * net
            excess = net - base[t]
            if excess > 0.0:
                for i in range(band_w.shape[0]):
                    take = excess if excess < band_width[t, i] else band_width[t, i]
                    hold += take * band_w[i]
                    excess -= take
                    if excess <= 0.0:
                        break
    capx = 0.0
    for s in range(count.shape[0]):
        if count[s] > cap:
            capx += count[s] - cap
    pen = 0.0
    for t in range(L + 1):
        pen += lam[t] * deficit[t]
    true = hold - pvw * pv_sum
    soft = true + pen + mu * capx
    feasible_true = true if (defsum <= 1e-9 and capx == 0.0) else INF
    return soft, defsum, capx, feasible_true


@njit(cache=True)
def _move(p, s_from, s_to, spend_y, act_y, count, spend, nz, dur):
    for k in range(dur[p]):
        spend_y[s_from + k] -= spend[p, k]
        act_y[s_from + k] -= nz[p, k]
        spend_y[s_to + k] += spend[p, k]
        act_y[s_to + k] += nz[p, k]
    count[s_from] -= 1
    count[s_to] += 1


@njit(cache=True)
def construct_alpha(cum_lp, opts_lo, alpha, spend, nz, dur, lo, hi, pv, F, cap, L):
    """cum_lp[p, j]: cumulative LP mass of project p over its options lo[p]+j. Returns a cash- and cap-repaired
    schedule (repair may fail on very tight instances; fj_search finishes the job)."""
    n = lo.shape[0]
    T = F.shape[0]
    st = np.empty(n, dtype=np.int64)
    for p in range(n):
        m = hi[p] - lo[p] + 1
        j = 0
        while j < m - 1 and cum_lp[p, j] < alpha[p]:
            j += 1
        st[p] = lo[p] + j
    spend_y = np.zeros(T)
    count = np.zeros(T, dtype=np.int64)
    for p in range(n):
        for k in range(dur[p]):
            spend_y[st[p] + k] += spend[p, k]
        count[st[p]] += 1
    # cash repair: delay the cheapest project (PV loss per unit spend moved) out of the first deficit year
    for _ in range(20 * n):
        run = 0.0
        bad = -1
        for t in range(L + 1):
            run += F[t] - spend_y[t]
            if run < -1e-9:
                bad = t
                break
        if bad < 0:
            break
        best_p, best_score = -1, INF
        for p in range(n):
            s = st[p]
            if s > bad or s + 1 > hi[p]:
                continue
            moved = spend[p, bad - s] if bad - s < dur[p] else 0.0  # spend that leaves year `bad` (and earlier)
            moved += spend[p, 0]
            if moved <= 0.0:
                continue
            loss = pv[p, s] - pv[p, s + 1]
            score = loss / moved
            if score < best_score:
                best_score, best_p = score, p
        if best_p < 0:
            break
        p = best_p
        for k in range(dur[p]):
            spend_y[st[p] + k] -= spend[p, k]
            spend_y[st[p] + 1 + k] += spend[p, k]
        count[st[p]] -= 1
        st[p] += 1
        count[st[p]] += 1
    # cap repair: move the cheapest starters of an over-full year one year later
    for s in range(T):
        while count[s] > cap:
            best_p, best_loss = -1, INF
            for p in range(n):
                if st[p] == s and s + 1 <= hi[p]:
                    loss = pv[p, s] - pv[p, s + 1]
                    if loss < best_loss:
                        best_loss, best_p = loss, p
            if best_p < 0:
                break
            count[s] -= 1
            st[best_p] += 1
            count[st[best_p]] += 1
    return st


@njit(cache=True)
def fj_search(starts, iters, seed, w0, bump, stall, temp0, spend, nz, dur, lo, hi, pv, F, base, band_w, band_width, bw,
              pvw, cap, L):
    np.random.seed(seed)
    n = starts.shape[0]
    T = F.shape[0]
    st = starts.copy()
    spend_y = np.zeros(T)
    act_y = np.zeros(T, dtype=np.int64)
    count = np.zeros(T, dtype=np.int64)
    pv_sum = 0.0
    for p in range(n):
        for k in range(dur[p]):
            spend_y[st[p] + k] += spend[p, k]
            act_y[st[p] + k] += nz[p, k]
        count[st[p]] += 1
        pv_sum += pv[p, st[p]]
    lam = np.full(T, w0)
    mu = w0 * 1e4
    deficit = np.zeros(T)
    cur, dsum, capx, true = _soft(spend_y, act_y, count, pv_sum, F, base, band_w, band_width, bw, pvw, lam, mu, cap,
                                  L, deficit)
    best_true = true
    best = st.copy()
    since = 0
    scale = abs(pvw * pv_sum) if pv_sum != 0.0 else 1.0
    for it in range(iters):
        temp = temp0 * scale * (1.0 - it / iters)
        r = np.random.random()
        if r < 0.55:  # shift
            p = np.random.randint(n)
            d = np.random.randint(1, 5)
            s1 = st[p] + d if np.random.random() < 0.5 else st[p] - d
            if s1 < lo[p] or s1 > hi[p]:
                continue
            s0 = st[p]
            _move(p, s0, s1, spend_y, act_y, count, spend, nz, dur)
            pv_sum += pv[p, s1] - pv[p, s0]
            v, dsum, capx, tv = _soft(spend_y, act_y, count, pv_sum, F, base, band_w, band_width, bw, pvw, lam, mu,
                                      cap, L, deficit)
            if v < cur or (temp > 0.0 and np.random.random() < np.exp(-(v - cur) / temp)):
                st[p] = s1
                improved = v < cur
                cur = v
            else:
                _move(p, s1, s0, spend_y, act_y, count, spend, nz, dur)
                pv_sum -= pv[p, s1] - pv[p, s0]
                improved = False
        elif r < 0.7:  # jump anywhere in the window
            p = np.random.randint(n)
            s1 = lo[p] + np.random.randint(hi[p] - lo[p] + 1)
            s0 = st[p]
            if s1 == s0:
                continue
            _move(p, s0, s1, spend_y, act_y, count, spend, nz, dur)
            pv_sum += pv[p, s1] - pv[p, s0]
            v, dsum, capx, tv = _soft(spend_y, act_y, count, pv_sum, F, base, band_w, band_width, bw, pvw, lam, mu,
                                      cap, L, deficit)
            if v < cur:
                st[p] = s1
                cur = v
                improved = True
            else:
                _move(p, s1, s0, spend_y, act_y, count, spend, nz, dur)
                pv_sum -= pv[p, s1] - pv[p, s0]
                improved = False
        else:  # swap
            p = np.random.randint(n)
            q = np.random.randint(n)
            sp, sq = st[p], st[q]
            if p == q or sp == sq or sq < lo[p] or sq > hi[p] or sp < lo[q] or sp > hi[q]:
                continue
            _move(p, sp, sq, spend_y, act_y, count, spend, nz, dur)
            _move(q, sq, sp, spend_y, act_y, count, spend, nz, dur)
            dpv = pv[p, sq] + pv[q, sp] - pv[p, sp] - pv[q, sq]
            pv_sum += dpv
            v, dsum, capx, tv = _soft(spend_y, act_y, count, pv_sum, F, base, band_w, band_width, bw, pvw, lam, mu,
                                      cap, L, deficit)
            if v < cur or (temp > 0.0 and np.random.random() < np.exp(-(v - cur) / temp)):
                st[p] = sq
                st[q] = sp
                improved = v < cur
                cur = v
            else:
                _move(p, sq, sp, spend_y, act_y, count, spend, nz, dur)
                _move(q, sp, sq, spend_y, act_y, count, spend, nz, dur)
                pv_sum -= dpv
                improved = False
        if tv < best_true and v == cur:
            best_true = tv
            best[:] = st
        if improved:
            since = 0
        else:
            since += 1
            if since >= stall:  # local minimum of the weighted cost: raise the weights of what is violated
                cur, dsum, capx, true = _soft(spend_y, act_y, count, pv_sum, F, base, band_w, band_width, bw, pvw,
                                              lam, mu, cap, L, deficit)
                for t in range(L + 1):
                    if deficit[t] > 0.0:
                        lam[t] *= bump
                if capx > 0.0:
                    mu *= bump
                cur, dsum, capx, true = _soft(spend_y, act_y, count, pv_sum, F, base, band_w, band_width, bw, pvw,
                                              lam, mu, cap, L, deficit)
                since = 0
    return best_true, best


def prepare(inst: Instance, L: int, lp_x: np.ndarray, model) -> dict:
    """Arrays for construct_alpha / fj_search from an instance, a fixed last year and a pulse-model LP solution."""
    pk = K.pack(inst, L)
    width = int((pk.hi - pk.lo).max()) + 1
    cum = np.ones((inst.n, width))
    for p, cols in enumerate(model.start_cols):
        v = np.clip(lp_x[cols], 0.0, None)
        v = v / v.sum() if v.sum() > 0 else np.ones_like(v) / len(v)
        c = np.cumsum(v)
        cum[p, :len(c)] = c
    return {"pk": pk, "cum": cum, "cap": int(inst.max_starts), "L": int(L)}


def run(prep: dict, seed: int, iters: int, w0: float = None, bump: float = 1.5, stall: int = 2000,
        temp0: float = 1e-7) -> tuple[float, np.ndarray]:
    pk = prep["pk"]
    rng = np.random.default_rng(seed)
    alpha = rng.uniform(0.05, 0.95, pk.n)
    st = construct_alpha(prep["cum"], pk.lo, alpha, pk.spend.astype(np.float64), pk.nz, pk.dur, pk.lo, pk.hi, pk.pv,
                         pk.F, prep["cap"], prep["L"])
    w0 = float(pk.bw + pk.band_w.max()) * 10.0 if w0 is None else w0
    return fj_search(st, iters, seed, w0, bump, stall, temp0, pk.spend.astype(np.float64), pk.nz, pk.dur, pk.lo, pk.hi,
                     pk.pv, pk.F, pk.base, pk.band_w, pk.band_width, pk.bw, pk.pvw, prep["cap"], prep["L"])
