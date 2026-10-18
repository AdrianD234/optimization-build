"""Benefit streams behind the PV table: recover, reshape and grow them, then rebuild pv_int.

An instance stores, per project, only the PV of its benefits at each allowed start (pv_int). For the CPO scenarios that
PV comes from a real benefit stream that starts at completion (start + duration) and runs for `years` years, discounted
by the MBCM factors and truncated at the horizon (tools/extract_cpo.py). With the CPO's "LIN40" benefits the stream is
flat (the same real benefit every year, so cumulative benefit grows linearly), which makes it recoverable exactly from
pv_int: `flat_annual`. From the stream this module builds:

* shapes (`shape_stream`), each with the same real-terms total over the same 40 years:
    "linear"  the supplied flat stream: cumulative benefit rises in a straight line (LIN40)
    "log"     concave accumulation C(x) = ln(1 + lam x) / ln(1 + lam N): front-loaded (half arrives in about 10 years)
    "exp"     convex accumulation C(x) = (e^(mu x) - 1) / (e^(mu N) - 1): back-loaded, the mirror image of "log"
              (mu is set so that C_exp(N - x) = 1 - C_log(x) at x = N / 4)
  The annual benefit of year k is the increase of C over [k, k + 1] times the total.
* growth (`pv_row`): annual benefits grow by g a year, either
    "calendar"  indexed to the calendar (year 0 = the base year): a later start earns more growth, so when g exceeds the
                discount rate the PV of a project *rises* with delay (the deferral bias);
    "opening"   anchored to each project's own opening year (year k of operation earns (1 + g)^k): delay earns nothing.

Everything is real-terms and in the instance's units (pv_int = PV in $ x 10,000). Pure functions of the instance dict;
no private data in here.
"""
from __future__ import annotations

import copy
import math

import numpy as np

PV_SCALE = 10_000


def mbcm_discount(T: int) -> np.ndarray:
    """MBCM discount factors: 2% a year for years 1-30, 1.5% after (year 0 = the base year)."""
    d = np.ones(T)
    for t in range(1, T):
        d[t] = d[t - 1] * (1.02 if t <= 30 else 1.015)
    return d


def _years(s: int, dur: int, n: int, T: int) -> np.ndarray:
    y = s + dur + np.arange(n)
    return y[y < T]


def flat_annual(raw: dict, p: int, years: int = 40, disc: np.ndarray | None = None) -> tuple[float, float]:
    """(annual real benefit in $, worst relative misfit over the allowed starts) of project p, assuming a flat stream of
    `years` years from completion. A misfit near 0 confirms the stream is flat."""
    T = int(raw["T"])
    d = mbcm_discount(T) if disc is None else disc
    pr = raw["projects"][p]
    dur = int(pr.get("duration", len(pr["spend_S"])))
    fac = [float((1.0 / d[_years(int(s), dur, years, T)]).sum()) for s in pr["allowed_starts"]]
    j = int(np.argmax(fac))
    c = pr["pv_int"][j] / PV_SCALE / fac[j] if fac[j] > 0 else 0.0
    mis = max((abs(c * f - v / PV_SCALE) / max(1.0, abs(v / PV_SCALE)) for f, v in zip(fac, pr["pv_int"]) if f > 0),
              default=0.0)
    return float(c), float(mis)


def _mirror_mu(lam: float, n: int) -> float:
    """mu with C_exp(n - n/4) = 1 - C_log(n/4) (bisection; C_exp(x) is decreasing in mu at fixed x < n)."""
    target = 1.0 - math.log1p(lam * n / 4) / math.log1p(lam * n)
    x = 0.75 * n
    lo, hi = 1e-6, 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        v = math.expm1(mid * x) / math.expm1(mid * n)
        lo, hi = (mid, hi) if v > target else (lo, mid)
    return 0.5 * (lo + hi)


def cumulative_share(shape: str, n: int = 40, lam: float = 0.25) -> np.ndarray:
    """C(0..n): the share of the total benefit delivered by the end of each year (C(0) = 0, C(n) = 1)."""
    x = np.arange(n + 1, dtype=float)
    if shape == "linear":
        return x / n
    if shape == "log":
        return np.log1p(lam * x) / math.log1p(lam * n)
    if shape == "exp":
        mu = _mirror_mu(lam, n)
        return np.expm1(mu * x) / math.expm1(mu * n)
    raise ValueError(f"unknown shape {shape!r}")


def shape_stream(total: float, shape: str, n: int = 40, lam: float = 0.25) -> np.ndarray:
    """Annual real benefits for years 1..n after opening with the given shape and the given real total."""
    return total * np.diff(cumulative_share(shape, n, lam))


def pv_row(stream: np.ndarray, dur: int, allowed, T: int, disc: np.ndarray | None = None, growth: float = 0.0,
           anchor: str = "calendar") -> list[int]:
    """pv_int for each allowed start of a project with this annual real stream (year 1 = the completion year),
    truncated at the horizon, with benefits growing by `growth` a year from the base year ("calendar") or from the
    project's own opening ("opening")."""
    d = mbcm_discount(T) if disc is None else disc
    stream = np.asarray(stream, dtype=float)
    k = np.arange(len(stream))
    out = []
    for s in allowed:
        y = int(s) + dur + k
        ok = y < T
        g = (1.0 + growth) ** (y[ok] if anchor == "calendar" else k[ok]) if growth else 1.0
        out.append(int(round(float((stream[ok] * g / d[y[ok]]).sum()) * PV_SCALE)))
    if anchor not in ("calendar", "opening"):
        raise ValueError(f"unknown anchor {anchor!r}")
    return out


def rebuild(raw: dict, streams: dict[int, np.ndarray], growth: float = 0.0, anchor: str = "calendar",
            name: str | None = None) -> dict:
    """A copy of the instance with pv_int rebuilt from the given streams (project index -> annual real benefits);
    projects without a stream keep theirs unless growth applies, in which case their flat stream is recovered first."""
    out = copy.deepcopy(raw)
    T = int(raw["T"])
    d = mbcm_discount(T)
    for p, pr in enumerate(out["projects"]):
        if p in streams:
            st = streams[p]
        elif growth:
            c, _ = flat_annual(raw, p, disc=d)
            st = np.full(40, c)
        else:
            continue
        dur = int(pr.get("duration", len(pr["spend_S"])))
        pr["pv_int"] = pv_row(st, dur, pr["allowed_starts"], T, d, growth, anchor)
    if name is not None:
        out["name"] = name
    out.setdefault("benefits", {}).update({"growth": growth, "anchor": anchor, "reshaped": sorted(streams)})
    return out
