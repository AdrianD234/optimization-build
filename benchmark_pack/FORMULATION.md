# Problem formulation

A capital programme of **100 projects** must be scheduled over **60 years (2026-2085)** under a fixed annual funding envelope. Every project is delivered exactly once; the only decision is **when each project starts**.

## Data (per instance file)

| Symbol | Meaning | Field |
|---|---|---|
| t = 0..59 | model year (0 = 2026) | `T`, `base_year` |
| d_p | build duration of project p (years) | `projects[].duration` |
| c_{p,k} | spend of project p in its k-th build year, integer units of $0.01M | `projects[].spend_S` |
| S_p | allowed start years for p (finishes inside the horizon, fundable on its own) | `projects[].allowed_starts` |
| v_{p,s} | PV of p's benefits if it starts in year s, integer ($ x 10,000) | `projects[].pv_int` (aligned with `allowed_starts`) |
| F_t | funding per active year: 21,000 units = $210M | `funding_S` |
| w_B, w_PV, bands | objective weights and holding-cash bands | `weights`, `tiers` |

`spend_nonzero[k]` marks build years with a non-zero amount (one tiny amount rounds to 0 units but still counts as an active year).

## Decision

x_{p,s} in {0,1}: project p starts in year s.  Constraint: sum over s in S_p of x_{p,s} = 1 for every p (at most 100 starts per year - never binding).

## Cash rules (derived from x)

* Spend in year t: spend_t = sum_{p,s} x_{p,s} c_{p,t-s}.
* `last` = the last year with any (non-zero) spend. The programme is **active** in years 0..last; funding F_t arrives in every active year and none after.
* Closing balance: net_t = net_{t-1} + F_t - spend_t, with **net_t >= 0** in every year (no borrowing). In year `last` the remaining balance is released at no cost.

## Objective (minimise)

    sum_{t < last} [ w_B * net_t + penalty(net_t) ]  -  w_PV * sum_{p,s} v_{p,s} x_{p,s}

penalty(net) = the part of net above **16.88% of F** (= $35.4M) is split into bands and charged per unit per year:

| Band (share of F) | $M | Weight per $0.01M-year |
|---|---|---|
| 16.88% - 22.50% | 35.4 - 47.3 | 1,000 |
| 22.50% - 28.13% | 47.3 - 59.1 | 10,000 |
| 28.13% - 33.76% | 59.1 - 70.9 | 100,000 |
| above 33.76% | > 70.9 | 1,000,000 |

`*_penalised` instances: w_B = 1, w_PV = 1e-4, band weights as above (the production objective).
`*_nopenalty` instances: w_B = 0, all band weights 0, w_PV = 1 (maximise PV only; holding cash is free).

## How v_{p,s} was built (for reference only - it is given)

v_{p,s} = round( 10,000 x sum_{t = s+d_p}^{min(s+d_p+39, 59)} b_p (1+g_p)^t / D_t )

b_p = annual benefit ($), flat for 40 years after construction; g_p = real benefit growth (0 in `raw`, 1-3% in `growth`); D_t = MBCM discount (2% p.a. for years 1-30, 1.5% after, D_0 = 1). Benefits after 2085 are not counted.

## Exact scoring

`reference/evaluate.py` is the referee: it applies exactly these rules and reproduces the MindOpt objective of the reference solutions to ~1e-15. Any formulation or algorithm is allowed; only the evaluator's score of the returned schedule counts.
