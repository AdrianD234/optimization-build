# Iteration loop: closing the gap to COPT

**Goal.** On the Capital Programme Optimiser (CPO) scenarios, reach COPT's stopping point (relative gap 1e-5) faster than COPT 8.0.1 on 20 threads did. Where we can't, get as close as possible and know why. Beating COPT is the target, not a given: it is a fast commercial solver. Our edge is structure: the fixed-last-year decomposition (about 100x smaller models), LP pruning of last years, problem-specific heuristics, and our own parallel search.

## The scoreboard (fixed, so iterations are comparable)

| Set | Instances | Reference | Where |
|---|---|---|---|
| **CPO timed** (primary) | 8 GPS27 "objective best" scenarios with a COPT solve time in the CPO notebook's log (s1500/s2000 × base/high benefits, CFADS after debt service × P50/P95 × base/high) | COPT time and gap from the log; the CPO's stored objective | `private/cpo/` (real data, gitignored) |
| **CPO untimed** | CFADS P50/P95, S1750 P50/P95 | the CPO's stored objective (gap 6e-6 to 1e-5) | `private/cpo/` |
| **Pack** (regression) | the four `benchmark_pack` instances | MindOpt recorded times; proven optima | public |

**Primary KPI:** shifted geometric mean, over the 8 timed scenarios, of (capopt time to gap 1e-5) / (COPT logged time), with a 10 s shift. Below 1.0 means we beat COPT.
**Secondary:** time to within 1% and 0.1% of the CPO objective, final objective vs the CPO's (we may find better schedules), and pack regression (no instance more than 20% slower).

Caveats that favour COPT, kept visible in every report:
- COPT's log time covers the solver only, after a warm start built in uncounted time. Ours is wall-clock including model build, from a cold start.
- The logged runs were most likely made on this machine (the log reports the same 20-core / 20-thread spec), but that is not proven.

## One iteration

1. **Pick** the top hypothesis from the backlog. Each hypothesis is a branch `exp/NN-<slug>` with a stated expected effect and a "how we'll know".
2. **Screen** on the 3 hardest timed scenarios with a 300 s cap, as one watched campaign (`capopt campaign launch` + the milestone watcher). That takes minutes, not hours.
3. **Decide** using the rules below. A promising screen goes on to the full scoreboard campaign (8 + 4 + pack).
4. **Record** in `docs/EXPERIMENTS.md`: hypothesis, protocol, numbers, verdict. Every claim must come from a stored run.
5. **Review.** After each iteration a reviewer (a fresh agent or session, with this file and the latest results) re-ranks the backlog and proposes the next 1-3 experiments. You approve or redirect at that checkpoint.

### Decision rules (converge vs back off)

- **Keep:** primary KPI improves by at least 5% on the full scoreboard, no CPO scenario gets more than 20% slower, and the pack regression holds.
- **Converge on a theme:** a kept change opens its theme (e.g. "primal heuristics"). Its next variants move up the backlog.
- **Back off:** two consecutive failed variants within a theme park that theme, with a recorded reason. Reopen it only with new evidence, such as a diagnostic showing that bottleneck has returned.
- **Diagnose before guessing:** if a result surprises us, add instrumentation first (dual-bound trace, time spent per phase, leaf statistics), then form the next hypothesis.

### Guardrails (every iteration)

- `pytest` and `pytest -m slow` green; every schedule re-scored by the referee or evaluator. An incorrect fast solver is worthless.
- One heavy job on the machine at a time; every long run goes through `capopt campaign` (heartbeat, a 5-minute stall alarm, no windows).
- CPO data and CPO-derived results stay under `private/`. Only methods and aggregate findings are published.

## Backlog (ranked; re-ranked at every review)

| # | Hypothesis | Why we think so | Expected effect | Status |
|---|---|---|---|---|
| H1 | **Instrument first**: dual bound and gap over time, time per phase (quick try / ramp-up / leaves), leaf statistics | The first CPO run was slow and we can't yet see why | Diagnosis, not speed | next |
| H2 | **Fast warm start** before or while the pool spawns: greedy fill and numba local search on the fixed-L model, then share it as the incumbent | COPT starts from a supplied schedule; capopt had no incumbent for ~40-60 s on CPO | Time to 1% / 0.1% drops to seconds; earlier pruning shortens the proof | queued |
| H3 | **Stream leaves**: start solving leaves while the ramp-up is still splitting, and do ramp-up LPs in the workers | The ramp-up is serial: ~19 s (CFADS) and likely more on the envelope models | Removes most of the idle start | queued |
| H4 | **Racing portfolio at start**: a few full-model HiGHS runs (seeds / encodings / heuristic effort) next to the tree | Erraticism (Fischetti-Monaci); HiGHS' primal heuristics vary a lot by seed | Faster first good incumbents | queued |
| H5 | **Cover cuts on wide-gap instances**: the cut loop overlapped with spawn, used when the root gap > 0.1% | On the pack, cuts helped most where the root gap was wide (raw_nopenalty -21%); the CPO root gaps are 0.1-0.6% | A tighter bound means fewer nodes | queued |
| H6 | **Reduced-cost fixing / start-window tightening** from the root LP and the incumbent | 233 projects × up to 60 starts; many starts are provably useless once a good incumbent exists | A smaller model per leaf | queued |
| H7 | **Branching priority**: split first on high-spend or long projects and on the binding 2026 start cap | The 100-starts cap binds in 2026 on the envelope scenarios | Better-balanced leaves | queued |
| H8 | **HiGHS option tuning** per leaf (heuristic effort, feasibility jump, RINS) on the screening set | Defaults are tuned for MIPLIB, not for this structure | Modest, cheap to test | queued |
| H9 | **Evolve-a-heuristic loop** (FunSearch/AlphaEvolve pattern, `docs/research/AI_MATH_RESULTS.md`) on the warm-start rule and the LNS neighbourhoods | Once H2 exists, its rule is a natural target | Long-term | later |
| H10 | **Native start-year local search** (numba): shift / swap / ejection-chain moves, exact O(T) incremental cost with bands, Feasibility-Jump/Local-MIP-style adaptive cash-row weights, lift moves; 1-16 seeded copies. Refines H2 | Generic one-flip searches fight the one-start-per-project GUB; the 0.8% cash slack needs weighted crossing of infeasible states (`research/HYBRID_AND_CROSSFIELD.md` §2.8) | Time to 1% / 0.1% in seconds; screen standalone at 2/5/10/30/60 s first | proposed |
| H11 | **LP-ordered list scheduling + dual-guided cash repair** (mining TopoSort, Lagrangian / α-point orderings, Chu-Beasley pseudo-utility repair) as the construction step of H2 | LP rounding is cash-infeasible; this is how mine planning and NPV scheduling get from LP to near-optimal (§2.1-2.3, 2.7) | Thousands of diverse cash-feasible seeds per second; first good incumbent < 1 s (today about 40 s) | proposed |
| H12 | **Structure-aware parallel ALNS**: UCB bandit over cash-crunch, year-0/1 cap swap, overlapping time windows, pool crossover, local branching, proximity; SCIP fixing-rate / node-limit rules; SA acceptance; late-phase polishing | Near-optima differ in 128/233 projects, so random 25-project neighbourhoods can't bridge them; ParBalans: diverse single-thread workers beat one big solve (§2.9-2.10) | 0.07% at 200 s → < 0.01% within 60 s | proposed |
| H13 | **Kernel search (MMKP variant)**: kernel = LP support ∪ incumbent ± 2 years; reduced-cost buckets; 2-3 s restricted sub-MIPs with cutoff | The model is a multiple-choice multidimensional knapsack, kernel search's home ground; reuses the sub-MIP code (§2.6) | < 0.1% in about 30 s on one worker | proposed |
| H14 | **Scenario-family donor warm start**: seed from the nearest solved GPS27 sibling (same cost family and benefit, donor funding ≤ recipient), repair + polish | The CPO campaign's own "keep" verdict; COPT's logged times were warm-started too (§2.13) | Within 0.1% before the pool spawns on batch runs; report donor use explicitly | proposed |
| H15 | **MindOpt referee and diagnostic** (protocol in §1.5): arms native-cold / fixed-L-cold / fixed-L-warm, row-column permutations as seeds, never concurrent with capopt timing, results private | Separates formulation, engine and warm-start effects; tells us whether to invest in cuts (H5) or heuristics. Licence clauses (§1.4) must be cleared before any MindOpt→capopt data flow | Diagnosis, not speed | proposed (needs licence check) |
| H16 | **Year-block Dantzig-Wolfe bound probe** (Caprara-Furini-Malaguti) on the screening scenarios | The only decomposition above the LP bound: Lagrangian, BZ and per-project DW all equal it here (§2.3-2.4) | A yes/no on a dual-side research direction | proposed (later) |

Research notes for this backlog: [research/HYBRID_AND_CROSSFIELD.md](research/HYBRID_AND_CROSSFIELD.md) (MindOpt↔HiGHS exchange, the MindOpt benchmark protocol, cross-field techniques). Its root-LP diagnostic finds reduced-cost fixing removes only about 1.5% of columns at the root gap, so H6 should target sub-MIPs and nodes rather than the root. If H2 is picked, build it as H11 (construction) + H10 (improvement).
