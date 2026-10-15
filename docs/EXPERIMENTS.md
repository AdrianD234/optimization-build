# Experiment log

Each experiment states its hypothesis, protocol and result, and links to its stored data. Machine: Intel family 6 model 198, 20 threads, Windows 11 build 26300 (the machine MindOpt was benchmarked on). Times are wall-clock seconds.

## exp01: formulation variants of the fixed-last-year model (single thread)

* **Hypothesis**: fixing the last year at L = 18 makes the model small enough for single-threaded HiGHS to compete with 20-thread MindOpt, and the step encoding branches better than pulse.
* **Protocol**: `benchmarks/experiments/exp01_formulations.py <instance> 18 <encoding> <balance> 180`, all four variants, both penalised instances, 180 s limit, run concurrently (8 processes on 20 threads).
* **Result**:

| Instance | Encoding / balance | Status | Time to 1% | Time to 0.1% | Time to best-known | Proof |
|---|---|---|---|---|---|---|
| growth_penalised | pulse / recursive | optimal | 1.2 | 7.7 | 27.4 | 27.9 |
| growth_penalised | pulse / cumulative | optimal | 2.0 | 2.0 | 27.7 | 28.7 |
| growth_penalised | **step / cumulative** | optimal | 0.7 | 0.7 | 16.5 | **17.8** |
| growth_penalised | step / recursive | optimal | 8.0 | 8.0 | 35.7 | 37.6 |
| raw_penalised | pulse / recursive | time limit (180) | 1.4 | 3.1 | - | - |
| raw_penalised | pulse / cumulative | time limit (180) | 5.4 | 31.2 | 167.7 | - |
| raw_penalised | step / cumulative | time limit (180) | 2.9 | 26.4 | - | - |
| raw_penalised | step / recursive | time limit (180) | 13.1 | 28.7 | - | - |

* **Verdict**: kept. Step encoding with cumulative balance rows is the default. `growth_penalised` is proven on one thread in 18 s. `raw_penalised` is still open after 180 s, so it needs parallelism.

## exp-LP: LP bounds of every last year L (penalised instances)

* Every LP bound for L in 19..59 is at least 66.6% (growth) or 122% (raw) worse than the best-known objective. All 42 LPs solve in about 3 s, so for both penalised instances the proof reduces to MIP(18). This is tested in `tests/test_decomposition.py`.

## exp02: parallel split-and-solve (20 workers)

* **Hypothesis**: a best-first LP ramp-up to K leaves, solved in 20 worker processes with a shared incumbent, gives a near-linear speed-up on the proof.
* **Protocol**: `benchmarks/experiments/exp02_parallel.py <instance> 18 20 <K> <encoding> cumulative`. Data: `benchmarks/results/exp/exp02_sweep.jsonl`.
* **Result**:

| Instance | Leaves | Encoding | Time to 0.1% | Time to best-known | Proof |
|---|---|---|---|---|---|
| raw_penalised | 60 | step | 3.1 | 71.6 | **79.8** |
| raw_penalised | 120 | step | 5.5 | 90.6 | 97.9 |
| raw_penalised | 240 | step | 10.4 | 110.6 | 116.4 |
| raw_penalised | 120 | pulse | 6.1 | 80.4 | 83.3 |
| growth_penalised | 60 | step | 1.8 | 2.3 | **4.0** |
| growth_penalised | 120 | step | 3.4 | 3.4 | 4.3 |

* **Verdict**: kept, with 60 leaves as the default. The proof finishes a few seconds after the optimum is found, so on raw_penalised the bottleneck is now the primal side (finding the optimum), not the bound. Next steps: time-sliced re-splitting (each restart inherits the newest cutoff) and stronger primal heuristics.

## Ladder runs I1/I2: fixed-last-year decomposition on one thread (all four instances)

* **Protocol**: `capopt bench --configs fixedL_pulse_1t` and `--configs fixedL_step_1t`, 900 s limit, run as two concurrent single-thread chains (plus the B0 chain) on the 20-thread machine. Stored under `benchmarks/results/fixedL_*`.
* **Result** (proof times): raw_penalised 447 s (I1) / **216 s** (I2); growth_penalised 24 / **21**; raw_nopenalty **222** / 249; growth_nopenalty 1.5 / **0.58**.
* **New proven optimum for P100_raw_nopenalty**: -74,119,916,766,966. That is 5.2e-6 relative better than the pack's best-known (-74,119,531,264,779, not previously proven). Both encodings prove it independently, and `benchmark_pack/reference/evaluate.py` confirms it. Stored in `benchmarks/solutions/P100_raw_nopenalty_proven_optimal.json` (the pack itself is left unchanged).

## Bug found: objective scaling on the no-penalty instances

* HiGHS' dual simplex failed with "excessive dual values" on the no-penalty LP relaxation (PV coefficients near 2.4e12). The MIP path recovers internally, but our LP bounding and ramp-up tree did not. Fix: `MipBuilder.auto_scale` scales the objective down by a power of two (exact) until the largest coefficient is at most 2^28, and `Model.user()` converts values back. Covered by `tests/test_cuts_and_scaling.py`.

## exp04: cover cuts on the cumulative-budget rows

* **Separation**: GUB covers in "started-by" variables (`src/capopt/cuts.py`). The first version found no cuts, because ties on y* = 1 picked the lightest pair (bug). After the fix, 30 LP rounds close 12.3% of the root gap on raw_penalised, 0.3% on growth_penalised, 9.2% on raw_nopenalty and 51.8% on growth_nopenalty. All cuts are valid: the best-known schedules satisfy them (tested).
* **A/B on branch-and-bound** (single thread, step/cumulative; `exp04_cuts.py`; one run each, so noisy):

| Instance | Cuts | Proof (s) | Nodes |
|---|---|---|---|
| raw_penalised (L=18) | none | 215.8 | 53,682 |
| raw_penalised (L=18) | 294 (30 rounds) | **177.1** | **30,811** |
| raw_nopenalty | none | **250.3** | 70,669 |
| raw_nopenalty | 329 (30 rounds) | 286.8 | 45,436 |
| growth_nopenalty | 102 (30 rounds) | 2.5 | 5 |

* **Verdict**: cuts always cut the node count (by 36-43%), but the extra rows slow every LP. The net effect is -18% wall time on raw_penalised and +15% on raw_nopenalty. They are kept as an option (`cut_rounds`, ladder rung I6) and judged on the 20-worker runs.

## Infrastructure bugs found by the experiments

* **HiGHS process-wide scheduler.** After any HiGHS run with the default thread count, a later `threads=1` run in the same process fails with model status "Not Set". `solve.new_highs()` now pins thread counts and resets the scheduler when the count changes (regression test in `tests/test_cuts_and_scaling.py`).
* **Concurrent appends on Windows.** Several Python processes appending (`>>`) to one file through Git Bash overwrite each other's lines, which looked like silently lost runs. Every run now writes its own file.
* **HiGHS ignoring its time limit** on the reference model for raw_nopenalty: the run was still going after 30 minutes on a 900 s limit. The benchmark watchdog (`bench.run_isolated`) kills it at limit + 180 s and rebuilds the record from the streamed incumbent trace. MindOpt runs are exempt from killing (licence seat; see `mindopt_runner.py`).
* **A background benchmark died at launch and nobody noticed for about 50 minutes** (2026-10-15).
  * **What happened:** the holdout session started its 48-run benchmark in the background from the Bash tool at 17:16. At 18:08 there were no solver processes, the log folder was empty and there were no result files. The agent was still waiting on a notification that never came.
  * **Root cause:** a detached shell job (`&`) that can die silently, with nothing checking that it had started or was still alive.
  * **Fix:** `capopt campaign` (`src/capopt/campaign.py`).
    * The runner keeps a heartbeat file and is started detached by a launcher that verifies the heartbeat within 60 s.
    * `capopt status` and `capopt watch` report STALLED within 5 minutes of a dead runner, a frozen heartbeat or an overdue run.
    * The agent runbook (MASTER_PLAN §7) forbids bare `&` for long jobs and requires a watcher.
  * **Tests:** `tests/test_campaign.py`, including a real kill of a detached runner.

## Parallel ladder I3-I6 (20 workers, 3 repeats each, machine otherwise idle)

Proof times in seconds, as the median (range) over 3 repeats. Stored under `benchmarks/results/capopt_*`.

| Config | raw_penalised | growth_penalised | raw_nopenalty | growth_nopenalty | Verdict |
|---|---|---|---|---|---|
| I3 split-and-solve, 60 leaves | **81** (80-82) | **6.4** (6.3-6.4) | 73 (73-73) | **2.6** | kept (default) |
| I4 + time-sliced re-splitting (10 s) | 139 (102-140) | 6.3 | 79 (79-81) | 2.6 | **rejected**: restarts discard HiGHS' search state, and the optimum is found later |
| I5 + 4 LNS workers (60 s) | 81 (79-81) | 6.6 | 75 (75-75) | 2.7 | neutral: 6-10 improvements per worker, but never the final one before the tree finds it |
| I6 + root cover cuts (30 rounds) | 95 (92-99) | 8.0 | **58** (57-59) | 5.1 | mixed: -21% on raw_nopenalty, +17% on raw_penalised; the cut loop ran serially before the pool |

Times to within 1% and 0.1% are 1.8-3.5 s for I3 on every instance. The pack's best-known value is reached in 2.4-73 s.

## I7/I8: overlap work with pool start-up

* **I7 quick try**: while the 20 workers spawn (about 2 s on Windows), the master tries a direct single-thread solve for 1.5 s.
* **I8**: I7 plus the root cover-cut loop, run during spawn; workers load the cuts from a file before their first leaf.

| Config | raw_penalised | growth_penalised | raw_nopenalty | growth_nopenalty |
|---|---|---|---|---|
| I3 | 81 | 6.4 | 73 | 2.6 |
| I7 quick try | 105 | 8.4 | 73 | **0.74** |
| I8 quick try + cuts | 131 | 7.9 | **65** | **0.74** |

The quick try makes time to within 1% / 0.1% at most 1.2 s on all four instances (I3: 1.8-3.5 s). But it slows the raw_penalised proof.

* **Repeats are deterministic**: the three repeats of every configuration agree to about 1%. The parallel solver is effectively deterministic, so a difference between configurations is a change of search path. A tiny change can flip that path, and on four instances real gains can't be separated from search-path luck. A held-out set of perturbed instances (`synthetic.perturb`) is needed before tuning further; see the plan.

## exp05: is the static leaf cutoff the culprit?

* **Hypothesis**: leaves start with the shared incumbent as HiGHS' `objective_bound`. In I7 that is the quick-try incumbent, and it steers HiGHS' search badly on raw_penalised.
* **Result** (raw_penalised, 1 run each): without the static cutoff (leaves are still stopped by the interrupt callback once their dual bound reaches the incumbent), I3 takes 86 s (81 with the cutoff) and I7 takes **87 s** (105 with the cutoff).
* **Verdict**: `leaf_cutoff` becomes an option; I9/I10 measure I7/I8 without it on all instances.

## CPO screening (real data, cap-free; aggregate findings only, data stays private)

All runs: s2000_P50high, 20 workers, rel_gap 1e-5, 300 s, one seed unless stated. Times are wall-clock from a cold start, including model build. Gaps are relative to capopt's own proven bound. Seed spread at 60 leaves is up to 7x in time to 1%, so single-seed screens only pick candidates; the KPI uses the median of 3 seeds.

### H17: holding-cash policy variants (start-capped copies, 90 s x 2 seeds)

| Variant | Time to 1% of the CPO schedule's score | Gap at 90 s | Peak closing balance vs the penalised optimum |
|---|---|---|---|
| tiers x0.01 | 1.7-12 s | 2.6e-5 | about 4x (the pure-PV hump returns) |
| tiers x0.1 | 16-17 s | 0.2% | about 1.8x |
| smooth (7 convex steps) | 29-31 s | 0.31% | about 1.5x |
| pure PV, hard cap at 500 | 1 of 2 seeds found a schedule (26 s) | - | capped |
| pure PV cap 350; tiers x0.01 cap 500 | no schedule found | - | - |

* **Verdict**: softer tiers solve faster but bring back the cash hump; hard caps make feasibility hard for the parallel search. The penalised policy stays the default. The knee (tiers x0.2 / x0.3 / x0.5) is untested.

### E1: live leaf bounds and a leaf census (16 LNS workers for the whole run)

* Each running leaf publishes its live dual bound, so the reported global gap is exact; re-split children inherit their parent's bound.
* **Result**: gap 3.4e-3 at 300 s, incumbent 0.19% short of the CPO objective. The census showed the worst-bound leaves had run few nodes: 16 LNS workers left only 4 for the tree, so the bound was starved, not hard.

### E2: incumbent cutoff for leaves launched after 60 s (`cutoff_after=60`)

* Leaves launched in the first 60 s search without a cutoff (better for finding schedules); later leaves get the shared incumbent as `objective_bound` (better for proving).
* **Result**: gap **2.5e-5** at about 312 s (from 3.4e-3 in E1), within 1% at 27.5 s, within 0.1% at 84.4 s, and a final schedule **1.2e-4 better** than the CPO's stored optimum (which carried the start cap).
* **Verdict**: the strongest single change so far. It needs the 3-seed confirmation (E10) before it becomes the default.

### E9: phased allocation (16 LNS workers for 45 s, then all 20 on the tree) vs no LNS, both without the late cutoff

| Run | Within 1% | Within 0.1% | Final schedule vs the CPO's | Gap at 300 s |
|---|---|---|---|---|
| phased lns16 x 45 s | 28.0 s | 103.1 s | 3.8e-4 short | 1.2e-3 |
| control (no LNS) | 23.7 s | 74.0 s | 3.8e-4 short (the same schedule) | 1.1e-3 |

* Both runs got stuck on the same schedule. In both, a single depth-6 leaf held the global bound after about 3,100-3,600 nodes.
* **Verdict**: freeing the tree after 45 s is no better than running no LNS at all. E2's 40x tighter gap came from two things together: the long LNS phase found a better schedule than the CPO's, and the late cutoff let the tree prune against it. E10 tests the two together with 3 seeds.

### E10: 3-seed confirmation (300 s each)

| Config | Gap at 300 s (3 seeds) | Final schedule vs the CPO's | Within 0.1% |
|---|---|---|---|
| C1: lns16 x 280 s + cutoff after 60 s (E2's setup) | 2.1e-5 / 3.5e-4 / 1.2e-3 (median 3.5e-4) | -1.2e-4 / +1e-5 / -8.7e-5 | 68-101 s |
| C2: lns16 x 45 s + cutoff after 60 s | 1.0e-3 / 1.1e-3 / 1.2e-3 | +8e-5 to +4.8e-4 | 103-118 s |

* E2's 2.5e-5 is the lucky end of C1's range. C1 needs both a better-than-CPO schedule (long LNS) **and** enough tree time. Seed 2 found the better schedule but its last leaves launched with 6-18 s left and ran 0 nodes: with 16 of 20 workers on LNS, the tree ran out of time.
* C2 is consistently about 3x worse than C1's median: 45 s of LNS rarely finds the better schedule. Dropped.

### E11: strong branching in the LP ramp-up (serial)

* `sb_candidates=k`: solve both child LPs of the top k candidates and split on the best product of bound gains (the gap anatomy study's top-ranked experiment).
* **Result** (60-leaf ramp-up, best-known incumbent as cutoff): the worst open leaf closes **99.7%** of the root gap with k = 8, against 74.7% for the default rule. Ramp-up time goes from 22 s to 217 s: a child LP needs about 1,300 dual simplex iterations (about 120 ms) even when warm-started, because one split moves a large project.
* **Verdict**: the bound effect is large, but it needs the child LPs run in parallel on the pool (next experiment). It stays off by default.
* **Bug found**: the cached node-LP solver had a 60 s time limit, which HiGHS counts over the solver's whole life. After 60 s of LP work every node returned "time limit", and `_lp_node` read that as infeasible (pruned). Default CPO runs never reached it (ramp-up about 21 s, no re-splits), so no reported result changes. The fix: no time limit; only proven infeasibility prunes; any other status triggers a cold retry, then an error. Covered by a regression test.

### E12: strong branching on the worker pool (batched ramp-up)

* The candidates' child LPs run as pool tasks, and the `width` best open nodes are expanded together so the pool stays busy. On an idle pool, k = 4 ramps up 60 leaves in 24.5 s (the default rule takes 22 s) and prunes 195 nodes, against 19. k = 8 takes 40 s. With 16 LNS workers holding the pool, k = 4 takes about 28-40 s.
* **Screen** (300 s, cutoff after 60 s, k = 4):

| LNS workers x 280 s | Seeds | Gap at 300 s | Final schedule vs the CPO's |
|---|---|---|---|
| 16 | 0 / 1 / 2 | 4.0e-5 / 6.8e-4 / 3.0e-4 (median 3.0e-4) | -1.06e-4 / +1.37e-4 / -3.5e-5 |
| 18 | 0 | 5.3e-4 | -1.2e-5 |
| 8 | 0 | 2.1e-4 | -9.0e-5 |
| 4 | 0 | 9.1e-4 | +3.1e-4 |

* **Verdict**: a median of 3.0e-4 against C1's 3.5e-4 (same setup without strong branching) is within noise. Strong branching stays available as an option, off by default. Across E2/E10/E12 the gap at 300 s tracks the schedule found: every run whose schedule beat the CPO's by about 1e-4 or more closed to 4e-5 or better, and none of the others did. More LNS workers do not fix this, because they all search around the same shared incumbent. **The bottleneck is now the primal side**: finding the near-optimal schedule reliably.

### E13: a big-project LNS neighbourhood (now the default)

* New LNS neighbourhood: free 1-3 of the largest projects (top 10% by spend) plus a random set of others, with a 3x longer sub-MIP limit. The gap anatomy study found the optimum moves a few big, long projects by 1-19 years relative to the LP, which the window and hot-spot neighbourhoods rarely allow.
* **Screen** (the E12 setup plus the new neighbourhood, 300 s, 3 seeds):

| | Gap at 300 s (seeds 0 / 1 / 2) | Final schedule vs the CPO's | Seeds beating the CPO by at least 1e-4 |
|---|---|---|---|
| E12 (no big-project moves) | 4.0e-5 / 6.8e-4 / 3.0e-4 (median 3.0e-4) | -1.06e-4 / +1.37e-4 / -3.5e-5 | 1 of 3 |
| E13 | 2.3e-4 / 1.8e-4 / 4.0e-5 (median **1.8e-4**) | -1.13e-4 / -1.02e-4 / -1.13e-4 | **3 of 3** |

* **Verdict**: a clear primal gain: the worst seed improves 3x and every seed finds a better-than-CPO schedule. It becomes the default (`lns_big=True`). The near-optimal schedule still arrives late, at about 200 s, so the primal side stays the bottleneck for the time-to-1e-5 KPI.
* A pure-LNS screen (all 20 workers on LNS) was abandoned: starting from the quick try's weak schedule it was still 1.3% off at 120 s, whereas the real setup gets within 0.1% by about 100 s because the tree leaves supply the good starting schedules. Primal changes must be screened inside the full setup.

### E14: warm start from the CPO's own schedule (like-for-like with COPT's logged time)

* COPT's logged times (56 s here) are warm-started. New option `warm_start` (a solution file), counted inside capopt's wall-clock. Setup: E13 (16 LNS workers x 280 s, cutoff after 60 s, strong branching k = 4, big-project LNS), 3 seeds.
* **Result**: **proven to gap 1e-5 in 184 s and 185 s** (seeds 0 and 2); seed 1 reached 1.17e-5 at 311 s. All three schedules are 1.23-1.26e-4 better than the CPO's stored optimum (which carried the start cap). These are capopt's first full proofs on the real data.
* Against COPT: median about 185 s vs 56 s (ratio 3.3). The ramp-up took 74-77 s, because strong branching only had the 4 workers outside LNS. E15 starts LNS after the ramp-up.

### E15: start LNS after the ramp-up (warm start)

* Option `lns_after_rampup`: LNS workers are submitted after the ramp-up, so strong branching gets the whole pool. Ramp-up time: 74-77 s down to about 25 s, with 151-162 nodes pruned.

| Arm (warm start, 16 LNS x 280 s, k = 4, big-project LNS) | Proof of 1e-5, seeds 0 / 1 |
|---|---|
| E14: LNS from the start, cutoff after 60 s | 184 s / not proven by 311 s (1.17e-5) |
| **E15A: LNS after the ramp-up, cutoff after 60 s** | **147 s / 115 s** |
| E15B: LNS after the ramp-up, cutoff from the start | 180 s / 138 s |

* **Verdict**: A. Even with a good warm start, searching leaves without a cutoff for the first minute is still worth it. About 11 s of each proof is L = 31: it survives the root-LP prune and gets its own pool and default ramp-up (no cutoff), which prunes everything in 10.8 s.

### E16: prune surviving last years with LP trees in the master's idle time

* Once the other last years are root-LP-bounded, the master's idle time grows a best-first LP tree (default branching, current incumbent as cutoff) for each last year that the root LP could not prune. A last year whose tree empties is pruned without a MIP solve. Offline on L = 31: closed after 41 expansions (10.9 s of master time), which fits in the roughly 100 s the master waits during the L = 30 tree.
* **Result** (E15A setup plus idle trees, warm start from the CPO schedule, 3 seeds): L = 31 was pruned in idle time in every run (41 expansions), with 29 of 29 other last years pruned. Proofs took 194 s / not proven by 303 s / 155 s, but these seeds found weaker schedules (-1.22 to -1.25e-4 vs the CPO's) than E15A's (-1.25 / -1.27e-4).
* Across all warm-started runs, proof time follows schedule quality: -1.27e-4 proved in 115 s; -1.26e-4 in 138-185 s; -1.25e-4 in 147-184 s; -1.24e-4 in 194 s; -1.22/-1.23e-4 did not prove within 300 s. 2-seed screens of tree-side changes are dominated by this primal luck. Idle trees stay on: they remove a whole MIP solve and cannot slow the tree.

### E17: decomposition: proof time given the best-known schedule

| Arm (warm start from the best-known schedule, -1.265e-4 vs the CPO's) | Proof of 1e-5 |
|---|---|
| P1: E15A setup (16 LNS workers x 280 s after the ramp-up, cutoff after 60 s) | 159.5 s |
| **P2: no LNS, all 20 workers on the tree, cutoff from the start** | **59.6 s / 54.4 s** (seeds 0 / 1) |

* **Verdict**: with the near-optimal schedule in hand, capopt proves 1e-5 in 54-60 s, against COPT's logged 56.1 s (warm-started). The proof machinery is at parity. In P1 the proof takes 2.7x longer because LNS holds 16 of 20 workers for 280 s. **The remaining gap to COPT is primal, plus handing LNS workers back to the tree once the schedule is good** (E18).

### E18: LNS patience (LNS workers rejoin the tree once the incumbent stalls)

| Patience (E15A setup + idle trees, warm start from the CPO schedule) | Proof of 1e-5, seeds 0 / 1 | Final schedule vs the CPO's | Final schedule found at |
|---|---|---|---|
| 15 s | 110.9 s / 264.9 s | -1.252e-4 / -1.233e-4 | 88 s / 101 s |
| 30 s | 265.5 s / 117.1 s | -1.226e-4 / -1.254e-4 | 207 s / 70 s |

* All four runs proved (the previous setup: 4 of 5), but the times split in two: about 115 s when a schedule near -1.25e-4 arrives by about 90 s, and about 265 s otherwise. The median does not clearly beat the previous setup's 155 s. Both patience values fall in the same pattern. All final schedules are within 1e-5 of the best known; a better incumbent only tightens the cutoff (incumbent minus the 1e-5 tolerance) the tree prunes against.

### E19: warm start from the CPO schedule, no LNS (all 20 workers on the tree, cutoff from the start)

* **Result**: 253.5 s (seed 0); seed 1 not proven by 300 s. The tree's leaves improve the schedule only slowly (final schedules -1.224e-4 / -1.211e-4, found at 253 s / 148 s).
* **Verdict**: LNS earns its workers on the primal side. The best warm-start setup so far is E15A/E18: about 110-120 s when LNS finds a near-optimal schedule by about 90 s, about 265 s otherwise. COPT's logged time is 56 s; proving from the best-known schedule takes capopt 54-60 s (E17), so the remaining gap is finding that schedule sooner.

### E20: adaptive LNS neighbourhood selection (ALNS; now the default)

* Each LNS worker picks neighbourhood kinds by adaptive weights. A win scores 10, an exhausted neighbourhood 1 and a timeout 0; a weight moves 20% toward each new score, with a floor of 0.2. The master logs tries / wins / sub-MIP seconds per kind.
* **Result** (warm start from the CPO schedule; 16 LNS workers after the ramp-up, cutoff after 60 s, k = 4, big-project LNS, patience 15 s; 3 seeds each):

| | Proof of 1e-5, seeds 0 / 1 / 2 | Median |
|---|---|---|
| E18 (uniform kinds) | 110.9 / 264.9 / 133.7 s | 134 s |
| **E20 (ALNS)** | **83.0 / 139.5 / 97.3 s** | **97 s** |

* Per-kind yield (wins per sub-MIP minute, pooled): start-time windows are the most productive by about 2x; random subsets, active-year windows and big-project moves come next; cash hot spots are the weakest.
* **Verdict**: a 28% lower median and a halved worst case. ALNS becomes the default (`lns_alns=True`). Against COPT's logged 56 s this setup is at 1.5-2.5x, from 2.3-4.7x before.

## KPI pass 1: the six COPT-timed scenarios (s1500 set aside)

Setup: the E20 configuration (warm start from each scenario's CPO schedule; 16 ALNS workers after the ramp-up with 15 s patience; cutoff after 60 s; strong branching k = 4; big-project LNS; idle LP trees), one seed, time limit 1.5x COPT's logged time (at least 300 s, at most 900 s).

| Scenario | COPT logged (warm) | capopt | Gap at the limit | Final schedule vs the CPO's |
|---|---|---|---|---|
| s2000_P50high | 56 s | **proven in 106 s** | - | -1.27e-4 |
| s2000_P50 | 108 s | not proven by 300 s | 1.5e-5 | -1.39e-4 |
| cfadsds_P50 | 272 s | not proven by 408 s | 3.0e-4 | -7.2e-6 |
| cfadsds_P95 | 433 s | not proven by 690 s | 1.9e-3 | 0 (no improvement) |
| cfadsds_P50high | 491 s | not proven by 737 s | 9.1e-5 | -5.8e-6 |
| cfadsds_P95high | 1,770 s | not proven by 938 s | 1.8e-3 | 0 (no improvement) |

* The s2000 scenarios are close (one proof; the other 1.5x above the target gap).
* The CFADS-after-debt-service scenarios are **bound-limited**. Their schedules match or beat the CPO's early (stable by 63-86 s), but their ramp-ups prune only 2-10 nodes (about 150 on s2000), and a few depth-5 to 9 leaves hold the global bound for the whole run.

### E21: re-splitting stuck leaves on a bound-limited scenario (cfadsds_P50)

* With `slice_seconds`, a leaf still open after its slice returns to the master, which re-splits it (with strong branching, k = 4); the children go back into the queue with a 1.5x longer slice. Idle workers can then take over hard subtrees instead of sitting idle while a few leaves hold the bound.
* **Result** (KPI pass 1 setup plus slicing, seed 0, 408 s limit; KPI pass 1 had gap 3.0e-4 at 408 s; COPT logged 272 s):

| Slice | Re-splits | Proof of 1e-5 |
|---|---|---|
| 30 s | 24 | 307 s |
| **60 s** | 4 | **197 s (0.72x COPT)** |

* **Verdict**: capopt's first win against COPT's logged time on a scenario. KPI pass 2 repeats all six scenarios with 60 s slices.

## KPI pass 2: 60 s re-split slices (stopped after five scenarios)

| Scenario | COPT logged | KPI pass 1 | **KPI pass 2 (slice 60 s)** | vs COPT |
|---|---|---|---|---|
| s2000_P50high | 56 s | proven 106 s | proven 177 s (weaker schedule this seed) | 3.2x |
| s2000_P50 | 108 s | gap 1.5e-5 at 300 s | gap 1.7e-5 at 300 s | - |
| cfadsds_P50 | 272 s | gap 3.0e-4 at 408 s | **proven 235 s** (E21: 197 s) | **0.86x** |
| cfadsds_P50high | 491 s | gap 9.1e-5 at 737 s | **proven 247 s** | **0.50x** |
| cfadsds_P95 | 433 s | gap 1.9e-3 at 690 s | not proven, stopped at 389 s (bug) | - |

* cfadsds_P95 exposed three bugs. KPI pass 2 was stopped before cfadsds_P95high, which carried the same problem.
  1. **Time budget counted twice.** A surviving last year got `time_limit - elapsed` *and* the shared start time, so it ran with T - 2 x elapsed. L = 42 was proven at 261 s; its survivor L = 43 then had only 128 s.
  2. **Survivors ran with default settings** (no slicing, no strong branching, no cutoff).
  3. **LNS was dead.** The CPO schedule ends in L = 43, but the solver started with L0 = 42, where 7 of the warm schedule's start years are not options. Every neighbourhood fixing any of them was infeasible: about 11,000 sub-MIPs, 212 real solves, 0 improvements.
* **Fixes**: the absolute time limit is passed through; survivors get the same tree settings with the cutoff from the start; a warm start's own last year is solved first; LNS frees any project whose incumbent start is not an option of the current model. A regression test covers the late-warm-start case.

## KPI pass 3: the P95 scenarios with the fixes

| Scenario | COPT logged | Result (60 s slices, fixes in) |
|---|---|---|
| cfadsds_P95 | 433 s | not proven by 669 s: L = 43 (the warm schedule's) at gap 1.6e-3 after 650 s; LNS now works (32 wins, schedule 1.5e-6 better than the CPO's) |
| cfadsds_P95high | 1,770 s | **L = 43 proven at 893 s**; L = 42 got the last 17 s, so the whole problem is not proven (gap 1.6e-3 from L = 42) |

* On the P95 scenarios both L = 42 and L = 43 are hard. They are solved one after the other, so the second never gets enough time.
* The research report `docs/research/CFADS_BOUND_STALL.md` explains the stall. The CFADS funding stream (0.5-1.64x its mean, binding in 29 of 38 years) spreads the gap over many projects. The default branching rule and 4-candidate strong branching miss the good splits. The product score is inflated by children that overshoot the cutoff. E22 tests the report's top rule.

### E22: strong-branching gains capped at the distance to the cutoff (cfadsds_P95)

* `_sb_pick` caps each child's gain at the cutoff minus the node bound (the research report's rule #1), and the cutoff is passed through the ramp-up and re-split. Setup: KPI pass 3 plus k = 16; 650 s limit; COPT logged 433 s.

| Arm | Ramp-up | L = 43 proven | Whole problem proven |
|---|---|---|---|
| KPI pass 3 (k = 4, uncapped) | - | not by 650 s (gap 1.6e-3) | no |
| **A: k = 16, capped** | 89 s, 16 pruned | 349 s | **593 s** (1.37x COPT) |
| B: A plus 5 rounds of root cover cuts (103 cuts) | 99 s | 347 s | 586 s |

* **Verdict**: the capped rule turns an unprovable instance into a proof. Root cuts add nothing measurable. E23 makes k automatic, 16 when the root LP has more than 15 fractional projects (cfadsds: 21-22; s2000: 11), else 4, because a 16-candidate ramp-up costs about 90 s that s2000 does not need. The batched ramp-up now expands enough nodes per round to give the pool about twice as many tasks as workers.

## KPI pass 4: automatic strong-branching candidates (E23), all six scenarios

Setup: KPI pass 2 (warm start, 16 ALNS workers after the ramp-up with 15 s patience, cutoff after 60 s, 60 s re-split slices, big-project LNS, idle LP trees) with `sb_candidates=-1` (auto) and the KPI pass 2 fixes. One seed. Time limit 1.5x COPT (at least 300 s, at most 900 s), except cfadsds_P95high at 1,800 s.

| Scenario | COPT logged (warm) | capopt | Ratio | Notes |
|---|---|---|---|---|
| s2000_P50high | 56 s | **proven 107 s** | 1.91 | k = 4 |
| s2000_P50 | 108 s | gap 2.2e-5 at 300 s | at least 2.6 | primal-limited: the schedule was still improving at 296 s |
| cfadsds_P50 | 272 s | **proven 172 s** | **0.63** | k = 16, 65 pruned in the ramp-up (9 before) |
| cfadsds_P95 | 433 s | **proven 658 s** | 1.52 | L = 43 at 480 s, then L = 42 |
| cfadsds_P50high | 491 s | **proven 218 s** | **0.44** | |
| cfadsds_P95high | 1,770 s | **proven 546 s** | **0.31** | L = 43 at 369 s, then L = 42 |

* **Primary KPI** (shifted geometric mean of capopt time / COPT time, 10 s shift), with s2000_P50 counted at its 300 s limit: **0.93**. This is a lower bound, because s2000_P50 was not proven, and it is a single seed; the KPI rule needs the median of 3 seeds.
* capopt now beats COPT's logged time on three of the six scenarios: the CFADS P50, P50high and P95high cases. The s2000 pair is primal-limited: LNS only starts after the 25 s ramp-up, then reaches -1.2e-4 within about 10 s (E24).

### E24: an all-worker LNS burst (10 s) before the ramp-up

* Option `lns_first`: with an incumbent in hand, all 20 workers run LNS for this long before the ramp-up, so the ramp-up prunes against a better cutoff and LNS starts earlier.
* **Result** (KPI pass 4 setup plus a 10 s burst, seed 0):

| Scenario | KPI pass 4 | E24 |
|---|---|---|
| s2000_P50high | proven 107 s | not proven by 300 s (gap 1.1e-5) |
| s2000_P50 | gap 2.2e-5 at 300 s | gap 1.5e-5 at 300 s (new best schedule, -1.42e-4) |
| cfadsds_P50 | proven 172 s | proven 194 s |

* **Verdict**: no gain. The burst finds 76 improvements in 12 s, but the ramp-up prunes the same number of nodes (189 vs 190), and the two runs move in opposite directions on the s2000 pair. The option stays, off by default.

### E25-E26: proof-only diagnostics on s2000_P50 (warm start from its best-known schedule, no LNS)

| Arm | Proof of 1e-5 |
|---|---|
| E25: k = 4 (auto), 60 s slices | 354 s (24 re-splits, bound-holding leaves at depth 29-34) |
| E26a: k = 16 capped, 60 s slices | not proven by 600 s (gap 1.1e-5); ramp-up 61 s, 158 pruned (vs 206) |
| E26b: k = 4, 120 s slices | **314 s** (5 re-splits) |

* Unlike s2000_P50high (54-60 s proof-only), s2000_P50 is **tree-limited**: even with its best schedule in hand, the proof takes 5x COPT's whole run (108 s). The capped 16-candidate rule that helps CFADS hurts here, which supports the automatic switch. Longer slices help a little, consistent with re-splits discarding HiGHS search state.
* During E26 capopt used only about 4 of 20 cores: in the proof tail the queue is empty and a few leaves hold the bound while the other workers wait for a slice to expire.

### E27: work stealing (opt-in, stays off)

* The idea: when the queue is empty and at most half the tree workers are busy (and an incumbent exists), the master interrupts the running leaf with the weakest live bound that has run at least `steal_after` seconds. The leaf comes back with status `stolen`, which is always re-split and never closed. Its children inherit max(child LP bound, the leaf's returned bound). The log now reports tree utilization (busy tree-worker time / available) and the number of steals for every solve.
* Tests: a flagged leaf returns `stolen` with a valid bound (between its LP bound and the known optimum); an unflagged one never does; on a pack instance steals do happen (11) and the proof lands on the known optimum.
* **Probes**:
  * On synthetic instances with immediate stealing (`steal_after=0`), solves that prove in 2-4 s failed to prove in 120 s. This is a steal storm: leaves interrupted before making progress, their children stolen again.
  * With a 2 s delay and 6 workers on the pack (all runs reached the known optimum):

| Instance | No stealing | Stealing after 2 s |
|---|---|---|
| P100_growth_penalised | proven 5.6 s, 34% utilization | proven 21.3 s, 11 steals |
| P100_raw_penalised | proven 119.6 s, 18% utilization | not proven by 150 s, 67% utilization, 13 steals |

* **Verdict**: correctness holds, but stealing makes solves slower. It raises utilization (18% to 67%) while destroying progress: an interrupted HiGHS leaf loses its cuts, pseudocosts and search tree, and the re-split children redo the work (as I4 found for time slicing). It stays opt-in and off. The design work continues with the parallel load-balancing literature review (`docs/research/PARALLEL_BB_LOAD_BALANCING.md`); candidates that use idle workers without discarding work are racing a hard leaf with different seeds, and solving a hard leaf's children while it keeps running.

### Load tags on timed results

From 2026-10-16, an external sampler logs the machine's CPU load outside capopt every 10 s. Each campaign result carries a `load` record (mean and peak outside cores, minimum free memory) and a `contended` flag (mean above 1 core or peak above 4). Runs before this (everything up to E26, including KPI passes 1-4) were not load-logged: in the first 18 minutes of logging the outside load averaged 1.5 cores (p90 2.4, one spike of 7.2) while the user worked in parallel. The KPI figures so far are therefore **provisional**; the official KPI will be rerun with 3 seeds and load tags in a quiet window.
* **Independent audit** (`docs/research/PARALLEL_BB_LOAD_BALANCING.md`: academic literature on parallel B&B load balancing, plus a code audit against its correctness invariants):
  * **Passed:** global bound validity, including leaves in flight (checked against the HiGHS source: the dual bound after a user interrupt is the queue's lower bound); children exactly partitioning a stolen subtree; no stolen leaf closed with a partial bound; termination; incumbent/cutoff consistency; steal-flag races; the time limit.
  * **One liveness bug, fixed:** a stolen leaf whose LP is integral was re-queued to "run to the end" but was still stealable, so it could be restarted forever. Such leaves are now pinned (never stolen). Defensive hardening: an unexpected interrupt status now reopens the subtree instead of closing it.
  * The literature (UG/ParaSCIP, CHiPPS/ALPS) keeps the donor solving and ships only part of its open nodes, throttles stealing adaptively, and splits a donor into enough pieces to fill all idle workers. A restarted solver loses its search state (about 4.7% per restart in SCIP), and in HiGHS an early interrupt also throws away the root cut loop. These are the directions for a stealing design that does not discard work.
