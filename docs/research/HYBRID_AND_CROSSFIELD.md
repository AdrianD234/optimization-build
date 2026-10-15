# MindOpt + HiGHS hybrids and techniques from other fields

**Date:** 2026-10-15
**Scope:**
- **Question 1.** What can usefully pass between MindOpt (commercial, closed source) and HiGHS/capopt, and how should MindOpt be benchmarked fairly under a one-seat floating licence?
- **Question 2.** Which techniques from other fields, or from the research frontier, fit this exact structure, judged above all on **primal** quality (our main weakness on the real CPO data)?
- **Question 3.** A ranked shortlist of experiments for the backlog in [ITERATION_LOOP.md](../ITERATION_LOOP.md) (H10-H16).

**The structure:**
- time-indexed start-year selection for 233 projects;
- a cumulative cash balance >= 0 every year (no borrowing);
- convex tiered holding-cash penalties;
- at most 100 starts per year, which binds in the first year;
- PV maximisation.

capopt solves it as a fixed-last-year MIP (step encoding) by parallel split-and-solve with HiGHS 1.15.1 (see [MASTER_PLAN.md](../MASTER_PLAN.md)).

## Method and citation rules

- Web searches and page fetches were done on 2026-10-15 (arXiv, Optimization Online, INFORMS, Springer/MPC, Dagstuhl LIPIcs, the HiGHS docs and releases, the MindOpt docs at opt.aliyun.com, Mittelmann's pages).
- Every citation is a URL that was fetched or returned by a search in this session.
  - **(abstract)** means only the abstract or a summary was seen.
  - **unverified** means the claim could not be confirmed from a primary source.
  - **(own reasoning)** marks my own derivations.
- **Local evidence** comes from three sources:
  1. The installed `highspy` 1.15.1 API, by listing names and reading options.
  2. Tiny single-threaded LP diagnostics on one real CPO scenario (s2000_P50high, each under 1 s). No solver benchmarks or multi-process runs were made; another session is timing on this machine.
  3. The CPO repository's earlier MindOpt R&D campaign (`legacy/ml_testing`, MindOpt 2.3.0 on this machine; read-only). Only methods and aggregate facts are quoted.
- CPO data stays private. Every real-data number below is a ratio or a count, never an objective value or a cash figure.
- This complements [MILP_FRONTIER.md](MILP_FRONTIER.md), which focused on the **dual** side (the proof) on the 100-project pack. On the 233-project CPO data the bottleneck has moved to the primal side. Overlapping papers are cited again only where the verdict changes.

## Headline

1. **Relaxation information can be exchanged, but it isn't the valuable part.** Both solvers expose LP solutions, duals, reduced costs and bases through public APIs.
   - HiGHS computes the same LP information itself in 0.14 s, so importing it from MindOpt buys nothing.
   - What MindOpt keeps private is its internal search state: cut pool, presolve reductions, tree, pseudo-costs.
   - What is worth passing is:
     1. **schedules** (MIP starts, incumbents), both ways;
     2. **objective cutoffs**;
     3. MindOpt's **dual bound** as an *advisory* number;
     4. possibly MindOpt's **cut-strengthened root relaxation** (`cbGetNodeRel`) as a heuristic guide.

     Exchange at the **schedule level** (start-year vectors re-scored by our evaluator), not through MPS/variable vectors. The CPO team's own attempt at external racing failed because exported models didn't round-trip (objective constants, name mangling).
2. **Read the MindOpt licence before building any MindOpt→capopt channel.**
   - The public MindOpt Community License Agreement says you *"can not use the Materials or any output therefrom to improve any other optimization solving model"*.
   - It also has a DeWitt-style clause on publishing benchmarks.
   - Whether the user's paid floating licence falls under these terms is **unverified**. Treat MindOpt→capopt data flows, and any public MindOpt numbers, as blocked until the vendor or contract says otherwise. The HiGHS→MindOpt direction (warm-starting MindOpt) and private benchmarking are much less exposed.
3. **The real data has a specific primal pathology.** On s2000_P50high:
   - the cash constraint has only about 0.8% slack over the whole horizon;
   - the LP vertex is almost integral (13 of 233 projects fractional), yet about 97 projects that are *integral* in the LP sit at a different year in the reference optimum;
   - capopt's 0.07%-off incumbent differs from the reference in 128 of 233 projects, 57 of them by 10 or more years.

   Near-optimal schedules are far apart in Hamming distance. Small "free k random projects" neighbourhoods and LP-agreement fixing (RINS/RENS) are therefore structurally weak here. What should help:
   - **cheap, massive local search** on the start-year vector with cash-aware moves;
   - **LP-ordered list scheduling with cash repair** (mining's TopoSort, NPV project scheduling);
   - **structure-aware LNS** (time windows, cash-crunch years, the year-0/1 cap) with a bandit and solution-pool crossover.
4. **The dual side, briefly.**
   - Relaxing the cash, band and cap rows (Lagrangian, Bienstock-Zuckerberg, per-project column generation) gives exactly the LP bound, because one-start-per-project has the integrality property.
   - Reduced-cost fixing removes only about 1.5% of columns at the root gap.
   - The only above-LP routes are cuts (already in H5) and a year-block Dantzig-Wolfe, which is a one-off bound probe (H16).

## 0. What the real data says (local diagnostics, s2000_P50high)

| Fact | Value | Consequence |
|---|---|---|
| Size | 233 projects, T = 60, 13,266 (project, start) pairs, durations 1-23 years (median 3), every project may start in year 0 | |
| Cash slack | Total funding exceeds total spend by only **0.8%** over the horizon | A tight packing. Almost any perturbation breaks some year's balance, which is why LP rounding is cash-infeasible. |
| Last year | Lmin = 30 = the reference schedule's last year | As on the pack, one fixed-L model decides the instance |
| Root LP (L = 30, pulse/cumulative) | 6,599 cols × 325 rows, **0.14 s** dual simplex; gap to the CPO objective **0.47%** | LP speed is not an issue. First-order or GPU LP is irrelevant (the CPO campaign also rejected PDLP for this reason). |
| Fractional projects at the LP vertex | **13 of 233** (spread up to 20 years, median 4) | At most one fractional project per binding linking row (own reasoning; both research threads derived the same bound) |
| LP-integral projects at the reference year | 123 of 220 | LP agreement is a weak predictor. RINS/RENS-style fixing would pin about 40% of projects wrongly. |
| Reduced-cost fixing at the reference gap | 98 of 6,509 pulse columns (**1.5%**) | H6 is weak at the root; it needs a tighter bound or a near-optimal incumbent plus node-level duals |
| Binding rows | 254 of 325 rows have non-zero duals | Cash and band rows are tight almost everywhere |
| Reference schedule | 100 starts in year 0 (cap binds), 47 in year 1, the rest spread over years 2-28 | Year 0/1 is the combinatorial core: which 100 go first |
| capopt incumbent at 200 s (0.069% off) vs reference | differs in **128 of 233** projects, 57 by ≥ 10 years | A flat landscape with distant near-optima. Neighbourhoods must be large and structured, and recombination of distant incumbents matters. |
| Symmetry | 227 distinct (spend, PV) projects of 233 | Symmetry handling is not worth doing |

## 1. MindOpt ↔ HiGHS/capopt: what can pass

### 1.1 The two APIs

**MindOpt 2.3.0 (`mindoptpy`).** Docs: [Model](https://opt.aliyun.com/doc/latest/en/html/API2/py/Model.html), [attributes](https://opt.aliyun.com/doc/latest/en/html/API2/attr/index.html), [parameters](https://opt.aliyun.com/doc/latest/en/html/API2/param/index.html), [callbacks](https://opt.aliyun.com/doc/latest/en/html/tools/callback.html), [MIP warm start](https://opt.aliyun.com/doc/latest/en/html/model/milp/milp-warmstart.html), [solution and basis files](https://opt.aliyun.com/doc/latest/en/html/io/sol_file.html), [model files](https://opt.aliyun.com/doc/latest/en/html/io/model_file.html), [changelog](https://opt.aliyun.com/doc/latest/en/html/overview/changelog.html). The CPO campaign confirmed several items on this machine (marked **CPO-verified**).

- **Attributes.** MindOpt's own names, not Gurobi's:
  - model: `ObjVal`, `DualObjVal`, `MIP/GapAbs`, `MIP/GapRel`, `Status`, `SolCount`, `SolutionTime`;
  - variable: `X`, `RC` (reduced cost), `Start`, `ColBasis` (settable), `Xn` with `MIP/SolutionNumber` (solution pool);
  - constraint: `DualSoln`, `Activity`, `RowBasis` (settable).
  - **There is no documented `ObjBound`/`NodeCnt` attribute.** After a MIP solve, `DualObjVal` is unverified as the best bound. The documented routes to the bound are `ObjVal` ± `MIP/GapAbs`, or `cbGet(MIP_OBJBND)` inside a callback.
- **MIP start.**
  - `Var.Start`, or `read("x.mst")`, which holds integers only. Partial starts are allowed, but it is "a reference" that "does not necessarily take effect". The log says "accept new sol" or "initial solution is not accepted".
  - Only one start vector is documented. `Start` is CPO-verified.
  - The CPO campaign also found that Start plus `MIP/MaxSols=1` can "drift under repair". To evaluate a given schedule exactly, fix its bounds instead.
- **LP warm start.**
  - `ColBasis`/`RowBasis` (settable) and `.bas` read/write. MindOpt's `.bas` codes (FR/BS/UU/UL/SB/EQ/UK) are MindOpt-specific, so map arrays in memory rather than exchanging files.
  - `Method` selects the LP algorithm (concurrent by default; PDHG = 3).
- **Callbacks** (since 1.0.0, MIP only; `optimize(callback)`).
  - `where`: MIPSTART = 3, MIPSOL = 4, MIPNODE = 5.
  - `cbGet` codes: MIP_OBJBST 300, MIP_OBJBND 301, MIP_RELVAL 302, MIP_NODCNT 306, MIP_SOLCNT 307, MIP_CUTCNT 308, MIP_SOL 316, MIP_REL 317.
  - Methods: `cbGetSolution`, `cbGetNodeRel`, `cbSetSolution` + `cbUseSolution`, `cbCut`, `cbBranch`, `terminate()`.
  - Lazy constraints: the `MIP/EnableLazyConstr` parameter since 2.0.0; there is no `cbLazy`.
  - **CPO-verified (A74):** the callback fired at MIPSTART and MIPSOL and read OBJBST, OBJBND, SOLCNT and `cbGetSolution`.
  - **Not proven:** termination from inside a callback, and injection (`cbSetSolution`).
  - **Caveat:** in Python "the model passed to the callback is a presolved model". Whether `cbSetSolution`/`cbCut`/`cbGetNodeRel` use presolved or original indices is undocumented.
- **Presolve, cuts, tree.** Nothing is exportable: no presolved-model writer, no cut pool, no node bounds. `cbCut` only adds cuts.
- **Parameters.**
  - CPO-verified: `MaxTime`, `NumThreads`, `MIP/GapRel` (default 1e-4), `MIP/GapAbs`, `MIP/Cutoff` (negate it for a maximisation), `MIP/MaxSols`, `MIP/MaxNodes`, `MIP/MaxStallingNodes`, `MIP/SolutionPoolSize` (default 1), `MIP/AutoConfiguration`, `MIP/LevelStrategy`.
  - From the docs: `MIP/RootParallelism`, `MIP/Static` (determinism), `MIP/Heuristics` (default 0.05) and the `MIP/Level*` effort levels.
  - **A seed parameter was not accepted** (CPO A73). There is no tuning tool.
- **Files.** Reads MPS/LP/`.mst`/`.bas`/`.prm`; writes MPS/LP/`.sol`/`.bas`/`.mst`.

**HiGHS 1.15.1 (`highspy`).** All names below were checked locally.

- **Solutions, bases and presolve:**
  - `setSolution` (full or sparse; a partial integer assignment is completed by a sub-MIP, capped by `mip_max_start_nodes`; [guide](https://ergo-code.github.io/HiGHS/dev/guide/further/));
  - `setBasis`/`getBasis`/`readBasis`/`writeBasis` (no presolve when a basis is given);
  - `getRanging`, `presolve`/`getPresolvedLp`/`writePresolvedModel`;
  - `mip_improving_solution_save` + `getSavedMipSolutions` (a solution pool).
- **Callbacks** ([docs](https://ergo-code.github.io/HiGHS/dev/callbacks/)):
  - types: `kCallbackMipSolution`, `kCallbackMipImprovingSolution`, `kCallbackMipInterrupt`, `kCallbackMipGetCutPool`, `kCallbackMipUserSolution` (inject incumbents; capopt already uses it in `solve.py`), and, **new in 1.15**, `kCallbackMipDefineLazyConstraints`;
  - `data_out` fields include `mip_dual_bound`, `mip_primal_bound` and `cutpool_*`.
- **Cutoff options:** `objective_bound` (also used as the MIP's `upper_limit`; [source](https://raw.githubusercontent.com/ERGO-Code/HiGHS/master/highs/mip/HighsMipSolverData.cpp)) and `objective_target`.
- **Not in 1.15.1:** options for LocalMIP, FPR or ALNS. Those exist only in Spoorendonk's patched fork (§2.8).

### 1.2 Exchange matrix (realistic)

| Item | MindOpt out | HiGHS in | Value to us | How we'd test it in capopt |
|---|---|---|---|---|
| **Schedule → MindOpt start** (capopt incumbent warm-starts MindOpt) | n/a | `Var.Start` / `.mst` | **High for benchmarking fairness** (COPT got a warm start); low licence risk | Arm M3 of the protocol (§1.5): start = capopt's schedule at t = 10 s; check the log says "accept new sol" |
| **MindOpt incumbent → capopt** | `X`, pool `Xn`, `cbGetSolution` at MIPSOL | `setSolution` (sparse is fine), or straight into the shared incumbent after re-scoring | **High technically; licence-blocked** (§1.4). Also, with one seat, MindOpt can be at most one racer. | After clearance: arm M4, where MindOpt runs alongside (not inside) a capopt run and its MIPSOL schedules go into capopt's pool via a file queue. Measure time to 0.1%/1e-5 with and without. |
| **Objective cutoff** | `ObjVal` | `objective_bound` / `objective_target` | Medium. It is just a number. Licence-exposed in the MindOpt→capopt direction. | `objective_bound` on HiGHS leaves (already used for capopt's own incumbent) |
| **MIP dual bound** | `cbGet(MIP_OBJBND)`, `ObjVal` ± `MIP/GapAbs` | No import; combine outside the solvers: gap = (best schedule, re-scored) vs (max over valid bounds) | **Medium, advisory.** It is a floating-point bound from a different solver on a model we must prove identical. Use it to report "certified by MindOpt's bound" and to prune last years L, never as our own proof. Exact certificates need VIPR-style checking ([Cheung et al.](https://arxiv.org/pdf/1809.01572)). | Arm M2 records MindOpt's bound trace on *capopt's* fixed-L model, then compare it with capopt's and the LP bound at equal time. That also tells us whether MindOpt's cuts close the root gap, which is the diagnostic for H5. |
| **Cut-strengthened root relaxation** | `cbGetNodeRel` at MIPNODE (the root after cuts) | used outside HiGHS (orderings, kernels) | **Medium, speculative.** This is the only "relaxation information" HiGHS can't compute itself. Presolved-index ambiguity, so only with `Presolve` off. Licence-exposed. | Compare H11's TopoSort ordering from the HiGHS LP vs from MindOpt's root relaxation: does the schedule found by list scheduling plus local search improve? |
| LP primal/duals/reduced costs | `X`, `DualSoln`, `RC` | via bounds (`changeColsBounds`) | **Low.** HiGHS gets the same LP optimum in 0.14 s | none needed |
| Optimal basis | `ColBasis`/`RowBasis` | `setBasis` | **None.** The LP is trivial; the bases also index different (presolved) models | none |
| Presolve-tightened bounds | not exported | `changeColsBounds` | none (not available) | Our own start-window propagation (H6) does this |
| Cuts / cut pool | not exported (`cbCut` only adds) | rows | **None** from MindOpt. HiGHS→MindOpt cuts via `kCallbackMipGetCutPool` are possible but rarely pay | none |
| Tree / node bounds / pseudo-costs | not exported | not importable | none | none |
| Model file | MPS/LP | `readModel` | Medium, as an interchange *format* only. Build both models from our own code instead of exporting (the CPO export round-trip failed) | Arm M2 builds capopt's fixed-L model directly in mindoptpy from the same `MipBuilder` arrays |

**Bottom line.**
- The user's assumption is half right. **Search-state** information (cuts, presolve, tree, pseudo-costs) cannot be handed over through public APIs. **LP-level** information can, but it is worthless here because HiGHS recomputes it in 0.14 s.
- The useful channels are schedules, a cutoff, an advisory bound and, speculatively, the cut-strengthened root relaxation. All but the first direction (capopt → MindOpt) are licence-exposed.
- Live callback coupling is impractical: one seat, an unproven injection path, and presolved-space ambiguity. Offline, schedule-level exchange is the realistic form.

### 1.3 Where MindOpt fits architecturally

capopt stays the open-source core and must run identically with MindOpt absent. Given the licence (one seat, plus the uncertain clauses), MindOpt has three legitimate roles, in decreasing order of safety:

1. **External referee (benchmark only).** Measure MindOpt on the same scenarios and the same machine, privately (§1.5). This needs no data flow into capopt.
2. **A different-engine diagnostic on capopt's own model.** For example, does MindOpt close the root gap with cuts? Does it find the optimum earlier on the fixed-L model than on the native indicator model? This tells us *where* to invest (cuts vs heuristics vs branching) without copying anything from MindOpt's outputs into capopt. Even so, check with the vendor whether using MindOpt diagnostics to steer capopt's development is "improving another solver".
3. **An optional single-seat racer.** Only after licence clearance. One MindOpt process runs next to capopt's pool and exchanges schedules through a file queue. `mindopt_runner.licensed()` already serialises the seat. MindOpt must never sit inside capopt's worker pool.

### 1.4 Licence

- **Licence types** ([licence page](https://opt.aliyun.com/doc/latest/en/html/installation/license.html), [Alibaba Cloud](https://www.alibabacloud.com/help/en/optimization-solver/developer-reference/license-description)):
  - single-machine (unlimited concurrency on one machine);
  - **floating** (a cloud token pool; concurrency = tokens bought). A crashed process does not release its token: expect HTTP 409 until a timeout or a console force-release. This matches the CPO finding that "killed MindOpt runs orphan the floating licence for minutes" and justifies `mindopt_runner`'s never-kill rule;
  - community.
- **The only agreement text online** is the [MindOpt Community License Agreement](https://mindopt.tech/legal/mindopt_community_license_agreement) (updated 2024-12-23; PRC law). It contains:
  - "You can not use the Materials or any output therefrom to improve any other optimization solving model". Read broadly, this covers feeding MindOpt incumbents, bounds or relaxations into capopt, and perhaps even using MindOpt results to tune capopt;
  - a **benchmark-publication clause**: disclose the full methodology, give 30 days' notice of a freeze date, make the tested software public, and use the latest GA MindOpt with documented tuning. MindOpt gets reciprocal rights;
  - no reverse engineering; job telemetry is collected.
- **Not found:** a commercial EULA, pricing, or OEM/embedding terms.
- **Recommendation.** Before any MindOpt→capopt channel or public MindOpt numbers, get written confirmation of which terms govern the user's paid floating licence. Until then:
  - keep MindOpt results under `private/`;
  - build only the capopt→MindOpt direction;
  - keep MindOpt numbers out of the public README/BENCHMARK (the pack's recorded MindOpt times were measured before this question arose and should be checked too).

### 1.5 A fair MindOpt benchmark protocol (one seat, long-term access)

**Questions it answers.**
- How fast is MindOpt on the CPO scenarios against COPT's logged times?
- How much of the difference is the **formulation** vs the **engine** vs the **warm start**?

**Arms.** One run = one scenario × one arm × one repeat.

| Arm | Model | Start | Threads | Isolates |
|---|---|---|---|---|
| M0 native-cold | the CPO's native indicator model (as `benchmark_pack/reference/mindopt_model.py`, extended with the start cap) | none | 20 | MindOpt as the CPO uses it |
| M1 native-warm | same | the warm start COPT got, if it can be reconstructed; otherwise capopt's 10 s schedule | 20 | warm-start effect; COPT comparability |
| M2 fixedL-cold | capopt's fixed-L step model at L = Lmin, built in mindoptpy from the same `MipBuilder` arrays | none | 20 | formulation effect (M2 vs M0); engine effect (M2 vs capopt on the same model) |
| M3 fixedL-warm | same | capopt's schedule at t = 10 s | 20 | what a MindOpt racer would add on top of capopt's own heuristics |
| M2-1t | as M2 | none | 1 | engine vs engine against HiGHS single-thread on the identical model (algorithmic, not parallel) |

**Rules.**
1. **Never concurrent with anything timed.**
   - MindOpt runs go in their own campaign, serially, in one process at a time, under `mindopt_runner.licensed()` (machine-wide lock, licence probe with backoff).
   - No capopt campaign may run at the same time; the machine stays idle (MASTER_PLAN §7.6).
   - Schedule the MindOpt campaign as a separate overnight block.
2. **Never kill.**
   - Stop through `MaxTime` (screen 300 s, scoreboard 1800 s) and `MIP/GapRel = 1e-5` (the scoreboard target, matching COPT's stopping point).
   - The campaign watchdog's never-kill flag stays on. A badly overrunning run is recorded as skipped and the seat is probed before the next run.
3. **Same clock and same referee as capopt.**
   - Wall-clock from process start, with model build reported separately. Report both "solver-only" (COPT-comparable) and "wall" (capopt-comparable).
   - A MIPSOL callback records `(t, cbGetSolution → start years, MIP_OBJBND)`. Every schedule is re-scored by capopt's evaluator. Never compare solver objectives across models: the objective constants differ, which is exactly the trap the CPO export hit.
   - A MIPNODE callback, throttled to about 1 Hz, records the bound trace.
4. **Variability without a seed.**
   - MindOpt rejects a seed parameter. Create independent repeats by **permuting row and column order** of the model, an equivalent model. Permutation-induced variability is the standard way to expose MIP performance variability ([Lodi & Tramontani, TutORials 2013](https://pubsonline.informs.org/doi/pdf/10.1287/educ.2013.0112)).
   - Use 3 permutations per scenario × arm; report the median and range. Set `MIP/Static = 1` (deterministic), so the permutation is the only source of variation.
5. **Parameters.**
   - Defaults plus `MIP/AutoConfiguration = 1` (as the pack's reference), recorded in the run file.
   - One extra "heuristic-heavy" variant, `MIP/Heuristics = 0.5` + `MIP/LevelHeurs = 2`, which the CPO campaign found surfaced better portfolios. Run it on M0 only, so tuning stays separate from the formulation question.
   - The licence's benchmark clause expects "documented tuning", so only documented parameters are used.
6. **Order and drift.**
   - Randomise run order across scenarios and arms; repeat one control run (M0 on the screening scenario) at the start and end of each block to detect machine drift.
   - Record the MindOpt version, the parameter dump (`.prm`) and the machine fingerprint (as bench.py does).
7. **Sets.**
   - Screening: the 3 hardest timed scenarios (ITERATION_LOOP), M0/M2/M3 × 3 permutations × 300 s = 27 runs ≈ 2.3 h worst case.
   - Scoreboard: 8 timed + 4 untimed + the pack, M0 and M2 × 3 × 1800 s ≈ 48 h worst case. That is too long, so run M2 only where M0 fails to reach 1e-5, or use one permutation.
   - Use the CPO campaign's own measurements as a sanity check: on the native model, Total rows took a median of about 11 min to 1%, and 0.1% took 9.9 h for 16 rows.
8. **Outputs.**
   - `private/cpo/results/mindopt_<arm>/...` (trace JSONL plus summary), in the same schema as capopt campaigns, so `capopt report` can show MindOpt rows next to capopt and COPT.
   - Nothing public until §1.4 is settled.

**Implementation sketch.**
- Extend `mindopt_runner.solve_cold` with:
  - `model_kind ∈ {native, fixedL}`;
  - `start: dict | None`;
  - `permutation_seed`;
  - the MIPSOL/MIPNODE recorder.
- Add a `mindopt` runner kind to `capopt campaign` that runs runs serially, with no pool and never-kill.

## 2. Techniques from other fields and the frontier

Verdict scale: impact on **primal** quality and on the **dual** bound H/M/L/none; implementation cost S (days) / M (1-2 weeks) / L (a research project).

### Summary table

| # | Technique | Primal | Dual | Cost | Fit (one line) |
|---|---|---|---|---|---|
| 2.1 | Mine planning: TopoSort / LP-ordered list scheduling | **H** | none | S | Our step variables are mining's "extracted by t"; cash rows are cumulative capacities |
| 2.1b | Bienstock-Zuckerberg LP | none | none | M | Equals the LP bound; our LP already takes 0.14 s |
| 2.2 | Max-NPV / capital-constrained project scheduling (Lagrangian list scheduling, forward-backward improvement) | **H** | none | S | The closest family: fixed cash-flow profiles, NPV, cash ≥ 0 |
| 2.3 | Lagrangian relaxation of cash rows + subgradient / volume | M-H (as a driver) | none (= LP); L via fixing | S | Diverse "tilted" schedules for repair; multipliers for move scoring |
| 2.4 | Column generation / Dantzig-Wolfe | L-M | **M** (year-block DW only, unproven) | L | Per-project DW = LP; Caprara-style year blocks are the only above-LP decomposition |
| 2.5 | DP over years / beam search | M | none | M | Exact DP is hopeless (state = set of started projects); beam search over LP order is a broader list scheduler |
| 2.6 | Fix-and-optimize, relax-and-fix, **kernel search (MMKP)** | **H** | none | S | We are a multiple-choice multidimensional knapsack; we already have the sub-MIP workers |
| 2.7 | MMKP / MKP heuristics (Chu-Beasley pseudo-utility repair, core problems) | **H** (as the repair operator) | none | S | Directly fixes "LP rounding is cash-infeasible" |
| 2.8 | Feasibility pump / Feasibility Jump / ViolationLS / Local-MIP → **native start-year local search** | **H** (native) / L-M (generic) | none | M | The generic one-flip methods fight the GUB; native shift/swap moves don't |
| 2.9 | LNS / ALNS neighbourhood design (bandit, SCIP control rules, ParBalans) | **H** | L | M | Our LNS is three random neighbourhood types with a fixed size; the literature says diversify and adapt |
| 2.10 | Solution polishing / crossover / path relinking / proximity | M-H (late phase) | none | S | Distant near-optima (§0) make recombination unusually valuable |
| 2.11 | Racing, cooperative parallel, bet-and-run | M | M | S | Already partly in H4; ParBalans says many diverse single-thread workers beat one big solve |
| 2.12 | GPU first-order LP (PDLP) and GPU heuristics | L | none | L | The LP is tiny; the CPO campaign already rejected PDLP |
| 2.13 | ML predict-and-search | L (single instance) / M (scenario families) | none | M-L | The LP already pins most projects; scenario-to-scenario warm starts are the cheap version |
| 2.14 | CP-SAT (reservoir / cumulative) | M (uncertain) | L | M | Reservoir can't carry convex band penalties; feasible only as one extra LNS worker on the Boolean model |
| 2.15 | Symmetry handling | none | L (only with duplicates) | S | 227 of 233 projects are distinct |
| 2.16 | Patched HiGHS presolve heuristics (FJ + FPR + LocalMIP) | M (first solution) | none | S-M (needs GCC 13+, no Windows/Python path documented) | Speeds time-to-first-good, not the last 0.1% |

### 2.1 Open-pit mine planning

- **TopoSort.** [Chicoisne, Espinoza, Goycoolea, Moreno, Rubio, OR 60(3) 2012](https://ideas.repec.org/a/inm/oropre/v60y2012i3p517-528.html) solves the LP of the precedence-constrained multi-period knapsack fast, then rounds by **topological sort on an LP-weighted expected time** and places blocks greedily.
  - (abstract) Within about 6% in seconds and 1-3% after local search, on up to 5M blocks.
  - The index, written out in [arXiv 1706.08264](https://arxiv.org/pdf/1706.08264), is the LP expected time plus a penalty for unscheduled mass. There TopoSort is far closer to the LP than index rules (432 vs 358 against an LP of 439).
- **[Rivera Letelier et al., OR 68(5) 2020](https://pubsonline.informs.org/doi/fpi/10.1287/opre.2019.1965)** (abstract): preprocessing and cuts plus new heuristics give a 1.52% average gap, and 0.71% after 4 h of customised branch-and-bound.
- **BZ.** [Muñoz et al. 2018](https://arxiv.org/abs/1607.01104) recast BZ as column generation, 4-6× faster than plain DW. Its value equals the LP bound.
- **Fit (own reasoning).** Our step variables z[p,t] are the mining "extracted by t" variables: chains inside each project, no cross-project precedences. Cash rows are cumulative capacities. TopoSort transfers almost verbatim:
  1. E_p = Σ_t t·(z[p,t] − z[p,t−1]) from the LP;
  2. sort the projects by E_p;
  3. place each at the earliest year that keeps every cumulative balance ≥ 0 and the start cap satisfied.

  Our instances are millions of times smaller, so thousands of orderings per second (LP, Lagrangian, perturbed α-points) are affordable.
- **How to test.** H11.

### 2.2 Max-NPV, capital-constrained and finance-based project scheduling

- **The closest model in the literature.** [Leyman & Vanhoucke, EJOR 256(3) 2017](https://ideas.repec.org/a/eee/ejores/v256y2017i3p757-776.html): capital-constrained project scheduling with NPV, where the cash balance must stay non-negative (abstract; metaheuristics).
- **Lagrangian heuristics.** [Kimms, Ann. OR 102 2001](https://ideas.repec.org/a/spr/annopr/v102y2001i1p221-23610.1023-a1010962300979.html) (abstract) and [Gu, Stuckey, Wallace, CP 2012](https://people.eng.unimelb.edu.au/pstuckey/papers/npv_decomp_paper.pdf) (full text): every subgradient iteration feeds its Lagrangian start times into a list-scheduling heuristic, pulling positive-cash-flow activities earlier.
- **Hybrid.** [Thiruvady et al., J. Heuristics 2014](https://research.monash.edu/en/publications/a-lagrangian-relaxation-and-aco-hybrid-for-resource-constrained-p/): a Lagrangian + ACO hybrid beats either alone.
- **Cash as a cumulative resource.** [Neumann & Schwindt, MMOR 2003](https://ideas.repec.org/a/spr/mathme/v56y2003i3p513-533.html) treat cash as a cumulative resource (reservoir) with filtered beam search (abstract).
- **Finance-based scheduling (Elazouni and others).** Small instances and genetic algorithms ([example](https://pure.kfupm.edu.sa/en/publications/heuristic-method-for-multi-project-finance-based-scheduling/)), so weak evidence for a 1-minute near-optimal target.
- **Fit.** Our problem is this family without precedences, so it is easier. What transfers is list scheduling driven by LP or Lagrangian times, plus forward/backward ("left/right shift") passes.
- **How to test.** H11, as alternative orderings.

### 2.3 Lagrangian relaxation of the cash rows

- **No bound gain.** With the cash, band and cap rows relaxed, each project independently picks argmax over t of PV − Σ λ·cumspend. These subproblems are integral, so the Lagrangian dual equals the LP bound (Geoffrion, via [Fisher's tutorial](https://my.eng.utah.edu/~kalla/phy_des/lagrange-relax-tutorial-fisher.pdf)).
- **The value is primal.**
  1. [Everett 1963](https://ideas.repec.org/a/inm/oropre/v11y1963i3p399-417.html): any x(λ) is optimal for the right-hand side it consumes, which suits "independent activities" sharing a resource.
  2. The [volume algorithm](https://optimization-online.org/wp-content/uploads/2001/01/265.pdf) gives approximate primal fractional solutions cheaply.
  3. Multipliers price the moves of the local search (H10) and order list scheduling (H11).
- **Reduced-cost / Lagrangian fixing.** Our diagnostic shows it fixes only 1.5% at the root gap. Worth doing inside LNS sub-MIPs, where the local gap is smaller, but not as a headline idea. Fold it into H6 rather than adding an item.

### 2.4 Column generation / Dantzig-Wolfe

- **Per-project DW** has one column per start year: the original model, so the LP bound and no gain.
- **[Caprara, Furini, Malaguti, IJOC 2013](https://pubsonline.informs.org/doi/fpi/10.1287/ijoc.1120.0521).** For the temporal knapsack, DW over **groups of consecutive time constraints** gives far better bounds than the LP, while per-constraint DW "performs extremely poorly" (abstract). The recursive follow-up ([2016](https://cris.unibo.it/handle/11585/562891)) is stronger still.
- **Primal heuristics in branch-and-price.** Diving in the restricted master ([Sadykov et al., IJOC 2019](https://hal.archives-ouvertes.fr/hal-01237204)) is effective, but resembles our existing pool and sub-MIP machinery.
- **Verdict.** The only above-LP decomposition, but a research project. A **one-off bound probe** (H16) settles whether it is worth more.

### 2.5 DP over years, approximate DP, beam search

- The multiperiod knapsack has cumulative capacity like ours ([Faaland, OR 1981](https://ideas.repec.org/a/inm/oropre/v29y1981i3p612-616.html); approximation schemes in [arXiv 2104.00034](https://arxiv.org/abs/2104.00034); the incremental knapsack in [arXiv 1801.04801](https://arxiv.org/abs/1801.04801)).
- But all of these assume single-period item weights, not multi-year spend profiles with a chosen start.
- Exact DP state = (cash, set of started projects), which cannot be aggregated.
- **Verdict.** Beam search over LP-ordered insertion is a broadened H11. Try it only if greedy list scheduling leaves large gaps.

### 2.6 Fix-and-optimize, relax-and-fix, kernel search

- **Fix-and-optimize** ([Helber & Sahling, IJPE 2010](https://ideas.repec.org/a/eee/proeco/v123y2010i2p247-256.html)): solve sub-MIPs over period-, product- and resource-oriented subsets.
  - [Lang & Shen, EJOR 2011](https://ideas.repec.org/a/eee/ejores/v214y2011i3p595-605.html): **overlapping time windows** were best, 19% better than relax-and-fix on tight instances (search summary).
  - [Toledo et al., J. Heuristics 2015](https://strathprints.strath.ac.uk/53600/): relax-and-fix to construct, then fix-and-optimize to improve.
- **Kernel search.**
  - [Angelelli, Mansini, Speranza, C&OR 2010](https://iris.unibs.it/handle/11379/33507): the kernel comes from the LP support; buckets of other variables, ordered by reduced cost, are added through a sequence of restricted MIPs.
  - The **MMKP variant** ([Lamanna, Mansini, Zanotti, EJOR 2022](https://ideas.repec.org/a/eee/ejores/v297y2022i1p53-65.html)) is our exact structure (one choice per class plus capacity rows) and runs in two phases: first feasibility and calibration of sub-MIP size and time, then quality.
  - [Mansini & Zanotti, IJOC 2020](https://ideas.repec.org/a/inm/orijoc/v32y4i2020p1061-1079.html): a core-based MMKP method beat Gurobi's 1-hour solution in 3 minutes (abstract).
- **Fit.** Very high, and cheap: it reuses `parallel.py`'s sub-MIP machinery. The §0 caveat is that the kernel must be wide (± years around the LP support, plus the incumbent's years), because the optimum is far from the LP.
- **How to test.** H13.

### 2.7 MKP / MMKP heuristics

- **[Chu & Beasley 1998](https://people.brunel.ac.uk/~mastjjb/jeb/gamkp.html).** The lasting component is the repair operator, ordered by LP-dual **pseudo-utility** p_j / Σ_i u_i a_ij: drop the worst items until feasible, then add the best that fit.
- **[A 2024 randomised-repair study](https://arxiv.org/html/2405.15569v1):** randomising within efficiency groups keeps diversity.
- **Our version.** Score shift moves by ΔPV / Σ_y λ_y·Δcumspend_y and apply them until every cumulative balance is ≥ 0, then pull projects earlier while slack allows. This is the repair half of H11 and a move-ordering rule for H10.

### 2.8 Feasibility pump, Feasibility Jump, ViolationLS, Local-MIP

- **Feasibility pumps** (the [original](https://publications.polymtl.ca/24161/), [objective FP](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/875), [Scylla](https://arxiv.org/pdf/2307.03466)) target feasibility on hard LPs. We already have feasibility, and our LP is easy. **Low.**
- **[Feasibility Jump](https://opus4.kobv.de/opus4-mpc/frontdoor/index/index/year/2024/docId/240):** weighted-violation one-variable jumps; winner of MIP 2022. It has been on by default in HiGHS since 1.11 ([releases](https://github.com/ERGO-Code/HiGHS/releases)).
- **[Local-MIP, CP 2024](https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.CP.2024.19):** tight, *lift* and breakthrough moves with two-level weighted scoring (abstract: beats FJ, HiGHS, SCIP and CPLEX).
  - [PyPI `localmip`](https://pypi.org/project/localmip/2.0.6/) exists; Windows support is unverified.
  - [Champion's HiGHS-workshop talk (June 2026)](https://jump.dev/assets/jump-dev-workshops/2026/slides_ben.pdf) shows a simpler constraint-based local search roughly matching it.
- **ViolationLS** (CP-SAT; [solver description](https://minizinc.org/challenge/2024/description_or-tools_cp-sat_ls.txt)): compound moves for exactly-one constraints are unverified.
- **[Spoorendonk, arXiv 2609.22938](https://arxiv.org/abs/2609.22938)** ([code](https://github.com/spoorendonk/mip-heuristics), MIT): FJ, FPR and LocalMIP as seeded parallel presolve heuristics in HiGHS 1.15.1. The primal integral is 23.7% better overall and 12.9% better out of sample; Scylla and the LP dive never helped.
- **The structural point (own reasoning).** Our basic move, "project p from year t to t′", is a *two*-binary move under a GUB. Generic one-flip searches must pass through a GUB violation to make it. A **native** search over the start-year vector has no such problem:
  - each shift costs O(T) via prefix sums of spend and balance, including the band penalties;
  - all moves can be evaluated in milliseconds in numba;
  - swaps and two-step ejection chains (shift A later to free cash, pull B earlier) come cheap.

  capopt's `heuristics/kernel.py` already has an exact incremental objective, plus annealing and descent. The step is to add FJ/Local-MIP-style **adaptive constraint weights** (to cross infeasible regions in this 0.8%-slack packing), **lift moves**, swaps and ejection chains, and to run 1-16 seeded copies.
- **How to test.** H10.

### 2.9 LNS / ALNS neighbourhood design

- **[Hendel, MPC 2022](https://link.springer.com/article/10.1007/s12532-021-00209-7) (ALNS in SCIP).** A UCB bandit over 8-9 neighbourhoods. The control rules, from [heur_alns.c](https://www.zib.de/userpage/mexi/scip_html-format-errors/1/98heur_alns.c.html):
  - fixing rate in [0.3, 0.9], adapted by sub-MIP outcome;
  - node budget 500 + 0.1·nodes, clamped to [50, 5000].
- **[Balans, IJCAI 2025](https://www.ijcai.org/proceedings/2025/286)** ([arXiv](https://arxiv.org/html/2412.14382v1), [code](https://github.com/skadio/balans/)): a bandit over 16 arms with simulated-annealing acceptance. 1-hour primal gap on D-MIPLIB 2.11% → 0.49% vs SCIP.
- **[ParBalans, arXiv 2508.06736](https://arxiv.org/html/2508.06736v1): diverse single-thread workers beat Gurobi** with the same total threads on real-world instances.
- Learned selectors ([Song et al. 2020](https://proceedings.neurips.cc/paper/2020/hash/e769e03a9d329b2e864b4bf4ff54ff39-Abstract.html), [CL-LNS](https://proceedings.mlr.press/v202/huang23g.html), [LLM-LNS](https://proceedings.mlr.press/v267/ye25j.html)) need training families. **Low for now.**
- **Our LNS today** (`parallel._lns_task`): three neighbourhood kinds (a time window of starts, random subset, active-in-window), a fixed size of about 25, a 3 s sub-MIP and greedy acceptance. On CPO:
  - "time window from year 0" covers 100 projects and gets truncated to a random subset;
  - "random 25 of 233" is too small to bridge distant near-optima.
- **Structure-aware neighbourhoods to add (own design):**
  1. **cash-crunch**: the projects with the largest outflows in the 2-3 years with the least cumulative slack;
  2. **cap swap**: k of year 0's 100 starters plus the k best year-1/2 candidates, so the sub-MIP re-chooses who goes first;
  3. **overlapping time windows** (fix-and-optimize);
  4. **crossover**: fix where 2-3 pool members agree;
  5. **local branching** on the step encoding (at most k moved projects);
  6. **proximity**.
- **How to test.** H12.

### 2.10 Polishing, recombination, solution pools

- **[Rothberg 2007](https://pubsonline.informs.org/doi/fpi/10.1287/ijoc.1060.0189):** CPLEX's evolutionary polishing (mutation and crossover over a pool, each child solved as a sub-MIP).
- **[Gurobi `ImproveStartTime`](https://gurobi.com/documentation/9.5/refman/improvestarttime.html):** switch to pure improvement late in the run.
- **[Proximity search](https://cris.unibo.it/handle/11585/542450):** avoids the ugly 1e3-1e4 penalty weights in the sub-MIP objective.
- **Why it matters here.** The §0 finding (good schedules 128 projects apart) is precisely the situation in which crossover of distant elites pays: their agreement set is a strong, non-local fixing.
- **How to test.** Folded into H12 as the late phase.

### 2.11 Racing and cooperative parallel search

- Bet-and-run ([Fischetti & Monaci, OR 2014](https://ideas.repec.org/a/inm/oropre/v62y2014i1p114-122.html)), [UG racing ramp-up](https://opus4.kobv.de/opus4-zib/frontdoor/deliver/index/docId/7839/file/ZIB-Report_20-16.pdf), and multi-solver racing that shares incumbents ([Smoothie](https://optimization-online.org/2025/10/smoothie-mixing-the-strongest-mip-solvers-to-solve-hard-mip-instances-on-supercomputers-phase-i-development/)).
- capopt already shares incumbents into HiGHS through the user-solution callback. Already covered by H4 and H12's worker split; no new item.

### 2.12 GPU first-order LP and GPU heuristics

- [cuPDLP](https://arxiv.org/pdf/2311.12180): GPU speed-up grows with instance size and is about equal to simplex at high accuracy.
- [Çördük et al. 2025](https://arxiv.org/html/2510.20499v2) (NVIDIA): their gains are on MIPLIB with H100s.
- The MIPcc26 winner, CHAP ([ZIB](https://www.zib.de/news/zib-team-wins-2026-land-doig-mip-competition), [arXiv 2605.05086](https://arxiv.org/abs/2605.05086)), is a CPU+GPU portfolio sharing one pool.
- **Our LP takes 0.14 s.** What transfers is the *architecture* (many cheap local-search workers, one shared pool, LP-seeded starts), not the GPU. **Skip.**

### 2.13 ML-guided search

- Neural diving ([Nair et al.](https://ar5iv.labs.arxiv.org/html/2012.13349)), predict-and-search ([Han et al., ICLR 2023](https://arxiv.org/pdf/2302.05636): 51% / 9.9% primal-gap gains vs SCIP / Gurobi), and [ConPaS](https://proceedings.mlr.press/v235/huang24f.html).
- On one instance the LP already does the predictor's job.
- **For scenario families** (GPS27 has dozens of related scenarios), the cheap, proven version is the **donor warm start**: start from the nearest solved scenario, then run proximity search or LNS around it.
  - The CPO campaign rated its "warm-start / donor cascade" **keep**, with the seeding rule: same cost family and benefit, donor funding ≤ recipient.
  - Gradient-boosted "start = donor/LP start" confidence for partial fixing is an optional add-on. GNNs are not worth it.
- **How to test.** H14.

### 2.14 Constraint programming

- CP-SAT's `add_reservoir_constraint` ([source](https://raw.githubusercontent.com/google/or-tools/stable/ortools/sat/python/cp_model.py)) models a cash level that must stay within bounds. But the **convex band penalties on the balance can't be expressed** in a reservoir, so the model falls back to the time-indexed Boolean model.
- CP-SAT's strength is its LNS and local-search portfolio ([Perron et al., CP 2023](https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.CP.2023.3)).
- **Verdict.** At most one extra LNS worker on the Boolean model, warm-started from our incumbent. Uncertain payoff, so not shortlisted.

### 2.15 Symmetry

- [Pfetsch & Rehn, MPC 2019](https://optimization-online.org/2015/11/5209/): about 15% on symmetric instances.
- Our 227 distinct projects of 233 make this negligible. Add ordering rows for exact clones only, if ever.

## 3. Ranked shortlist (backlog H10-H16)

**Ranking.** By expected effect on the primary KPI through the primal side, with cost as the tiebreak.

**Screening for every item.**
- Two stages, so most screening needs no multi-process timing:
  - **(a) Standalone, single thread.** Seconds per run on the 3 screening scenarios, scored against the CPO objective by the evaluator: gap at 2/5/10/30/60 s and the primal integral.
  - **(b) In the pipeline.** As one watched campaign, 300 s cap (ITERATION_LOOP step 2): time to 1%, 0.1% and 1e-5. Run stage (b) only when (a) beats the current incumbent trace.
- Decision rules as in ITERATION_LOOP.

### H10. Native start-year local search with adaptive cash weights (refines H2)

- **Hypothesis.** A numba local search over the start-year vector reaches below 0.5% in 10 s single-threaded, and below 0.2% with 16 seeded copies. LNS needs 90 s for 0.88%.
  - Moves: shift ±1..k, swap two projects' years, a two-step ejection chain.
  - Exact O(T) incremental cost including the bands.
  - FJ/Local-MIP-style adaptive weights on violated cash and cap rows.
  - Lift moves and short tabu.
- **Why.** §2.8. The two-binary GUB move defeats generic one-flip searches. `heuristics/kernel.py` already has the exact incremental objective. The 0.8% cash slack means good moves often need to cross briefly infeasible states, which is what adaptive weights are for.
- **Expected effect.** Time to 1% and 0.1% drops to seconds. Earlier pruning in the tree workers.
- **Screen.**
  - (a) From the LP-rounded schedule and from H11's schedules: 1 thread, 60 s, 3 seeds. Report gap vs time and the number of swaps and chains accepted.
  - (b) As 2-4 of the 20 workers sharing the incumbent.
- **Cost.** M.

### H11. LP-ordered list scheduling with dual-guided cash repair (TopoSort / Lagrangian / α-points)

- **Hypothesis.** Order projects by LP expected start (and by Lagrangian starts from about 50 subgradient steps, and by randomised α-points), place each greedily at the earliest cash- and cap-feasible year, then apply pseudo-utility repair plus a pull-earlier pass. This gives cash-feasible schedules within 1-2% in milliseconds each, and thousands of **diverse** seeds per second for H10 and H12.
- **Why.** §2.1, §2.2, §2.3, §2.7. Mining and NPV scheduling get from the LP to near-optimal this way. It replaces "LP rounding is cash-infeasible" with a constructive repair.
- **Expected effect.** First good incumbent in under 1 s (today capopt's first CPO incumbent is very poor and the first good one arrives at about 40 s). Better diversity for crossover.
- **Screen.** (a) Gap of the best of N orderings for N ∈ {1, 100, 10k}, the time taken, and the gap after 10 s of H10 from the best 20. (b) As the warm start before the pool spawns (H2's slot).
- **Cost.** S.

### H12. Structure-aware parallel ALNS with bandit, SCIP control rules and pool crossover

- **Hypothesis.** Replace `_lns_task`'s three fixed-size random neighbourhoods. Instead, a UCB bandit chooses among:
  - cash-crunch;
  - year-0/1 cap swap;
  - overlapping time windows;
  - crossover of 2-3 pool elites;
  - local branching (k ∈ {5, 10, 20});
  - proximity;
  - random.

  Control: SCIP's fixing-rate and node-limit rules, 0.5-3 s HiGHS sub-MIPs, simulated-annealing acceptance, a shared solution pool, and H10 polishing after each sub-MIP. From about 70% of the budget, all LNS workers switch to crossover plus proximity (polishing).
- **Why.** §2.9 and §2.10. ParBalans: diverse single-thread workers beat a big solver. §0: near-optima are far apart, so recombination and big structured neighbourhoods are needed. I5's LNS was "neutral" on the pack because the tree found the optimum first; on CPO the tree is what stalls.
- **Expected effect.** From 0.07% at 200 s to under 0.01% within 60 s. Feeds the tree's pruning.
- **Screen.**
  - (a) 1 worker, 60 s, single thread, against today's `_lns_task`, from the same start. Log bandit arm usage.
  - (b) 4 vs 8 LNS workers out of 20 in the pipeline.
- **Cost.** M.

### H13. Kernel search for the MMKP structure

- **Hypothesis.**
  1. Kernel = (p, t) pairs with positive LP value or in the incumbent, ± 2 years.
  2. Buckets = the remaining pairs ordered by reduced cost.
  3. A sequence of 2-3 s restricted HiGHS MIPs with the incumbent as cutoff, adding the variables that turn on.

  One pass (about 30 s, single-threaded) reaches below 0.1%.
- **Why.** §2.6. The MMKP two-phase variant (Lamanna-Mansini-Zanotti 2022) is our exact structure, and it reuses the existing sub-MIP code.
- **Expected effect.** Below 0.1% in about 30 s on one worker. Complements H12, since it is a systematic rather than a random sweep.
- **Screen.** (a) Kernel width ±1/±2/±4, bucket count 5/10/20, 1 thread. (b) As one worker.
- **Cost.** S.

### H14. Scenario-family donor warm start

- **Hypothesis.** Seed each CPO scenario from the optimum of its nearest solved sibling (same cost family and benefit, donor funding ≤ recipient), repaired by H11's repair and polished by H10 or proximity search. That puts the start within 0.1% before the pool spawns.
- **Why.** §2.13. It is the CPO campaign's own **keep** verdict. COPT's logged times were themselves after a supplied warm start, so this also makes the comparison like-for-like. The KPI report must say when a donor was used.
- **Expected effect.** Large for batches of related scenarios (the CPO use case); none for one-off solves.
- **Screen.** (a) Donor → repair → 10 s H10, gap per screening scenario using the other GPS27 scenarios as donors. (b) Pipeline time to 1e-5, warm vs cold.
- **Cost.** S.

### H15. MindOpt as referee and diagnostic (protocol §1.5), then possibly a single-seat racer

- **Hypothesis.** The arms M0/M2/M3 separate formulation, engine and warm-start effects:
  - if M2 (MindOpt on capopt's fixed-L model) closes the root gap with cuts that HiGHS lacks, H5 (cuts) moves up;
  - if M2 finds the optimum much earlier than capopt with the same model, the gap is in heuristics and branching, not the formulation.
- **Why.** §1. It is the only way to learn *where* a commercial engine gains on this structure without copying anything into capopt.
- **Expected effect.** Diagnosis, not speed. A racer arm (M4) only after licence clearance (§1.4).
- **Screen.** The protocol's screening block, 27 runs, run overnight, never concurrent with capopt timing.
- **Cost.** S (runner extension) plus licence clarification.

### H16. Year-block Dantzig-Wolfe bound probe (dual side)

- **Hypothesis.** A DW reformulation whose pricing subproblems are blocks of 3-5 consecutive years (Caprara-Furini-Malaguti) gives a root bound noticeably above the LP, for example closing more than 30% of the 0.47% gap. If so, this is the only decomposition route to a stronger proof.
- **Why.** §2.4. Every other relaxation equals the LP bound here.
- **Expected effect.** A yes/no on a large research direction. Speed only later.
- **Screen.** One-off: compute the DW root bound on the 3 screening scenarios, single-threaded, with a 30-minute cap per scenario, run off the timing machine's critical path, and compare it with the LP bound and the cover-cut bound (H5).
- **Cost.** M for the probe only.

### Notes on existing items

- **H6 (reduced-cost fixing).** The root diagnostic shows only about 1.5% of columns fixable at the root gap. Keep H6 for the sub-MIPs and node level, but expect little from root fixing until the bound or incumbent improves. Lagrangian fixing (§2.3) gives the same numbers.
- **H2 (fast warm start)** is refined by H11 (construction) and H10 (improvement). If H2 is picked first, implement it as H11 + H10.
- **H8 (HiGHS option tuning)** can include Spoorendonk's patched presolve heuristics (§2.8) if a Windows build is feasible. They improve time to first good solution, not the final gap.
- **Not shortlisted:** PDLP/GPU (the LP takes 0.14 s), ML predict-and-search beyond donor warm starts, symmetry, feasibility pumps, exact DP, CP-SAT (one optional extra worker at most).

## References (by section)

See the inline links. Primary sources per section:
- **§1.** MindOpt docs (opt.aliyun.com), the MindOpt Community License Agreement, the HiGHS docs and source, Mittelmann's [benchmark page](https://plato.asu.edu/bench.html) and [INFORMS 2023 talk](https://plato.asu.edu/talks/informs2023.pdf). MindOpt withdrew from the MIP benchmarks in late 2024; its last published MIPLIB2017 scaled time was 3.70 against Gurobi's 1 and COPT's 1.54, with HiGHS at 8.83. Plus Lodi & Tramontani 2013 and Cheung et al. (VIPR).
- **§2.** The papers linked in each subsection.
- **Earlier reviews** in this repo: [MILP_FRONTIER.md](MILP_FRONTIER.md) (dual side, pack) and [AI_MATH_RESULTS.md](AI_MATH_RESULTS.md) (evolve-a-heuristic, the natural next target once H10/H12 exist).
