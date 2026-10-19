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

## exp06: held-out perturbed instances (on hold)

* **Hypothesis**: the default I9 (`capopt_quick_nocut`) was chosen on four instances with a near-deterministic solver, so it may be fitted to them. If it is not, it stays the best of I3/I7/I8/I9 by shifted geometric mean (shift 10 s) of proof time on perturbed copies.
* **Instances**: `capopt holdout` writes `synthetic.perturb(name, seed)` for seeds 1-3 of each pack instance (each project's spend and PV scaled by independent factors in [0.9, 1.1]) to `benchmarks/holdout/` (12 files). Lmin stays 18 on all of them.
* **Protocol**: `capopt bench --holdout --configs <cfg> --instances <inst> --time-limit 900`, one run per command and one log per run, instance-major so that a slow period of the machine hits every config, with the machine otherwise idle. Results go to `benchmarks/results/holdout/<config>/`. `capopt report` adds a holdout section in which every time-to metric targets the instance's best proven objective across configs.
* **Status**: tooling done and smoke-tested (I9 proved two of the instances, in 0.6 s and 13.5 s, both referee-feasible). The 48 runs are **on hold** while the machine is kept free for solves on real Capital Programme Optimiser data. No verdict yet; the default is unchanged.

### E28: racing copies of the weakest open leaf (opt-in, stays off)

* With `race_after`: when nothing is queued, an incumbent exists and a tree worker is idle, the master starts another copy (different seed, same splits and deadline) of the open leaf group with the weakest bound. Up to `max_racers` copies run per group. The first copy to finish the subtree (optimal, pruned, infeasible) closes it and the others are stopped. A group ending open is re-split or closed only after its last copy ends, with the best bound any copy reached. Nothing is interrupted while its subtree is still open (the lesson of E27).
* **Pack** (6 workers, racing after 2 s; all runs proved the known optimum):

| Instance | No racing | Racing |
|---|---|---|
| P100_growth_penalised | 5.5 s, 34% utilization | 5.7 s, 54% utilization, 3 races |
| P100_raw_penalised | 110.4 s, 37% | 106.7 s, 99%, 6 races (1 won by a later copy) |

* **CPO** (racing after 10 s; load-tagged):

| Run | Baseline | Racing | Load tag |
|---|---|---|---|
| s2000_P50 proof-only (E25 setup) | 354 s (E25, untagged) | 483 s, 76 races (5 won) | **contended** (outside load 2.7 cores mean, 5.3 peak) |
| cfadsds_P95 (KPI pass 4 setup) | 586-658 s (E22, KPI pass 4) | 564 s, 32 races (1 won) | clean (0.98 mean) |

* **Audit** (`docs/research/PARALLEL_BB_LOAD_BALANCING.md` §5): sound. The bound is valid across copies with different cutoffs; no subtree is lost or closed twice; termination and the time limit hold. Four robustness issues were found:
  * Fixed: B1, an ignored copy's exception crashed the run. B2, the run waited for copies whose results it ignores; it now loops only while work is queued or an open group is running, and workers stop writing incumbents once the master stops. B5, accounting.
  * Documented as prerequisites for ever combining racing with stealing or slicing: B3 (stealing and racing working against each other) and B4 (racers giving way to queued work).
* **Verdict**: correct, with a gain of 0-15%, inside seed noise. The audit measured the same thing directly on HiGHS: seeds change node counts up to 4.2x on a leaf, the best of 4 seeds is 1.3x faster, but the run's critical path improves only 7%. That matches the literature: racing ramp-up has not proven effective in ParaSCIP (Koch et al. 2012, Carvajal et al. 2014). Using all cores may also cost the bound-holding leaves their single-core boost clock. Racing stays opt-in and off. The remaining tail lever is BRANCHING_AND_TREE E4 (hedged re-split: the parent keeps running while its children use the idle workers).
* Side check from the audit: HiGHS's `mip_abs_gap=1e-6` is in scaled solver units. After auto-scaling it is at most 2e-6 objective units on the CPO models (about 0.01 on the no-penalty pack), against a 1e-5 relative tolerance of about 1e6 units, so it cannot close a leaf early.

### E29: more leaves on the CFADS scenarios (200 instead of 60; CFADS_BOUND_STALL #2(a))

| Scenario | KPI pass 4 (60 leaves) | E29 (200 leaves) | Load tag |
|---|---|---|---|
| cfadsds_P50 | 172 s | 321 s: the ramp-up alone took 312 s (437 pruned), then 9 s of tree | contended (1.6 mean) |
| cfadsds_P95 | 658 s | 575 s: **the ramp-up alone proved L = 43** (383 s, 384 pruned, no HiGHS tree work), then L = 42 | contended (2.1 mean, 8.6 peak) |

* **Verdict**: no gain, not adopted. With 16-candidate strong branching, the batched LP ramp-up is expensive per expansion, and 200 leaves turns it into most of the run.
* **Finding**: on cfadsds_P95 the outer strong-branching LP tree on 20 workers **proved a whole last year by itself**, about as fast as the HiGHS leaves usually do (369-480 s). That is real evidence for GAP_ANATOMY #2 (a persistent outer LP tree as the proof engine, handing HiGHS only the tail).

### E30: HiGHS settings for proof-phase leaves (BRANCHING_AND_TREE E5)

* Option `proof_options`: HiGHS options set only on leaves launched with the incumbent cutoff. A rejected option name now raises; highspy only returns an error status, which a test caught.

| Run (all load-clean) | Baseline | `mip_heuristic_effort=0` | + `mip_pscost_minreliable=16` |
|---|---|---|---|
| cfadsds_P50 (KPI pass 4 setup) | 172 s | 163 s | 172 s |
| s2000_P50 proof-only (E25 setup) | 354 s (E25, untagged); 314 s (E26b, 120 s slices) | 496 s | 433 s |

* **Verdict**: no gain, not adopted. HiGHS's default leaf settings are as good as or better than these, even when the incumbent is optimal; its heuristics seem to help the proof as well. The option stays for future screens.

### E31: where s2000_P50's proof time goes (pre-screens for the hedged re-split)

All on s2000_P50, L = 30, with the best-known schedule as cutoff and the 60-leaf strong-branching ramp-up (k = 4):

* **Leaf census** (BRANCHING_AND_TREE E8; 60 leaves solved independently on 20 workers, 120 s cap): **7 leaves are hard** (unfinished at 120 s after 4,000-5,800 nodes); the other 53 have a median of 11 s. The top 10 leaves take 65% of all leaf time. Hardness is not predicted by the LP bound: every leaf sits 1.2-1.5e-5 below the cutoff, the near-optimal plateau the gap-anatomy study found. The leaf with the weakest LP bound solves in 9 s.
* **Splitting the hard leaves** (the hedged re-split's premise): their strong-branching children are as hard as their parents. Of the 7 subtrees, only 1 closed within 120 s, and only because one child was pruned outright by its LP; the other children ran unfinished with 3,600-7,100 nodes.
* **HiGHS parallel MIP** (`threads=3` per hard leaf): the same node counts as one thread, and none finished.
* **Verdict**: on s2000_P50 the tail is a handful of plateau leaves that resist splitting, racing (E28) and HiGHS threads. The hedged re-split (BRANCHING_AND_TREE E4) would buy little here and is deprioritised. COPT closes this scenario in 108 s, which points to stronger node-level machinery (cuts, conflict analysis, node selection) rather than parallelism: fork-level work.

### E32: year-block Lagrangian bounds for pruning a last year (CFADS_BOUND_STALL #4, GAP_ANATOMY #3)

* New module `capopt.bounds.block_bound`. Keep the integrality, the per-project rows and the year rows of one block of years; relax the other year rows priced at the LP duals; solve that MIP. By weak duality its dual bound is a valid lower bound on MIP(L), even when it stops at a time limit. Tests: with integrality relaxed it equals the LP bound exactly (relative difference 1e-16), which checks HiGHS's dual sign convention, and on the pack it always lies between the LP bound and the optimum.
* **Pack** (L = 18, 30 s cap): years 8-17 close 44.6% (raw_penalised) and 99.2% (growth_penalised) of the LP gap.
* **The target** (cfadsds_P95/P95high, L = 42, which must be pruned against the L = 43 incumbent; its LP sits 2.4-2.5e-3 below the cutoff; 60 s caps):

| Block kept | P95 | P95high |
|---|---|---|
| years 37-42 | 18.1% of the gap to the cutoff (5 s) | 18.1% (3 s) |
| years 32-42 | 25.0% (time limit) | 25.3% (time limit) |
| years 4-16 | 18.4% (52 s) | 19.0% (53 s) |
| years 22-42 | 32.2% (time limit) | 42.9% (time limit) |

* **Verdict**: valid but too weak to prune L = 42; it matches the research report's estimate of about 25% for one block. The module stays as a tested certificate tool. Pruning L = 42 would need multi-block Lagrangian decomposition with subgradient multipliers (backlog H16), a research-level change.

### E33: SCIP 10 as the leaf engine? (the 7 hard s2000_P50 plateau leaves from E31)

* Each leaf was exported from HiGHS as MPS, so both solvers saw identical input, then solved by SCIP 10.0 (pyscipopt 6.3.0) and HiGHS 1.15 side by side: the same objective limit (the best-known schedule), relative gap 1e-5, 120 s, one thread each.

| | Leaves closed within 120 s | Node counts on unfinished leaves |
|---|---|---|
| SCIP 10 | 1 of 7 (leaf 37 in 77 s) | 3,700-7,100 |
| HiGHS 1.15 | 1 of 7 (leaf 34 in 72 s) | 3,500-5,900 |

* **Verdict**: no engine advantage. A different single leaf closes for each, which is run-to-run noise. The plateau is intrinsically hard for both open-source engines, so swapping the leaf engine is not the path to COPT's 108 s on s2000_P50. COPT's edge more likely comes from its natively parallel tree on the whole problem plus commercial node machinery. pyscipopt is kept installed for future screens; it is not a capopt dependency.

## Official KPI (2026-10-16): 3 seeds x 6 COPT-timed scenarios, load-tagged

Setup (the KPI pass 4 configuration): warm start from each scenario's CPO schedule; 16 ALNS workers after the ramp-up, with big-project moves and 15 s patience; incumbent cutoff after 60 s; strong branching with automatic k and the capped score; 60 s re-split slices; idle-time LP trees for the other last years; 20 workers. Time limit 1.5x COPT's logged time (at least 300 s, at most 900 s), 1,200 s for cfadsds_P95high. Each scenario's number is the median of 3 seeds; unproven runs count at their time limit.

Load: all 18 runs were tagged, with the user's machine quiet. Two were mildly contended (mean 1.06 / peak 5.4 outside cores) and were rerun clean; the reruns (511 s, 233 s) replace them. One impossible sample (55 "cores") was dropped as a counter artefact. Final: **18 of 18 clean**.

| Scenario | COPT logged (warm) | capopt, seeds 0 / 1 / 2 | Median | Ratio (10 s shift) | Whole range below COPT? |
|---|---|---|---|---|---|
| cfadsds_P95high | 1,770 s | 582 / 617 / 544 | 582 | **0.33** | ✅ |
| cfadsds_P50high | 491 s | 213 / 233 / 229 | 229 | **0.48** | ✅ |
| cfadsds_P50 | 272 s | 203 / 227 / 207 | 207 | **0.77** | ✅ |
| cfadsds_P95 | 433 s | 518 / 511 / 531 | 518 | 1.19 | ❌ |
| s2000_P50high | 56 s | 135 / 169 / not proven (1.5e-5) | 169 | 2.70 | ❌ |
| s2000_P50 | 108 s | not proven x3 (gap 1.7-1.9e-5 at 300 s) | at least 304 | at least 2.65 | ❌ |

* **Primary KPI** (shifted geometric mean of the ratios, all six): **1.007**, a lower bound because s2000_P50 never proved. That is parity with COPT, not yet a win.
* **By funding family** (the user expects CFADS-type funding in practice):
  * **CFADS after debt service (4 scenarios): 0.62.** capopt is faster than COPT on three of four, with the whole 3-seed range below COPT's time, and 1.2x on the fourth.
  * **Flat envelope (s2000, 2 scenarios): 2.68.** COPT is clearly faster. The flat envelope leaves a thin near-optimal plateau that only node-level machinery closes (E31, E33).
* Caveats that favour COPT stay as stated in `ITERATION_LOOP.md`: COPT's times are solver-only after a warm start built in uncounted time, while capopt's are wall-clock including model build, from the CPO's stored schedule.

### E34: prove the surviving last years concurrently (now the default)

* On cfadsds_P95/P95high the time is two proofs back to back, L = 43 (the warm schedule's) then L = 42, while the L = 43 tree ran at about 40% worker utilization. With `concurrent_proofs` (default on; it acts only with a warm incumbent and last years the LP cannot prune), every other last year is LP-bounded first (seconds), and the survivors' proofs start at once in background threads with their own pools. Soundness: a last year proven against the warm incumbent stays proven when the main solve improves the incumbent, and a better incumbent only shrinks the set of survivors. Test: the late-warm-start regression test, with concurrent proofs on, proves the brute-force optimum.
* **Result** (official KPI setup plus concurrent proofs, 3 seeds; capopt then uses about 18 of 20 cores instead of about 8):

| Scenario | Official (sequential) | E34, seeds 0 / 1 / 2 | Median | COPT |
|---|---|---|---|---|
| cfadsds_P95 | 518 / 511 / 531 (median 518, ratio 1.19) | 390 / 575 / 433 (L = 42 done at 306-330 s) | **433 (ratio 1.00)** | 433 |
| cfadsds_P95high | 582 / 617 / 544 (median 582, ratio 0.33) | 555 / 452 / 595 (L = 42 done at 300-347 s) | **555 (ratio 0.32)** | 1,770 |

* Load: outside load averaged 0.9-1.5 cores during these runs, just over the 1-core threshold, plus one isolated spike of about 18. That is consistent with system overhead from running about 40 solver processes, not outside work.
* **Verdict**: adopted. cfadsds_P95 moves from 1.19x to parity. The trade-off: the L = 43 proof slows while it shares cores (390-575 s spread). E34 changes nothing on the other four scenarios (no survivors), so the **updated KPI** uses the official rows plus these medians: **overall 0.970 (a lower bound), CFADS family 0.584, flat envelope 2.680**.

### Generalisation check with the current defaults (after E34)

Most tuning since E10 used the six CPO scenarios, so the defaults were rechecked on instances never used for tuning:

* **Pack regression** (slow suite, `tests/test_regression.py`): all 4 public instances prove their known optima within their limits (163 s in total).
* **Held-out perturbed set** (exp06, 12 instances; each project's spend and PV scaled by independent factors in [0.9, 1.1]): **12 of 12 proven** within 300 s with the default pipeline (0.6-1.2 s growth/no-penalty; 4.9-12.7 s growth/penalised and one raw/penalised; 27-203 s raw/no-penalty; 102-166 s for the other two raw/penalised). Every returned schedule re-scores exactly under the exact evaluator.
* **Verdict**: no sign of over-fitting to the CPO scenarios.

### E35: engine racing and branching quality on the hard s2000_P50 plateau leaves

The same 7 hard leaves (E31), as identical MPS files (E33); 120 s, one thread, objective limit = the best-known schedule.

| Strategy | Leaves closed within 120 s |
|---|---|
| HiGHS, seed 0 / best of 3 seeds | 1 / 1 of 7 |
| SCIP 10, best of 3 seeds | 2 of 7 |
| **Best of both engines (racing)** | **3 of 7** |
| SCIP, full strong branching | **0 of 7** (only 150-340 nodes each) |
| SCIP, pseudocost branching | 1 of 7 |

* Engine diversity beats seed diversity (3 of 7 against 1), but 4 of the 7 leaves resist every engine, seed and branching rule, and those set the proof's critical path, so engine racing would barely shorten s2000_P50.
* Even full strong branching (the best possible branching decisions) closes none: **the plateau is not a branching-quality problem**. A SCIP branching callback with capopt's rule has no headroom; dropped on evidence.
* The outer LP-tree proof engine (GAP_ANATOMY #2) is also re-ranked down: E29's data shows it about as fast as HiGHS leaves on P95's L = 43 (383 s against 350-480 s) and slower on cfadsds_P50.

### E36: run the concurrent proofs below normal OS priority (now the default)

* E34's concurrent L = 42 proof competed with the main L = 43 tree for cores, which slowed L = 43 (390-575 s). With `low_priority_proofs` (default on), the concurrent proof's worker processes run at below-normal OS priority (`SetPriorityClass` on Windows, `os.nice(10)` elsewhere), so they use only the cores the main last year leaves idle. A test confirmed a real bug before any timing: untyped ctypes truncated Windows' pointer-sized process handle, making the call a silent no-op. The handle is now typed, and the call raises if it fails.

| Scenario | Official (sequential) | E34 (concurrent) | **E36 (concurrent, low priority), seeds 0 / 1 / 2** | Median | COPT |
|---|---|---|---|---|---|
| cfadsds_P95 | 518 (1.19x) | 433 (1.00x) | 378 / 373 / 475 (L = 42 done at 335-345 s) | **378 (0.88x)** | 433 |
| cfadsds_P95high | 582 (0.33x) | 555 (0.32x) | 400 / 504 / 518 | **504 (0.29x)** | 1,770 |

* Load: mean outside load 0.28-0.41 cores on the five tagged runs (one had a single 6.7 peak). P95 seed 0 is untagged because the load sampler had died during an app restart; `capopt campaign launch` now warns when the load log is stale.
* **Verdict**: adopted. On cfadsds_P95, 2 of 3 seeds and the median beat COPT. **Updated KPI: 0.934 overall (a lower bound); CFADS family 0.552**, with the median below COPT's time on all four CFADS scenarios; flat envelope 2.680 (unchanged).

### Validation on the untimed CPO scenarios (never used for tuning)

Current defaults (the official KPI configuration plus E34/E36), warm start from each scenario's CPO schedule, one seed, 900 s cap, load-clean (mean outside load 0.27-1.11 cores):

| Scenario | Funding family | Result | Final schedule vs the CPO's |
|---|---|---|---|
| cfads_P50 | CFADS (without debt service) | **proven in 57 s** | -5.3e-6 (better) |
| cfads_P95 | CFADS (without debt service) | **proven in 68 s** | equal |
| s1750_P50 | envelope (s1500 +/- 250) | not proven at 900 s, gap 1.7e-5 | -2.5e-5 (better) |
| s1750_P95 | envelope | not proven at 900 s, gap 3.3e-5 | -4.9e-6 (better) |

* **The family pattern holds on unseen data.** CFADS-type funding proves in about a minute. Envelope-type funding finds schedules as good as or better than the CPO's but stalls on the last plateau just above the 1e-5 target, as on s2000 (E31, E33, E35).
* For practical use: on envelope scenarios capopt returns a schedule within about 2-3e-5 of proven optimal (better than the CPO's stored plan) well inside 15 minutes; the remaining sliver needs commercial node machinery.

## Certified-gap presets (from 2026-10-17)

The user's priorities changed: proofs to 1e-5 are set aside, and day-to-day work needs schedules **certified within 1% and 0.5%** as fast as possible, warm (from a previous plan) and cold. The metric for E37 onwards is wall-clock seconds until `solve` returns a schedule whose certified gap (incumbent against the proven bound over **every** last year) is at or below the target. Setup: all 10 CPO scenarios (6 COPT-timed, 4 untimed), warm from the CPO's stored plan and cold, 3 seeds, 120 s cap. Campaigns now wait for the machine to be quiet before each run and rerun contended runs (below), so every number here is load-clean.

### E37: the `1pct` and `0.5pct` presets

* **Hypothesis**: the proof-oriented defaults spend their first 25-80 s on a strong-branching ramp-up and only then publish a bound, and their stopping rule is 1e-5. At a 1% or 0.5% target the root LP already certifies most warm plans, so the time should be dominated by the bounding of the other last years and the process start-up. Cold, it should be the primal side.
* **What changed** (all tested; `tests/test_presets.py`):
  * `bounds.cash_floor_bound(inst, L)`: an O(T) lower bound on MIP(L) with no LP. Cumulative spend by year t is at most what every project would have spent by t had it started at its earliest allowed year. That gives the closing balance a floor, which is charged at the holding tiers in years 0..L-1, and every project is credited its best PV among the starts that finish by L. It is a relaxation of the LP (tested: never above it; never above any schedule's model value at any L). On the CPO scenarios it settles 50-90% of the last years at a 1% target.
  * `solver.screen_last_years`: the floor first, then LP bounds for the rest, solved in threads. HiGHS releases the GIL, so 12 LPs take 0.5 s instead of 3.4 s.
  * `solve(lp_screen=True)`: with a warm plan, every last year is bounded up front; if that certifies the plan, `solve` returns without spawning the pool. Last years the screen prunes, including the warm plan's own, are never solved.
  * The ramp-up prunes at the target gap instead of at the incumbent itself.
  * **Honest bounds at coarse gaps.** A pruned subtree used to report the incumbent as its bound. That was harmless at 1e-5 but would overstate the bound by up to 1% at the presets' gap, so pruned leaves, ramp-up nodes and queued subtrees now keep their real bound. The reported gap is the true certified gap.
  * LNS sub-MIPs stop as soon as the master finishes (they used to run to their own limit, up to 9 s). LNS is capped at workers - 1 and at the time limit. A test found a 2-worker solve that never ended with the preset's 8 LNS workers.
  * Plain branching (k = 1) can run batched on the pool.
  * `PRESETS` and `solve(preset=...)`, plus `capopt solve --preset 1pct|0.5pct [--warm-start plan.json]`. Arguments left at their defaults take the preset's values; anything passed explicitly wins.
* **Presets** (both): screen on, no quick try, 24 leaves with 4-candidate strong branching, 8 LNS workers from the first second, incumbent cutoff for leaves launched after 10 s, 10 s re-split slices; `rel_gap` 1e-2 or 5e-3, so the solve stops at the target.
* **Screens that shaped them** (single seed, cold; exploratory):
  * Cold, every arm was **primal-limited**. The certificate arrives seconds after the incumbent first gets within about 0.5-0.7% of the CPO plan, and that takes 10-50 s. Variants of the ramp-up (k = 1/2/4, 20-24 leaves, 4-12 LNS workers, 5-10 s slices) moved cold times only within seed noise, so the presets use the settings that are best warm, and the cold primal side is E39's subject.
  * HiGHS's own heuristics are weak on these models. A single HiGHS run on the full fixed-L model is still 3-14% off after 60 s, whatever the encoding, and 20 seeded copies reach only 11%. HiGHS on 20 ramp-up leaves reaches 0.5% within 8 s of starting: the splits matter more than the seeds.
  * LP-rounding constructions do not help. RENS on the root-LP support is 5-19% off; it is 1-10% off when widened by ±1-4 years. Full LP dives are 0.1-5.6% off after 8-50 s. Time-window relax-and-fix is 0.3-1% off on CFADS but 5-23% off on the flat envelope.
  * Warm 0.5% on the envelope scenarios: HiGHS's root cut loop closes the root gap (0.58-0.61%) to 0.5% in 10-31 s. The strong-branching LP tree does it in 1 s (s2000_P50) and 8 s (s1750_P95, k = 4).
* **Result** (median of 3 seeds, seconds to a returned schedule with certified gap ≤ target; `>` = most seeds hit the 120 s cap, `*` = at least one did; all 120 runs load-clean after 10 automatic reruns):

| Scenario | warm, 1% | warm, 0.5% | cold, 1% | cold, 0.5% |
|---|---|---|---|---|
| s2000_P50high | 0.4 | 2.2 | 17.1 | 24.2 |
| s2000_P50 | 0.4 | 2.0 | 15.8 | 41.4 |
| cfadsds_P50 | 0.2 | 0.2 | 32.0 | 35.4 |
| cfadsds_P50high | 0.3 | 0.3 | 19.3 | 30.9 |
| cfadsds_P95 | 0.3 | 0.3 | 22.6 | 48.8 |
| cfadsds_P95high | 0.4 | 0.4 | 19.8 | 37.2 |
| cfads_P50 | 0.2 | 0.2 | 8.5 | 8.6 |
| cfads_P95 | 0.3 | 0.2 | 15.4 | 15.2 |
| s1750_P50 | 0.3 | 3.8 | 34.3 | >120.0* |
| s1750_P95 | 0.4 | 6.4 | 70.5* | >120.0* |
| **shifted geo. mean** | 0.3 | 1.5 | 22.7 | 38.5 |

* Warm 1% is certified by the screen alone on every scenario (no MIP, no worker pool), in a third of a second. Warm 0.5% needs a few strong-branching LP nodes on the envelope scenarios (2-6 s); CFADS stays at the screen. The achieved gaps sit just under the target, as expected from a solver that stops there.
* Cold is 9-70 s at 1% and 9-120+ s at 0.5%. Both s1750 scenarios miss 0.5% within 120 s, so the cold primal side (E39) is the main lever left.
* **0.1% (logged, not a preset)**: the `0.5pct` settings with `rel_gap` 1e-3, 1 seed, 300 s cap (`>` = not certified by then):

| Scenario | warm, 0.1% | cold, 0.1% |
|---|---|---|
| s2000_P50high | 17.9 | 258.7 |
| s2000_P50 | 20.6 | 207.3 |
| cfadsds_P50 | 9.9 | 270.1 |
| cfadsds_P50high | 6.5 | 154.8 |
| cfadsds_P95 | 300.0 | >300.0* |
| cfadsds_P95high | 287.9 | >300.0* |
| cfads_P50 | 0.2 | 11.5 |
| cfads_P95 | 0.3 | 35.2 |
| s1750_P50 | 23.7 | >300.0* |
| s1750_P95 | >300.0* | >300.0* |
| **shifted geo. mean** | 34.2 | 163.2 |

  Warm 0.1% takes 0.2-24 s except cfadsds_P95/P95high (288-300 s) and s1750_P95 (not by 300 s). The presets' 4-candidate strong branching is too weak there; the CFADS rule (k = 16, E22-E23) and the concurrent proofs are what closed those gaps in the 1e-5 runs. A `0.1pct` preset would need `sb_candidates=-1`; not pursued, since 0.1% is not a target.
* **Verdict**: adopted as the named presets. The proof-oriented defaults are unchanged.
* **Fix found on the way**: once the time limit had passed, `solve` still solved each surviving last year in turn, each with a new pool and an untimed LP ramp-up; one 0.1% cold run overran by more than 10 minutes. Survivors are now reported open with their LP bound once time is up, and the ramp-up stops at the deadline.

### Campaign infrastructure: wait for a quiet machine, rerun contended runs

The user shares this machine; a parallel build pushed the outside load to 2.5-8 cores during the first E37 screen. A campaign spec can now say `"quiet": {"max_cores": 1.0, "window_s": 30, "max_wait_s": 1800}`. Before each run the runner waits until every load-log sample of the last 30 s shows at most 1 core busy outside capopt. With `"rerun_contended": n`, a run whose load tag comes back contended is rerun up to n times (tested). In E37 this waited 37 min in total and reran 10 runs; none of the 120 kept results is contended. Runs shorter than the 10 s sampling interval carry no load samples.

### E38: one model iteration (Q8): edit 1-5 projects, re-solve from the previous plan

* **Hypothesis**: the real loop is "change some data, re-solve". A previous plan is almost as good as a CPO plan, so a re-solve should cost about what a warm solve costs (under a second at 1%), provided the old plan survives the edit.
* **Setup**: `synthetic.perturb_projects`. For each of the 10 scenarios, k = 1, 3 or 5 random projects get spend and PV scaled by independent factors in [0.8, 1.2], and their earliest allowed start delayed by 1-2 years (a slipped approval). Each run warm-starts from the unedited scenario's stored plan. Presets `1pct`/`0.5pct`, 120 s cap, one perturbation per cell, load-clean (`cpo_presets.py iterate`).
* **The previous plan almost never survives an edit.** In 29 of 30 edited scenarios it was infeasible (a negative balance after a spend increase, or a start no longer allowed); the old pipeline then dropped it and started cold. New `solver.repair_schedule` (on by default, `repair_warm`):
  * Moves disallowed starts to the nearest allowed year.
  * If the balance still goes negative, re-times only the projects active around the first negative year, everything else fixed (a sub-MIP at the plan's own last year, widening the window until it finds a schedule).
  * Polishes to a 1e-4 gap within 5 s. A first version stopped at a 1e-2 sub-MIP gap and lost 0.5-1% of the plan's value; it also used a later last year, which charges holding cost for extra years.
  * Repair median 1.3 s.
* **Fixes found on the way**:
  * **Concurrent proofs of a poor plan's other last years** (E34) were launched against the repaired plan and then waited for, even after LNS had found a schedule good enough to LP-prune them. On s1750_P95 the run ended "not certified" at a real gap of 0.62%. Such proofs are now cancelled as soon as the improved incumbent LP-prunes their last year (tested), and leaves stop with their master.
  * **Worker memory**: numpy's OpenBLAS reserved about 620 MB of commit per worker process (per-thread buffers for 20 cores), about 13 GB per pool. Pools crashed with "the paging file is too small" while other applications held most of the memory. Workers now start with one BLAS thread: 29 MB each.
  * Campaigns also wait for at least 8 GB of free commit memory, and rerun a run whose pool died for lack of memory.
* **Result**: seconds from the unedited plan to a certified schedule for the edited scenario (`s` = certified by the screen right after the repair, no MIP; `>` = not by 120 s):

`1pct`:

| Projects edited | s2000_P50high | s2000_P50 | cfadsds_P50 | cfadsds_P50high | cfadsds_P95 | cfadsds_P95high | cfads_P50 | cfads_P95 | s1750_P50 | s1750_P95 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 7.2 | 16.5 | 5.4s | 5.4s | 4.1s | 3.0s | 1.4s | 3.0s | 20.6 | 54.8 |
| 3 | 4.6s | 1.1s | 1.6s | 5.4s | 1.4s | 1.0s | 0.2s | 0.9s | 1.4s | 47.2 |
| 5 | 1.0s | 1.3s | 1.3s | 1.1s | 1.6s | 1.2s | 1.0s | 0.9s | 1.7s | 29.4 |

`0.5pct`:

| Projects edited | s2000_P50high | s2000_P50 | cfadsds_P50 | cfadsds_P50high | cfadsds_P95 | cfadsds_P95high | cfads_P50 | cfads_P95 | s1750_P50 | s1750_P95 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 16.9 | >120 | 5.4s | 17.5 | 4.1s | 3.0s | 1.4s | 3.0s | 26.9 | 54.4 |
| 3 | 6.2 | 2.8 | 1.5s | 5.4s | 1.4s | 1.0s | 0.2s | 0.9s | 4.7 | 120.6 |
| 5 | 2.6 | 3.0 | 1.3s | 1.1s | 1.5s | 1.1s | 1.0s | 0.9s | 5.3 | 110.1 |

* Median over the 30 edits: **1.6 s to 1%** (24 of 30 certified by the screen after the repair) and **3.0 s to 0.5%**. The slow cells are edits the local repair cannot absorb, such as s2000_P50 with one project whose spend increase pushes the earliest feasible last year from 30 to 31. There the repaired plan starts 1-5% off and LNS has to close the rest, like a cold start (E39).
* **Verdict**: plan repair adopted (default on). A model iteration typically re-solves to 1% in about 1-5 s.

### E40: start-window tightening by reduced-cost fixing at the target gap (MASTER_PLAN Phase 4)

* **Hypothesis**: at 1e-5, reduced-cost fixing removes about 1.5% of columns at the root (HYBRID_AND_CROSSFIELD). At a 0.5% target the cutoff is the incumbent minus 0.5%, so the threshold is far looser. Dropping provably useless start years before the tree should shrink every LP of the strong-branching ramp-up and speed the warm 0.5% runs on the envelope scenarios.
* **What changed** (opt-in `rc_fix`, tested): `parallel.rc_fixings(model, cutoff)` solves the root LP and uses its reduced costs. A step column at 0 whose reduced cost lifts the LP value above the cutoff is fixed to 0 (the project starts later than that option); one at 1 is fixed to 1. By the Lagrangian bound, every schedule excluded has value at least the cutoff, so the fixings become the root splits of the tree, and the cutoff enters the reported bound. Test: on tiny instances, at five cutoffs, every schedule whose model value is below the cutoff satisfies every fixing; presets with `rc_fix` still match brute force.
* **Result** (warm from the CPO plan, `0.5pct`, 3 seeds, load-clean): the fixing is real (64-118 start-window bounds per scenario, in 0.7-1.5 s) but changes nothing measurable:

| Scenario | base | `rc_fix` |
|---|---|---|
| s2000_P50high | 2.0 | 2.2 |
| s2000_P50 | 2.1 | 2.1 |
| s1750_P50 | 3.7 | 3.7 |
| s1750_P95 | 6.5 | 6.6 |

* **Why**: the warm 0.5% time is the strong-branching ramp-up itself (1 expansion on s2000, 9 pruned nodes on s1750_P95). Fixing columns at their LP values leaves those LPs' cost and the child bounds almost unchanged; the fixed columns were not the ones branching moved. Cold runs gain nothing either: the fixing needs an incumbent at ramp-up time, which a cold start does not have.
* **Verdict**: no gain; `rc_fix` stays opt-in and off. The warm 0.5% lever is the ramp-up's worker count (E42).

### E39: the cold-start primal side

* **Where the time goes**: every cold E37 run is primal-limited. The certificate lands seconds after the incumbent first gets within about 0.5% of the optimum, which takes 10-50 s (more on s1750). The incumbent comes from two sources:
  * **leaves**: HiGHS on ramp-up leaves, in 4-15 s;
  * **LNS**: 60-90% of its sub-MIPs improve the schedule, a sign that the incumbent is far from locally optimal and that each step is small.
* **Ideas tried** (cold, `0.5pct`, the 6 hardest scenarios, 120 s cap, load-clean):
  * `lns_rins`: an LP-disagreement neighbourhood that frees projects whose start is a year or more from the root LP's mean start, or that the LP splits.
  * `primal_options`: HiGHS settings for leaves that search without a cutoff, e.g. `mip_heuristic_effort` 0.3.
  * `lns_wait_incumbent`: LNS workers join the tree until the first schedule exists.
  * `lns_max_size`: the neighbourhood size cap, 45 before; E39b, cap 90.
  * 12 or 16 LNS workers instead of 8.
* **Offline probes that failed** (not run as campaigns):
  * Seeding from a softer-penalty model (tiers x0.1 or x0.01): 4-27% off on the true objective.
  * HiGHS leaves on s1750_P95 stay 9% off after 20 s whatever the settings: its root node alone takes over 10 s, so the 10 s leaf slices never get past it, and LNS does all the work there.
* **Result** (shifted geometric mean of per-scenario medians, seconds; every run of the E39 campaigns pooled; unproven runs count at 120 s):

| Arm | Seeds per scenario | Geo. mean | s2000_P50high | s2000_P50 | cfadsds_P50 | cfadsds_P95 | s1750_P50 | s1750_P95 |
|---|---|---|---|---|---|---|---|---|
| base (E37 preset, 8 LNS) | 7 | 49.5 | 25 | 29 | 31 | 41 | 108 | >120 |
| 12 LNS workers | 5 | **44.0** | 37 | 24 | 19 | 44 | 64 | >120 |
| 12 LNS + heuristic effort 0.3 | 3 | 42.4 | 22 | 40 | 19 | 55 | 42 | >120 |
| heuristic effort 0.3 | 2 | 46.0 | 31 | 25 | 27 | 41 | 76 | >120 |
| neighbourhood cap 90 | 2 | 46.2 | 26 | 26 | 27 | 45 | 86 | 118 |
| LP-disagreement neighbourhood | 2 | 50.2 | 24 | 32 | 25 | 46 | 119 | >120 |
| LNS waits for the first schedule | 2 | 55.3 | 35 | 35 | 42 | 48 | 88 | >120 |
| 16 LNS workers | 3 | 56.2 | 24 | 33 | 28 | 82 | >120 | >120 |

* Single-seed screens were misleading: 12 LNS workers looked 22% faster on 2 seeds and 1% faster on the next 3. Only the pooled 5-seed figure (-11%) is worth acting on. More LNS helps up to a point (12 helps, 16 hurts: too few tree workers). The other ideas are within seed noise.
* **Verdict**: the presets move to 12 LNS workers. The other options stay opt-in. The cold primal side remains the open problem: s1750_P95 needs more than 120 s for 0.5% under every arm, and the cold medians move only about 10% under every knob tried. A step change would need a new kind of primal method; candidates are compound cash-balanced moves (the parked H10 theme), or a donor plan from a sibling scenario (H14).

### E42: start LNS after the ramp-up (warm 0.5%)

* **Hypothesis**: warm 0.5% on the envelope scenarios is all strong-branching ramp-up (E40), which ran on the 12 workers the 8 LNS workers left free, while LNS adds nothing to a near-certified warm plan.
* **Result** (warm, `0.5pct` with 8 LNS, 3 seeds, tight ranges):

| Scenario | LNS from the start | LNS after the ramp-up | + 8 candidates |
|---|---|---|---|
| s2000_P50high | 2.0 | 1.9 | 1.9 |
| s2000_P50 | 2.0 | 1.9 | 1.9 |
| s1750_P50 | 3.8 | 3.5 | 2.9 |
| s1750_P95 | 6.8 | 5.7 | 5.9 |

* **Verdict**: adopted: both presets start LNS after the ramp-up (about 10% warm). It costs a cold start nothing, since there is no incumbent to search around before the ramp-up ends. With E39 the presets are now 12 LNS workers, started after the ramp-up; the final table below measures them.

### E41: scaling with programme size (Q7)

* **Setup**: `synthetic.subset_projects` keeps n random projects of a scenario (n = 100, 150, 200; the full programme has 233) and scales every year's funding by the kept projects' share of total spend, so overall cash tightness is preserved. Scenarios: s2000_P50, cfadsds_P50, cfadsds_P95, s1750_P95. Cold, both presets, 1 seed, 120 s cap, load-clean (24 runs). A warm arm (the full plan restricted to the subset and repaired) was dropped part-way: with funding rescaled, that plan is a poor start for the subset (75-120+ s), so it measures nothing useful. The full-size column is E37's median.

| Scenario | 100, 1% | 100, 0.5% | 150, 1% | 150, 0.5% | 200, 1% | 200, 0.5% | 233 (E37), 1% / 0.5% |
|---|---|---|---|---|---|---|---|
| s2000_P50 | 47.7 | 55.9 | 50.0 | 50.5 | 10.0 | 36.9 | 15.8 / 41.4 |
| cfadsds_P50 | >120 | >120 | 21.4 | 35.3 | 16.1 | 13.7 | 32.0 / 35.4 |
| cfadsds_P95 | 99.8 | >120 | >120 | >120 | 120.1 | >120 | 22.6 / 48.8 |
| s1750_P95 | >120 | >120 | >120 | >120 | >120 | >120 | 70.5 / >120 |

* **Finding: time does not grow with the number of projects; smaller programmes are harder.** At equal overall tightness, a smaller programme has fewer, relatively bigger pieces to pack into each year's cash envelope. The ten largest projects average 2.4x the peak annual funding at n = 100 against 1.94x in the full programme (cfadsds_P95), so near-optimal schedules are rarer and the primal side (E39) takes longer. One random subset per size, so single cells are noisy, but the direction is consistent across all four scenarios.
* **Q1 (every scenario)**: the presets have now been measured on all 10 extracted scenarios that are in scope (E37 and the final table below). The s1500 pair stays set aside by the user's decision; the H17 policy variants are not presets' targets.
* **Verdict**: no change. For capacity planning, expect a run's time to depend on how lumpy the projects are relative to annual funding, not on how many there are.

## Final table: certified-gap presets (2026-10-17)

The `1pct` and `0.5pct` presets as merged after E37-E42: lp screen; 24 leaves with 4-candidate strong branching; 12 LNS workers started after the ramp-up; 10 s slices; leaf cutoff after 10 s; plan repair for warm starts. All 10 CPO scenarios in scope, warm (from the CPO's stored plan) and cold, 3 seeds each, 300 s cap, every run load-clean (120 runs, 1 automatic rerun). Seconds until `solve` returns a schedule certified within the target over every last year: the median, with the seed range for the cold columns (warm ranges are within 0.1 s, except s1750_P50 1% 0.2-0.3).

| Scenario | warm, 1% | warm, 0.5% | cold, 1% (range) | cold, 0.5% (range) |
|---|---|---|---|---|
| s2000_P50high | 0.4 | 1.9 | 14.8 (14.4-19.9) | 22.1 (21.8-34.4) |
| s2000_P50 | 0.4 | 1.9 | 15.2 (15.0-20.5) | 22.4 (20.2-47.5) |
| cfadsds_P50 | 0.2 | 0.2 | 18.8 (14.7-25.9) | 26.7 (18.6-31.4) |
| cfadsds_P50high | 0.3 | 0.3 | 18.1 (18.0-18.4) | 25.8 (18.4-26.3) |
| cfadsds_P95 | 0.3 | 0.3 | 16.1 (15.4-17.7) | 34.2 (28.6-59.4) |
| cfadsds_P95high | 0.3 | 0.3 | 14.6 (14.1-17.6) | 40.2 (27.9-56.3) |
| cfads_P50 | 0.2 | 0.2 | 7.3 (4.3-7.6) | 7.8 (6.6-8.3) |
| cfads_P95 | 0.3 | 0.2 | 11.5 (10.4-12.2) | 12.0 (10.6-14.3) |
| s1750_P50 | 0.3 | 3.5 | 27.8 (24.4-28.7) | 77.7 (38.2-174.7) |
| s1750_P95 | 0.4 | 5.6 | >300 (202.3->300) | >300 (104.8->300) |
| **shifted geo. mean** (unproven at 300) | 0.3 | 1.3 | 22.7 | 34.8 |

* **Warm** (a stored plan): **1% in 0.2-0.4 s on every scenario**, certified by the screen alone (no MIP, no worker pool). **0.5% in 0.2-0.3 s** on the CFADS family and **1.9-5.6 s** on the flat envelope (a few strong-branching nodes).
* **Model iteration** (E38: edit 1-5 projects, re-solve from the previous plan, repaired): median **1.6 s to 1%** and **3.0 s to 0.5%**.
* **Cold** (no plan): **1% in 7-28 s** on 9 of 10 scenarios, **0.5% in 8-78 s** on 9 of 10. Cold is limited by finding a good schedule, and s1750_P95 is the outlier: its leaves never get past HiGHS's root node, and LNS alone reaches 1% after 40 s to more than 300 s depending on the seed (E39).
* **Against E37** (same protocol, 120 s cap for comparability; shifted geometric means): cold 1% 22.7 -> 20.0 s, cold 0.5% 38.5 -> 31.0 s, warm 0.5% 1.5 -> 1.3 s, warm 1% unchanged at 0.3 s. Without s1750_P95, cold improves by about 21% at both targets.
* **0.1%** (logged with the E37 settings, 1 seed): warm 0.2-24 s on 7 of 10 (about 300 s or more on cfadsds_P95/P95high and s1750_P95); cold 12-270 s or more than 300 s.
* **Before/after: the proof-oriented defaults with only `rel_gap` set to the target** (16 LNS workers after the ramp-up, automatic strong branching, quick try, 60 s slices; 1 seed, 120 s cap, load-clean). These already run on today's code, with its honest bounds, gap-aware pruning and plan repair, so the gap to the presets understates the full gain:

| Scenario | warm, 1% | warm, 0.5% | cold, 1% | cold, 0.5% |
|---|---|---|---|---|
| s2000_P50high | 2.2 | 3.8 | 18.3 | 17.2 |
| s2000_P50 | 2.1 | 3.6 | 21.1 | 20.2 |
| cfadsds_P50 | 1.9 | 1.9 | 46.3 | 48.7 |
| cfadsds_P50high | 2.0 | 2.0 | 42.8 | 43.8 |
| cfadsds_P95 | 2.8 | 2.7 | 72.4 | 86.3 |
| cfadsds_P95high | 2.9 | 2.9 | 70.8 | >120.0* |
| cfads_P50 | 1.2 | 1.3 | 18.2 | 18.0 |
| cfads_P95 | 1.6 | 1.6 | 33.0 | 34.2 |
| s1750_P50 | 1.9 | 5.0 | 22.5 | 30.0 |
| s1750_P95 | 4.2 | 13.6 | >120.0* | >120.0* |
| **shifted geo. mean** | 2.2 | 3.5 | 39.5 | 44.0 |

  Shifted geometric means, presets against these defaults (120 s cap): warm 1% **0.3 vs 2.2 s**, warm 0.5% **1.3 vs 3.5 s**, cold 1% **20.0 vs 39.5 s**, cold 0.5% **31.0 vs 44.0 s**. The defaults spend 1-3 s warm on process start-up and serial LP bounds that the presets' screen avoids. Cold, their 16-candidate strong-branching ramp-up on CFADS costs 40-80 s before any leaf runs.
* **Bug found on the way**: a bound from the cash floor could be a numpy float, which made `Result.proven` a numpy bool; the campaign's JSON writer then failed (7 baseline runs). Results now carry plain Python types (tested).
* **Stopping point**: E39-E42 tried every remaining lever on the list. The only changes beyond seed noise were 12 LNS workers (cold) and LNS after the ramp-up (warm), both adopted. Window tightening (E40), the LP-disagreement neighbourhood, primal-leaf settings, bigger neighbourhoods and deferring LNS all stayed within noise. Further cold gains need a new primal method (cash-balanced compound moves, H10; a donor plan from a sibling scenario, H14); that is research, not tuning.

### E43: don't re-split a leaf that is still in HiGHS's root (cold s1750 outlier)

* **Hypothesis**: on s1750_P95 every 10 s leaf slice ends inside HiGHS's root node (0 nodes), and the re-split children start the same root work again. That wastes the tree workers, which on other scenarios supply schedules. Option `root_extend`: such a leaf goes back to the queue whole, with a 4x slice.
* **Result** (cold, `0.5pct`, 2 seeds, 300 s cap, load-clean; the option fired 6-15 times per run):

| Scenario | base | `root_extend` |
|---|---|---|
| s2000_P50 | 39.2 | 35.0 |
| cfadsds_P95 | 44.9 | 48.2 |
| s1750_P50 | 54.6 | 41.6 |
| s1750_P95 | 164 / >300 | 154 / >300 |

* **Verdict**: within seed noise; it does not fix the s1750_P95 outlier. Leaves that do get past the root still find nothing on that scenario (E39), so the outlier is down to LNS luck. The option stays opt-in and off.

## Distribution layers (from 2026-10-18)

The user's next goal after total-PV optimisation: bring the Capital Programme Optimiser's three "distribution" extensions into capopt and test them in layers, **dimension scoring, then activity-class adherence, then regional fairness**. Soft forms are agreed. Each layer is linear and adds only continuous columns (no binaries), so the fixed-last-year decomposition, the cash-floor bound, ALNS and the certified-gap presets all carry over: the spend in a window does not depend on the last build year, so MIP(L) still equals the true objective at a schedule's own L and overstates it at a later one.

### The layers in capopt (`capopt.layers`)

An instance may carry a `layers` section, which `evaluate` scores exactly and `last_year_model` builds as rows. Workers reload instances from disk, so a layered instance is just another JSON file. There are three primitives:

* **PV floor** (hard): `sum_p pv_floor[p][start_p] >= min`. This is the epsilon constraint of dimension scoring. The objective's `pv_int` becomes the dimension-weighted PV, and the floor applies to the Total PV.
* **Shortfall rows** (soft): `deficit = max(0, target - free - spend_S(a..b))`, charged in convex piecewise-linear segments. The target is a **fixed** base (the funding entitlement), so the rows stay linear. Shares of realised spend would be a ratio of decision variables, which is not linear. The penalty is **shortfall-only**: shares sum to 1, so a two-sided penalty would charge every misallocation twice.
* **Hard bands**: `|spend_S - s * spend_all| <= tol * s * spend_all + floor` around a share of *realised* spend. This is the CPO's activity-class baseline. It is linear because both sides are linear in the starts.

The generic allocation layer (`allocation_rows`) serves both class and region:

* **Target:** `s * B_w`. B_w is the funding that falls in window w, with cumulative funding capped at the programme's total cost.
* **Free zone:** within `max(tol * target, the largest spend one member can put in the window)`, the lumpiness floor.
* **Beyond the free zone:** slopes 1 : 4 : 12, with breakpoints at 10% and 20% of `N = max(target, $25M)`. These are the CPO's regional breakpoints (20/30/40% of N with a 20% free zone).
* **Window weights:** decaying.
* **Pricing in PV-equivalent units:** one knob per layer. A deficit of 1% of N in the first window, on the first slope, costs **x% of PV\*** (`pv_weight x PV*`). Later windows scale by their weight.

The CPO's calibrated regional λ (γ = 4) corresponds to about x = 0.01% for a representative region.

**Data.** `tools/extract_cpo_attrs.py` reads the CPO's files without writing to them and writes `private/cpo/attributes/` (gitignored):

* per-dimension PV at every allowed start, discounted exactly as the Total PV, and PV0, the PV of a year-0 start used in the notebook's weights;
* the Activity Class and Region columns of the Costs sheet;
* the population capsule from the CPO's per-capita branch (approximate Stats NZ 2024).

Checks: the TOT kernel reproduces every instance's `pv_int` exactly, and the five dimensions sum to it within 4e-7.

**Fix found on the way.** When MIP(Lmin) has no schedule at all, which a hard layer can cause, the cutoff `inf - rel_gap * inf` was NaN. Every comparison with NaN is False, so every other last year was "LP-pruned" and the solve returned no schedule. Cutoffs without an incumbent are now +inf. An infeasible last year counts as proven, and a survivor that an earlier survivor's schedule prunes is skipped. Both are tested against brute force with layers.

**Timing protocol.** Same as the final table: the `1pct` / `0.5pct` presets, warm (from the CPO's stored Total plan, the "turn a layer on" workflow) and cold, 120 s cap, 2 seeds.

* Scenarios: s2000_P50 (flat envelope) and cfadsds_P50 (CFADS).
* PV\*: the Total PV of the `0.5pct` preset's base plan. It is certified within 0.43% (s2000_P50) and 0.14% (cfadsds_P50) on the objective, which is 99.95% PV, so PV\* is accurate to about 0.4% of PV.
* Campaigns run from a frozen git worktree, so later edits cannot leak into timed runs.
* Since 2026-10-18 OneDrive's sync keeps about 1 core of 20 busy. The quiet gate allows 2 outside cores, with no reruns, and the load tags record the outside load.

### E44: dimension scoring (Stage A1)

* **Formulation** (the CPO notebook's `weights_for_dimension`):
  * The objective's PV becomes `w_p x PV_D(p, start)`. The weight is `w_p = (PV0_D / mean PV0 of the other four dimensions) x (percentile rank of PV0_D)^0.5`, normalised to mean 1 and clipped to [1, 2]. PV0 is the PV of a year-0 start.
  * The carry tiers stay in the objective.
  * PV floor: `Total PV >= f x PV*`.
  * Metrics:
    * **PV retained**: Total PV / PV\*.
    * **Raw dimension PV**: PV_D against the PV\* plan.
    * **Score**: the objective's weighted `sum w_p PV_D` against the PV\* plan.
* **Choice of dimensions.** All five dimensions correlate 0.91-1.00 with Total PV across projects at the CPO plan. INC is the least correlated. HSP is the largest (26% of PV) and has the most weight at the 2.0 cap (27% of projects).
* **Result** (median of 2 seeds, seconds to the certified gap; `>` = most seeds hit the 120 s cap; f = 0.85 shown, f = 0.90 is within seed noise of it):

| Scenario | Layer | Cold 1% | Cold 0.5% | Warm 1% | Warm 0.5% | PV retained | Raw dim PV | Score |
|---|---|---|---|---|---|---|---|---|
| s2000_P50 | none | 18.7 | 28.8 | 0.4 | 2.0 | 100% | | |
| s2000_P50 | INC | 29.5 | 39.4 | 32.7 | 44.3 | 98.6-99.3% | +4.8-5.5% | +10.2-10.8% |
| s2000_P50 | HSP | 47.8 | >120 | >120 | >120 | 95.8-97.8% | 0 to +1.3% | +5.1-5.2% |
| cfadsds_P50 | none | 17.9 | 31.2 | 0.3 | 0.3 | 100% | | |
| cfadsds_P50 | INC | 15.1 | 22.0 | 15.7 | 22.8 | 97.1% | +2.8% | +7.7% |
| cfadsds_P50 | HSP | 31.5 | 56.5 | 19.1 | 43.0 | 96.4-98.2% | +0.3-0.9% | +2.4-4.7% |

* **f = 0.85 and 0.90 never bind.** Unconstrained dimension scoring already keeps 95.8-99.3% of Total PV (f_free = 0.96-0.99), so both floors leave the problem unchanged.
* **Time to a certified gap.** The layer costs 15-50 s to 1% and 22 s to more than 120 s to 0.5%, against 0.3-2 s warm without it.
  * A warm start from the Total plan helps little or not at all. That plan is 4-8% off the score optimum, so the run is effectively cold.
  * The gap is now relative to the score, which is about 26-29% of Total PV. A 1% certified gap on the score is therefore about 0.3% of Total PV: a tighter absolute target than the base preset's.
* **HSP is a poor trade and hard to certify.**
  * Its weights shift value toward HSP-specialist projects, so the score rises 2.4-5.2% while raw HSP PV hardly moves (0 to +1.3%), for 2-4% of Total PV.
  * INC buys +2.8-5.5% raw INC PV for 0.7-2.9% of Total PV.
* **Stage-1 plans can be dominated (lexicographic polish).** A cold s2000 HSP plan certified within 1% on the score kept 95.7% of Total PV. Maximising Total PV subject to `score >= stage-1 score` (`cpo_layers.build_polish`, warm from the stage-1 plan) brought it to **98.0% at the same score**, but needed 134 s; 15 s recovered 0.2 points and 30 s 0.5 points. cfadsds HSP went from 96.8% to 97.6% (93 s). The INC plan was already non-dominated (0.5 s, no change). This is the research note's point (MULTI_OBJECTIVE_LAYERS §1): at a coarse gap, a quick second stage is needed before a frontier point is reported.
* **Warm plans from another objective swamp the machine.** With the Total plan as the warm start, the E34 concurrent proofs start a pool for every last year the screen leaves open, while the main solve sits at the plan's own last year (L = 31-33) instead of the optimum's (L = 30). Single-seed probes on s2000 HSP warm 1%:
  * default: >120 s;
  * `best_first` (solve the lowest-bound last year first): 118 s;
  * concurrent proofs off: 52 s;
  * both: 32.5 s.

  New opt-in `warm_triage` switches both when the screen finds the warm plan more than 3x the target gap from its bound. Over 2 seeds it is mixed: s2000 HSP 1% >120 → 72 s and INC 1% 33 → 24 s, but INC 0.5% 44 → 77 s, and cfadsds is unchanged. Base warm runs are unaffected (screen-certified). It stays opt-in.
* **Anchors f = 0.99 and f = 1.0** (`warm_triage`, 1 seed; MULTI_OBJECTIVE §1 predicted the tight floor to be the slowest point):
  * **Where the floor does not bind, f = 0.99 is the same problem as f = 0.85.** s2000 INC: 28 s cold to 1%, 37 s to 0.5%, at 99.1% of Total PV and INC +5.3%.
  * **Where it binds, certification becomes hard.** HSP on both scenarios and cfadsds INC take 103 s to more than 120 s at 1%, and more than 120 s at 0.5%. Each holds exactly 99.0% of Total PV for HSP +0.9-1.8% or INC +1.9%.
  * **f = 1.0 is pathological.** Nearly every run hits the 120 s cap, and cold cfadsds runs found no schedule at all: a schedule at PV ≥ PV\* is as hard to find as the best Total plan.
  * Two cfadsds HSP f = 1.0 runs failed with "node LP ended with status Not Set". HiGHS returned neither optimal nor infeasible on a node LP twice, warm and cold. Such a node is now kept open as a MIP leaf with bound -inf (never pruned), instead of failing the solve (tested).
* **Verdict (A1)**:
  * Dimension scoring works as a soft tilt. INC is the dimension worth offering: +2.8-5.5% raw INC PV for 0.7-2.9% of Total PV.
  * The 85-90% floors are irrelevant on these scenarios, because the unconstrained tilt never goes below 96%. A useful floor sits at or above f_free (0.96-0.99), and there it binds and slows certification sharply.
  * Recommended default: **INC**, with a lexicographic polish whenever a point is reported. The floor value comes from the Stage C frontier, which sweeps f downward from 1.0 to 0.96.
  * Time: plan on 15-50 s to 1% and 20-60 s to 0.5%, cold or warm.

### E45: activity-class adherence (Stage A2)

* **Layer.**
  * Classes and cost shares come from the Costs sheet's Activity Class column.
  * Windows: the first four 3-year NLTP windows.
  * Target: share x funding entitlement.
  * Shortfall-only, free within 20% of the target or the lumpiness floor.
  * Slopes 1 : 4 : 12 beyond the free zone.
  * Decay: half per decade, giving window weights 1 : 0.81 : 0.66 : 0.54.
  * Price x = 0.01% (about the CPO's calibrated regional λ).
* **Baseline:** the CPO's hard bands: ±10% of realised window spend + $25M, first 4 windows, classes with ≥ 2% share (3 of 5).
* **Every plan is measured against the same default rows** ("worst w1 / w2" = the largest class shortfall beyond the free zone in windows 1 and 2, as % of target).
* **Diagnostic first (MULTI_OBJECTIVE §2, Jensen / lumpiness gap).** The root LP pays 0.000-0.008% of PV in class penalty against 9-25% for the stored Total plan. The LP bound is the same as without the layer. Certifying therefore depends on the primal side finding low-penalty schedules.
* **Lumpiness floor.** The approved floor, the most one member can put in the window, is larger than the target for most class-window cells: one mega-project can out-spend a class's whole 3-year entitlement. It switches those rows off (`-lumpwin`: the Total plan pays 0-0.3% of PV and nothing changes). The default floor is therefore the largest single-year spend of a member, capped at half the target.
* **Result** (1 seed, 120 s cap, seconds to the certified gap; `>` = not certified):

| Scenario | Variant | Cold 1% | Cold 0.5% | Warm 1% | Warm 0.5% | Total PV | Worst w1 / w2 |
|---|---|---|---|---|---|---|---|
| s2000_P50 | none (Total plan) | | | | | 100% | 16% / 48% |
| s2000_P50 | **soft (default)** | 32.1 | 36.0 | 65.8 | 114.4 | 99.5-99.8% | 7-9% / 0-3% |
| s2000_P50 | soft, window floor (approved literal) | 21.4 | 34.7 | 0.5 | 4.4 | 99.8-100% | 10-16% / 39-48% |
| s2000_P50 | soft, mega carve-out 13 | 20.9 | 22.9 | 129 | >120 | 99.6% | 7-10% / 19-20% |
| s2000_P50 | soft, carve-out 13 + endogenous base | 29.2 | 60.6 | >120 | >120 | 99.3-99.8% | 9-12% / 0% |
| s2000_P50 | CPO hard ±10% | >120 | >120 | >120 | >120 | 98.7-99.1% | 0-6% / 0-6% (0 band violations) |
| cfadsds_P50 | none (Total plan) | | | | | 100% | 48% / 31% |
| cfadsds_P50 | **soft (default)** | 23.7 | 46.3 | 43.6 | 72.7 | 99.5-99.7% | 0-11% / 0% |
| cfadsds_P50 | soft, window floor | 18.7 | 34.5 | 0.3 | 0.3 | 99.7-100% | 42-50% / 22-31% |
| cfadsds_P50 | soft, carve-out 13 | 27.7 | 39.8 | 56.5 | 64.3 | 99.5-99.8% | 22-27% / 19-27% |
| cfadsds_P50 | soft, carve-out 13 + endogenous base | 46.2 | >120 | 63.2 | 46.8 | 99.6-99.9% | 40-47% / 2-21% |
| cfadsds_P50 | CPO hard ±10% | 25.8 | 50.8 | 36.3 | 120.1 | 98.8-98.9% | 0% / 0% (0 band violations) |

* **The soft default delivers almost all of the adherence for a quarter of the hard bands' PV cost.**
  * Cost: 0.2-0.5% of Total PV, against 1.0-1.3% for the hard bands.
  * Adherence: window-2 shortfalls vanish, and window 1 drops to at most 11%.
  * Speed: it certifies 1% in 24-32 s and 0.5% in 36-46 s cold. The hard bands never certify on s2000 within 120 s; on CFADS they take 26 s / 51 s.
  * The CPO's own run of the same hard bands reported about 1% NPV and a 1,200 s time limit (ACTIVITY_CLASS_WINDOWS_FINDINGS).
* **The carve-out is as fast but answers a different question.** With the 13 largest projects out of the targets, the remaining classes keep 19-27% shortfalls against the full-programme yardstick. On a fixed base it also charges the Total plan 27-29% of PV, because the mega-projects take the early funding. The endogenous base, which is the CPO's carve-out measurement, is slower and leaves window-1 shortfalls of 40-47% on CFADS.
* **Warm from the Total plan is again slower than cold** (44-114 s against 24-46 s). That plan carries a 10-14% class penalty, so it is far from certified, and the concurrent proofs swamp the machine (E44). For layered solves, start cold or use `warm_triage`.
* **Verdict.** Default class layer: **soft, 20% free, single-year lumpiness floor capped at half the target, x = 0.01%**. Keep the hard bands as the CPO-compatible baseline only. The carve-out and the endogenous base stay options.

### E46: regional fairness (Stage A3)

* **Layer.**
  * Spans: cumulative checkpoints at years 10 / 20 / 30, weights 4 : 2 : 1.
  * Target: share x the funding entitlement, capped at what the region's own pipeline can spend by the checkpoint. The National project is excluded.
  * Shortfall-only, free within 20% or the single-year lumpiness floor, slopes 1 : 4 : 12, x = 0.01%.
* **Target basis** (the user's policy choice, so both are carried):
  * **cost share**: each region's share of programme cost;
  * **blend**: 50/50 population share (approximate Stats NZ 2024, from the CPO's per-capita work) and cost share.
* **Variants:** a top-13 mega-project carve-out, and the endogenous base (share of realised spend).
* **Two design findings before any run:**
  * **Uncapped population targets are unattainable.** Canterbury's blend share is 7.7% against 2.6% of programme cost. Without the cap the stored Total plan paid 79-84% of PV in penalty that no schedule could avoid. That shifts the objective and blurs its relative gap. With targets capped at what each region's pipeline can spend, it pays 29-41%.
  * **The root LP pays 0.04-0.09% of PV in regional penalty, against 10-17% for the Total plan.** This is the same Jensen / lumpiness gap as the class layer (MULTI_OBJECTIVE §3).
* **Result** (1 seed, 120 s cap; shortfall = the worst region beyond its free zone at checkpoints 10 / 20, % of target; the Total plan has 49 / 44% (s2000) and 49 / 42% (cfadsds) on cost share, 72 / 60% and 67 / 70% on the blend):

| Scenario | Variant | Cold 1% | Cold 0.5% | Warm 1% | Warm 0.5% | Total PV | Own-basis shortfall c10 / c20 |
|---|---|---|---|---|---|---|---|
| s2000_P50 | cost share | 28.9 | 101.1 | >120 | >120 | 99.1-99.5% | 5-14% / 1-13% |
| s2000_P50 | blend | 21.5 | 22.5 | >120 | 127 | 99.7-99.9% | 3-8% / 0-12% |
| s2000_P50 | cost share, carve-out 13 | 15.7 | 21.4 | 101 | 101 | 99.8-100% | 29-45% / 8-13% |
| s2000_P50 | cost share, endogenous base | 23.3 | 66.5 | >120 | >120 | 99.2-99.8% | 6-21% / 8-14% |
| cfadsds_P50 | cost share | >120 | >120 | >120 | >120 | 98.4-99.2% | 2-27% / 10-18% |
| cfadsds_P50 | blend | 25.8 | 71.8 | 64.9 | 71.7 | 99.2-99.4% | 10-18% / 3-8% |
| cfadsds_P50 | cost share, carve-out 13 | 28.1 | 24.8 | 73.4 | 79.2 | 99.3-99.7% | 47% / 51-69% |
| cfadsds_P50 | cost share, endogenous base | >120 | >120 | >120 | >120 | 98.8-99.0% | 23-29% / 14-20% |

* **Both target bases remove most of the regional shortfall for under 1% of Total PV.**
  * **Blend:** the cheaper and faster of the two here (0.1-0.8% PV, 1% certified in 22-26 s cold). Capped at attainable spend, it mostly pulls small regions' projects earlier, and it asks less of the mega-project regions than cost share does.
  * **Cost share:** pushes against the mega-project regions (Northland, Auckland). On CFADS, where early funding is thin, it does not certify within 120 s at either gap: the integer schedule pays more penalty than the LP bound sees, which is the Jensen gap.
* **The carve-out is fast but leaves the large regions' first-checkpoint shortfall in place** under the full-programme yardstick. The endogenous base is slower and no better.
* **Warm starts from the Total plan are slow again.** That plan carries a 10-17% penalty, so start layered solves cold or with `warm_triage` (E44/E45).
* **Verdict.**
  * Regional layer: **soft, capped targets, 20% free, single-year lumpiness floor, x = 0.01%, checkpoints 10 / 20 / 30 at 4 : 2 : 1**.
  * Target basis: **the user's choice**. The blend is faster and cheaper on these two scenarios; cost share is the CPO's earlier basis and is hard to certify on CFADS.
  * Not tried (MULTI_OBJECTIVE §3): 5-yearly checkpoints, a worst-case term, and enumerating the start years of the mega-projects. The last is the remedy for the CFADS cost-share case.

### E47: blends (Stage B)

* **Setup.** The default layers from E44-E46 (INC tilt at f = 0.85, soft class at x = 0.01%, regional cost share at x = 0.01%), combined in pairs and all three; 1 seed, 120 s cap.
  * With INC in the mix, the class and regional prices are a share of the INC score's PV\* (the PV the objective maximises), so x keeps its meaning.
  * Shortfalls are measured against the default rows: class worst w1 / w2; region worst at c10 / c20.
  * `cpo_layers.py compact` prints one line per run.

| Scenario | Blend | Cold 1% | Cold 0.5% | Warm 1% | Warm 0.5% | Total PV | INC score | Class w1/w2 | Region (cost) c10/c20 |
|---|---|---|---|---|---|---|---|---|---|
| s2000_P50 | INC + class | 28.6 | 86.7 | 44.0 | 83.6 | 98.7-98.9% | +10.0-10.5% | 15-16% / 2-10% | 66-71% / 16-33% |
| s2000_P50 | class + region | 18.0 | 32.4 | >120 | >120 | 99.0% | | 0% / 0-5% | 9% / 10-11% |
| s2000_P50 | all three | 70.4 | >120 (0.78%) | >120 | >120 (0.50%) | 96.7-97.2% | +8.3-8.7% | 9% / 7-10% | 19% / 6-21% |
| cfadsds_P50 | INC + class | 13.9 | 15.3 | 17.2 | 19.5 | 97.1% | +7.7% | 1% / 0% | 49% / 67% |
| cfadsds_P50 | class + region | 89.4 | >120 (0.84%) | >120 (2.2%) | >120 (1.5%) | 97.8-98.8% | | 0-1% / 0% | 2-22% / 8-20% |
| cfadsds_P50 | all three | >120 (2.0%) | >120 (2.6%) | >120 (2.0%) | >120 (2.8%) | 95.4-96.4% | +6.2-7.0% | 0% / 0% | 24-27% / 11-43% |

(Gaps in brackets are the certified gaps at the 120 s cap.)

* **The layers compose, and each costs about its own share of PV.**
  * Class + region together keep 98.7-99.0% of Total PV. That is about the sum of the two alone (0.2-0.5% and 0.5-1.6%).
  * Adding the INC tilt takes another 2-2.5 points. INC's score gain shrinks a little (+6-9% against +8-11% alone), because the tilt and the allocation targets pull on the same early funding.
* **Certification slows with each layer.**
  * s2000: class + region certifies 1% in 18 s cold, about as fast as either layer alone. All three need 70 s.
  * CFADS: anything that includes the regional cost-share layer is hard. Class + region needs 89 s for 1%; all three end at a 2% gap after 120 s.
  * This is E46's Jensen gap: the LP pays almost none of the regional penalty that the integer schedules must pay. The research note's remedy is to enumerate the mega-projects' start years (MULTI_OBJECTIVE §2).
* **INC + class on CFADS is the cheapest blend to certify** (14-20 s): class adherence is complete, and INC +7.7%.
* **Warm starts from the Total plan are slower than cold across the blends**, as for each layer alone.
* **Verdict.** Blends work and keep their PV cost additive. For a fast certified answer, use one or two layers. The triple certifies within about a minute on the flat envelope, but not on CFADS within 2 minutes; the CFADS triple with the regional blend basis is E47b.

### E48: knob calibration and trade-off frontiers (Stage C)

* **Method** (MULTI_OBJECTIVE §4): a chained sweep with `cpo_layers.py frontier`.
  * Points run in the direction that loosens the problem: a lower PV floor, or a higher price.
  * Each point warm-starts from the previous point's plan, so reuse is free.
  * Every point is a `1pct` solve on cfadsds_P50 with a 180 s cap.
* **Dimension frontier** (INC, floor f swept down from 0.995; the first point warm-starts from the CPO plan):

| f | Seconds to certified 1% | Total PV | INC score | Raw INC PV |
|---|---|---|---|---|
| 0.995 | >180 (1.20%) | 99.50% | +2.8% | +1.3% |
| 0.99 | 177.4 | 99.00% | +4.7% | +2.0% |
| 0.985 | 31.0 | 98.58% | +5.4% | +2.2% |
| 0.98 | 65.8 | 98.01% | +6.2% | +2.4% |
| 0.975 | >180 (1.06%) | 97.80% | +6.7% | +2.6% |
| 0.97 (≈ f_free) | 15.6 | 97.17% | +7.8% | +2.8% |

  * Across the whole curve, about 2 points of INC score (0.6 points of raw INC PV) per point of Total PV, steepest near f = 1: the first 0.5 point of PV buys +2.8% score and +1.3% raw INC.
  * Neighbouring points are 0.5 points of PV apart, while each certified gap is up to 1% of the score (MULTI_OBJECTIVE §5). By the 4g rule only the overall slope is signal; the local rates are not.
  * A binding floor costs time: 31 s to more than 180 s, against 16 s unconstrained.
* **Class price frontier** (x swept up; all five points certified 1% in 14-24 s):

| x (% of PV\* per 1% shortfall in window 1) | Total PV | Class w1 / w2 |
|---|---|---|
| none (Total plan) | 100% | 48% / 31% |
| 0.001 | 99.87% | 45% / 23% |
| 0.003 | 99.61% | 21% / 8% |
| **0.01** | 99.59% | 0% / 3% |
| 0.03 | 99.52% | 1% / 0% |
| 0.1 | 99.52% | 1% / 0% |

  * The knee is at **x = 0.01%**: full class adherence for about 0.4% of PV. A higher price buys nothing more; the layer saturates.
  * The PV differences between the last three points (0.07%) are well inside the 1% gap. The adherence change is the signal.
  * The whole sweep took under 2 minutes, which is what the presets make possible.

**E47b: the same blends with the regional blend basis** (50/50 population and cost share; cold only, 1 seed):

| Scenario | Blend | Cold 1% | Cold 0.5% | Total PV | INC score | Class w1/w2 | Region (blend) c10/c20 |
|---|---|---|---|---|---|---|---|
| s2000_P50 | class + region (blend) | 27.1 | 66.9 | 99.6-99.7% | | 11-14% / 0% | 0-5% / 0-11% |
| s2000_P50 | all three (blend) | >120 (2.7%) | >120 (2.6%) | 95.4-95.5% | +7.4% | 14-16% / 0% | 19% / 5-6% |
| cfadsds_P50 | class + region (blend) | 28.1 | 78.5 | 98.9-99.0% | | 0-1% / 0% | 2-10% / 0-8% |
| cfadsds_P50 | all three (blend) | 62.2 | >120 (0.64%) | 96.3-96.5% | +6.9-7.0% | 0-3% / 0% | 12-13% / 10-13% |

* **On CFADS, class + region certifies with the blend basis in 28 s, against 89 s with cost share.** The blend asks less of the mega-project regions.
* **The triple stays fragile and depends on the scenario and the basis.**
  * Blend: certifies on CFADS, but not on s2000.
  * Cost share: the other way round.
* **Recommendation:** two layers for routine certified runs. For the triple, the 0.5% preset is a stretch within 2 minutes. Accept a 1-3% certified gap there. Mega-project start enumeration was tried in E49 and did not help: the gap is in the bound.
* **Regional price frontiers** (s2000_P50, both bases, x swept up, chained, `1pct`; the first point starts cold):

| x | Cost basis: s | Total PV | Cost shortfall c10/c20 | Blend basis: s | Total PV | Blend shortfall c10/c20 |
|---|---|---|---|---|---|---|
| none (Total plan) | | 100% | 49% / 44% | | 100% | 72% / 60% |
| 0.001 | 22.1 | 99.64% | 36% / 11% | 17.2 | 99.61% | 8% / 10% |
| 0.003 | 16.7 | 99.90% | 21% / 11% | 1.0 | 99.61% | 8% / 10% |
| 0.01 | 5.3 | 99.90% | 21% / 11% | 2.5 | 99.61% | 8% / 10% |
| 0.03 | 98.3 | 99.15% | 6% / 0% | 10.3 | 99.57% | 7% / 10% |
| 0.1 | 8.8 | 99.15% | 6% / 0% | 108.0 | 99.74% | 0% / 0% |

  * **A chained sweep at a coarse gap is sticky.** Once a plan is close to adherent, its penalty at the next price is below the 1% gap, so the preset certifies the previous plan as it is. The x = 0.003 and 0.01 points (and 0.03 → 0.1 on cost) return the previous point's schedule in 1-9 s. This is the 4g rule of MULTI_OBJECTIVE §5 in practice: the frontier is only as fine as its gap. A finer regional frontier needs the 0.5% or 0.1% target, or independent cold points.
  * **Blend:** even x = 0.001 removes most of the shortfall (72 → 8%) for 0.4% of PV. Full adherence needs x = 0.1.
  * **Cost share:** needs x ≈ 0.03 to bring the worst checkpoint-10 region to 6%, for 0.85% of PV.
* **Verdict (Stage C).** Frontiers cost seconds per point with the presets: class 2 min, regional 4-5 min, dimension 13 min (its binding floors are slow).
  * **Class knob: x = 0.01%.** This is the knee: full adherence for about 0.4% PV.
  * **Regional knob: x = 0.01%** (the CPO-equivalent λ) for the blend basis; x = 0.03% if the basis is cost share.
  * **Dimension: report the frontier rather than a single floor.** About 2 score points per PV point; f between 0.985 and 0.97 certifies quickly.

## Distribution layers: recommended defaults (2026-10-19)

| Layer | Form | Default knob | PV cost (2 scenarios) | Certified 1% / 0.5% (cold, 1 seed) |
|---|---|---|---|---|
| Dimension scoring | `w_p x PV_D` (CPO notebook weights), Total PV ≥ f · PV\*, carry kept; lexicographic polish before reporting | INC; pick f from the frontier (0.97-0.985 certify fast) | 1-3% for +2-3% raw INC (+5-8% score) | 15-30 s / 20-60 s |
| Activity class | soft, shortfall-only, fixed entitlement base, 20% free or the single-year lumpiness floor (capped at half the target), slopes 1:4:12, windows 1:0.81:0.66:0.54 | x = 0.01% | 0.2-0.5% | 24-32 s / 36-46 s |
| Region | **the user's early-horizon population profile (E56)**: 100% population share at cumulative checkpoints 5 and 10 only, ±10% free, capped at attainable spend, National excluded. Variants: blend or cost share at 10/20/30 (or 5-yearly + worst-case, E51) | x = 0.01% | 0.1-0.6% | 16-34 s / 23-54 s |
| Two layers | class + region | as above | 0.3-1.3% | 18-89 s / 32-79 s |
| All three | | as above | 3-5% | 62 s to >120 s; often a 1-3% gap at 120 s |

* **Chosen default (updated after E56): class at x = 0.01% plus the population profile at years 5 and 10** (`cpo_layers.DEFAULT_VARIANT`). Two intermediate choices were superseded:
  * the blend basis (cheaper and faster than cost share; cost-share targets are circular, since the pipeline sets its own target, MULTI_OBJECTIVE §3);
  * then the user's early-horizon population profile, which beats both in the first decade.
  * Blend and cost share remain variants (`reg-blend-x0.01`, `reg-cost-x0.03`).
* **Run layered solves cold, or warm with `warm_triage`.** A warm plan made for another objective (the Total plan) is slower than no plan at all.
* **Every soft layer leaves the LP bound unchanged** (the Jensen / lumpiness gap).
  * Easy cases certify once the primal side finds a low-penalty schedule.
  * The hard CFADS cases (cost-share region, the triple) are bound-limited: E49 pooled 18 runs and still had a 0.6-0.85% gap.
  * Branching on the mega-projects first (E49) did not help. The next lever is cuts on the shortfall rows.
* **Not done:**
  * 5-yearly checkpoints and a worst-case regional term;
  * lexicographic polish inside frontier sweeps (it costs about 100 s a point for HSP);
  * class × window LNS;
  * a 1e-5 proof of the chosen defaults (the user's "prove at the very end").

### E49: branch on the mega-projects first (MULTI_OBJECTIVE §2, the Jensen / lumpiness gap)

* **Hypothesis.** The soft layers leave the LP bound almost unchanged, because the LP spreads a mega-project's spend over several start years. Enumerating the start years of the k largest projects should make node bounds pay the lumpy shortfall and certify the hard CFADS cases (E46-E47).
* **What changed** (opt-in `mega_first=k`, tested for exactness against brute force with layers):
  * The LP tree branches on the k largest projects (total spend) while any of them is fractional. This is a lazy, LP-pruned, best-first form of the start-year enumeration.
  * It applies to every node LP: the ramp-up, strong branching in the workers, and re-splits.
  * A separate outer loop over all 7^k start-year cells (±2 years plus the two tails) would need 343 LPs per last year at k = 3.
* **Result** (cfadsds_P50, cold, 3 seeds, 120 s cap; seconds to the certified gap, or the gap left at 120 s):

| Case | k = 0 (off) | k = 2 | k = 4 |
|---|---|---|---|
| region (cost), 1% | 47 / >120 (1.15%) / >120 (1.55%) | 74 / >120 (1.12%) / >120 (2.16%) | 32 / 53 / >120 (1.15%) |
| region (cost), 0.5% | 0.81% / 1.62% / 1.62% | 1.13% / 1.22% / 1.41% | 0.85% / 0.93% / 1.06% |
| class + region, 1% | 117 / 73 / >120 (1.21%) | 62 / >120 (1.19%) / >120 (2.19%) | 1.13% / 1.17% / 1.23% |
| class + region, 0.5% | 0.88% / 1.09% / 1.73% | 1.03% / 1.06% / 1.14% | 1.10% / 1.12% / 1.36% |

* **Diagnosis: these cases are bound-limited.**
  * Pooling the best incumbent and the best bound over all 18 runs of a case still leaves a gap of 0.62% (region) and 0.85% (class + region). No arm can certify 0.5% within 2 minutes on this data.
  * 1% succeeds only when the run's incumbent lands within about 0.3% of the best known.
* **Mega-first trades bound for primal.**
  * At k = 4, every incumbent is within 0.4% of the best known (0.00-0.39%). At k = 0, incumbents drift up to 1%.
  * The reported bound is 0.2 points weaker (0.83-0.86% below the best known, against 0.62-0.65%): the 24-leaf ramp-up spends its splits on the mega-projects, and the gap is not carried by them.
  * The Jensen gap comes from the many medium-sized lumpy projects, not from the 2-4 largest.
* **Verdict.** No gain in time to a certified gap. `mega_first` stays opt-in and off.
  * Next levers are on the dual side:
    * c-MIR / cover cuts on the most violated shortfall rows, with the dominant member's spend as the divisor (MULTI_OBJECTIVE §2);
    * a proof-length run of one hard case, to learn how much of the 0.6-0.85% is really primal.
  * Meanwhile the practical answer for CFADS with a regional cost-share layer is a 1-1.5% certified gap in 2 minutes, or the blend basis (E46-E47b).

### E50: dual-side levers on the hard layered CFADS cases

* **Setup.**
  * Cases (cfadsds_P50, cold, 120 s cap): E49 found the two cost-share cases bound-limited, plus the chosen default pair (class + regional blend).
  * Seeds: 2 at 0.5% and 1 at 1%.
  * Arms are levers that already exist:
    * the presets as they are (4-candidate strong branching, 24 leaves);
    * `sb_candidates=16`, the E22-E23 CFADS bound-stall rule;
    * automatic strong branching with 60 leaves;
    * `cut_rounds=5`, root GUB-cover cuts.

| Case | Preset | base | sb16 | sbauto + 60 leaves | root cuts |
|---|---|---|---|---|---|
| region (cost) | 0.5% | >120 (1.41%) / >120 (1.42%) | **46.3 / 48.6** | 66.8 / 92.8 | 106.2 / >120 (0.95%) |
| region (cost) | 1% | >120 (1.59%) | **45.0** | 45.7 | 89.8 |
| class + region (cost) | 0.5% | >120 (0.94%) / >120 (0.98%) | >120 (1.02%) / >120 (1.96%) | >120 (1.06%) / >120 (2.00%) | >120 (1.87%) / failed |
| class + region (cost) | 1% | **59.9** | >120 (1.08%) | >120 (1.98%) | 63.3 |
| class + region (blend), the default | 0.5% | **31.2** / >120 (0.52%) | >120 (0.57%) / >120 (0.62%) | 41.4 / >120 (0.68%) | 120.1 / >120 (0.55%) |
| class + region (blend), the default | 1% | **24.9** | 48.8 | 41.9 | 34.4 |

* **Stronger strong branching certifies the regional cost-share layer alone at 0.5%, in 46-49 s, where the presets fail in 120 s.**
  * Its bound is 0.06-0.1 points tighter than base, and its incumbents are better too.
  * This is the same lever that fixed the CFADS bound stall without layers (E22-E23).
* **On class + region and on the default blend pair it costs time.** At 1%: 60 s → >120 s and 25 s → 49 s. The wider strong branching delays the leaves that find schedules.
  * Class + region (cost share) stays uncertified at 0.5% under every arm. Its bounds improve (0.74-0.77 points below the best known, against 0.94-0.97), but the pooled best-known gap is still 0.74%.
* **Root cuts change little. One cuts run failed with an IndexError** that did not reproduce. The campaign runner now records a failed run's traceback, so a recurrence can be diagnosed. `cut_rounds` with layers stays unsupported in practice (opt-in, off).
* **Verdict (and E53).** The presets stay as they are. The default pair certifies fastest with them (25 s to 1%, 31 s or a 0.52% gap at 0.5%). For a run with the regional **cost-share** layer alone, pass `sb_candidates=16`. No automatic rule: the effect reverses as soon as the class layer joins.

### E51: 5-yearly regional checkpoints and a worst-case term (MULTI_OBJECTIVE §3)

* **What changed** (tested: model = evaluator with worst-case rows in the mix):
  * **Worst-case primitive** (`layers.worst_case_block`, the `worst` section): a continuous M ≥ every row's normalised shortfall beyond its free zone, (target - free - spend) / N. M costs x% of PV\* per 1% of N, so the worst-off region counts on top of the sum.
  * **New options:**
    * `-cp5`: cumulative checkpoints at years 5, 10, ..., 30, with the same half-per-decade decay.
    * `-wcX`: the worst-case term at price X.
  * **New metric:** the worst blend shortfall at any 5-yearly checkpoint, which detects bunching between the 10/20/30 checkpoints.
* **Result** (regional blend basis x = 0.01%, cold, 1 seed, 120 s cap; the baseline row is E46):

| Scenario | Variant | 1% | 0.5% | Total PV | Blend c10/c20 | Worst at any 5-yearly checkpoint |
|---|---|---|---|---|---|---|
| s2000_P50 | 10/20/30 (E46) | 21.5 | 22.5 | 99.7-99.8% | 3-8% / 0-10% | 27-30% |
| s2000_P50 | + worst-case | 21.5 | 31.4 | 99.6-99.7% | 1% / 0-11% | 33-50% |
| s2000_P50 | 5-yearly | 28.7 | 46.3 | 99.5-99.7% | 1-8% / 0-4% | 1-8% |
| s2000_P50 | 5-yearly + worst-case | 22.1 | 69.7 | 99.6-99.7% | 0-5% / 0% | 0-5% |
| cfadsds_P50 | 10/20/30 (E46) | 25.8 | 71.8 | 99.3-99.4% | 10-18% / 5-8% | 35% |
| cfadsds_P50 | + worst-case | 21.4 | 32.4 | 99.2% | 2-6% / 4-8% | 35-46% |
| cfadsds_P50 | 5-yearly | 28.1 | >120 (0.78%) | 99.1% | 10% / 14% | 20% |
| cfadsds_P50 | 5-yearly + worst-case | 28.2 | >120 (0.76%) | 99.0-99.1% | 10% / 3-7% | 15-17% |

* **Three checkpoints invite bunching, as the research note predicted.** Plans optimised for years 10/20/30 leave a region 27-35% short at an in-between 5-yearly checkpoint (year 5, 15 or 25). 5-yearly checkpoints bring that worst case down to 1-20%, for 0.1-0.3 more points of PV.
* **The worst-case term tightens the worst region at the checkpoints it covers** (c10 shortfall 1-6% instead of 3-18%), at the same speed. On its own it does nothing between checkpoints.
* **Time:** all variants certify 1% in 21-29 s. At 0.5%, 5-yearly checkpoints are slower (46-70 s on s2000) and miss 120 s on CFADS, with a 0.76-0.78% gap.
* **Verdict.** For the regional layer, **5-yearly checkpoints plus the worst-case term (`-cp5-wc0.01`)** give the most even regional delivery for at most 0.3 points of PV. Certify those runs at 1%. The 10/20/30 form stays the faster 0.5% option. The final choice of regional default waits for E56 (the user's population-only, early-horizon profile).

### E56: the user's early-horizon population profile (regional)

* **The user's design.** The target is 100% population share, with no cost blend, applied **only at early checkpoints**. Over the full horizon a population target cannot hold, because a region runs out of selected projects. Early on there is room to pull projects forward. The rest of the design:
  * soft, within ±X% (free band), shortfall penalised;
  * each region's target capped at what its pipeline can spend by then (the existing safeguard).
* **Implementation.**
  * `reg-pop` basis: population share of the governed regions (National excluded), x the pool fraction.
  * `-atY[_Y2]`: cumulative checkpoints at those years only, half-per-decade decay.
  * New yardstick in the metrics: the worst region's population-share shortfall at years 5 and 10 (20% free).
* **Variants:** checkpoints {year 5, year 10, years 5 and 10} x free band {±10%, ±20%}, x = 0.01%; cold, 1 seed, 120 s cap. The stored Total plan's worst population shortfall is 63% / 80% at years 5 / 10 (s2000) and 55% / 67% (cfadsds).

| Scenario | Variant | 1% | 0.5% | Total PV | Worst population shortfall y5 / y10 |
|---|---|---|---|---|---|
| s2000_P50 | year 5 only, ±10% | 14.4 | 17.3 | 100.0% | 6% / 49-50% |
| s2000_P50 | year 10 only, ±10% | 22.1 | 55.6 | 99.9-100.0% | 0-33% / 0-1% |
| s2000_P50 | **years 5 + 10, ±10%** | 34.4 | 53.7 | 99.6-99.9% | **0% / 0%** |
| s2000_P50 | years 5 + 10, ±20% | 22.4 | 33.9 | 99.6-99.7% | 0% / 0% |
| s2000_P50 | blend at 10/20/30 (E46) | 21.5 | 22.5 | 99.7-99.8% | 26-44% / 7-25% |
| cfadsds_P50 | year 5 only, ±10% | 16.6 | 27.0 | 99.6% | 0-13% / 47-52% |
| cfadsds_P50 | year 10 only, ±10% | 21.4 | 50.0 | 99.3% | 43-49% / 0-9% |
| cfadsds_P50 | **years 5 + 10, ±10%** | 16.0 | 23.2 | 99.4% | **0-3% / 2-4%** |
| cfadsds_P50 | years 5 + 10, ±20% | 22.5 | >120 (0.57%) | 99.3-99.5% | 2-7% / 0-20% |
| cfadsds_P50 | blend at 10/20/30 (E46) | 25.8 | 71.8 | 99.3-99.4% | 49% / 40-51% |

* **Both early checkpoints are needed.** Either one alone leaves the other year 25-52% short.
* **With both, population fairness in the first decade is near-exact** (worst region 0-4% short), for 0.1-0.6% of Total PV. It certifies 1% in 16-34 s and 0.5% in 23-54 s: as fast as the blend, and faster than cost share.
* **±10% is the better band.** It is equally cheap and certified 0.5% on CFADS where ±20% did not.
* **Verdict: the regional default becomes the user's profile.** Population share, cumulative checkpoints at years 5 and 10, ±10% free, capped at attainable spend, x = 0.01% (`cpo_layers.DEFAULT_VARIANT = cls-x0.01+reg-pop-x0.01-at5_10-tol10`). It settles the target-basis question without a 50/50 compromise. Blend and cost share stay variants. After year 10 the layer sets no regional target, by design. E54 measures the default pair on all 10 scenarios.

### E55: MIR cuts on the shortfall rows (adopted in both presets)

* **Hypothesis.** E49-E50 showed the hard layered cases are bound-limited, and the root LP pays almost none of the lumpy shortfall (the Jensen gap). Each shortfall row is a covering knapsack over start choices plus continuous slack, so MIR cuts should lift the bound.
* **The cut.** For a fixed-target shortfall row `sum_{p,j} a_pj [p starts at j] + sum_k u_k >= D` (a ≥ 0, u ≥ 0), any divisor d > 0 with f = frac(D/d) > 0 gives the valid inequality

  `sum (floor(a/d) + min(1, frac(a/d)/f)) [p starts at j] + sum_k u_k / (d f) >= ceil(D/d)`

  This is the MIR of the covering knapsack; the ≤-form coefficients are invalid here. A first diagnostic with them pushed the LP above a feasible schedule's value.
* **What changed** (opt-in `layer_cuts=rounds`):
  * `layers.shortfall_mir` separates the most violated cut per row. Divisors tried: the members' largest window spends and D/k. The cut is written in either encoding: pulse directly, or step by differencing, with constants moved to the right-hand side.
  * `layers.mir_loop` runs the root rounds.
  * The rows reach the workers through the existing root-cut file.
  * Tests:
    * every cut holds at every sampled feasible schedule, in both encodings;
    * solves with cuts match brute force (proof mode and preset).
* **Offline diagnostic.** On the cfadsds class + region (cost) model, 3 rounds and 32 cuts lift the root LP by 0.17% of |objective|.
* **Result** (cfadsds_P50, cold, 2 seeds at 0.5% and 1 seed at 1%, 120 s cap):

| Case | Preset | base | 5 rounds of shortfall MIR cuts |
|---|---|---|---|
| region (cost) | 0.5% | >120 (0.57%) / >120 (1.03%) | 120.1 / **67.1** |
| region (cost) | 1% | >120 (1.51%) | **46.7** |
| class + region (cost) | 0.5% | >120 (1.11%) / >120 (1.32%) | **71.2** / >120 (0.83%) |
| class + region (cost) | 1% | 59.4 | **40.5** |
| class + region (blend) | 0.5% | 101.3 / 107.6 | **32.1 / 36.7** |
| class + region (blend) | 1% | 29.2 | **21.6** |

* **Every cell improves or stays the same.**
  * Each run's bound gets up to 0.25 points closer to the best known: class + region (cost) 0.48-0.53% below the best known, against 0.69-0.76% without cuts.
  * The default-style pair certifies 0.5% three times faster.
  * The cuts tighten the LP where the Jensen gap lives: the rows themselves. Branching on the largest projects (E49) and stronger strong branching (E50) could not do that.
* **Verdict: adopted.** Both presets now run `layer_cuts=5`. It only acts on instances with layers, so the no-layer presets and their final table are unchanged. Covered by the existing preset tests.

### E54: final table for the chosen default layers (all 10 CPO scenarios)

* **Setup.**
  * Layers: `cls-x0.01+reg-pop-x0.01-at5_10-tol10` (`cpo_layers.DEFAULT_VARIANT`): class x = 0.01%, plus the population profile at years 5 and 10, ±10%, x = 0.01%.
  * Presets as merged after E55, which run 5 rounds of shortfall MIR cuts.
  * Warm runs start from the CPO's stored Total plan with `warm_triage`; cold runs have no plan.
  * 1 seed, 120 s cap, load-clean. PV\* for the eight new scenarios comes from the `0.5pct` preset on their base instance (warm).
* Shortfalls are the worst of each layer's own yardstick (class windows 1-2; population share at years 5 and 10).

| Scenario | warm 1% | warm 0.5% | cold 1% | cold 0.5% | Total PV | worst class w1-w2 | worst pop y5-y10 |
|---|---|---|---|---|---|---|---|
| s2000_P50high | 29.0 | 40.0 | 36.4 | 39.0 | 99.7% | 9-10% | 0-8% |
| s2000_P50 | 29.6 | 41.8 | 20.0 | 37.9 | 99.3-99.8% | 10-11% | 0% |
| cfadsds_P50 | 33.6 | 70.0 | 31.7 | 41.9 | 99.1% | 0% | 0-11% |
| cfadsds_P50high | 34.8 | 57.2 | 34.0 | 61.8 | 99.0-99.2% | 0-1% | 0-9% |
| cfadsds_P95 | >120 (1.85%) | >120 (1.84%) | 67.7 | >120 (1.24%) | 99.1% | 0-7% | 12-25% |
| cfadsds_P95high | 94.6 | >120 (1.28%) | 79.8 | >120 (0.89%) | 99.0-99.2% | 0% | 7-19% |
| cfads_P50 | 16.6 | 19.1 | 15.6 | 16.4 | 99.6% | 0-2% | 0-6% |
| cfads_P95 | 28.6 | 28.9 | 19.7 | 25.1 | 99.3-99.4% | 0-7% | 0-7% |
| s1750_P50 | >120 (1.30%) | >120 (1.03%) | 108.9 | >120 (1.19%) | 99.4-99.8% | 2-4% | 0-3% |
| s1750_P95 | >120 (1.03%) | >120 (0.78%) | >120 (1.04%) | >120 (0.98%) | 98.9-99.5% | 0-10% | 0-12% |
| **shifted geo. mean** (unproven at 120) | 51.0 | 63.3 | 43.9 | 58.6 | | | |

* **The default layers cost 0.2-1.1% of Total PV on every scenario.**
  * Class shortfalls go from 16-48% (the Total plan) to 0-11%.
  * The worst population shortfall at years 5/10 goes from 55-80% to 0-12%, and 25% on the hardest case.
* **Cold, a certified 1% within 2 minutes on 9 of 10 scenarios (16-109 s), and 0.5% on 6 of 10.**
  * The misses are the CFADS P95 pair at 0.5% and the s1750 pair. s1750_P95 is also the outlier without layers (more than 300 s cold).
  * Their certified gaps at 120 s are 0.8-1.9%.
* **Warm from the stored Total plan is no faster than cold** (shifted geometric means 51 / 63 s against 44 / 59 s). That plan is far from the layered optimum, so a layered solve is effectively a cold solve.
  * Day-to-day use: the warm start that should help is a previous *layered* plan, as in E38. That is not yet measured with layers.
* **Against the presets without layers** (final table, cold): 1% shifted geometric mean 22.7 → 43.9 s, 0.5% 34.8 → 58.6 s. The layers roughly double the time to a certified answer.

### E52: proof runs of the chosen defaults ("prove at the very end")

* **Setup.**
  * Layers: the default (class + early population profile), on s2000_P50 and cfadsds_P50.
  * Warm start: E54's best plan.
  * Settings: the `0.5pct` preset with `rel_gap=1e-4`, automatic strong branching, 60 leaves, 60 s slices, shortfall MIR cuts; 1,800 s cap, load-clean.

| Scenario | Certified gap at 1,800 s | Improvement over the E54 plan |
|---|---|---|
| s2000_P50 | 0.014% | 0.13% |
| cfadsds_P50 | 0.065% | 0.26% |

* **Neither reached 0.01% in 30 minutes, but both are now certified close to the optimum.** The 1-2 minute 0.5% answers of E54 were within 0.15% (s2000) and 0.33% (CFADS) of the optimal layered schedule, as the presets promise.
* The 1e-5 standard of the no-layer KPI is out of reach for layered models in this budget, as expected from E49-E55 (bound-limited). For decision use, the 0.5% preset answer is accurate enough. A 0.1% answer needs tens of minutes.

### E57: one model iteration with the default layers (the E38 protocol)

* **Setup.**
  * Edits: k = 1, 3 or 5 random projects of the layered s2000_P50 and cfadsds_P50 instances. Spend and PV are scaled by U[0.8, 1.2] and the earliest start is delayed 1-2 years, exactly as in E38.
  * Each edited instance is re-solved warm from the previous *layered* plan (E54's best), repaired where infeasible.
  * Presets as merged, 1 seed, 120 s cap. `s` = certified by the screen alone after the repair.

| Edited projects | s2000 1% | s2000 0.5% | cfadsds 1% | cfadsds 0.5% |
|---|---|---|---|---|
| 1 | 1.0s | 8.9 | 0.6s | 19.7 |
| 3 | 6.2s | 19.9 | 27.9 | 115.1 |
| 5 | 9.7 | 17.8 | 2.0s | 19.6 |

* **A layered model iteration re-solves in seconds.** Median about 4 s to 1% (4 of 6 certified by the screen) and about 20 s to 0.5%. Cold layered solves of the same scenarios take 20-32 s and 38-42 s (E54).
* Without layers, E38 took 1.6 s / 3.0 s. With layers the screen certifies less often, because the edited plan's penalty moves, so the tree runs more.
* **Verdict.** In day-to-day use, keep the previous layered plan and warm-start from it. A plan made for a different objective (the Total plan) does not help (E44-E54).

**E56 addendum.** This was requested again by the other session as "E50"; that number was already taken, so it is E56.
* **What was already covered:**
  * pure population share, attainable caps, National excluded;
  * year 5 / year 10 / both, at ±10% and ±20%;
  * cfadsds_P50 and s2000_P50, cold, at 1% and 0.5%;
  * comparison against the blend and cost share (table above);
  * class + region on all 10 scenarios (E54).
* **Weighting.** The 5 + 10 variant weights the checkpoints half per decade (1 : 0.71), not 2 : 1. Both checkpoints end at 0-4% shortfall in the layer's own yardstick, so the relative weight has nothing left to trade. A higher price was not needed for the same reason.
* **Drift diagnostic** (reported only, not optimised). Each plan's shortfall below the attainable-capped population target, at checkpoints through year 30, with no free band and no lumpiness allowance. The worst / mean is taken over the 16 governed regions, as % of target, for the 0.5% cold plans.

| Scenario | Plan | y5 | y10 | y15 | y20 | y30 |
|---|---|---|---|---|---|---|
| s2000_P50 | Total plan (CPO) | 100% / 37% | 100% / 31% | 100% / 32% | 84% / 25% | 3% / 1% |
| s2000_P50 | population profile (5 + 10, ±10%) | 45% / 12% | 41% / 8% | 41% / 9% | 35% / 7% | 3% / 0% |
| s2000_P50 | blend (10/20/30) | 72% / 15% | 50% / 12% | 41% / 10% | 35% / 7% | 3% / 0% |
| s2000_P50 | cost share (10/20/30) | 100% / 31% | 81% / 30% | 60% / 16% | 60% / 14% | 12% / 1% |
| cfadsds_P50 | Total plan (CPO) | 100% / 62% | 100% / 55% | 100% / 50% | 100% / 39% | 75% / 22% |
| cfadsds_P50 | population profile (5 + 10, ±10%) | 47% / 12% | 52% / 22% | 61% / 17% | 72% / 12% | 68% / 10% |
| cfadsds_P50 | blend (10/20/30) | 99% / 41% | 70% / 29% | 79% / 25% | 45% / 9% | 19% / 3% |
| cfadsds_P50 | cost share (10/20/30) | 100% / 43% | 87% / 41% | 89% / 38% | 82% / 27% | 35% / 8% |

  * **Up to year 10 the profile is the fairest of the four** (mean shortfall 8-22%). The remaining worst cases (41-52%) are small regions whose deficit is within one year's spend of a project: the free band and lumpiness floor the layer uses absorb them, which is why its own yardstick shows 0-4%.
  * **After year 10, with no regional target by design:**
    * s2000: delivery stays as even as the blend (mean 7-9%).
    * CFADS: the worst region drifts to 61-72% short at years 15-30, against 19-79% for the blend, but the mean stays at 10-17%.
  * If the late years should not drift on CFADS, a later light checkpoint (for example year 20 at low weight) is the cheap addition. It is not part of the user's stated profile, so it is not in the default.

## Practitioner capabilities (from 2026-10-19)

The user is preparing a practitioner's report that presents capopt as a rapid optioneering tool. Each capability below is built (where code is needed), tested, and demonstrated on the CPO data with report-ready outputs. These are capability demonstrations, not speed races: timing runs use the `1pct` / `0.5pct` presets, one seed, on a machine with at most 2 outside cores busy (the OneDrive sync keeps about 1-1.5 cores busy). Comparisons of PV use **value runs**: re-optimisations warm from the base plan, `0.5pct` preset with `rel_gap=1e-4`, 300 s cap; their achieved certified gap is reported. Driver: `benchmarks/experiments/practitioner.py` (one command per capability; chart data and tables go to the gitignored `private/report_data/`). Scenarios: CFADS = `cfads_P50`, CFADS-DS = `cfadsds_P50`.

### E58: forced start windows

* **What changed** (`capopt.timing`, tested): `with_windows(raw, {project: (a, b)})` restricts a project's start to [a, b], inclusive; `pin` is the single-year case a = b. It is an instance transform: the allowed starts (and their PV) are cut to the window, so the decomposition, the cash-floor bound, the screen, ALNS and warm-start repair all handle it unchanged. A window that holds none of a project's allowed starts is an error, not a silent relaxation. Tests: windowed solves match brute force (proof mode and the `1pct` preset), and a previous plan that breaks a new window is repaired into it.
* **Bug found on the way**: the node-LP solver cache in `parallel` was keyed by `id(model)`. After the warm-start repair built and dropped its sub-models, a new model could reuse a dropped model's id with the same row count but other columns, and `_lp_node` indexed past its solution (an `IndexError`, seen once in CI). The solver now lives on the model itself (regression test in `test_smoke.py`).
* **Arms**: P217 (the largest project, low BCR, optimal start 2046-2054) in a 2030-2035 window, or pinned to 2030; ten random mid-size projects with 5-year windows around their base starts, 5-9 years earlier than their base starts, or pinned 7 years earlier.

| Scenario | Windows | PV change (value run) | Value-run gap | Warm 1% | Warm 0.5% | Cold 1% | Other projects re-timed |
|---|---|---|---|---|---|---|---|
| CFADS | none (base) | 0 | 0.010% | | | | |
| CFADS | P217 in 2030-2035 | -0.73% | 0.007% | 10.1 | 13.2 | 6.9 | 171 |
| CFADS | P217 pinned to 2030 | -1.62% | 0.008% | 52.0 | 54.4 | 9.7 | 165 |
| CFADS | 10 windows around the base starts | 0.00% | 0.010% | 0.2 | 0.2 | 5.0 | 0 |
| CFADS | 10 windows 5-9 years earlier | -0.01% | 0.008% | 5.3 | 5.3 | 5.0 | 92 |
| CFADS | 10 pins 7 years earlier | -0.02% | 0.003% | 5.3 | 5.3 | 7.4 | 151 |
| CFADS-DS | none (base) | 0 | 0.049% | | | | |
| CFADS-DS | P217 in 2030-2035 | -2.87% | 0.143% | 67.1 | 65.1 | 39.3 | 167 |
| CFADS-DS | P217 pinned to 2030 | -5.34% | 0.032% | 43.1 | 47.7 | 14.5 | 196 |
| CFADS-DS | 10 windows around the base starts | 0.00% | 0.038% | 0.2 | 0.2 | 18.0 | 34 |
| CFADS-DS | 10 windows 5-9 years earlier | -0.02% | 0.047% | 5.4 | 15.7 | 16.6 | 83 |
| CFADS-DS | 10 pins 7 years earlier | -0.02% | 0.067% | 5.4 | 14.8 | 16.5 | 88 |

* **Windows that contain the optimum cost nothing and certify at once** (0.2 s warm). Ten mid-size projects forced 5-9 years early cost only 0.01-0.02% of PV: the rest of the programme re-times around them (80-150 other projects move, mostly by a year or two, on a near-flat plateau of equally good schedules).
* **One large, low-BCR project forced early is expensive**: 0.7% (window) to 1.6% (pin) of PV on CFADS, and 2.9% to 5.3% on CFADS-DS, where early cash is tighter. The window is worth having: letting the optimiser choose inside 2030-2035 halves the cost of the pin.
* **Speed**: every arm certifies 1% in 5-67 s. When a pin moves a large project by decades, a cold solve is faster than a warm start from the old plan (14-39 s against 43-67 s on CFADS-DS): the repaired old plan is far from the new optimum, while for small or contained windows warm is faster (0.2-5 s).
* **Verdict**: forced windows are an instance option (`timing.with_windows`, `timing.pin`). For a big override, start cold.

### E59: sequencing rules (precedence)

* **What changed** (`capopt.timing`, tested): an instance may carry a `precedence` section, `lo <= start_B - start_A <= hi`. Builders cover the usual rule types: `after_start` (B not before A starts, plus a lag), `after_finish` (B not before A's completion, start + duration, plus a lag; with `within=k`, B starts no later than k years after A finishes, and k = 0 means exactly then), `not_after` (B no later than A) and `lag_between` (any lag window).
  * The evaluator rejects a schedule that breaks a rule.
  * The fixed-last-year model writes each bound in the strong time-indexed form `[start_B <= t] <= [start_A <= t - lo]` for every year t; with the step encoding that is the two-term row `z_B,t <= z_A,t-lo`. The rules restrict starts only and do not depend on the last build year, so MIP(L) still equals the true objective at a schedule's own L and over-estimates it at a later one: the decomposition is unchanged.
  * Chains of rules tighten Lmin (`timing.earliest_starts`, which also detects rule sets that cannot hold); warm-start repair re-times the projects of a broken rule; the numba kernel refuses such instances.
  * Tests (`tests/test_timing.py`, 53 cases): every rule type and a chain against brute force (proof mode and the `1pct` preset); the model rows accept exactly the schedules the evaluator accepts, in both encodings, at a schedule's own and a later last year; impossible rules return no schedule; a previous plan that breaks a new rule is repaired.
* **Demonstration** (CPO data): a random pair of mid-size projects per scenario where B starts well before A in the optimum (CFADS: A = P167, B = P088, 15 years; CFADS-DS: A = P137, B = P114, 10 years), so every rule below binds; plus a chain of four projects that must each finish before the next starts (in reverse of their optimal order). Value runs certified to 0.01-0.06%; timing with the `1pct` preset.

| Rule | CFADS: PV change | warm 1% (s) | cold 1% (s) | CFADS-DS: PV change | warm 1% (s) | cold 1% (s) |
|---|---|---|---|---|---|---|
| B not before A starts | 0.000% | 0.4 | 7.9 | -0.001% | 0.7 | 18.6 |
| B not before A finishes | -0.001% | 0.5 | 7.3 | -0.001% | 1.8 | 16.9 |
| B within 2 years of A finishing | -0.002% | 0.4 | 7.9 | -0.001% | 1.8 | 28.3 |
| B exactly when A finishes | +0.002% | 0.5 | 7.4 | -0.001% | 2.2 | 17.2 |
| B no later than A | 0.000% | 0.4 | 7.7 | -0.001% | 0.6 | 18.4 |
| B 3-6 years after A starts | -0.002% | 0.4 | 5.0 | -0.001% | 2.1 | 18.4 |
| chain of 4, finish to start | 0.000% | 2.8 | 7.4 | -0.003% | 0.5 | 18.8 |

* **Every rule type is honoured and certified fast**: warm 1% in 0.4-2.8 s from the previous plan (always repaired, since the plan broke the rule), cold 1% in 5-28 s. Changes within ±0.002% are inside the value runs' certified gap (0.01% on CFADS), i.e. no measurable cost.
* **On mid-size projects a rule is nearly free**: the optimiser re-times the pair and 40-76 other projects by a year or two, along the near-flat plateau of equally good schedules. A rule's price depends on what it binds, not on its type; rules involving a large project are measured with E62 (the cost of overriding the optimum).

### E60: benefit profile shapes

* **What changed** (`capopt.benefits`, tested): an instance stores only each project's PV per start. For the CPO scenarios that PV comes from a flat real benefit for 40 years after completion ("LIN40"), discounted with the MBCM factors and cut at the horizon, so the stream is recovered exactly from `pv_int` (`flat_annual`; worst misfit over 233 projects 1e-7). From the stream, `shape_stream` builds three shapes with the **same real-terms total over the same 40 years**:
  * **linear**: the supplied stream; cumulative benefit rises in a straight line;
  * **logarithmic**: concave accumulation `C(x) = ln(1 + 0.25x) / ln(11)`, front-loaded (34% of the total in 5 years, 52% in 10);
  * **exponential**: convex accumulation `C(x) = (e^(mu x) - 1) / (e^(40 mu) - 1)`, back-loaded, with mu set so that it mirrors the logarithmic curve (7% in 10 years, 48% in 30).

  `pv_row` / `rebuild` turn streams back into `pv_int`. Tests (`tests/test_benefits.py`): exact recovery and rebuild, equal totals, the mirror property, and front-loaded > linear > back-loaded PV.
* **Setup**: CFADS and CFADS-DS; two scopes: a 40-project subset (35 at random plus P217, the two highest-BCR mid-size projects and the two smallest) and the whole programme. Value runs warm from the base plan (certified to 0.01-0.14%); cold `1pct` runs to show the solve itself is unaffected.

| Scenario | Reshaped | Shape | Programme PV vs linear | Projects re-timed | PV gained by re-optimising (vs the linear plan, re-valued) | Cold 1% (s) |
|---|---|---|---|---|---|---|
| CFADS | all 233 | logarithmic | +12.9% | 25 | 0.001% | 4.5 |
| CFADS | all 233 | exponential | -12.4% | 0 | 0 | 5.1 |
| CFADS-DS | all 233 | logarithmic | +12.6% | 57 | 0.001% | 18.8 |
| CFADS-DS | all 233 | exponential | -12.6% | 70 | 0.013% | 17.6 |
| CFADS | 40 | logarithmic | +1.7% | 32 of the 40 (24 earlier, mean 4.7 years) | 0.02% | 4.5 |
| CFADS | 40 | exponential | -1.6% | 0 | 0 | 6.7 |
| CFADS-DS | 40 | logarithmic | +1.9% | 23 of the 40 (14 earlier) | 0.009% | 17.5 |
| CFADS-DS | 40 | exponential | -1.9% | 23 of the 40 (14 later) | 0.115% | 15.6 |

* **Any profile is just a different PV per start year**, and every case certifies 1% cold in 5-19 s, as for the supplied benefits.
* **The shape moves the value of the programme a lot, the schedule very little.** Discounting rewards early benefits, so the same real benefits are worth about 12.5% more front-loaded and 12.5% less back-loaded. But re-optimising for the new shape gains at most 0.1% over keeping the old schedule: front-loaded projects drift a few years earlier, back-loaded ones later, and the cash envelope still sets the sequence.

### E61: benefit growth against the discount rate (the deferral bias and two remedies)

* **Setup** (`capopt.benefits.rebuild` with `growth` and `anchor`): every project's real benefit grows by g = 3% or 5% a year, above the MBCM discount rate (2%, then 1.5% after year 30). Two anchors:
  * **calendar-indexed**: benefit in calendar year y is multiplied by (1 + g)^(y - 2026), so a later start earns more growth;
  * **opening-anchored**: year k after opening earns (1 + g)^k, whenever the project opens, so delay earns nothing.

  CFADS-DS; the production objective (holding-cash penalty on) and the pure-PV objective (penalty off, the extreme); value runs certified to 0.01-0.1%. Each resulting schedule is then valued under every basis (real, calendar 3/5%, opening 3/5%).
* **PV of one project against its start year** (analytic, `growth_curves`): a median-size project is worth $11M started in 2026 and $6M started in 2060 with no growth, but under calendar 5% growth $34M in 2026 rising to $90M in 2060. Opening-anchored 5% growth falls with delay ($29M to $15M), like the un-grown stream. The large project P217 under calendar 5% rises from $14.7bn (2026) to $32.6bn (2050), and falls only when the 2099 horizon starts cutting its 40-year stream.

| Objective | Optimised on | Median start | Starting after 2050 | Real PV | PV, calendar 3% | PV, calendar 5% | PV, opening 3% | PV, opening 5% |
|---|---|---|---|---|---|---|---|---|
| penalty on | real (no growth) = remedy (a) | 2045 | 39% | **$73.66bn** | $256.8bn | $634.7bn | $130.27bn | $200.73bn |
| penalty on | calendar 3% | 2054 | 55% | $65.42bn (-11.2%) | **$282.6bn** | | | |
| penalty on | calendar 5% | 2057 | 56% | $65.30bn (-11.3%) | | **$794.9bn** | | |
| penalty on | opening 3% = remedy (b) | 2045 | 38% | $73.66bn | | | **$130.27bn** | |
| penalty on | opening 5% = remedy (b) | 2045 | 40% | $73.66bn | | | | **$200.75bn** |
| penalty off | real (no growth) | 2047 | 40% | **$73.70bn** | | | | |
| penalty off | calendar 3% or 5% | 2058 | 95% | $61.32bn (-16.8%) | **$298.7bn** | **$889.4bn** | | |
| penalty off | opening 3% / 5% | 2046-2047 | 43-45% | $73.66-73.70bn | | | $130.33bn | $200.84bn |

* **The deferral bias is real and large.** With calendar-indexed growth above the discount rate every project is worth more the later it starts. With the cash penalty off, 95% of projects start after 2050 (median 2058, against 2047 without growth), clustering just before the horizon would cut their benefit streams. With the penalty on, holding cash idle is costly, so the spend profile stays on the funding path, but the order flips: the median start moves from 2045 to 2054-2057.
* **The bias destroys real value**: valued on real benefits, the calendar-grown optimum is 11% (penalty on) to 17% (penalty off) worse. The extra "PV" it reports (+10% at 3%, +25% at 5% over the real optimum's grown PV) is an artefact of where calendar growth is anchored, not of better sequencing.
* **Remedy (a), growth-neutral prioritisation** (the user's idea): optimise on real benefits, then report the schedule's PV with growth applied. It keeps the real optimum ($73.66bn real) and reports $256.8bn / $634.7bn under calendar 3% / 5% growth.
* **Remedy (b), opening-anchored growth**: optimise on growth that runs from each project's opening. Delay earns nothing, so the optimiser picks the same schedule as remedy (a) (median 2045, real PV $73.66bn), and the reported PV ($130.3bn at 3%, $200.8bn at 5%) is internally consistent with the growth assumption.
* **Verdict**: both remedies remove the bias and give the same programme. (b) is the coherent model when growth describes demand that builds after opening; (a) is the simple, conservative choice when the growth assumption is uncertain: it never lets the growth rate change the order of projects.
