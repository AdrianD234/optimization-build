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
