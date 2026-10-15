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
4. **Parallel split-and-solve.** HiGHS' tree search is sequential. capopt splits the model into subtrees chosen best-first from LP bounds and solves them in 20 worker processes. The workers share an incumbent, which stops a subtree as soon as its bound reaches it.
5. **Overlapping start-up.** While the worker pool spawns, the master tries a direct solve for 1.5 s, which finishes the easy instances before the pool is up.

## Results (this machine, capopt default vs MindOpt cold)

| Instance | Proof of optimality | Within 0.1% |
|---|---|---|
| P100_raw_penalised | **86 s** vs 232 s | 4.2 s vs 32 s |
| P100_growth_penalised | **7.4 s** vs 179 s | 0.9 s vs 105 s |
| P100_raw_nopenalty | **74 s** vs not proven in 900 s; new optimum, 5.2e-6 better than the pack's best-known | 1.2 s vs 28 s |
| P100_growth_nopenalty | **0.74 s** vs 132 s | 0.3 s vs 10 s |

The full ladder (baseline B0, then I1-I10) is in [benchmarks/BENCHMARK.md](benchmarks/BENCHMARK.md). The QA scorecard and known gaps are in [docs/QA.md](docs/QA.md).

## MindOpt

MindOpt's floating licence allows one solve at a time. `capopt bench --configs mindopt_cold` re-runs MindOpt live under a machine-wide lock: solves queue across processes, the licence is probed with backoff, a solve is never killed, and the run is skipped cleanly when MindOpt or the licence is missing. See `src/capopt/mindopt_runner.py`.

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

## Running long benchmarks

Long benchmark runs go through a **campaign**, which keeps a heartbeat and can't die unnoticed:

```bash
capopt campaign launch --spec my_campaign.json   # detached runner, verified heartbeat within 60 s
capopt watch my_campaign                         # one line per state change: STARTED / PROGRESS / STALLED / COMPLETE
capopt status my_campaign                        # % done, current run, ETA; exit 1 if STALLED / NOT_STARTED / FAILED
```

A campaign is STALLED when its heartbeat is 5 minutes old, its runner process is gone, or a run outlives its time limit. Agents must follow the runbook in [docs/MASTER_PLAN.md §7](docs/MASTER_PLAN.md#running-long-benchmarks-agent-runbook): never start long jobs with bare `&` from a shell tool, always arm `capopt watch`, and never leave a run unobserved for more than 5 minutes.

## Status

See [benchmarks/BENCHMARK.md](benchmarks/BENCHMARK.md) for current numbers against MindOpt, and [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) for the experiment log.
