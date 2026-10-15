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

* **Verdict**: kept. Step encoding with cumulative balance rows is the default. `growth_penalised` is proven on one thread in 18 s (MindOpt cold: 179 s). `raw_penalised` is still open after 180 s, so it needs parallelism.

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
* **Result** (proof times; MindOpt cold on 20 threads in brackets): raw_penalised 447 s (I1) / **216 s** (I2) [232]; growth_penalised 24 / **21** [179]; raw_nopenalty **222** / 249 [not proven in 900 s]; growth_nopenalty 1.5 / **0.58** [132].
* **New proven optimum for P100_raw_nopenalty**: -74,119,916,766,966. That is 5.2e-6 relative better than the pack's best-known (-74,119,531,264,779, which MindOpt never proved). Both encodings prove it independently, and `benchmark_pack/reference/evaluate.py` confirms it. Stored in `benchmarks/solutions/P100_raw_nopenalty_proven_optimal.json` (the pack itself is left unchanged).

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
| MindOpt cold (recorded) | 232 | 179 | not proven (900) | 132 | reference |
| I3 split-and-solve, 60 leaves | **81** (80-82) | **6.4** (6.3-6.4) | 73 (73-73) | **2.6** | kept (default) |
| I4 + time-sliced re-splitting (10 s) | 139 (102-140) | 6.3 | 79 (79-81) | 2.6 | **rejected**: restarts discard HiGHS' search state, and the optimum is found later |
| I5 + 4 LNS workers (60 s) | 81 (79-81) | 6.6 | 75 (75-75) | 2.7 | neutral: 6-10 improvements per worker, but never the final one before the tree finds it |
| I6 + root cover cuts (30 rounds) | 95 (92-99) | 8.0 | **58** (57-59) | 5.1 | mixed: -21% on raw_nopenalty, +17% on raw_penalised; the cut loop ran serially before the pool |

Times to within 1% and 0.1% are 1.8-3.5 s for I3 on every instance (MindOpt: 5-94 s and 10-105 s). The pack's best-known value is reached in 2.4-73 s (MindOpt: 107-704 s).

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
