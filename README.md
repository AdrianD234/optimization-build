# optimization-build: capopt

capopt is an open-source optimiser for the capital-programme scheduling MILP in `benchmark_pack/`. It is built on HiGHS, and the target to beat is MindOpt's times on the pack's benchmark.

* `benchmark_pack/` is the problem set: instances, formulation, the referee (`reference/evaluate.py`), best-known solutions and MindOpt's benchmark times. It is read-only, and the tests hash-check it.
* `src/capopt/` is the optimiser (see [docs/MASTER_PLAN.md](docs/MASTER_PLAN.md) for the architecture and roadmap).
* `benchmarks/` holds experiment scripts, stored runs, and the generated [benchmarks/BENCHMARK.md](benchmarks/BENCHMARK.md).
* `docs/research/` holds the literature reviews behind the roadmap: [MILP frontier](docs/research/MILP_FRONTIER.md) and [AI-for-maths results](docs/research/AI_MATH_RESULTS.md).
* `tools/` holds the scripts that exported the pack, kept for provenance.

## How capopt works

1. **Fixed-last-year decomposition.** The only awkward coupling in the reference MIP is the endogenous last build year. Fixing it at L removes every activity binary and big-M row. On the penalised instances the model drops from 6.3k columns × 18.3k rows to 1.7k × 137. OPT = min over L of MIP(L) (the proof is in `formulation.py` and is property-tested).
2. **LP pruning of last years.** L >= 18 because cumulative funding must cover total spend. For L >= 19, the LP bounds sit at least 66% above the optimum, so one MIP (L = 18) decides the instance.
3. **Step encoding.** "Project p has started by year s" variables give balanced branching, which HiGHS cannot get from the reference encoding because it has no SOS branching.
4. **Parallel split-and-solve.** HiGHS' tree search is sequential. capopt splits the model into subtrees chosen best-first from LP bounds and solves them in 20 worker processes. The workers share an incumbent, which serves as a cutoff and as an early-stop signal. Time-sliced re-splitting is optional.

## Quick start

```bash
pip install -e ".[dev]"
capopt solve P100_raw_penalised --out schedule.json      # proven-optimal schedule
python benchmark_pack/reference/evaluate.py benchmark_pack/instances/P100_raw_penalised.json schedule.json
pytest                                                    # unit, parity, decomposition and smoke tests (~30 s)
pytest -m slow                                            # full-scale regression (minutes, 20 threads)
capopt bench --configs capopt_parallel --seeds 0,1,2      # store benchmark runs
capopt report                                             # regenerate benchmarks/BENCHMARK.md
```

## Status

See [benchmarks/BENCHMARK.md](benchmarks/BENCHMARK.md) for current numbers against MindOpt, and [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) for the experiment log.
