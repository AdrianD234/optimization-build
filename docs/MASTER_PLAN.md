# capopt master plan

**Goal.** An open-source optimiser for the capital-programme scheduling MILP (`benchmark_pack/`). It should beat MindOpt on the same machine for time to within 1%, time to within 0.1%, time to the best-known objective and time to a proof of optimality. The only score that counts is the referee's (`benchmark_pack/reference/evaluate.py`) score of the returned schedule.

**Base engine.** HiGHS 1.15 (MIT licence), used through `highspy`, plus our own layer for decomposition, formulation, parallel search and heuristics. We do **not** fork the HiGHS C++ source on day one. Every improvement so far lives above the solver API, and pinning a released `highspy` keeps the build simple on Windows (no cmake or compiler needed). A C++ fork (`ERGO-Code/HiGHS` → our GitHub) becomes Phase 5 once we need internals the API does not expose: lazy constraints, custom branching, or sharing a cutoff in-tree. SCIP stays the fallback engine if we need branching or cut callbacks (research note: `docs/research/MILP_FRONTIER.md`, ideas 12 and 16).

## 1. Problem facts that shape the design

* **One decision**: the start year of each of 100 projects. The penalised objective is holding cost of the cash balance (linear plus convex piecewise bands, weights 1e3 to 1e6) minus 1e-4 × the PV of benefits.
* **The last build year L is the only non-convex coupling.** Funding stops after L, and holding cost is charged only before L. If L is fixed, the model has no activity binaries and no big-M rows: just start choices, a cumulative balance and a convex penalty. For any L, the model's value is at least the true objective of every schedule it contains, with equality when the schedule really ends in L. So **OPT = min over L of MIP(L)** (proof in `src/capopt/formulation.py`).
* **L >= Lmin = 18.** The balance must stay non-negative, so cumulative funding must cover the 382,962 units of total spend. On both penalised instances, the LP bounds of every L >= 19 are at least 66% worse than the best-known objective. All 42 LPs solve in about 3 s, so **the proof reduces to a single MIP at L = 18**. That model has 1.7k columns and 137 rows, against 6.3k columns and 18.3k rows for the reference model.
* **No-penalty instances**: holding cash is free, so L = T-1 alone is exact. This gives a pure PV knapsack with nested cumulative-budget rows.
* HiGHS' branch-and-bound is sequential. **Parallelism has to come from us**: split-and-solve and racing.

## 2. Architecture

```
src/capopt/
  instance.py      load the pack JSON into flat arrays; Lmin
  evaluate.py      exact scorer (bit-identical to the referee; parity-tested)
  formulation.py   MipBuilder; reference_bigm (HiGHS port of the MindOpt model = baseline);
                   last_year_model(L, encoding=pulse|step, balance=recursive|cumulative)
  solve.py         single HiGHS run with an incumbent trace (improving-solution callback, re-scored by the evaluator)
  parallel.py      split-and-solve: best-first LP ramp-up tree -> leaves -> worker processes with a shared incumbent
  heuristics/      numba kernel (exact objective, annealing, descent); constructions and LNS (Phase 3)
  solver.py        the pipeline: Lmin, the L=Lmin MIP in parallel, LP-bound pruning of every other L
  bench.py         benchmark runner: configurations x instances -> JSON traces -> BENCHMARK report vs MindOpt
  cli.py           `capopt solve|bench|report`
```

## 3. Phases, experiments and exit criteria

Each phase is a numbered experiment with a hypothesis, a fixed protocol and a recorded result in `benchmarks/results/`. A step is kept only if it improves the primary metric without regressing the others beyond run-to-run noise.

| Phase | Work | Exit criterion |
|---|---|---|
| 0. Foundations | Instance loader, exact evaluator, formulations, trace capture, test suite, CI | All tests green; evaluator parity with the referee on the best-known schedules and 1,000+ random schedules |
| 1. Baseline | **B0** = HiGHS on the faithful port of the MindOpt model (`reference_bigm`), 900 s limit, all 4 instances | Times recorded next to the MindOpt cold row |
| 2. Formulation ladder | **I1** fixed-L decomposition (pulse/recursive, 1 thread); **I2** step and cumulative encodings (exp01); **I3** LP-bound pruning of L != Lmin | I2 single-thread beats B0 on all four instances |
| 3. Parallel and primal | **I4** split-and-solve on 20 workers (exp02: leaves 60/120/240, encodings); **I5** racing portfolio for the primal side (seeds × encodings) plus injection of external solutions; **I6** LP-rounding and LNS warm start | Penalised instances proven faster than MindOpt cold |
| 4. Bound strengthening | Lifted cover / GUB-cover cuts on the cumulative rows, added as rows in an outer root loop; window aggregation; start-window tightening (MILP_FRONTIER ideas 2 and 4) | Fewer nodes at L = Lmin; raw_nopenalty proven |
| 5. Engine work | Fork HiGHS C++ if Phase 4 needs in-tree hooks; evaluate SCIP 10 as an alternative engine; Para-B&B-style deterministic parallel tree | Only if Phases 2-4 plateau |
| 6. Evolve-a-heuristic loop | LLM-in-the-loop evolution (FunSearch/AlphaEvolve/EoH pattern) of the construction priority rule, then the LNS neighbourhood selector, then cuts behind a validity gate. Fitness = primal integral on 30-50 perturbed instances, scored by the referee; the 4 benchmark instances held out (`docs/research/AI_MATH_RESULTS.md`) | Generalises to held-out perturbed instances |

### Research inputs

* `docs/research/MILP_FRONTIER.md`: 41 papers with 15 ranked, testable ideas, plus calibration (Mittelmann, Oct 2026: HiGHS about 5x slower than the best commercial solver on MIPLIB, so the gains must come from structure and parallelism).
* `docs/research/AI_MATH_RESULTS.md`: OpenAI and DeepMind AI-for-maths releases (IMO gold, Erdős results, the `openai/math` manuscripts, AlphaEvolve, FunSearch). The verdict is that no theorem in them speeds up this MILP. The transferable part is the *method*: an LLM proposes code and an exact scorer judges it. That becomes Phase 6.

## 4. Benchmark protocol

* **Instances**: the four in `benchmark_pack/instances/`, unchanged.
* **Comparator**: MindOpt 2.3 cold, from `benchmark_pack/results/benchmark_results.json`. It was measured on this same machine (Intel family 6 model 198, 20 threads, Windows 11 build 26300). MindOpt is not installed here, so it is not re-run. Same hardware and OS make the comparison like-for-like, but MindOpt's run-to-run variance (noted in the pack) still applies.
* **Metrics**: wall-clock seconds from process start to an incumbent within 1% / 0.1% / 1e-6 relative of best-known (the pack's definition), and to a proof (gap <= 1e-9 relative). Every incumbent in a trace is a schedule re-scored by our evaluator. The final schedule is also re-scored by the referee script itself.
* **Repeats**: three seeds per configuration for headline numbers; report median and range.
* **Outputs**: `benchmarks/results/<config>/<instance>.json` (trace, result, settings, machine) and `benchmarks/BENCHMARK.md` (generated).

## 5. Test suite (wired into CI)

| Layer | What it checks | Where |
|---|---|---|
| Parity | Our evaluator equals the referee exactly: best-known and reference schedules, 1,000 random schedules per instance, infeasible cases (negative balance, bad start) | `tests/test_evaluate.py` |
| Kernel | The numba objective equals the evaluator; annealing and descent never return infeasible or worse-than-start schedules | `tests/test_kernel.py` |
| Formulation consistency | For every model variant and instance, the best-known schedule's MIP vector satisfies all rows and its model objective equals the evaluator's (L = true last); schedule → vector → schedule round-trips | `tests/test_formulation.py` |
| Decomposition soundness | Model value >= true objective when last < L (property test on random schedules); LP bound <= best-known at L = Lmin; split bounds partition the option sets | `tests/test_decomposition.py` |
| Smoke | End to end in seconds: a small synthetic instance solved to proven optimality and checked against brute force; one benchmark instance solved for 20 s with a feasible, referee-scored result | `tests/test_smoke.py` |
| Regression (slow, `-m slow`) | Full pipeline proves both penalised instances optimal and matches best-known to 1e-9 relative | `tests/test_regression.py` |
| CLI and bench | `capopt solve` writes a schedule that the referee accepts; report generation from stored results | `tests/test_cli.py` |

CI (GitHub Actions): ruff lint, then the unit and smoke tests on Ubuntu and Windows, Python 3.11 and 3.13. The slow regression and benchmark runs stay on the 20-thread workstation (CI runners have 2-4 cores and noisy timing).

## 6. QA rubric (10 points; release = 10/10)

1. Every returned schedule is feasible and re-scored by the referee script.
2. Evaluator parity tests pass (bit-identical objective).
3. Every formulation variant is consistent with the evaluator on all instances.
4. The decomposition argument (min over L) is documented and property-tested.
5. Both penalised instances are proven optimal and match best-known.
6. Benchmark numbers are reproducible from one command, with stored traces.
7. Every improvement claim in `BENCHMARK.md` comes from a stored run, not from a description.
8. Lint is clean and CI is green.
9. No changes to the benchmark instances or referee (hash-checked in tests).
10. Docs cover how to run, how to add an experiment, and what is not yet done.

## 7. Workflow

* `main` is protected in spirit: work happens on `exp/<id>-<slug>` or `feat/<slug>` branches with PRs; CI must be green.
* New experiment: add `benchmarks/experiments/expNN_*.py`, record results under `benchmarks/results/exp/`, and summarise in `docs/EXPERIMENTS.md`.
* Never edit `benchmark_pack/instances` or `benchmark_pack/reference` (tests enforce this).

## 8. Risks

* **MindOpt variance**: the pack notes run-to-run variance (`growth_nopenalty` proved in 30 s once and 132 s another time). Our claims use medians over seeds and keep the margin visible.
* **Over-fitting to four instances**: Phase 6 must use perturbed instances. The decomposition and parallel layers are generic for this problem family.
* **Numerics**: penalty weights up to 1e6 next to objectives of about 1e10. Proofs use rel_gap 1e-9, and results are re-scored exactly.
* **HiGHS API limits**: no lazy or user-cut callbacks, so cuts are added as rows. Shared cutoffs between processes go through `objective_bound` plus an interrupt callback.
