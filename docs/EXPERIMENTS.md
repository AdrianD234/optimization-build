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
* **A/B on branch-and-bound**: see below.
