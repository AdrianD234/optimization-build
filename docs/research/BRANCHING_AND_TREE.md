# Branching, endgame and parallel-tree review for capopt

**Date:** 2026-10-15
**Scope:** How to close the last 0.15% of the dual bound on the 233-project CPO models (scenario `s2000_P50high`), given HiGHS 1.15.1 (highspy) as the engine and capopt's own split-and-solve tree (`src/capopt/parallel.py`) as the parallel layer. Covers branching, endgame bound closing, parallel bound balancing and bound-only side techniques, then ranks experiments.

## Method

- **Searches.** Web searches and fetches on 2026-10-15 over arXiv, ZIB OPUS, Optimization Online, INFORMS, Springer, AAAI, the UG/ParaSCIP docs, and the HiGHS GitHub sources and release notes. I extracted the full texts of the ParaSCIP 80,000-core report (ZIB-Report 20-16) and Para-B&B (arXiv 2604.09556) locally.
- **Local reading.**
  - HiGHS C++ sources on GitHub `master`: `HighsPseudocost.h`, `HighsSearch.cpp`, `HighsMipSolver.cpp`, `HighsMipSolverData.cpp` and `HighsCallbackStruct.h`. I fetched these as of today; the 1.15.1 tag may differ in detail.
  - The installed highspy 1.15.1 option list, from `writeOptions` (no solve).
  - capopt's `parallel.py`, `solver.py` and `campaign.py`.
- **No solvers were run** while the benchmark was timing.
- **Citation rule.** Every source below was found in this session and has a URL. Where only an abstract or listing could be checked, the entry says so. **[unverified]** marks claims I could not confirm from a source.

## 0. Findings from capopt's own code (read these first)

These come from reading `src/capopt/parallel.py` and `solver.py`. They may explain much of the "plateau at -0.15%" before any algorithmic change is needed.

1. **The reported global bound freezes running leaves at their ramp-up LP bound.**
   - `bound_now` (parallel.py ~l.449) is the minimum of three things: closed-leaf bounds, the queue head, and `rv[0]` for running leaves. `rv[0]` is the LP bound the leaf had when it was *launched*.
   - Workers never report their live `mip_dual_bound` to the master, although the interrupt callback receives it on every call.
   - So the global bound cannot rise while the worst-LP leaf is still running, however much HiGHS has closed inside it. The -0.15% plateau may partly be an accounting artefact, and it also hides *which* leaf is the bottleneck.
2. **Leaves in the CPO campaign run without a cutoff.**
   - `solver.solve` defaults to `leaf_cutoff=False` (exp05 chose this on `raw_penalised`, in the primal phase), and `campaign.reference_runner` passes only `rel_gap`.
   - Inside a leaf, HiGHS therefore prunes only against incumbents it finds *inside that leaf*. The interrupt callback stops a leaf only once its whole dual bound reaches the shared incumbent.
   - HiGHS selects nodes by best estimate in about 9 of 10 picks (see §1). A leaf without a cutoff spends node budget on nodes whose bound is already above the global incumbent. **That work is wasted in the proof phase.**
3. **Re-splitting throws away the leaf's MIP bound.**
   - On a slice timeout (`slice_seconds`), the children are queued with their *LP* bound (`child.bound`, l.471), not with `max(child LP, r["bound"])`.
   - The parent's `mip_dual_bound` is valid for both children because they partition it. Discarding it makes the bound regress and mis-orders the queue.
4. **The ramp-up branching rule is "most fractional times spend weight"** (`_lp_node`, l.130-141), with no strong branching.
   - In Achterberg, Koch and Martin's comparison, most-infeasible branching performs about like random branching ([Branching rules revisited, ORL 2005](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/788); the "about like random" summary is my reading of the well-known result **[unverified wording]**).
   - The ramp-up decides the top 5-6 levels of the tree, where branching quality matters most.
5. **Every leaf relearns pseudocosts from scratch.** The 60 leaves each pay HiGHS's strong-branching warm-up again. HiGHS has no API to import pseudocosts.

## 1. What HiGHS 1.15 actually does (verified in source)

| Component | What the source shows | Consequence for capopt |
|---|---|---|
| Branching score | `HighsPseudocost::getScore`: product pseudocost score, plus conflict (weight 1e-2), cutoff and inference (1e-4 each), adjusted by a degeneracy factor. | This is effectively **hybrid branching** (Achterberg and Berthold, [CPAIOR 2009](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/2823)). HiGHS already does reliability, hybrid and conflict-weighted variable branching. |
| Strong-branching budget | `HighsSearch::branch`: budget = 100,000 + ½ × (non-heuristic, non-SB LP iterations). `minreliable` ramps to 0 as the budget runs out, and is 0 if the degeneracy factor is ≥ 10. SB children are propagated, with conflict analysis on infeasible ones and fixing of the other side. | `mip_pscost_minreliable` (default 8) is the only knob. Raising it helps only until the iteration budget caps it. On a dual-degenerate LP, SB switches off silently. |
| Node selection | `installNodes`: with one worker, best-bound only when ≥ 10 leaves have passed since the last best-bound pick; otherwise best-estimate (`popBestNode`). | Mostly estimate-driven diving. There is no option to switch to pure best bound (that would need a fork). |
| Restarts | `checkRestart`: votes from a tree-size estimate (gap reduction against the active-integer ratio). | This is a "clairvoyant"-style restart ([Anderson et al., AAAI 2019](https://ojs.aaai.org/index.php/AAAI/article/view/3944)), switchable by `mip_allow_restart`. |
| Cutoff | `objective_bound` sets `upper_limit` and `optimality_limit` **once, at MIP init** (`HighsMipSolverData.cpp` l.799-800). | It is a static cutoff. `HighsCallbackDataIn` carries only `user_interrupt` and `user_solution`, with **no cutoff field**. A shared incumbent can reach a running leaf only by restarting it, or through a fork. |
| Parallel MIP (1.15) | Workers spawn while open nodes outnumber workers. Only worker 0 takes the best-bound node; the others take best-estimate nodes. Each worker has its own LP, cut pool, conflict pool and pseudocosts ([v1.15.0 notes](https://newreleases.io/project/github/ERGO-Code/HiGHS/release/v1.15.0); [HiGHS workshop slides 2026](https://jump.dev/assets/jump-dev-workshops/2026/slides_julian.pdf)). The slides warn that all threads can end up waiting on one long dive. | Usable inside a single hard leaf in the endgame (§4, E6). Not a replacement for capopt's tree. |
| Options (highspy 1.15.1) | `mip_pscost_minreliable`, `mip_heuristic_effort`, `mip_allow_restart`, `mip_allow_cut_separation_at_nodes`, `mip_lp_age_limit`, `mip_pool_age_limit`, `mip_pool_soft_limit`, `mip_detect_symmetry`, `mip_max_nodes`, `mip_max_leaves`, `mip_max_stall_nodes`, `mip_root_presolve_only`, `mip_lifting_for_probing`, `random_seed`, `threads`, `mip_search_simulate_concurrency`. | `mip_max_nodes=1` gives a cheap root-with-cuts bound per leaf. `mip_max_leaves` gives deterministic slices. There is no option for node selection or branching rule. |

## 2. Branching that closes time-indexed / cumulative-knapsack gaps

**Structural facts (from `HYBRID_AND_CROSSFIELD.md` §0):**
- The root LP has only 13 of 233 projects fractional.
- At a vertex, at most about one project is fractional per binding linking row.
- The year-0 cap (100 starts) binds.
- 227 of 233 projects have distinct (spend, PV) profiles.
- The gap sits on a handful of fractional projects coupled through about 30 cumulative cash rows.

| Technique | Literature | Fit to capopt | How to get it with HiGHS |
|---|---|---|---|
| **GUB / SOS1 "start ≤ s vs > s"** | Beale and Tomlin's SOS branching (OR69 proceedings; **[unverified citation details]**); time-indexed LP equivalence: Artigues 2017 ([HAL](https://hal-univ-tlse3.archives-ouvertes.fr/hal-01461447)) | The right dichotomy. Pulse `x[p,s]=0` branching is nearly useless. | **Already obtained:** the step encoding makes every `z[p,t]` branch an SOS1 split, and the outer tree's `Split=(p,s,side)` is the same split. Nothing more to do. |
| **Strong / reliability / hybrid branching** | Achterberg, Koch, Martin 2005 ([ZIB](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/788)); hybrid branching ([ZIB](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/2823)); SB with propagation, Gamrath 2014 ([ZIB](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/4326)); probabilistic lookahead SB, Mexi et al. CPAIOR 2024 ([arXiv 2312.07041](https://arxiv.org/abs/2312.07041), now in SCIP 10) | High. A commercial reference closes most of the gap in a small tree (figures in private notes), i.e. very informed nodes. | (a) Inside leaves: `mip_pscost_minreliable` 16-64. (b) **In the outer tree:** full SB with a product score over the top K candidates, using the warm-started master LP (§6 E3). This is where capopt has full control. |
| **Aggregated / general disjunctions** (`πx ≤ π0 ∨ πx ≥ π0+1`, π integer) | Owen and Mehrotra 2001 (cited in Karamanov's abstract); Karamanov and Cornuéjols, MP 2011 (doi 10.1007/s10107-009-0332-3; abstract via [SUFE listing](https://academicnewsletter.sufe.edu.cn/info/399033)): smaller trees than full SB on most instances; Cornuéjols, Liberti, Nannicini MP 2011 ([IP Paris](https://researchportal.ip-paris.fr/en/publications/improved-strategies-for-branching-on-general-disjunctions/)); Mahajan and Ralphs ([Optimization Online](https://optimization-online.org/?p=10468)) | Medium. Valid integer aggregates here are: **counts** `N_t(S)=Σ_{p∈S} z[p,t]` (all projects, the fractional set, or high-spend projects); **project start** `start_p` (equivalent to the existing split); **pair order** `start_p − start_q ≤ 0 ∨ ≥ 1`. **Cumulative spend by year t is not usable directly:** spend coefficients are not integer, so `S_t ≤ α ∨ S_t ≥ α` cannot cut off the LP point on both sides. The year-0 count is integral at the LP (the cap binds), so counts help only at t ≥ 1. | Outer tree only. Extend `Split` to carry a row (coefficients, side, rhs). Leaves get `addRow`. Rank candidates by SB product score against variable splits (§6 E7). |
| **"Which year is cash-critical"** | No direct source found. Related: SB candidate filtering by duals/pseudocosts is standard (SCIP, HiGHS) | It is not a valid disjunction by itself (cash is continuous). As a **candidate filter** it is useful: score `z[p,t]` splits by fractionality × (spend falling in years with the largest cash-row duals). | Outer tree: pre-filter the SB candidate list (§6 E3). |
| **Ryan-Foster-like** | Ryan and Foster 1981 (pair-together / pair-apart; [SCIP docs](https://scipopt.org/doc/html/BINPACKING_BRANCHING.php)) | The analogue is the **pair-order** disjunction above, for two fractional projects competing for the same cash years. Ryan-Foster is designed for set partitioning with column generation, so its strength does not transfer automatically. | Outer tree, as a general disjunction (E7). |
| **Orbital / symmetry-aware** | Ostrowski, Linderoth, Rossi, Smriglio, MP 126 (2011) ([UW TR](https://research.cs.wisc.edu/techreports/2008/TR1641.pdf)) | **Low:** 227 of 233 profiles are distinct. HiGHS already has `mip_detect_symmetry` with orbitopes and orbital fixing (seen in `HighsSearch.cpp`). | Leave it on. Nothing else to do. |

**Emulation routes, ranked:**
1. **Outer-tree SB with general disjunctions** (full control, LP-only, cheap).
2. **Reformulation:** done (step encoding).
3. **HiGHS options:** `mip_pscost_minreliable`, plus effort and age limits.
4. **Priorities via split order:** the outer tree *is* the priority mechanism for the top levels. HiGHS has no priority API.

## 3. Endgame bound closing: what top solvers do, and what matters here

| Technique | Source | In HiGHS? | Relevance to cumulative-knapsack scheduling |
|---|---|---|---|
| Conflict analysis (infeasibility and bound-exceeding conflicts) | Achterberg 2007 ([ZIB](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/853)); Witzig, Berthold, Heinz ([arXiv 1611.07887](https://arxiv.org/abs/1611.07887)); cut-based conflict analysis ([arXiv 2410.15110](https://arxiv.org/abs/2410.15110), SCIP 10). A figure of up to 28% faster proofs on affected instances is attributed to Achterberg and Wunderling 2013 by [arXiv 1902.02615](https://arxiv.org/abs/1902.02615) **[secondary]**. | Yes (conflict pool; conflict score in branching) | Medium. Tight cash rows produce many infeasible or cut-off children. It is already on, so there is nothing to emulate. |
| Restarts (global, tree-size driven) | Clairvoyant restarts ([AAAI 2019](https://ojs.aaai.org/index.php/AAAI/article/view/3944)) | Yes (`checkRestart`) | Low to medium. Capopt's re-split is a *subtree* restart with re-presolve. |
| Node / "layered" presolve | ParaSCIP re-presolves every transferred subproblem and reports fewer nodes with more solvers (ZIB-Report 20-16, §3.2; [PDF](https://opus4.kobv.de/opus4-zib/frontdoor/deliver/index/docId/7839/file/ZIB-Report_20-16.pdf)) | Only at root and on restarts | **Free in capopt.** Each leaf is a fresh HiGHS model, so it is presolved under its fixings. This is a reason the outer split can beat a single tree. |
| Strong branching with propagation and SB-based fixing | Gamrath 2014 ([ZIB](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/4326)) | Yes | High. The budget is the limit (§1). |
| Local cuts | Chvátal, Cook, Espinoza, MPC 5 (2013) ([PDF](https://math.uwaterloo.ca/~bico/papers/local_cuts.pdf)) | No (node cuts are global) | Low to medium. It would need a fork or an outer cut loop per leaf. Capopt's own GUB-cover cuts per *leaf* (a leaf-local root loop) are the cheap analogue. |
| Lookahead / dynamic SB limits | Mexi et al. ([arXiv 2312.07041](https://arxiv.org/abs/2312.07041)) | No | Medium. Use it in the outer-tree SB (stop sampling candidates when the expected gain is low). |
| Racing / erraticism | Fischetti and Monaci, OR 2014 ([INFORMS](https://pubsonline.informs.org/doi/10.1287/opre.2013.1231)); UG racing ramp-up (ZIB-Report 20-16, §3.3) | No | High for the *last* leaf. Bounds from different runs on the same subproblem combine by **max**, so racing is pure upside for the bound. |
| Local rapid learning / CP at nodes | Berthold, Stuckey, Witzig, CPAIOR 2019 ([arXiv 1902.02587](https://arxiv.org/abs/1902.02587)) | No | Low. It mainly helps dual-degenerate pure binaries. |

**Verdict for knapsack-like scheduling MIPs:** the endgame is dominated by **(i) cutoff-based pruning against the true incumbent, (ii) branching quality near the top of each subtree, (iii) presolve under fixings, and (iv) not leaving one subtree alone at the end.** Conflict analysis and restarts are already in HiGHS. Local cuts and lookahead are second order.

## 4. Parallel branch-and-bound bound balancing

**Does one subtree hold the bound down? Almost certainly yes.**
- With static leaves the global bound is `min` over open leaves, and leaf solve times are typically heavy-tailed.
- The ParaSCIP report describes exactly this ramp-down: "only a few Solvers have a significant search tree remaining" while the rest finish fast (ZIB-Report 20-16, §3.1.3).
- In capopt the master cannot even *see* it, because the bound is frozen (§0.1).

**How the reference systems handle it:**
- **UG / ParaSCIP / FiberSCIP** (supervisor-worker; [UG parameters](https://ug.zib.de/doc/html/PARAMETERS.php); FiberSCIP, IJOC 30(1) 2018, [Optimization Online](https://optimization-online.org/2017/04/5944/); ZIB-Report 20-16):
  - **Collecting mode.** The load coordinator keeps at least p "heavy" nodes in its pool. A node is heavy if `(NodeBound − GlobalBound)/max(|GlobalBound|,1) < Threshold`. When the pool runs short, it switches solvers into collecting mode **in ascending order of their minimum open-node lower bound** (the worst-bound subtree first).
  - A collecting solver switches to **best-bound order** and ships its best-bound open nodes, using fewest bound changes as the tie-break (the largest region). Collecting stops when the pool exceeds 1.5·p.
  - Only one solver collects at first. One more is allowed each time the pool has been empty for 10 s. p doubles if collecting is re-triggered within 10 s.
  - **Ramp-down special case:** if fewer than 90% of solvers are active for 10 s and open nodes exceed 100 × solvers, the coordinator collects first and redistributes afterwards.
  - **Racing ramp-up:** every solver runs the root with different settings and permutations. A winner is chosen on a combination of lower bound and number of open nodes. Its open nodes are redistributed and the other trees are discarded, keeping their solutions.
  - **Checkpoint/restart with node merging:** on restart, open nodes that share fixings can be merged, which re-roots early bad branching decisions (§4.1 of the report).
- **SelfSplit** (Fischetti, Monaci, Salvagnin; CPAIOR 2014 and C&OR 93, 2018, [PDF](https://www.dei.unipd.it/~salvagni/pdf/selfsplit.pdf)) is static splitting without communication. It works well when there are many more subtrees than workers. It is capopt's current design, and its known weakness is the tail.
- **HiGHS 1.15 parallel MIP** is node-level parallel inside one tree (§1).
- **Para-B&B** ([arXiv 2604.09556](https://arxiv.org/abs/2604.09556)) is a deterministic fork of HiGHS. Workers dive from distinct nodes on replicated solver state, then merge results in a fixed order. ML models predict per-dive cost and retune per-worker dive budgets (the four-stage regression/GBDT is described in §7 of the paper). Reported results:
  - 2.17x geometric-mean speed-up on 8 threads (80 MIPLIB instances);
  - up to 5.12x on node-heavy instances;
  - 34.7% average idle rate.

  This is a C++ fork, there is no subtree stealing, and the paper gives no public repository URL **[code location unverified]**. It is not a fit for capopt's process-level tree.
- **Ralphs, Shinano, Berthold, Koch**, "Parallel Solvers for MILP" (Handbook of Parallel Constraint Reasoning, 2018; [ZIB](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/6867)) is the survey. Only the listing was checked.

**Concrete policy for capopt: when and how to re-split the worst-bound open leaf**

HiGHS cannot export its open nodes, so capopt cannot "collect" nodes. The emulation is to **split a running leaf at the master and run the children alongside it (a hedge)**, then kill whichever side loses.

- **Live bounds.** Each worker writes `model.user(mip_dual_bound)` and the node count to a shared `mp.Array` slot from `on_interrupt`. The master's bound is the minimum over closed leaves, queued leaves and *live* running bounds.
- **Trigger.** Fire when either:
  - a worker is idle and the queue is empty (ramp-down); or
  - the same running leaf has defined the global bound for more than Δ = 30 s.
- **Target.** The running leaf with the **lowest live bound** (ParaSCIP's ascending-bound order). It must:
  - have run at least τ = 20-30 s (to amortise the leaf's presolve, root cuts and SB warm-up); and
  - have a projected remaining time above about 2τ. Project it from the rate of change of its live gap; Hendel et al. show gap-based projections are crude but usable ([IJOC 2022](https://ideas.repec.org/a/inm/orijoc/v34y2022i2p934-952.html); [Optimization Online](https://optimization-online.org/2020/04/7722/)).
- **How to split.**
  - Use the master's warm LP with the target's splits.
  - Run full SB over the top K candidates (variable splits, plus E7 disjunctions if adopted). Apply any one-sided fixings, then pick the best product-score split. Two-level splits (4 children) are fine if more workers are idle.
  - Each child bound = `max(child LP, parent live bound)`. Each child starts with `objective_bound` = the shared incumbent (proof phase, E2).
- **Hedge, not kill.**
  - Keep the parent running while idle workers exist.
  - The subtree closes when the parent finishes, or when all its children finish. Its bound = `max(parent live, min(children))`.
  - Kill the parent (through a per-slot stop flag in `on_interrupt`) only if its workers are needed for queued work with a lower bound. The same check applies recursively to children.
- **Why exp03 failed and this may not.** exp03 sliced *every* leaf every 10 s, discarded the parent bound, and restarted without the incumbent. This policy splits only the bottleneck, only when workers are idle, and keeps the parent's work.

## 5. Bound-only techniques usable alongside

1. **Root-with-cuts bound per queued leaf.**
   - Run `mip_max_nodes=1` on each leaf (seconds each).
   - This gives presolve, cuts and reduced-cost fixing under the leaf's fixings. The resulting root bound is a tighter and still valid leaf bound than its LP bound.
   - Use it to reorder the queue (hardest, i.e. lowest bound, first; this is the classic LPT rule for makespan) and to decide in advance which leaves to pre-split.
2. **Master-side LP-tree bounding of the bottleneck leaf.**
   - While the master idles (it already has an `idle` hook), it can run best-first LP B&B with SB *on the worst running leaf*.
   - The leaf's bound becomes `max(HiGHS live bound, min over the master's LP frontier)`, which is valid.
   - The LP costs about 0.1 s warm. This mostly matters early; HiGHS with cuts will usually win later.
3. **Leaf-local cut loops.** Run capopt's lifted GUB-cover loop (`cuts.py`) on the leaf's LP with the leaf fixings, since cuts are much stronger under fixings than at the root. Ship them as rows with the leaf.
4. **Which subtree to attack.** Estimate it from these, in order:
   - (a) the live bound (lowest first);
   - (b) the projected remaining time (gap-closure rate);
   - (c) the leaf's root-with-cuts gap.

   ParaSCIP uses (a) only, and that is a good default.
5. **Year-block Dantzig-Wolfe bound** (`HYBRID_AND_CROSSFIELD.md` H16; Caprara, Furini, Malaguti, [IJOC 2013](https://pubsonline.informs.org/doi/10.1287/ijoc.1120.0521)). This is a one-off probe of whether any bound above the LP exists cheaply. It is not an endgame tool.

## 6. Ranked experiments

| # | Experiment | Mechanism | Implementation (highspy / outer tree) | Expected effect on the endgame bound | Cheap screen (no full benchmark needed) |
|---|---|---|---|---|---|
| **E1** | **Live leaf bounds + bound inheritance** | Correct bookkeeping. The global bound becomes min(closed, queued, *live running*). Re-split children inherit the parent's MIP bound. | `mp.Array('d', workers)` and a slot id per task. `on_interrupt` writes `model.user(e.data_out.mip_dual_bound)`. In the re-split path, push `max(child.bound, r["bound"])`. About 30 lines; add a unit test. | Reported bound rises immediately to the true value (likely well above -0.15% mid-run **[expected, unmeasured]**). The bottleneck leaf becomes visible, and E4 depends on it. No change to proof time on its own. | Unit test plus one 120-s run when the machine is idle. Offline: replay a trace with a per-leaf bound log. |
| **E2** | **Phase-aware leaf cutoff** | Leaves prune against the global incumbent. Use no cutoff in the primal phase (exp05) and `objective_bound` = shared incumbent in the proof phase. | Switch to "proof phase" once the incumbent has not improved for X s, or LNS has stalled, or the gap is below about 0.2%. Leaves launched after that (and E4 children) get `objective_bound`. Optionally restart running no-cutoff leaves *once* at the switch. | Fewer nodes per leaf in the proof phase. Without a cutoff, best-estimate dives explore nodes above the incumbent. Plausibly a large reduction on hard leaves **[estimate]**. | Single thread: take the 5 lowest-bound leaves from the ramp-up and run each 120 s with and without `objective_bound` = best-known value. Compare the live bound at 30/60/120 s and node counts. About 20 min on one core. |
| **E3** | **Strong-branching ramp-up** (+ SB fixing, cash-dual candidate filter) | Better top-of-tree decisions. Product score `max(Δ−,ε)·max(Δ+,ε)`. A one-sided infeasible or cut-off child becomes a fixing. Candidates are pre-filtered by fractionality × spend in high-dual cash years. | In `_lp_node`, evaluate the top K = 20-40 candidates by `changeColsBounds` and a warm `run()` on the cached LP. Add an optional dynamic stop (Mexi et al.). Optionally split until the worst leaf's bound stops improving rather than to a fixed 60 leaves. | Higher minimum leaf bound at launch, more balanced leaves, fewer HiGHS nodes in the worst leaf. This is where a commercial solver's small tree suggests the leverage lies **[inference]**. | LP only, single thread, < 2 min: compare the min, median and spread of the 60 leaf LP bounds for the current rule and SB, and the number of fixings found. Then E2's 5-leaf screen on the new worst leaves. |
| **E4** | **Worst-bound hedged re-split** (UG collecting-mode emulation) | Spread the bottleneck subtree over idle workers without discarding the parent's search. | Policy in §4: trigger on idle workers or a stalled bound; target the lowest live bound with run time ≥ 20-30 s; SB split at the master; children inherit the bound and get the cutoff; the parent keeps running; per-slot stop flags. Needs E1. | Shortens the ramp-down tail, which is where the plateau is. Turns idle worker-seconds into bound progress. Expect the largest wall-clock gain of any item when 1-3 leaves dominate **[expected]**. | Single thread proxy: on the worst leaf, run the leaf for 300 s, then its two SB children for 150 s each. Check whether min(child bounds) beats the parent's bound at 150 s and at 300 s. Then one 300-s full run when idle. |
| **E5** | **Leaf option sweep (proof phase)** | More SB per node and less heuristic effort once the incumbent is (near) optimal. | `mip_pscost_minreliable` ∈ {8, 16, 32, 64}; `mip_heuristic_effort` ∈ {0.05, 0.01, 0}; `mip_allow_cut_separation_at_nodes`; `mip_lp_age_limit` ∈ {10, 30}; with the E2 cutoff. Applies to proof-phase leaves only. | Moderate. The SB budget cap (§1) limits the gain from `minreliable`. Heuristic effort is wasted once the incumbent is optimal. | The same 5-leaf, single-thread, 90-s protocol per setting (about 1 h on one core, or fewer settings). Check the HiGHS log for SB iterations and the degeneracy cut-off. |
| **E6** | **Endgame racing on the last leaf** | Bounds on the same subproblem combine by max. Exploit erraticism and the HiGHS 1.15 parallel MIP. | When ≤ 2 leaves are open and ≥ 4 workers are idle, launch copies of the bottleneck leaf: different `random_seed`, pulse vs step encoding, and one `threads=k` HiGHS run. Leaf bound = max of the live bounds; close when any copy proves. | Cuts the tail variance of the last leaf. It is complementary to E4 (E4 splits, E6 races). | Worst leaf, 4 seeds × 60 s single thread: the spread of the bound at 60 s. If it is wide, racing pays. Plus `threads=4` vs 1 for 60 s (short, 4 cores). |
| **E7** | **General-disjunction candidates in the outer tree** | Count disjunctions `N_t(S)`, pair-order disjunctions, and possibly Gomory-based disjunctions (Karamanov and Cornuéjols). | Extend `Split` with `(cols, vals, side, rhs)` rows. The master LP adds and deletes a temporary row per SB evaluation. Leaves `addRow`. | Uncertain. The literature shows smaller trees on most MIPLIB instances, but nothing specific to scheduling. | LP only, at the root and at depth ≤ 3: is the best disjunction's SB product score better than the best variable split? If not, drop it (minutes). |
| **E8** | **Leaf difficulty census and scheduling** | Predict hard leaves and schedule them first or pre-split them. | Per leaf: LP bound, `mip_max_nodes=1` root bound, and the HiGHS bound after 10 s single thread. Queue by root bound (lowest first). Pre-split leaves whose root gap is in the worst decile. | Small to moderate on its own. It provides the data to tune E2, E4 and E5. | This is the shared screening dataset: 60 leaves × (root + 10 s) single thread, about 15 min on one core. |

**Suggested order:**
1. E1 (needed for everything else).
2. E8 census and E2 screen (the same harness).
3. E3.
4. E4.
5. E5 and E6.
6. E7 last.

E1-E4 together give the UG-style design: best-bound balancing with live bounds.

## 7. What would need a HiGHS C++ change (fork)

| Change | Size | Value |
|---|---|---|
| **Dynamic cutoff:** a `data_in` field (or a user-solution-like hook) to tighten `upper_limit` mid-solve from the shared incumbent | Small. `HighsCallbackDataIn` has no such field today. | **High.** It removes E2's phase heuristic and restart cost. This is the best-value fork. |
| Node-selection policy switch (pure best-bound in the proof phase) | Small (`installNodes`) | Medium. With an optimal incumbent the set of nodes that must be explored barely depends on order. The gain is mostly earlier bound movement and better pseudocost learning. |
| Pseudocost export/import (warm-start leaves with the master's or siblings' statistics) | Medium | Medium. It saves each leaf's SB warm-up. |
| Open-node export or subtree hand-off (true UG-style collecting and work stealing) | Large | High in theory. E4's hedge is the no-fork approximation. |
| Branching callback / SOS or GUB branching / general disjunctions in the tree | Large | Medium. The step encoding and the outer tree already cover most of it. |
| Local cuts / lazy constraints | Large | Low to medium for this model. |
| Para-B&B (deterministic parallel dives) | A fork already exists (research code) | Low for capopt (node-level, not subtree-level). |

## Sources

- Achterberg, Koch, Martin, "Branching rules revisited", ORL 2005: https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/788
- Achterberg, Berthold, "Hybrid branching", CPAIOR 2009: https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/2823
- Achterberg, "Constraint Integer Programming" (thesis): https://opus4.kobv.de/opus4-zib/frontdoor/deliver/index/docId/1018/file/Achterberg_Constraint_Integer_ProgrammingDissZweite_Vers.pdf
- Achterberg, "Conflict analysis in MIP": https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/853
- Witzig, Berthold, Heinz, "Experiments with conflict analysis in MIP": https://arxiv.org/abs/1611.07887
- Cut-based conflict analysis in MIP: https://arxiv.org/abs/2410.15110
- Status report on conflict analysis in MINLP (source of the 28% figure attributed to Achterberg and Wunderling 2013): https://arxiv.org/abs/1902.02615
- Achterberg, Wunderling, "MIP: analyzing 12 years of progress" (listing only): https://ideas.repec.org/h/spr/sprchp/978-3-642-38189-8_18.html
- Gamrath, "Improving strong branching by domain propagation": https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/4326
- Mexi, Shamsi, Besançon, Le Bodic, "Probabilistic lookahead strong branching": https://arxiv.org/abs/2312.07041
- SCIP Optimization Suite 10.0: https://arxiv.org/abs/2511.18580
- Karamanov, Cornuéjols, "Branching on general disjunctions" (abstract listing): https://academicnewsletter.sufe.edu.cn/info/399033
- Cornuéjols, Liberti, Nannicini, "Improved strategies for branching on general disjunctions": https://researchportal.ip-paris.fr/en/publications/improved-strategies-for-branching-on-general-disjunctions/
- Mahajan, Ralphs, "Experiments with branching using general disjunctions": https://optimization-online.org/?p=10468
- Ryan-Foster branching (SCIP documentation): https://scipopt.org/doc/html/BINPACKING_BRANCHING.php
- Ostrowski, Linderoth, Rossi, Smriglio, "Orbital branching" (TR): https://research.cs.wisc.edu/techreports/2008/TR1641.pdf
- Artigues, "On the strength of time-indexed formulations for the RCPSP": https://hal-univ-tlse3.archives-ouvertes.fr/hal-01461447
- Anderson, Hendel, Le Bodic, Viernickel, "Clairvoyant restarts": https://ojs.aaai.org/index.php/AAAI/article/view/3944
- Hendel, Anderson, Le Bodic, Pfetsch, "Estimating the size of B&B trees", IJOC 2022: https://ideas.repec.org/a/inm/orijoc/v34y2022i2p934-952.html and https://optimization-online.org/2020/04/7722/
- Chvátal, Cook, Espinoza, "Local cuts for MIP": https://math.uwaterloo.ca/~bico/papers/local_cuts.pdf
- Berthold, Stuckey, Witzig, "Local rapid learning": https://arxiv.org/abs/1902.02587
- Fischetti, Monaci, "Exploiting erraticism in search": https://pubsonline.informs.org/doi/10.1287/opre.2013.1231
- Shinano et al., "Solving previously unsolved MIP instances with ParaSCIP ... 80,000 cores", ZIB-Report 20-16: https://opus4.kobv.de/opus4-zib/frontdoor/deliver/index/docId/7839/file/ZIB-Report_20-16.pdf
- UG parameters (RampUpPhaseProcess, collecting mode): https://ug.zib.de/doc/html/PARAMETERS.php
- Shinano, Heinz, Vigerske, Winkler, "FiberSCIP", IJOC 30(1) 2018: https://optimization-online.org/2017/04/5944/
- Ralphs, Shinano, Berthold, Koch, "Parallel solvers for MILP" (listing): https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/6867
- Fischetti, Monaci, Salvagnin, "Self-splitting of workload in parallel computation": https://www.dei.unipd.it/~salvagni/pdf/selfsplit.pdf
- Zhang et al., "Para-B&B", arXiv 2604.09556: https://arxiv.org/abs/2604.09556
- HiGHS v1.15.0 release notes: https://newreleases.io/project/github/ERGO-Code/HiGHS/release/v1.15.0
- HiGHS workshop slides 2026 (parallel MIP design): https://jump.dev/assets/jump-dev-workshops/2026/slides_julian.pdf
- HiGHS sources (read 2026-10-15, `master`): https://github.com/ERGO-Code/HiGHS/tree/master/highs/mip (`HighsPseudocost.h`, `HighsSearch.cpp`, `HighsMipSolver.cpp`, `HighsMipSolverData.cpp`) and `highs/lp_data/HighsCallbackStruct.h`
- Caprara, Furini, Malaguti, "Uncommon DW reformulation for the temporal knapsack problem": https://pubsonline.informs.org/doi/10.1287/ijoc.1120.0521
