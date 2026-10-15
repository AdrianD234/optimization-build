# AI-driven mathematics and algorithm discovery: what can help our MILP optimiser?

**Date:** 2026-10-15
**Scope:** The capital-programme scheduling MILP (100 projects, choose start years, cumulative cash balance >= 0, convex piecewise-linear holding-cash penalty minus PV of benefits). Engine: HiGHS plus our own heuristics (LNS, last-build-year decomposition, rounding, local search). Goal: shorter solve times.

## Method

- Web searches and page fetches run on 2026-10-15. Primary sources (arXiv, DeepMind blog, GitHub READMEs) were fetched where possible.
- openai.com returned HTTP 403 to the fetcher and nature.com redirected to a login page. Claims about OpenAI blog posts and the Nature news piece therefore rest on search-result summaries and secondary coverage. They are marked **(secondary)**.
- Anything that could not be checked against a primary source is marked **unverified**.
- Relevance score (1-5) means how directly the item could shorten solve times for *our* MILP: 1 = none beyond general interest, 5 = could be adopted as-is or nearly so.

## Headline judgement

None of the theorem-proving results (IMO gold, Erdős problems, OpenAI's 2026 manuscript releases, AlphaProof, FrontierMath) will make our MILP solve faster. They prove statements in pure mathematics or theoretical CS, and none is about LP/MIP algorithms or scheduling. The parts that do transfer are:

1. **The method:** an LLM proposes code, an exact evaluator scores it, and evolutionary selection keeps the best (FunSearch, AlphaEvolve, EoH, ReEvo, OpenEvolve, ShinkaEvolve). The evolved artefact is ordinary readable code that can be reviewed and kept.
2. **MILP-specific uses of that method:** LLM-evolved LNS neighbourhood selectors (LLM-LNS, ICML 2025), primal heuristics (DHEvo), and model-level cuts (EvoCut). EvoCut also tested whether its cuts transfer to HiGHS.

## Results table

| # | Source | Date | What was shown | Type | Artefact | Relevance (1-5) |
|---|---|---|---|---|---|---|
| 1 | OpenAI experimental reasoning model, IMO 2025 | Jul 2025 | Solved P1-P5 (35/42) under exam conditions in natural language. Graded by former medallists, not by the IMO. | Theorem proving (competition) | Proofs: https://github.com/aw31/openai-imo-2025-proofs (outputs only, no model or code) | 1 |
| 2 | Google DeepMind Gemini Deep Think, IMO 2025 | Jul 2025 | 35/42, officially certified by the IMO, end-to-end natural language | Theorem proving (competition) | Blog: https://deepmind.google/discover/blog/advanced-version-of-gemini-with-deep-think-officially-achieves-gold-medal-standard-at-the-international-mathematical-olympiad | 1 |
| 3 | GPT-5 Pro "Erdős problems" claims (Bubeck, Sellke, Weil) | Oct 2025 | Problems listed as "open" (e.g. #339) were found to be already solved in the literature. This was literature search, not new proofs, and the claim was walked back. | Literature search | Coverage: https://www.eweek.com/news/openai-math-hype/ (secondary) | 1 |
| 4 | Bubeck et al., "Early science acceleration experiments with GPT-5" | Nov 2025 | Case studies, including GPT-5 Pro improving a sufficient step size for convexity of the GD objective sequence from 1/L to 1.5/L (humans reached 1.75/L). The report says experts could have done this in hours to days. | Theorem (convex optimisation) | https://arxiv.org/abs/2511.16072 | 1 |
| 5 | Jang & Ryu, "Point convergence of Nesterov's accelerated gradient method: an AI-assisted proof" | Oct 2025 (v2 Jan 2026) | Proves iterate convergence of NAG, with heavy ChatGPT assistance. OpenAI attributes it to GPT-5 (secondary); the paper says "ChatGPT". | Theorem (first-order optimisation) | https://arxiv.org/abs/2510.23513 ; OpenAI post https://openai.com/index/gpt-5-mathematical-discovery/ (403, secondary) | 1 (first-order LP methods such as PDLP at most; irrelevant at our size) |
| 6 | GPT-5.2 Pro + Harmonic Aristotle, Erdős #728 | Jan 2026 | Close to autonomous proof of a factorial-divisibility problem, formalised in Lean. Tao: "lowest-hanging fruit", about 1-2% of open problems are this accessible. | Theorem | https://arxiv.org/abs/2601.07421 | 1 |
| 7 | Epoch AI, FrontierMath Tier 4 | Jan 2026 | GPT-5.2 Pro scored 31% (manual run), against a previous best of 19%. No later 2026 OpenAI FrontierMath figure was checked. | Benchmark | https://epochai.substack.com/p/new-record-on-frontiermath-tier-4 | 1 |
| 8 | OpenAI internal "Astra" model, "ten proofs" | Aug 2026 | Ten results in maths and theoretical CS: sphere packing and code bounds, a non-sofic group, a Connes rigidity counterexample, permanent lower bounds, quantum parallel repetition, GapCVP hardness, the Ehrhart volume bound, Erdős #183/#146/#180. Not peer reviewed. | Theorems / constructions | Lean: https://github.com/openai/ten-proofs (verified exists) ; PDF https://cdn.openai.com/pdf/ten-proofs-oai.pdf (not fetched) ; coverage https://siliconangle.com/2026/08/02/openais-astra-solves-10-long-open-math-problems-publishes-proofs/ | 1 |
| 9 | OpenAI `openai/math` release | 6 Oct 2026 | 722 manuscripts in 372 families from an unreleased model, given about 4,000 problems. About 42% of top-line results are formalised in Lean (162/722 papers per secondary sources). Three were withdrawn after a sign error and 14 revised; the catalogue now holds 719. The only optimisation-adjacent item found is family 102, on UGC/SDP approximation thresholds (hardness, not an algorithm). | Theorems (mostly unrefereed) | https://github.com/openai/math (verified exists) ; https://openai.com/index/sharing-ai-progress-in-mathematics/ (403) ; coverage https://cellcog.ai/blog/openai-math-results/ and https://www.washingtonpost.com/technology/2026/10/07/openai-releases-progress-more-than-300-math-research-problems/ (secondary) | 1 |
| 10 | OpenAI Navier-Stokes claim | Sep 2026 | Reported in search summaries only. | Theorem claim | **Unverified**; no primary source fetched | 1 |
| 11 | AlphaProof (Hubert et al., Nature) | Nov 2025 (system announced Jul 2024) | RL agent that finds Lean proofs, with test-time RL. IMO 2024 silver level together with AlphaGeometry 2. | Formal theorem proving | https://research.google/pubs/olympiad-level-formal-mathematical-reasoning-with-reinforcement-learning/ (DOI 10.1038/s41586-025-09833-y) | 1 |
| 12 | AlphaGeometry (Trinh et al., Nature 625) | Jan 2024 | Neuro-symbolic geometry prover; 25/30 olympiad problems | Theorem proving | https://deepmind.google/discover/blog/alphageometry-an-olympiad-level-ai-system-for-geometry/ | 1 |
| 13 | FunSearch (Romera-Paredes et al., Nature) | Dec 2023 | LLM plus evolutionary search over a single Python *priority function*. New cap-set constructions, and online bin-packing heuristics that beat first-fit and best-fit. | Construction plus heuristic algorithm | https://github.com/google-deepmind/funsearch (evolved heuristics and evaluation notebooks; no full search engine) ; DOI 10.1038/s41586-023-06924-6 | 4 (the "evolve a priority function" pattern maps directly onto our construction heuristic) |
| 14 | AlphaEvolve white paper (Novikov et al.) | May/Jun 2025 | Evolves whole code files with Gemini and a user-defined evaluator. Results: 4x4 complex matmul with 48 multiplications; a Borg scheduling heuristic in production for more than a year that recovers 0.7% of fleet compute; 23% kernel speed-up; 11-D kissing number 593; about 20% of 50+ open problems improved. | Algorithms plus constructions | https://arxiv.org/abs/2506.13131 ; https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/ ; matmul results repo github.com/google-deepmind/alphaevolve_results (README not fetched) | 4 (method; the Borg result is a scheduling heuristic evolved against a simulator) |
| 15 | Georgiev, Gómez-Serrano, Tao, Wagner, "Mathematical exploration and discovery at scale" | Nov 2025 | AlphaEvolve on 67 problems. Matched most best-known results and improved several. The authors say some gains could likely have been matched by conventional methods. | Constructions | https://arxiv.org/abs/2511.02864 ; prompts, evaluators and evolved programs: https://github.com/google-deepmind/alphaevolve_repository_of_problems | 2 (good worked examples of evaluator and prompt design) |
| 16 | AlphaEvolve on Google Cloud (GA) | Jul 2026 | Commercial service: you supply a baseline program and a scorer. | Tool | https://blog.google/innovation-and-ai/infrastructure-and-cloud/google-cloud/alphaevolve-on-cloud/ | 3 (usable, but paid and cloud-hosted; open-source equivalents exist) |
| 17 | OpenEvolve (open-source AlphaEvolve re-implementation) | 2025 | MAP-Elites plus island model plus LLM ensemble; any OpenAI-compatible API | Tool | https://github.com/codelion/openevolve | 5 (drop-in engine for our loop) |
| 18 | ShinkaEvolve (Sakana AI; Lange, Imajuku, Cetin) | Sep 2025 | Sample-efficient code evolution, e.g. a new best circle packing in about 150 samples | Tool | https://github.com/SakanaAI/ShinkaEvolve ; https://arxiv.org/abs/2509.19349 | 5 (sample efficiency matters because our evaluations cost minutes) |
| 19 | EoH, Evolution of Heuristics (Liu et al., ICML 2024) | Jan 2024 | Co-evolves natural-language "thoughts" and code. Beats FunSearch on bin packing with far fewer LLM queries. | Heuristic algorithms | https://arxiv.org/abs/2401.02051 ; platform https://github.com/Optima-CityU/LLM4AD (https://arxiv.org/abs/2412.17287) | 4 |
| 20 | ReEvo (Ye et al., NeurIPS 2024) | Feb 2024 | Reflective evolution: the LLM writes "verbal gradients" from comparisons of pairs of heuristics. Six combinatorial optimisation problems. | Heuristic algorithms | https://arxiv.org/abs/2402.01145 | 4 |
| 21 | LLM-LNS (Ye, Xu, Yan, Cheng, ICML 2025) | 2025 | Two-layer self-evolving LLM agent that evolves LNS *neighbourhood selection* for large MILPs. Trained on small instances; reported to beat ACP, CL-LNS, Gurobi and SCIP, and to beat FunSearch and EoH. Author-reported results. | MILP heuristic | https://proceedings.mlr.press/v267/ye25j.html (official code repo not found) | 5 (same component as our LNS) |
| 22 | DHEvo (Zhang et al.) | Jul 2025 | Co-evolves training-instance selection and primal-heuristic code, so heuristics generalise within a problem class | MILP primal heuristic | https://arxiv.org/abs/2507.15615 (code not found) | 3 (its instance-selection idea addresses our main risk, over-fitting to four instances) |
| 23 | EvoCut (Yazdani, Mostajabdaveh, Aref, Zhou) | Aug 2025 (v2 Feb 2026) | LLM agents evolve model-level *acceleration cuts* (extra constraints in the formulation). Screens: the reference optimum stays feasible, and the cut separates a stored LP optimum. Up to 76% smaller gaps and up to 7.2x faster to target gap (Gurobi). Includes JSSP and unit commitment, and tested transfer to **HiGHS**. Validity is empirical only, not proved. | Model strengthening (cuts) | https://arxiv.org/abs/2508.11850 ; code https://github.com/milad1378yz/EvoCut | 5 (the only item aimed at *proof* time, which dominates on our penalised instances) |
| 24 | Lawless et al., "LLMs for cold-start cutting-plane separator configuration" | Dec 2024 (v2 Sep 2025) | LLM picks separator settings from a text description, with ensembling; matches data-hungry tuners | Solver configuration | https://arxiv.org/abs/2412.12038 | 2 (HiGHS exposes far fewer separator switches than SCIP/Gurobi; plain option search is likely as good) |
| 25 | Zheng, Wang, Shao, "LLM-guided cutting-plane management for MILP" (IJCAI 2026) | 2026 | Instance-specific separator configuration plus stage-aware cut selection, in SCIP | Solver configuration | https://www.ijcai.org/proceedings/2026/736 | 2 (needs solver cut callbacks we lack in HiGHS) |
| 26 | MILP-Evo (Nie et al.) | May 2026 | Closed-loop LLM evolution of SCIP cut-selector and branching-rule code, scored by real SCIP runs | Solver components | https://www.alphaxiv.org/abs/2607.18252 (no code found; no figures checked) | 3 for the method; 1-2 for HiGHS (no branching or cut-selection plug-in) |
| 27 | BiFE (Zhang et al.) | Sep 2026 | LLM-generated CPU-only branching rules. A cheap imitation score pre-screens candidates; full B&B evaluation is reserved for elites. | Branching rule | https://www.alphaxiv.org/abs/2609.36735 (no code found) | 2 (branching not pluggable in HiGHS), but **bi-fidelity evaluation** is worth copying |
| 28 | G-LNS | Feb 2026 | LLM designs destroy/repair operators for LNS | Heuristic algorithms | https://arxiv.org/abs/2602.08253 | 3 |
| 29 | OptiMUS / OptiMUS-0.3 (AhmadiTeshnizi, Gao, Udell) | 2023-2024 | LLM agent that turns natural-language problems into MILP code | Modelling | https://arxiv.org/abs/2310.06116 ; https://arxiv.org/abs/2407.19633 ; https://github.com/teshnizi/OptiMUS | 1 (we already have the model) |
| 30 | CO-Bench (AAAI 2026) and HeuriGym (NeurIPS 2025) | 2025-2026 | Benchmarks of LLM agents designing combinatorial optimisation algorithms. HeuriGym: top models reach a quality-yield index of about 0.6 against an expert baseline of 1. | Evaluation | https://arxiv.org/abs/2504.04310 ; https://arxiv.org/abs/2506.07972 | 2 (a sobering calibration: LLM heuristics often trail expert ones) |

## What we can actually use

### Be honest about what does not transfer

- **The headline OpenAI results don't transfer.** That covers IMO gold, the Erdős work, the Astra ten proofs and the 722-manuscript `openai/math` release. They are theorems in pure maths and theoretical CS. None is a new LP/MIP algorithm, bound or scheduling result that HiGHS could use. The only optimisation-flavoured items are the step-size result (#4) and the Nesterov result (#5), which concern first-order convex methods and do not matter for a 100-project MILP solved by simplex-based branch and bound. Family 102 in `openai/math` is a hardness result (approximation thresholds), not an algorithm.
- **The DeepMind theorem provers don't transfer either.** AlphaProof, AlphaGeometry and Gemini Deep Think likewise give us nothing to run.
- **One thing does carry over in principle.** A frontier LLM can now produce correct short proofs in a narrow area. We could use one to *propose and then prove* that a candidate valid inequality for our formulation is valid. We would still check every such proof by hand or by brute force on small instances.

### What does transfer

1. **The evolve-a-heuristic method:** FunSearch, AlphaEvolve, EoH, ReEvo, OpenEvolve and ShinkaEvolve. Each works only when there is a cheap, exact, un-gameable scorer, and `reference/evaluate.py` is exactly that.
2. **Evolving the LNS neighbourhood selector** (LLM-LNS, ICML 2025). This is the same component we already have, and its authors report that it beats hand-designed and ML-learned LNS.
3. **Evolving model-level cuts** (EvoCut). Our penalised instances need minutes of *proof* time even with a commercial solver. Primal heuristics cannot shorten proof time; a tighter formulation can. EvoCut reports cuts that carried over to HiGHS. Its validity check is only empirical, so we would add a formal proof step (see below).
4. **Bi-fidelity evaluation** (BiFE) and **co-evolving the training instances** (DHEvo). These keep evaluation cost down and limit over-fitting to our four instances.

### Constraints specific to HiGHS

- HiGHS has no user branching rule or cut-selection plug-in comparable to SCIP's. MILP-Evo and BiFE-style branching evolution and LLM cut management are therefore out of scope unless we switch solvers.
- What we can change is everything *around* HiGHS: the formulation (extra constraints and cuts), the warm start we pass in, the sub-MIPs our LNS builds, the decomposition, and HiGHS options.
- **Unverified:** the exact HiGHS callback set (MIP-solution and interrupt callbacks, user-solution injection) depends on the version. Check the installed `highspy` version before designing anything that needs callbacks.

## Proposed design: a small "evolve-a-heuristic" loop

### Targets, in order of expected payoff for effort

| Target | Evolved code (fixed signature) | What it improves | Risk |
|---|---|---|---|
| **A. Construction priority rule** (FunSearch style) | `def priority(p: Project, state: CashState, inst: Instance) -> float`, called by a fixed greedy that places projects in descending priority at the earliest (or best) cash-feasible start year | Warm-start quality, so time to 1% | Low: always feasible because the greedy enforces cash >= 0 |
| **B. LNS neighbourhood selector** (LLM-LNS style) | `def select_neighbourhood(sol: Schedule, inst: Instance, history: LnsHistory, rng: Random, k: int) -> set[str]` returns the project ids to free; HiGHS re-optimises them with the rest fixed | Time to 0.1% and to best-known | Low: the sub-MIP keeps feasibility |
| **C. Model-level valid inequalities** (EvoCut style) | `def extra_constraints(inst: Instance, m: ModelHandles) -> list[Constraint]`, plus a short written validity argument | **Proof time** (dual bound) | Medium/high: an invalid cut silently loses the optimum. Guardrails below are mandatory. |
| D. HiGHS option set | A dict of options | Everything a little | Low. **Do this with plain random or grid search first, without an LLM**; it is the baseline the LLM loop must beat. |

Start with A and B together (one loop each, sharing the harness), then C. D runs once up front as a baseline.

### Fitness function

The scorer is the existing exact referee (`benchmark_pack/reference/evaluate.py` / `src/capopt/evaluate.py`). The loop must never modify it.

- **Targets A and B: a primal integral over a fixed wall-clock budget T** (e.g. 60 s for B, 5 s for A).
  - The primal integral is the integral of `gap(t) = (obj(t) - best_known) / |best_known|` over [0, T], with gap = 1 before the first feasible solution.
  - Average it across training instances using a shifted geometric mean.
  - It rewards both reaching good solutions fast and ending low, and it is smoother than "time to 0.1%".
- **Secondary terms:** final gap at T (tie-break), and a small penalty on code length or cyclomatic complexity.
- **Hard reject (fitness = +inf):** infeasible schedule per the referee, exception, timeout, non-determinism under a fixed seed, or forbidden imports or I/O.
- **Target C:**
  - Compare root dual bound and time to proven optimality on the training instances *with and without* the cuts, using the same HiGHS options and threads.
  - Fitness is the shifted geometric mean of the time ratio.
  - Any cut that fails the validity gate gets +inf.
- **Bi-fidelity scoring (from BiFE):**
  - Low fidelity: 1-3 small instances at a short budget, with one seed.
  - Only the top ~20% of candidates get high fidelity: the full training set, 3 seeds, the full budget.
  - This cuts evaluation cost several-fold.

### Instances: avoid over-fitting the four benchmark instances

- **Training set:** 30-50 perturbed instances generated from the four originals.
  - Jitter costs, durations, benefit-to-cost ratios and growth rates; rescale the cash inflows; vary project counts (60-140).
  - Keep the same penalty structure, and include penalised and no-penalty variants.
  - Solve each to optimality once offline (HiGHS with a long time limit) to get its `best_known`.
- **Validation set:** 10 further perturbed instances, used for selection between generations (DHEvo-style refreshing optional).
- **Test set:** the four `benchmark_pack/instances/*.json`, never seen during evolution. Scored only once at the end.

### Guardrails

1. **Sandbox.**
   - Each candidate runs in a subprocess with a CPU-time and wall-clock limit, a memory cap, and no network.
   - No file access outside a temp directory. In particular, block `benchmark_pack/solutions/`, so a candidate cannot read best-known schedules.
   - An AST-checked import whitelist: `math`, `random`, `heapq`, `itertools`, `collections`, `numpy`, and our `capopt` read-only API.
2. **Referee isolation.**
   - The referee runs in the parent process on the schedule the candidate returns. A candidate's own claimed objective is ignored.
   - Hash `evaluate.py` and check the hash on every run.
3. **Interface contract.** The function signature is fixed and the surrounding code is frozen. The LLM edits only the marked block (the AlphaEvolve/OpenEvolve `EVOLVE-BLOCK` convention).
4. **Determinism.**
   - Fixed seeds and a fixed HiGHS thread count during evaluation.
   - A candidate whose score varies by more than a tolerance across two identical runs is rejected.
5. **Anti-gaming.**
   - Never use the four test instances in the loop.
   - Flag candidates whose code contains large numeric constants or project-ID literals, since that suggests hard-coding.
6. **Cut validity (target C only).** A cut must pass all of these:
   - (a) every best-known and reference schedule, plus every solution in the run's archive, still satisfies it;
   - (b) on 20+ small generated instances (10-15 projects), brute-force or exact HiGHS solves give the same optimum with and without the cut;
   - (c) the LLM supplies a short written proof, and a human reviews it before the cut enters the production model.
   EvoCut itself states that its screening "does not provide a formal guarantee".
7. **Human merge gate.** Evolved code goes into `src/capopt/heuristics/evolved/` only after review, with the generation, score and prompt recorded in a header comment.
8. **Budget caps.** Set a maximum number of LLM calls per run (e.g. 300-500), a maximum wall-clock per run (e.g. overnight), and early stopping if validation fitness has not improved for N generations.

### Plugging it into the benchmark harness

```
benchmarks/
  evolve/
    run_evolve.py        # wraps OpenEvolve or ShinkaEvolve; config: target, budget, model, islands
    targets/
      priority_rule.py   # seed program with EVOLVE-BLOCK around priority()
      lns_selector.py    # seed program with EVOLVE-BLOCK around select_neighbourhood()
      cuts.py            # seed program with EVOLVE-BLOCK around extra_constraints()
    evaluator.py         # sandboxed runner -> referee -> primal integral -> JSON fitness
    instances/gen.py     # perturbed train/val instance generator + offline optimum solves
    archive/             # every candidate: code, fitness per instance, seeds, timings
```

- `evaluator.py` exposes `evaluate(program_path) -> {"combined_score": ..., "per_instance": ..., "feasible": bool}`. That is the evaluator contract OpenEvolve and ShinkaEvolve expect.
- Inside the evaluator, the candidate is dropped into the *same* optimiser entry point the benchmark uses (`capopt` pipeline: construction, then LNS, then HiGHS), so the measured gain is the gain that will ship.
- **Seed programs:** our current hand-written priority rule and LNS selector. Evolution must beat them, which gives an honest baseline.
- **Final step:** run the existing benchmark runner on the four test instances with the best evolved component switched in. Report time to 1%, 0.1%, best-known and proof against the current pipeline and the MindOpt reference times in `benchmark_pack/results/BENCHMARK.md`.

### Rough cost and expectations

- About 300 LLM calls at a few thousand tokens each is cheap. Evaluation time dominates. At ~60 s per high-fidelity evaluation across 3 seeds and 30 instances, BiFE-style pre-screening is needed, or the low-fidelity evaluation must run on 3-5 instances.
- **Realistic expectations.**
  - FunSearch, EoH and ReEvo report solid but incremental gains over hand-designed baselines.
  - HeuriGym shows LLM-written heuristics often trail expert ones.
  - Because our instances are small and the penalised ones are proof-bound, targets A and B will mostly improve time to 1% and 0.1%. Only target C (or better decomposition bounds) can cut time to proof.
  - Treat this as a side experiment that runs overnight, not a replacement for formulation work.

## Unverified or not checked

- The OpenAI blog pages (https://openai.com/index/sharing-ai-progress-in-mathematics/ and https://openai.com/index/gpt-5-mathematical-discovery/) returned 403. Their content is reported from secondary coverage.
- The OpenAI Navier-Stokes claim (September 2026) appears only in search summaries; no primary source was fetched.
- Exact Lean-coverage numbers for `openai/math` differ between sources (162/722 papers, about 42% of top-line results, 235/372 results per a third-party index).
- No 2026 OpenAI FrontierMath figure beyond the January 2026 Epoch result was checked.
- MILP-Evo, BiFE, DHEvo and LLM-LNS: no code repositories were found and the quantitative claims were not checked; all are author-reported.
- The README of `google-deepmind/alphaevolve_results` was not fetched; its contents come from secondary descriptions.
