# Iteration loop: closing the gap to COPT

**Goal.** On the Capital Programme Optimiser (CPO) scenarios, reach COPT's stopping point (relative gap 1e-5) faster than COPT 8.0.1 on 20 threads did. Where we can't, get as close as possible and know why. Beating COPT is the target, not a given: it is a fast commercial solver. Our edge is structure: the fixed-last-year decomposition (about 100x smaller models), LP pruning of last years, problem-specific heuristics, and our own parallel search.

## Goals and targets (in priority order)

| # | Goal | Target | Measured by | Status (2026-10-15) |
|---|---|---|---|---|
| G0 | **Correct, always** | every schedule feasible and re-scored by the evaluator; brute-force parity on tiny instances; CI green | `pytest`, `pytest -m slow`, the referee | met |
| G1 | **Beat COPT** on the real CPO data | primary KPI below 1.0: capopt time to gap 1e-5 vs COPT's logged time, 8 timed scenarios | timed campaign | not met: s2000_P50high reaches 0.1% at about 50-90 s, the gap stalls near 0.18% (COPT: 1e-5 in 56 s, warm-started) |
| G2 | **Beat MindOpt on this machine** (standing reference, private results) | capopt faster than MindOpt cold to 1% / 0.1% / gap 1e-5 on the screening set | `mindopt_reference_runner` campaign (one solve at a time, never killed) | results in private notes |
| G3 | **Decision-quality speed** (what a user waits for) | within 1% in under 10 s and within 0.1% in under 60 s on the screening set | reference campaigns (time to 1% / 0.1%) | 1%: 14-23 s (24 leaves); 0.1%: 49-87 s, not on every seed |
| G4 | **Policy insight** for the holding-cash rule | a PV vs peak-cash frontier across the H17 variants, each with its solve speed | H17 campaign | pure PV: +0.36% PV but $1.46B peak cash (vs $344M); variants queued |
| G5 | **Pack regression** | no pack instance more than 20% slower than its last kept result | pack campaign | met |

## The scoreboard (fixed, so iterations are comparable)

| Set | Instances | Reference | Where |
|---|---|---|---|
| **CPO timed** (primary) | 8 GPS27 "objective best" scenarios with a COPT solve time in the CPO notebook's log (s1500/s2000 × base/high benefits, CFADS after debt service × P50/P95 × base/high) | COPT time and gap from the log; the CPO's stored objective | `private/cpo/` (real data, gitignored) |
| **CPO untimed** | CFADS P50/P95, S1750 P50/P95 | the CPO's stored objective (gap 6e-6 to 1e-5) | `private/cpo/` |
| **Pack** (regression) | the four `benchmark_pack` instances | HiGHS-baseline times; proven optima | public |

**Primary KPI:** shifted geometric mean, over the 8 timed scenarios, of (capopt time to gap 1e-5) / (COPT logged time), with a 10 s shift. Below 1.0 means we beat COPT. capopt runs on the **cap-free** instances (removing the start cap moves the root bound by about 0.003%, so this counts as like-for-like; `_startcap100` copies are kept for occasional strict checks). **Variance rule:** each capopt KPI number is the **median of 3 seeds**, shown with its range. COPT's logged times are **single runs** (a commercial solver's time on one instance has been seen to vary about 4x between runs), so a win counts only when capopt's whole range sits below COPT's number.
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

### Noise threshold (calibrated once, 2026-10-15)

The parallel solver is not deterministic on the CPO data: workers share incumbents at timing-dependent moments. A one-off calibration (s2000_P50high, 5 seeds × 90 s, 16 LNS workers) measured the run-to-run spread with nothing changed:

| Config | Time to 1% | Incumbent vs CPO at 30 s | at 90 s |
|---|---|---|---|
| 60 leaves | median 36 s, range 11.5-78.7 s (7x) | +2.1% (+0.33..+3.3%) | +0.098% (+0.064..+0.20%) |
| 24 leaves | median 20.6 s, range 14.3-22.7 s | +0.35% (+0.19..+0.64%) | +0.147% (+0.072..+0.34%) |

Rules that follow:
- **Screen early-phase ideas against the 24-leaf configuration.** Its tight band makes a 1-seed result readable.
- **Better** only if outside the band on the good side: time to 1% under about 14 s, or the 90 s incumbent under about +0.07%. **Worse** only if outside on the bad side. **Inside the band: neutral**, move on.
- **1 seed per screen; 2 only when borderline.** Recalibrate only after a change to the search strategy that could change the noise itself.

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
| H10 | **[PARKED 2026-10-15]** **Native start-year local search** (numba): shift / swap / ejection-chain moves, exact O(T) incremental cost with bands, Feasibility-Jump/Local-MIP-style adaptive cash-row weights, lift moves; 1-16 seeded copies. Refines H2 | Generic one-flip searches fight the one-start-per-project GUB; the 0.8% cash slack needs weighted crossing of infeasible states (`research/HYBRID_AND_CROSSFIELD.md` §2.8) | Time to 1% / 0.1% in seconds; screen standalone at 2/5/10/30/60 s first | proposed |
| H11 | **[PARKED 2026-10-15]** **LP-ordered list scheduling + dual-guided cash repair** (mining TopoSort, Lagrangian / α-point orderings, Chu-Beasley pseudo-utility repair) as the construction step of H2 | LP rounding is cash-infeasible; this is how mine planning and NPV scheduling get from LP to near-optimal (§2.1-2.3, 2.7) | Thousands of diverse cash-feasible seeds per second; first good incumbent < 1 s (today about 40 s) | proposed |
| H12 | **Structure-aware parallel ALNS**: UCB bandit over cash-crunch, year-0/1 cap swap, overlapping time windows, pool crossover, local branching, proximity; SCIP fixing-rate / node-limit rules; SA acceptance; late-phase polishing | Near-optima differ in 128/233 projects, so random 25-project neighbourhoods can't bridge them; ParBalans: diverse single-thread workers beat one big solve (§2.9-2.10) | 0.07% at 200 s → < 0.01% within 60 s | proposed |
| H13 | **Kernel search (MMKP variant)**: kernel = LP support ∪ incumbent ± 2 years; reduced-cost buckets; 2-3 s restricted sub-MIPs with cutoff | The model is a multiple-choice multidimensional knapsack, kernel search's home ground; reuses the sub-MIP code (§2.6) | < 0.1% in about 30 s on one worker | proposed |
| H14 | **Scenario-family donor warm start**: seed from the nearest solved GPS27 sibling (same cost family and benefit, donor funding ≤ recipient), repair + polish | The CPO campaign's own "keep" verdict; COPT's logged times were warm-started too (§2.13) | Within 0.1% before the pool spawns on batch runs; report donor use explicitly | proposed |
| H15 | **MindOpt referee and diagnostic** (protocol in §1.5): arms native-cold / fixed-L-cold / fixed-L-warm, row-column permutations as seeds, never concurrent with capopt timing, results private | Separates formulation, engine and warm-start effects; tells us whether to invest in cuts (H5) or heuristics. Licence clauses (§1.4) must be cleared before any MindOpt→capopt data flow | Diagnosis, not speed | proposed (needs licence check) |
| H16 | **Year-block Dantzig-Wolfe bound probe** (Caprara-Furini-Malaguti) on the screening scenarios | The only decomposition above the LP bound: Lagrangian, BZ and per-project DW all equal it here (§2.3-2.4) | A yes/no on a dual-side research direction | proposed (later) |

Research notes for this backlog: [research/HYBRID_AND_CROSSFIELD.md](research/HYBRID_AND_CROSSFIELD.md) (MindOpt↔HiGHS exchange, the MindOpt benchmark protocol, cross-field techniques). Its root-LP diagnostic finds reduced-cost fixing removes only about 1.5% of columns at the root gap, so H6 should target sub-MIPs and nodes rather than the root. If H2 is picked, build it as H11 (construction) + H10 (improvement).

**Parked: the native local-search theme (H2 via H10 + H11).** Two variants failed stage (a) on all 3 screening scenarios: the greedy cash-safe construction, and alpha-point construction with cash repair followed by adaptive-weight local search. The local search ran at 6-8M moves/s but stalled within 2 s at +4% to +21% from the CPO objective; single-thread LNS reaches +0.88% in 90 s. Reason: on this data any one- or two-project move shifts spend between years and breaks the cumulative cash balance or lands in the steep tiers. Good moves need several projects to move together, which sub-MIP neighbourhoods (LNS, H12, H13) do and move-based searches don't. Reopen only with compound, cash-balanced moves (e.g. ejection chains that keep every year's cumulative balance), or as a polisher inside a sub-MIP framework.

## Model decision: no start-count cap (2026-10-15)

The CPO model capped project starts at 100 per year, which binds in 2026 on the envelope scenarios. **capopt's default model now has no start cap** (user decision). `tools/extract_cpo.py` writes the uncapped instances under the scenario names, plus `<name>_startcap100` copies that are kept *only* as the like-for-like reference for COPT's logged times, since COPT solved the capped model. Removing the cap barely changes the root LP bound: -0.565% → -0.568% (s2000_P50high), -0.574% → -0.577% (s2000_P50), and -0.142% unchanged (cfadsds_P50). So the cap is not the source of the difficulty (this answers Q5 below at the root). Results recorded before this date used the capped model.

## Research questions (Q-list, 2026-10-15)

| # | Question | Why it matters | Cheap test | Status |
|---|---|---|---|---|
| Q1 | Have we over-fitted to s2000_P50high? | Nearly all tuning used one scenario | Current best config once across all 8 timed scenarios | queued |
| Q2 | Does a warm start (H14 donor) close most of the gap to COPT? | COPT's times were warm-started; the real workflow is batches of related scenarios | Seed from a sibling scenario's optimum, then time to 1e-5 | queued |
| Q3 | What gap is decision-relevant? | 1e-5 of about $95B is about $1M of PV; 0.01% (about $10M) may be enough | User decision, then re-score stored runs | needs the user |
| Q4 | How different are near-optimal schedules? | Firm vs swappable projects is more useful to planners than one schedule | Pool all schedules within 0.01%; per-project start-year ranges | queued |
| Q5 | Does the start cap drive the difficulty? | It bound in 2026 | Root LP with and without the cap | answered at the root: no (see above) |
| Q6 | Where does hardness come from (envelope tightness)? | Slack is about 0.8%; predicts which scenarios will be slow | Envelope sweep $1.5B-$2.5B: root gap and time to 0.1% | queued |
| Q7 | How does it scale with programme size? | Programmes grow | Project subsets of 150 / 200 / 233 | queued |
| Q8 | How fast is an incremental re-solve? | Re-optimising after a cost update | Perturb 1-5 projects, warm start, time to 0.1% | queued |
| Q9 | Is a deterministic mode needed (audit)? | The parallel solver can return different equal-value schedules | User decision | needs the user |
| Q11 | How much of a commercial solver's time is the end-of-programme logic? | In the original model the last build year is decided inside the MIP (per-year activity binaries, indicators, final-year dividend); capopt removes this by fixing L | MindOpt arms on the 3 screening scenarios (one solve at a time, overnight block): M0 original (done); M-fixL original model with the last year fixed; M2 capopt's fixed-L model; optional M-nodiv (no dividend, funding every year, all years penalised: a policy variant compared on time only) | queued (overnight) |
| Q10 | Fork HiGHS for an in-run cutoff update? | The branching research's most valuable small fork | Decide after the E1/E2 screen | pending E1/E2 |


## Research recommendations tracker

Every recommendation in `docs/research/` is tracked here. Status: ✅ done, ◐ partly, ❌ not tried, ✖ dropped on evidence.

| Source | Recommendation | Status | Where |
|---|---|---|---|
| BRANCHING_AND_TREE E1 | Live leaf bounds, bound inheritance | ✅ | E1 |
| BRANCHING_AND_TREE E2 | Phase-aware leaf cutoff | ✅ | E2 (`cutoff_after`) |
| BRANCHING_AND_TREE E3 | Strong-branching ramp-up | ✅ | E11, E12, E22, E23 |
| BRANCHING_AND_TREE E4 | Hedged re-split: the parent keeps running while its children use idle workers | ✖ (s2000) | E31 pre-screen: hard plateau leaves' children are as hard as their parents (1 of 7 subtrees closed); not built |
| BRANCHING_AND_TREE E5 | Proof-phase leaf settings (heuristic effort, pseudocost reliability) | ✖ | E30: no gain (defaults as good or better) |
| BRANCHING_AND_TREE E6 | Endgame racing | ◐ | E28: correct (audited), 0-15% gain, off; E31: `threads=3` no gain; encoding variants untried |
| BRANCHING_AND_TREE E7 | General disjunctions | ✖ | gap anatomy: variable splits dominate |
| BRANCHING_AND_TREE E8 | Leaf difficulty census and scheduling | ◐ | E31 census: 7 of 60 leaves hard, not predictable from the LP bound |
| BRANCHING_AND_TREE fork | Dynamic in-solve cutoff (HiGHS change) | ❌ | Q10 |
| GAP_ANATOMY #1 | Strong-branching ramp-up and re-split | ✅ | E11-E12 |
| GAP_ANATOMY #2 | Outer LP tree as the main proof engine | ◐ deprioritised | E29: about as fast as HiGHS leaves on P95's L = 43, slower on cfadsds_P50; not worth a big build yet |
| GAP_ANATOMY #3 | Year-block Lagrangian bound | ✅ tool | E32: `capopt.bounds`, valid and tested; 18-43% of the L = 42 gap |
| GAP_ANATOMY #4-5 | Gomory splits; drop list | ✖ | as advised |
| CFADS_BOUND_STALL #1 | Capped product score, k = 16 | ✅ | E22, E23 |
| CFADS_BOUND_STALL #2(a) | More leaves (about 200) when the root has more than 15 fractional projects | ✖ | E29: the ramp-up dominates (P50 321 s vs 172 s) |
| CFADS_BOUND_STALL #2(b) | Work stealing | ✖ | E27: correct but slower |
| CFADS_BOUND_STALL #3(a) | Root cover cuts | ✖ | E22B: no gain |
| CFADS_BOUND_STALL #3(b) | End-of-horizon covering cuts | ❌ | queued |
| CFADS_BOUND_STALL #4 | Final-years block bound to prune P95's L = 42 | ✖ | E32: closes at most 43% of the gap, so it cannot prune; multi-block decomposition (H16) would be needed |
| PARALLEL_BB R0, R5 | Pin LP-integral re-queues; reopen unexpected interrupts | ✅ | E27 audit |
| PARALLEL_BB R1-R4 | Steal tuning | — | moot while stealing is off |
| PARALLEL_BB R6 / UG | Never kill the donor | ❌ | equals BRANCHING E4 above |
| MASTER_PLAN Phase 5 | SCIP as an alternative engine | ✖ (leaves) | E33: SCIP 10 is no better than HiGHS on the hard plateau leaves |
| E33 follow-up | Engine racing: HiGHS + SCIP copies of a hard leaf | ✖ | E35: 3 of 7 closed against 1 of 7, but the 4 hardest resist everything; not integrated |
| E33 follow-up | SCIP callbacks: capopt's capped strong-branching rule inside the tree | ✖ | E35: even full strong branching closes 0 of 7, so branching has no headroom; a cut separator remains untested |
| E33 follow-up | SCIP exact (rational) mode to certify proofs | ❌ | only if audit-grade proofs are needed (Q9) |
| KPI analysis | Prove the surviving last years concurrently | ✅ | E34: cfadsds_P95 1.19x to 1.00x; now the default |

Queue (cheap and high value first):
1. CFADS #2(a) more leaves (one flag).
2. BRANCHING E5 proof-phase leaf settings (one flag).
3. BRANCHING E4 hedged re-split (tests and an audit, as for E27/E28).
4. CFADS #4 final-years block bound.
5. GAP #2 persistent outer LP tree.
6. The HiGHS dynamic-cutoff fork.
