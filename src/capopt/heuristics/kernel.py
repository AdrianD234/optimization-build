"""Compiled (numba) schedule state, exact objective and simulated annealing.

The objective here must equal capopt.evaluate.evaluate (tests/test_kernel.py checks this on random schedules).
Start options are contiguous per project: lo[p]..hi[p]; `hi` can be tightened to force a last build year.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numba import njit

from ..instance import Instance

INF = np.inf


@dataclass
class Packed:
    n: int
    T: int
    dur: np.ndarray        # (n,)
    spend: np.ndarray      # (n, Dmax) int64
    nz: np.ndarray         # (n, Dmax) int64 (1 = active year)
    last_nz: np.ndarray    # (n,)
    lo: np.ndarray         # (n,) earliest start
    hi: np.ndarray         # (n,) latest start
    pv: np.ndarray         # (n, T) float64 pv_int at start s (0 where not allowed)
    F: np.ndarray          # (T,) float64
    base: np.ndarray       # (T,) float64 base cap per year
    band_w: np.ndarray     # (nb,) weights
    band_width: np.ndarray  # (T, nb) widths (inf for the open band)
    bw: float
    pvw: float


def pack(inst: Instance, L: int | None = None) -> Packed:
    if inst.layers is not None:
        raise NotImplementedError("the numba kernel does not score distribution layers")
    n, T = inst.n, inst.T
    D = max(len(s) for s in inst.spend)
    spend = np.zeros((n, D), dtype=np.int64)
    nz = np.zeros((n, D), dtype=np.int64)
    pv = np.zeros((n, T))
    lo = np.empty(n, dtype=np.int64)
    hi = np.empty(n, dtype=np.int64)
    for p in range(n):
        d = len(inst.spend[p])
        spend[p, :d] = inst.spend[p]
        nz[p, :d] = inst.nonzero[p]
        a = inst.allowed[p]
        if np.any(np.diff(a) != 1):
            raise NotImplementedError("non-contiguous allowed starts")
        lo[p], hi[p] = a[0], a[-1]
        if L is not None:
            hi[p] = min(hi[p], L - inst.last_nz[p])
        pv[p, a] = inst.pv[p]
    F = inst.F.astype(np.float64)
    env = np.maximum(0.0, F)
    nb = len(inst.bands)
    width = np.empty((T, nb))
    for i, (flo, fhi, _w) in enumerate(inst.bands):
        width[:, i] = np.inf if fhi is None else env * (fhi - flo)
    return Packed(n, T, np.array([len(s) for s in inst.spend], dtype=np.int64), spend, nz, inst.last_nz.copy(), lo, hi,
                  pv, F, env * inst.base_frac, np.array([w for *_, w in inst.bands]), width, inst.backlog_weight,
                  inst.pv_weight)


@njit(cache=True)
def _cost(spend_y, act_y, pv_sum, F, base, band_w, band_width, bw, pvw, penalised):
    T = spend_y.shape[0]
    last = -1
    for t in range(T - 1, -1, -1):
        if act_y[t] > 0:
            last = t
            break
    net = 0.0
    cost = 0.0
    for t in range(last + 1):
        net = net + F[t] - spend_y[t]
        if net < -1e-9:
            return INF
        if t < last and penalised:
            cost += bw * net
            excess = net - base[t]
            if excess > 0:
                for i in range(band_w.shape[0]):
                    take = excess if excess < band_width[t, i] else band_width[t, i]
                    cost += take * band_w[i]
                    excess -= take
                    if excess <= 0:
                        break
    return cost - pvw * pv_sum


@njit(cache=True)
def _apply(p, s, sign, spend_y, act_y, spend, nz, dur):
    for k in range(dur[p]):
        spend_y[s + k] += sign * spend[p, k]
        act_y[s + k] += sign * nz[p, k]


@njit(cache=True)
def state_of(starts, spend, nz, dur, pv, T):
    spend_y = np.zeros(T)
    act_y = np.zeros(T, dtype=np.int64)
    pv_sum = 0.0
    for p in range(starts.shape[0]):
        _apply(p, starts[p], 1, spend_y, act_y, spend, nz, dur)
        pv_sum += pv[p, starts[p]]
    return spend_y, act_y, pv_sum


@njit(cache=True)
def cost_of(starts, spend, nz, dur, pv, F, base, band_w, band_width, bw, pvw, penalised):
    spend_y, act_y, pv_sum = state_of(starts, spend, nz, dur, pv, F.shape[0])
    return _cost(spend_y, act_y, pv_sum, F, base, band_w, band_width, bw, pvw, penalised)


@njit(cache=True)
def anneal(starts, free, iters, t_start, t_end, seed, spend, nz, dur, lo, hi, pv, F, base, band_w, band_width, bw, pvw,
           penalised):
    """Simulated annealing over shift (+-1..4 years) and swap moves on the projects flagged in `free`.
    Infeasible states are never accepted. Returns (best cost, best starts, accepted moves)."""
    np.random.seed(seed)
    T = F.shape[0]
    cur_starts = starts.copy()
    spend_y, act_y, pv_sum = state_of(cur_starts, spend, nz, dur, pv, T)
    cur = _cost(spend_y, act_y, pv_sum, F, base, band_w, band_width, bw, pvw, penalised)
    best = cur
    best_starts = cur_starts.copy()
    idx = np.nonzero(free)[0]
    m = idx.shape[0]
    if m == 0:
        return best, best_starts, 0
    scale = abs(pvw * pv_sum) if pv_sum != 0 else 1.0
    accepted = 0
    for it in range(iters):
        temp = scale * t_start * (t_end / t_start) ** (it / iters)
        if np.random.random() < 0.65 or m < 2:
            p = idx[np.random.randint(m)]
            s0 = cur_starts[p]
            step = np.random.randint(1, 5)
            s1 = s0 + step if np.random.random() < 0.5 else s0 - step
            if s1 < lo[p] or s1 > hi[p]:
                continue
            _apply(p, s0, -1, spend_y, act_y, spend, nz, dur)
            _apply(p, s1, 1, spend_y, act_y, spend, nz, dur)
            pv_sum += pv[p, s1] - pv[p, s0]
            v = _cost(spend_y, act_y, pv_sum, F, base, band_w, band_width, bw, pvw, penalised)
            if v < cur or (v < INF and np.random.random() < np.exp(-(v - cur) / temp)):
                cur = v
                cur_starts[p] = s1
                accepted += 1
            else:
                _apply(p, s1, -1, spend_y, act_y, spend, nz, dur)
                _apply(p, s0, 1, spend_y, act_y, spend, nz, dur)
                pv_sum -= pv[p, s1] - pv[p, s0]
        else:
            p = idx[np.random.randint(m)]
            q = idx[np.random.randint(m)]
            sp, sq = cur_starts[p], cur_starts[q]
            if p == q or sp == sq or sq < lo[p] or sq > hi[p] or sp < lo[q] or sp > hi[q]:
                continue
            _apply(p, sp, -1, spend_y, act_y, spend, nz, dur)
            _apply(q, sq, -1, spend_y, act_y, spend, nz, dur)
            _apply(p, sq, 1, spend_y, act_y, spend, nz, dur)
            _apply(q, sp, 1, spend_y, act_y, spend, nz, dur)
            dpv = pv[p, sq] + pv[q, sp] - pv[p, sp] - pv[q, sq]
            pv_sum += dpv
            v = _cost(spend_y, act_y, pv_sum, F, base, band_w, band_width, bw, pvw, penalised)
            if v < cur or (v < INF and np.random.random() < np.exp(-(v - cur) / temp)):
                cur = v
                cur_starts[p] = sq
                cur_starts[q] = sp
                accepted += 1
            else:
                _apply(p, sq, -1, spend_y, act_y, spend, nz, dur)
                _apply(q, sp, -1, spend_y, act_y, spend, nz, dur)
                _apply(p, sp, 1, spend_y, act_y, spend, nz, dur)
                _apply(q, sq, 1, spend_y, act_y, spend, nz, dur)
                pv_sum -= dpv
        if cur < best:
            best = cur
            best_starts[:] = cur_starts
    return best, best_starts, accepted


@njit(cache=True)
def descend(starts, spend, nz, dur, lo, hi, pv, F, base, band_w, band_width, bw, pvw, penalised):
    """First-improvement local search to a local optimum of the full shift + swap neighbourhood."""
    n = starts.shape[0]
    T = F.shape[0]
    st = starts.copy()
    spend_y, act_y, pv_sum = state_of(st, spend, nz, dur, pv, T)
    cur = _cost(spend_y, act_y, pv_sum, F, base, band_w, band_width, bw, pvw, penalised)
    improved = True
    while improved:
        improved = False
        for p in range(n):
            s0 = st[p]
            _apply(p, s0, -1, spend_y, act_y, spend, nz, dur)
            best_s, best_v = s0, cur
            for s in range(lo[p], hi[p] + 1):
                if s == s0:
                    continue
                _apply(p, s, 1, spend_y, act_y, spend, nz, dur)
                v = _cost(spend_y, act_y, pv_sum + pv[p, s] - pv[p, s0], F, base, band_w, band_width, bw, pvw, penalised)
                _apply(p, s, -1, spend_y, act_y, spend, nz, dur)
                if v < best_v - 1e-9 * abs(best_v):
                    best_s, best_v = s, v
            _apply(p, best_s, 1, spend_y, act_y, spend, nz, dur)
            if best_s != s0:
                pv_sum += pv[p, best_s] - pv[p, s0]
                st[p] = best_s
                cur = best_v
                improved = True
        for p in range(n):
            for q in range(p + 1, n):
                sp, sq = st[p], st[q]
                if sp == sq or sq < lo[p] or sq > hi[p] or sp < lo[q] or sp > hi[q]:
                    continue
                _apply(p, sp, -1, spend_y, act_y, spend, nz, dur)
                _apply(q, sq, -1, spend_y, act_y, spend, nz, dur)
                _apply(p, sq, 1, spend_y, act_y, spend, nz, dur)
                _apply(q, sp, 1, spend_y, act_y, spend, nz, dur)
                dpv = pv[p, sq] + pv[q, sp] - pv[p, sp] - pv[q, sq]
                v = _cost(spend_y, act_y, pv_sum + dpv, F, base, band_w, band_width, bw, pvw, penalised)
                if v < cur - 1e-9 * abs(cur):
                    cur = v
                    pv_sum += dpv
                    st[p] = sq
                    st[q] = sp
                    improved = True
                else:
                    _apply(p, sq, -1, spend_y, act_y, spend, nz, dur)
                    _apply(q, sp, -1, spend_y, act_y, spend, nz, dur)
                    _apply(p, sp, 1, spend_y, act_y, spend, nz, dur)
                    _apply(q, sq, 1, spend_y, act_y, spend, nz, dur)
    return cur, st


def args(pk: Packed, penalised: bool) -> tuple:
    """Positional data arguments shared by cost_of / anneal / descend (after their own leading arguments)."""
    return (pk.spend, pk.nz, pk.dur, pk.lo, pk.hi, pk.pv, pk.F, pk.base, pk.band_w, pk.band_width, pk.bw, pk.pvw, penalised)


def cost(pk: Packed, starts: np.ndarray, penalised: bool) -> float:
    return float(cost_of(np.asarray(starts, dtype=np.int64), pk.spend, pk.nz, pk.dur, pk.pv, pk.F, pk.base, pk.band_w,
                         pk.band_width, pk.bw, pk.pvw, penalised))
