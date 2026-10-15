# QA pass: initial framework (2026-10-15)

The rubric is the one in [MASTER_PLAN.md §6](MASTER_PLAN.md). Each line links to its evidence. The score is 10 points only if every item is met with evidence in the repository.

| # | Criterion | Evidence | Result |
|---|---|---|---|
| 1 | Every returned schedule is feasible and re-scored by the referee script | `bench.run` re-scores every stored run with `benchmark_pack/reference/evaluate.py`, loaded from file. All 70+ stored runs show "Referee feasible: yes" in [BENCHMARK.md](../benchmarks/BENCHMARK.md). The slow regression asserts referee feasibility and an equal objective | ✅ |
| 2 | Evaluator parity (bit-identical objective) | `tests/test_evaluate.py`: best-known and reference schedules, plus 1,000 random or perturbed schedules per instance, give an identical objective and identical feasibility | ✅ |
| 3 | Every formulation is consistent with the evaluator on all instances | `tests/test_formulation.py`: 4 instances × 4 last-year variants, plus the big-M reference. Rows are satisfied, the objective matches, and schedule → vector → schedule round-trips | ✅ |
| 4 | The decomposition argument is documented and property-tested | Proof in the `formulation.py` docstring. `tests/test_decomposition.py` checks model value >= true objective (equal at L = last), the no-penalty exactness, LP pruning of L >= 19, and the split partition and leaf bounds. Brute force on tiny instances whose optimum ends after Lmin (`tests/test_smoke.py`) | ✅ |
| 5 | Penalised instances proven optimal and matching best-known | `pytest -m slow` (`tests/test_regression.py`) proves **all four** instances with the default pipeline (87 s, 7.6 s, 75 s, 0.74 s on this machine) | ✅ |
| 6 | Benchmark reproducible from one command, with stored traces | `capopt bench --configs <cfg> --seeds 0,1,2` and `capopt report`. Every run in `benchmarks/results/<config>/` stores its full incumbent trace, settings, machine and schedule | ✅ |
| 7 | Every improvement claim comes from a stored run | Ladder runs: `benchmarks/results/<config>/`. Experiments: `benchmarks/results/exp/` (exp01 formulations, LP bounds by last year, exp02 sweep, exp04 cuts, exp05 cutoff). [EXPERIMENTS.md](EXPERIMENTS.md) cites them | ✅ |
| 8 | Lint clean and CI green | `ruff check src tests benchmarks` is clean. GitHub Actions passes lint and tests on Ubuntu and Windows × Python 3.11/3.13 (see the PR checks) | ✅ |
| 9 | No changes to the benchmark instances or referee | `tests/test_pack_integrity.py` hash-checks the 16 instance, referee and solution files. capopt's new optimum is stored separately in `benchmarks/solutions/` | ✅ |
| 10 | Docs cover running, adding an experiment, and what is not yet done | [README.md](../README.md) (run), [MASTER_PLAN.md](MASTER_PLAN.md) §7 (workflow and experiments) and §3b (status), plus "Known gaps" below | ✅ |

**Score: 10 / 10**

## What the scorecard does not cover (known gaps)

* **MindOpt results are private.** MindOpt runs (the pack's recorded runs and live diagnostics through the licence-safe `mindopt_runner.py`: machine-wide lock, licence backoff, no killing) are kept under the gitignored `private/` folder because of the licence's terms on publishing benchmarks. Public comparisons use the HiGHS baseline.
* **Only four instances.** The parallel solver is effectively deterministic, so repeats measure noise but not robustness. Configuration choices (I9 default, cuts off) may be fitted to these four. The next step is a held-out set of perturbed instances (`synthetic.perturb`).
* **raw_penalised is the slowest instance (86 s).** About 85% of that time goes to finding the optimum. LNS and re-splitting did not help (I4 and I5 in EXPERIMENTS.md).
* **HiGHS C++ has not been forked.** Nothing so far needs solver internals (MASTER_PLAN Phase 5).
* **The evolve-a-heuristic loop (Phase 6)** is designed but not built.
