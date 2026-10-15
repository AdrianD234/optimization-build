# Capital programme scheduling - benchmark pack

Four instances of a 100-project capital programme scheduling problem, an exact scoring script, the best-known solutions, and the MindOpt benchmark times to beat.

## Contents

| Path | What it is |
|---|---|
| `FORMULATION.md` | The maths: decision, cash rules, objective. Read this first. |
| `HANDOFF.md` | Context, what is known about the instances, ideas for beating the benchmark. |
| `instances/*.json` | The four instances (solver-neutral, all numbers in the solver's integer units). |
| `solutions/*_best_known.json` | Best-known schedule and objective per instance (`proven_optimal` flag). |
| `solutions/*_reference.json` | The schedules from the original MindOpt pipeline run. |
| `results/BENCHMARK.md` | **The benchmark table**: MindOpt cold vs heuristic-then-MindOpt, time to 1% / 0.1% / best-known / proof. |
| `results/benchmark_results.json`, `results/logs/` | Full results and MindOpt logs. |
| `reference/evaluate.py` | **The referee**: scores any schedule exactly as the MIP does (no solver needed). |
| `reference/hill_climb.py` | Baseline heuristic (constructions, simulated annealing, hill-climb polish). Pure Python. |
| `reference/mindopt_model.py` | The reference MIP (MindOpt), incl. warm-start loading. |
| `reference/run_benchmark.py` | Re-runs the whole benchmark on your machine (needs MindOpt + licence). |
| `data/projects.csv` | Human-readable project list (cost, duration, BCR, growth, spend profile). |
| `excel/` | The original live workbook for the 100-project model (cross-check only). |

## Instances

| Instance | Benefits | Objective | Best-known objective | Proven optimal |
|---|---|---|---|---|
| `P100_raw_penalised` | no growth | production (holding-cash penalties) | -7,408,929,414 | yes |
| `P100_growth_penalised` | 1-3% real growth | production | -13,624,914,443 | yes |
| `P100_raw_nopenalty` | no growth | maximise PV only | -74,119,531,264,779 | no (gap 0.0007%) |
| `P100_growth_nopenalty` | 1-3% real growth | maximise PV only | -138,938,222,387,039 | yes |

## Scoring a schedule

    python reference/evaluate.py instances/P100_raw_penalised.json my_schedule.json

`my_schedule.json` = `{"starts": {"P001": 0, "P002": 5, ...}}` (start index 0 = 2026, one entry per project). The output gives feasibility, the objective and its components. Lower is better.

## Beating the benchmark

* Use the instances unchanged; the score is `reference/evaluate.py`'s objective of the schedule you return.
* Report wall-clock time to reach within 1% and 0.1% of the best-known objective, and to reach it (or beat it).
* For like-for-like times, re-run `reference/run_benchmark.py` on the same machine as your optimiser (the reference figures are from a 20-thread Windows machine; MindOpt times also vary between runs).
* The two **penalised** instances are the meaningful test: local search struggles there and MindOpt needs 3-4 minutes to prove the optimum.

## Requirements

Python 3.10+. `evaluate.py` and `hill_climb.py` use the standard library only. `mindopt_model.py` / `run_benchmark.py` need `mindoptpy` and a MindOpt licence (floating licence: one solve at a time).
