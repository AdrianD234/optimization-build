# MILP frontier review for the capital-programme scheduler

**Date:** 2026-10-15
**Scope:** Literature and benchmark review to guide a HiGHS- or SCIP-based optimiser for the capital-programme scheduling problem. The problem has 100 projects, a 60-year horizon, a time-indexed start-year choice, a cumulative funding balance that must stay non-negative, and an objective made of holding cost (with convex piecewise penalty bands weighted 1e3 to 1e6) minus the PV of benefits. The target is to beat a commercial solver's time to proven optimality on the penalised instances.

## Method

- **Searches:** I ran web searches and fetched pages over arXiv, Optimization Online, MPC (Kobv/Springer), INFORMS (IJOC, OR, MS), Dagstuhl LIPIcs (CP), PMLR/NeurIPS/ICLR, the HiGHS docs and release notes, and Mittelmann's benchmark pages.
- **Query themes:** LP-free and first-order primal heuristics, LNS (adaptive and ML-guided), cut selection, branching, parallel and racing MIP, HiGHS and SCIP releases, time-indexed (pulse and step) formulations, RCPSP and RCPSP-DC (NPV), Lagrangian relaxation and minimum cut, Dantzig-Wolfe and column generation for time-indexed and temporal-knapsack models, incremental knapsack, open-pit mine scheduling (which is structurally very close: NPV plus per-period capacity plus "by-time" variables), and finance-based (credit-limited) project scheduling.
- **Citation rule:** Every entry below was found during this search session and has a working URL. When a detail (year, venue, author order) could only be confirmed from an abstract or listing, that is noted. Nothing is cited from memory alone.

### Structural observations that drive the ranking

These come from my own analysis of the model, not from any one paper.

1. **The LP bound equals the Lagrangian bound.** Each project's own constraint is `sum_s x[p,s] = 1`, a single GUB/simplex, so it has the integrality property. Dualising the cumulative budget rows (and the balance-definition rows, if the convex holding and penalty term is moved onto copy variables `net_t`) therefore gives exactly the LP bound (Geoffrion). Classic Lagrangian relaxation of the budget gives speed and a heuristic, but no bound improvement. Bound improvement has to come from cuts on the cumulative-knapsack rows, a stronger reformulation (Dantzig-Wolfe over groups of time rows, as for the temporal knapsack), or branching.
2. **Pulse and step formulations have the same LP bound.** The pulse model uses `x[p,s]` = start in `s`; the step model uses `z[p,t] = sum_{s<=t} x[p,s]`. In their disaggregated forms the two are equivalent (Artigues 2017), so a reformulation buys **branching balance and propagation, not a better root bound**. Branching on `x[p,s]=0` is a very weak, unbalanced dichotomy. Branching on `z[p,t]` ("start by t" versus "after t") is the SOS1/GUB dichotomy. HiGHS has no SOS branching, so reformulating is the only way to get it there.
3. **Proving optimality is mostly about the bound, not finding solutions.** With 100 projects, good incumbents should come quickly from Feasibility Jump, RINS or a custom greedy. The time a commercial solver spends are probably spent closing the gap. Expect the largest gains from formulation, cuts, branching and parallel racing, and smaller ones from fancier primal heuristics.
4. **Cliff-like penalty weights (up to 1e6) are a numerics risk.** They inflate the objective dynamic range, which hurts reduced-cost fixing, gap tolerances and LP conditioning (see Miltenberger, Ralphs and Steffy).

## Calibration: HiGHS against commercial solvers

- **Current Mittelmann MILP benchmark** ([plato.asu.edu/ftp/milp.html](https://plato.asu.edu/ftp/milp.html)): page dated 10 Oct 2026; 240 MIPLIB2017 benchmark instances; Ryzen 9 5900X (12 cores); 2-hour limit. Scaled shifted geometric mean of run time, with instances solved:

  | Solver | Scaled mean time | Solved (of 240) |
  |---|---|---|
  | COPT 8.0.3 | 1 | 219 |
  | Optverse 2.0.1 | 1.72 | 210 |
  | HiGHS 1.15.1, parallel | 5.44 | 179 |
  | HiGHS 1.15.0 | 7.55 | 158 |
  | SCIP/SoPlex 10.0.0 | 9.93 | 136 |

  MindOpt and Gurobi withdrew from the benchmarks in 2024 ([plato.asu.edu/bench.html](https://plato.asu.edu/bench.html)), so there is no direct MindOpt-vs-HiGHS number. If MindOpt is roughly in the top commercial tier, **out-of-the-box HiGHS should be expected to be about 3-8x slower on a generic MIP**. Beating MindOpt therefore depends on exploiting the problem's structure and the 20 threads.
- **HiGHS 1.15** shipped the first (prototype) parallel MIP solver, which runs parallel primal heuristics and parallel dives. 1.15.1 stopped using parallel simplex inside the MIP solver ([v1.15.0 notes](https://newreleases.io/project/github/ERGO-Code/HiGHS/release/v1.15.0), [v1.15.1 notes](https://newreleases.io/project/github/ERGO-Code/HiGHS/release/v1.15.1)). Feasibility Jump was added during 2025 ([JuMP Open Energy Modeling update, Jul 2025](https://jump.dev/open-energy-modeling/2025/07/21/oem_update/)).
- **HiGHS options worth sweeping** ([option list](https://ergo-code.github.io/HiGHS/dev/options/definitions/)): `random_seed`, `threads`, `parallel`, `mip_heuristic_effort` (default 0.05), `mip_heuristic_run_{feasibility_jump,rins,rens,root_reduced_cost,zi_round,shifting}`, `mip_pscost_minreliable` (reliability threshold, default 8), `mip_lp_age_limit`, `mip_pool_age_limit`, `mip_pool_soft_limit`, `mip_allow_restart`, `mip_detect_symmetry`, `mip_lp_solver`, `mip_rel_gap`, `mip_abs_gap`, `mip_feasibility_tolerance`.
- **HiGHS callbacks** ([callbacks docs](https://ergo-code.github.io/HiGHS/dev/callbacks/)): there is a MIP *user solution* callback for injecting external incumbents, a MIP *cut pool* callback that is read-only after the root, a MIP interrupt callback, and improving-solution callbacks. **There is no user-cut or lazy-constraint callback** (lazy constraints are on the roadmap), so any custom cuts must be added as rows before the solve or in an outer cut loop.

## Paper table

Applicability score: 5 = directly usable and likely high impact for our instances; 1 = little relevance at this scale or setting.

| # | Title | Authors | Year | Venue / id | URL | Key idea | Applicability |
|---|---|---|---|---|---|---|---|
| 1 | On the strength of time-indexed formulations for the RCPSP | C. Artigues | 2017 | Oper. Res. Letters, doi 10.1016/j.orl.2017.02.001 | [HAL](https://hal-univ-tlse3.archives-ouvertes.fr/hal-01461447) | Compares pulse, step and on/off time-indexed RCPSP models. The disaggregated pulse, step and on/off forms are LP-equivalent. | **5**: our model is exactly this family. It shows the step reform gives no LP gain, only branching and propagation gains. |
| 2 | A time indexed formulation of non-preemptive single machine scheduling problems | J.P. de Sousa, L.A. Wolsey | 1992 | Math. Prog. 54(3):353-367 | [IAOR](https://iaorifors.com/paper/8382) | The foundational time-indexed formulation. Its valid inequalities come from aggregating constraints and from knapsack-with-GUB structure. | **4**: knapsack-with-GUB cuts map directly onto cumulative budget rows over `sum_s x[p,s]=1`. |
| 3 | Strong Bounds for RCPSP: Preprocessing and Cutting Planes | J.A.S. Araujo, H.G. Santos, B. Gendron, S.D. Jena, S.S. Brito, D.S. Souza | 2019 | arXiv 1909.02737 | [arXiv](https://arxiv.org/abs/1909.02737) | Preprocessing that tightens resource rows, lifted precedence and cover cuts, and conflict-graph clique, odd-hole and CG cuts. A MILP solver then closed 754 open RCPSP instances. | **4**: the closest recipe for strengthening time-indexed cumulative-resource rows before handing them to a MIP solver. |
| 4 | Lifted Cover Inequalities for 0-1 Integer Programs: Computation | Z. Gu, G.L. Nemhauser, M.W.P. Savelsbergh | 1998 | INFORMS JoC 10(4):427-437 | [INFORMS](https://pubsonline.informs.org/doi/10.1287/ijoc.10.4.427) | Practical separation strategies for lifted cover and lifted **GUB** cover inequalities. | **4**: each year's budget row is a knapsack over GUB sets (one start per project). Lifted GUB covers are the textbook cut here. |
| 5 | Uncommon Dantzig-Wolfe Reformulation for the Temporal Knapsack Problem | A. Caprara, F. Furini, E. Malaguti | 2013 | INFORMS JoC 25:560-571 | [INFORMS](https://pubsonline.informs.org/doi/10.1287/ijoc.1120.0521) | DW where subproblems are **groups of consecutive time constraints**, solved by a MIP solver. DW on single constraints performs very poorly. | **4**: our nested cumulative rows are a temporal-knapsack-like structure. Grouping consecutive years is a credible route to a bound stronger than the LP. |
| 6 | Approximating the Incremental Knapsack Problem | F. Della Croce, U. Pferschy, R. Scatamacchia | 2019 | Discrete Appl. Math.; arXiv 1801.04801 | [arXiv](https://arxiv.org/abs/1801.04801) | Capacity grows over periods, inserted items stay, and profit is time-weighted. PTAS and LP-based bounds. | **3**: the closest pure analogue of a cumulative budget with time-dependent value. Mainly gives theory and bounds. |
| 7 | Solving Project Scheduling Problems by Minimum Cut Computations | R.H. Möhring, A.S. Schulz, F. Stork, M. Uetz | 2003 | Management Science (pp. 330-350) | [TU Berlin](https://depositonce.tu-berlin.de/items/230c4e59-b10d-41f2-95d2-7fd01950c9a7) | Lagrangian relaxation of resource rows in a time-indexed model reduces to a min-cut, giving strong bounds plus relaxation-based list scheduling. | **3**: with no precedences our Lagrangian subproblem is trivial (pick the best start per project). The relaxation-guided list-scheduling heuristic transfers. |
| 8 | Maximizing the NPV of a project under resource constraints using a Lagrangian relaxation based heuristic with tight upper bounds | A. Kimms | 2001 | Annals of OR 102:221-236 | [IAOR](https://iaorifors.com/paper/35779) | Lagrangian relaxation of resource constraints for NPV RCPSP gives tight upper bounds and a near-optimal heuristic. | **3**: same objective type (NPV under resource limits). The bound only equals the LP bound in our case, but the heuristic is useful. |
| 9 | Maximising the net present value of large resource-constrained projects | H. Gu, P.J. Stuckey, M.G. Wallace | 2012 | CP 2012, LNCS 7514:767-781 | [PDF](https://people.eng.unimelb.edu.au/pstuckey/papers/npv_decomp_paper.pdf) | Lagrangian decomposition for RCPSP-DC with hierarchical subgradient and a parallel implementation. Best known solutions on mining instances with 11k activities over 20+ years. | **3**: confirms Lagrangian plus parallel works for long-horizon NPV scheduling. Mainly a primal and bounding tool. |
| 10 | Solving LP relaxations of large-scale precedence constrained problems (BZ algorithm), and A study of the Bienstock-Zuckerberg algorithm | D. Bienstock, M. Zuckerberg (IPCO 2010); G. Muñoz et al. (2017) | 2010 / 2017 | IPCO 2010; Comput. Optim. Appl. (doi 10.1007/s10589-017-9946-1); arXiv 1607.01104 | [arXiv](https://arxiv.org/abs/1607.01104) | Very fast LP solving for "by-time" (step) NPV scheduling with per-period capacity. Recast as column generation and applied to RCPSP. | **3**: our LP is small, so speed matters less. The step-variable modelling style it validates is the point. |
| 11 | A New Algorithm for the Open-Pit Mine Production Scheduling Problem | R. Chicoisne, D. Espinoza, M. Goycoolea, E. Moreno, E. Rubio | 2012 | Operations Research 60(3):517-528 | [RePEc](https://ideas.repec.org/a/inm/oropre/v60y2012i3p517-528.html) | Decomposition and pseudoflow LP solve, then a topological-sort rounding heuristic for NPV scheduling with per-period capacity. | **3**: "LP solution, then order-based rounding" is a strong, cheap incumbent generator for our problem. |
| 12 | Time-Indexed Formulations for Machine Scheduling Problems: Column Generation | J.M. van den Akker, C.A.J. Hurkens, M.W.P. Savelsbergh | 2000 | INFORMS JoC 12(2):111-124 | [INFORMS](https://pubsonline.informs.org/doi/10.1287/ijoc.12.2.111.11896) | DW and column generation to tame time-indexed size, while still allowing cuts. | **3**: relevant if we pursue DW. Per-project DW alone gives no gain (integrality property), so grouping is needed (see #5). |
| 13 | A Bucket Indexed Formulation for Nonpreemptive Single Machine Scheduling Problems | N. Boland, R. Clement, H. Waterer | 2016 | INFORMS JoC (doi 10.1287/ijoc.2015.0661) | [INFORMS](https://pubsonline.informs.org/doi/10.1287/ijoc.2015.0661) | Coarser time buckets give much smaller models with comparable strength. | **2**: a 60-year horizon is already small. Possibly useful for a coarse warm-start or bounding model. |
| 14 | Multi-objective optimization for the multi-mode finance-based project scheduling problem | S. Al-Shihabi, M. AlDurgam | 2020 | Frontiers of Eng. Mgmt | [HEP](https://journal.hep.com.cn/fem/EN/1160052414378206064) | MILP for finance-based scheduling, where the cash position must stay within a credit limit in every period. Notes the field mostly abandoned exact methods. | **3**: same "cash balance must stay within a limit" constraint. Gives modelling reference and little algorithmic novelty. |
| 15 | Exploiting Erraticism in Search | M. Fischetti, M. Monaci | 2014 | Operations Research 62(1):114-122 | [INFORMS](https://pubsonline.informs.org/doi/10.1287/opre.2013.1231) | MIP run time varies chaotically with seed. Bet-and-run over several randomised short runs exploits this. | **4**: with 20 threads, seed and formulation racing is nearly free and attacks the variance directly. |
| 16 | Para-B&B: Load-Balanced Deterministic Parallelization of Solving MIP | J. Zhang, D. Huang, Y. Liu, S. Wang, Z. Pu, Z. Liu | 2026 | arXiv 2604.09556 | [arXiv](https://arxiv.org/abs/2604.09556) | Deterministic parallel B&B for HiGHS. Geometric-mean speed-up of 2.17x on 8 threads, up to 5.12x on node-heavy instances. | **3**: an option if HiGHS 1.15's own parallelism is insufficient. Research code. |
| 17 | The SCIP Optimization Suite 10.0 | C. Hojny, M. Besançon, et al. | 2025 | arXiv 2511.18580 | [arXiv](https://arxiv.org/abs/2511.18580) | Exact rational MIP, an implied-integer presolver, cut-based conflict analysis, new heuristics, better branching and symmetry handling, and UG parallel updates. Apache 2.0 licence. | **3**: SCIP is slower than HiGHS on Mittelmann, but it allows custom branching and cut callbacks, which HiGHS lacks. |
| 18 | Presolve heuristics in HiGHS: implementation and computational study | S. Spoorendonk | 2026 | arXiv 2609.22938 | [arXiv](https://arxiv.org/abs/2609.22938) | Adds Feasibility Jump, fix-propagate-repair, LocalMIP and Scylla to HiGHS with parallel seeded workers. Primal integral 23.7% better (12.9% out of sample). Scylla and the LP dive never helped. | **4**: directly about our base solver. Improves the primal side, not the proof. |
| 19 | Feasibility Jump: an LP-free Lagrangian MIP heuristic | B. Luteberget, G. Sartor | 2023 | MPC (doi 10.1007/s12532-023-00234-8) | [Springer](https://link.springer.com/article/10.1007/s12532-023-00234-8) | Weighted-violation local search with Lagrangian-like weight bumps. Won the MIP 2022 competition and is now default in Xpress and HiGHS. | **3**: already in HiGHS (`mip_heuristic_run_feasibility_jump`). Easy to reimplement specialised for start-year moves. |
| 20 | A fix-propagate-repair heuristic for mixed integer programming | D. Salvagnin, R. Roberti, M. Fischetti | c.2024-25 (exact year unverified) | MPC | [Kobv MPC](https://opus4.kobv.de/opus4-mpc/frontdoor/index/index/docId/281) | Fix-and-propagate dives with a WalkSAT-style repair step after each fixing. A portfolio found feasible solutions for 189 of 240 MIPLIB instances in seconds. In Xpress. | **3**: a cheap diving heuristic. Our propagation is weak-ish (knapsack rows), so ranking order matters. |
| 21 | An Efficient Local Search Solver for Mixed Integer Programming (Local-MIP) | P. Lin, M. Zou, S. Cai | 2024 | CP 2024, LIPIcs 307:19 | [Dagstuhl](https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.CP.2024.19) | Two new operators (objective-driven and feasibility-driven), dynamic constraint and objective weighting, and two-level scoring. Beats FJ on solution quality. C++ code available. | **3**: a strong LP-free incumbent improver. Integrated in the patched HiGHS of #18. |
| 22 | ViolationLS: Constraint-Based Local Search in CP-SAT | T.O. Davies, F. Didier, L. Perron | 2024 | CPAIOR 2024, LNCS 14742:243-258 | [ISMP abstract](https://ismp2024.gerad.ca/schedule/FB/165) | FJ-derived CBLS with multi-variable compound moves, inside CP-SAT's parallel portfolio. | **2**: only relevant if CP-SAT is used as a racing worker. |
| 23 | Structure-driven fix-and-propagate heuristics for MIP | G. Gamrath, T. Berthold, S. Heinz, M. Winkler | 2019 | MPC | [Opt. Online](https://optimization-online.org/2019/03/7129/) | Variable orderings for fix-and-propagate based on global structures (cliques, variable bounds, components). | **2**: our structure (GUBs plus knapsacks) is simple. A custom order such as benefit or cost density does as well. |
| 24 | Adaptive large neighborhood search for mixed integer programming | G. Hendel | 2022 | MPC 14(2):185-221 | [Springer](https://link.springer.com/article/10.1007/s12532-021-00209-7) | A bandit selects among 8 LNS neighbourhoods (RINS, RENS, local branching, crossover, and so on) with a generic fixing prioritisation. In SCIP. | **4**: a ready blueprint for an outer LNS that calls HiGHS sub-MIPs with problem-specific neighbourhoods (time window, project subset). |
| 25 | Exploring relaxation induced neighborhoods to improve MIP solutions (RINS) | E. Danna, E. Rothberg, C. Le Pape | 2005 | Math. Prog. 102:71-90 | [Springer](https://link.springer.com/article/10.1007/s10107-004-0518-7) | Fix variables where the incumbent and LP agree, then solve the sub-MIP. Also guided dives. | **3**: on by default in HiGHS. Also a natural neighbourhood in a custom LNS. |
| 26 | Local branching | M. Fischetti, A. Lodi | 2003 | Math. Prog. 98:23-47 | [Polytechnique](https://publications.polymtl.ca/25914/) | Hamming-ball soft-fixing constraints define neighbourhoods inside a black-box MIP. | **3**: a natural move here is "at most k projects change start year". |
| 27 | RENS: the optimal rounding | T. Berthold | 2014 | MPC (doi 10.1007/s12532-013-0060-9) | [ZIB](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/1520) | Sub-MIP restricted to the roundings of the LP solution. | **2**: in HiGHS already. With GUB rows the LP usually splits each project over 2-3 starts, so RENS is cheap and sometimes very effective. |
| 28 | Proximity search for 0-1 mixed-integer convex programming | M. Fischetti, M. Monaci | 2014 | J. Heuristics 20(6):709-731 | [Unibo](https://cris.unibo.it/handle/11585/542450) | Replace the objective by Hamming distance to the incumbent, add an improvement cutoff, and iterate. | **3**: good for polishing when objective weights are ugly (1e6 bands), since it avoids the objective in the sub-MIP LP. |
| 29 | Branching rules revisited | T. Achterberg, T. Koch, A. Martin | 2005 | Oper. Res. Letters 33(1):42-54 | [ZIB](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/788) | Reliability branching: strong-branch until the pseudocosts are reliable. | **3**: HiGHS exposes the reliability threshold (`mip_pscost_minreliable`). Worth tuning, especially after the step reformulation. |
| 30 | Exploring the Numerics of Branch-and-Cut for MILP | M. Miltenberger, T. Ralphs, D.E. Steffy | 2018 | OR Proceedings 2017; arXiv 1803.03455 | [arXiv](https://arxiv.org/abs/1803.03455) | How LP conditioning evolves under cuts and branching, and why it matters. | **3**: our 1e3-1e6 penalty weights create exactly this risk. Supports rescaling and hard-constraint conversion. |
| 31 | Automated Configuration of MIP Solvers | F. Hutter, H.H. Hoos, K. Leyton-Brown | 2010 | CPAIOR 2010 | [PDF](https://www.cs.ubc.ca/~hutter/papers/10-CPAIOR-MIP-Config.pdf) | ParamILS-based configuration gives large speed-ups on homogeneous instance families and beats the CPLEX tuner. | **3**: we have a homogeneous family (variants of one programme). Tuning HiGHS parameters is cheap. |
| 32 | Scylla: a matrix-free fix-propagate-and-project heuristic for MIO | G. Mexi, M. Besançon, S. Bolusani, A. Chmiela, A. Hoen, A. Gleixner | 2023/25 | arXiv 2307.03466; OR Proc. 2023 | [arXiv](https://arxiv.org/abs/2307.03466) | Feasibility pump with PDLP approximate projections and fix-and-propagate rounding. Good when the LP is hard. | **1**: our LP is easy, and #18 found Scylla never helped in HiGHS. |
| 33 | Fix-and-Propagate Heuristics Using Low-Precision First-Order LP Solutions for Large-Scale MILP | N.-C. Kempke, T. Koch | 2025 | arXiv 2503.10344 | [arXiv](https://arxiv.org/abs/2503.10344) | Low-accuracy GPU PDLP solutions are enough to guide fix-and-propagate. Huge energy models. | **1**: aimed at problems far larger than ours. |
| 34 | GPU-Accelerated Primal Heuristics for Mixed Integer Programming | A. Çördük, P. Sielski, A. Boucher, K. Aatish | 2025 | arXiv 2510.20499 (NeurIPS 2025 workshop) | [arXiv](https://arxiv.org/abs/2510.20499) | GPU PDLP plus a probing cache, with GPU versions of FP, FJ and fix-and-propagate. | **1**: GPU is not needed at 6k binaries. |
| 35 | cuPDLP.jl: GPU restarted PDHG for LP | H. Lu, J. Yang | 2023/25 | arXiv 2311.12180; Oper. Res. 73(6) | [arXiv](https://arxiv.org/abs/2311.12180) | First-order LP on GPU that is competitive with commercial LP solvers at scale. | **1**: the LP is tiny, so dual simplex wins. Listed for completeness. |
| 36 | Exact Combinatorial Optimization with Graph Convolutional Neural Networks | M. Gasse, D. Chételat, N. Ferroni, L. Charlin, A. Lodi | 2019 | NeurIPS 2019; arXiv 1906.01629 | [arXiv](https://arxiv.org/abs/1906.01629) | GNN imitation of strong branching. Generalises to larger instances. | **1**: needs solver branching hooks (SCIP) and lots of training data. Gains are unclear for a 4-minute target. |
| 37 | Learning Cut Selection for MILP via Hierarchical Sequence Model (HEM) | Z. Wang, X. Li, J. Wang, et al. | 2023 | ICLR 2023; arXiv 2302.00244 | [arXiv](https://arxiv.org/abs/2302.00244) | RL learns how many cuts to select, which ones, and in what order. | **1**: HiGHS exposes no cut-selection hook. |
| 38 | A GNN-Guided Predict-and-Search Framework for MILP | Q. Han, L. Yang, et al. | 2023 | ICLR 2023; arXiv 2302.05636 | [arXiv](https://arxiv.org/abs/2302.05636) | Predict variable marginals, then solve a trust-region sub-MIP around the prediction. | **2**: with many solved variants we could predict start years for warm starts. That helps the primal side only. |
| 39 | Searching Large Neighborhoods for ILPs with Contrastive Learning (CL-LNS) | T. Huang, A. Ferber, Y. Tian, B. Dilkina, B. Steiner | 2023 | ICML 2023; arXiv 2302.01578 | [PMLR](https://proceedings.mlr.press/v202/huang23g.html) | Learns neighbourhood selection for LNS from expert samples. | **2**: could replace bandit selection later. Not a priority. |
| 40 | Learning a Large Neighborhood Search Algorithm for MIPs | N. Sonnerat, P. Wang, I. Ktena, S. Bartunov, V. Nair | 2021 | arXiv 2107.10201 | [arXiv](https://arxiv.org/abs/2107.10201) | Neural diving for the start, then learned neighbourhood selection solved with a MIP solver. | **2**: same caveat as #39. |
| 41 | PyJobShop (CP-SAT vs CP Optimizer on 9,000+ scheduling instances) | (authors per arXiv) | 2025 | arXiv 2502.13483 | [arXiv](https://arxiv.org/abs/2502.13483) | OR-Tools CP-SAT is highly competitive on project scheduling, while CP Optimizer is stronger on large and permutation problems. | **2**: evidence that CP-SAT is a credible extra racing worker. Our objective is LP-heavy, which favours MIP. |

Notes on the table:

- Author lists marked "et al." or "(authors per arXiv)" were not fully confirmed in my fetches.
- For #20 the venue (MPC) is confirmed but the exact year is not.
- For #7 the author order varies across listings. The TU Berlin preprint order is used.

## Ranked ideas to test

Ranked by expected impact on **time to proven optimality** for the penalised instances. The "expected effect" figures are my estimates from the literature plus the structure above, not measured results. Measure every idea with the same protocol:

- **Instances and seeds:** the penalised variants, 5 seeds each.
- **Metrics:** shifted geometric mean of time to optimal at `mip_rel_gap=0` (or the tolerance MindOpt used), node count, root gap closed `(z_root - z_LP)/(z_opt - z_LP)`, and the primal integral.
- **Baseline:** the same model in stock highspy 1.15.x, plus the commercial-solver reference (kept privately).

### 1. Step ("started-by") reformulation and branching on it
- **Implement.** Replace or augment the pulse variables `x[p,s]` with `z[p,t] = sum_{s<=t} x[p,s]`.
  - Constraints become `z[p,t] <= z[p,t+1]` and `z[p,T_last] = 1`.
  - Cumulative spend becomes `CumSpend_t = sum_p sum_s z[p,s]*c_p(t-s)`, by Abel summation of the cumulative profile.
  - Benefits become `PV = sum_p sum_t z[p,t]*(B_p(t) - B_p(t+1))`.
  - Run it as a separate model in the race (idea 3), and try a hybrid that keeps `x` linked by `x[p,s] = z[p,s]-z[p,s-1]`.
- **Backed by.** #1 (Artigues: LP-equivalent, so any gain is from branching and propagation), #2, #10 and #11 (mining practice uses "by" variables for NPV with per-period capacity), and the GUB/SOS-branching rationale.
- **Expected effect.** Same root bound. Much more balanced branching ("start by t" against "after t"), stronger bound propagation, and smaller trees. Plausibly a 1.5-5x node reduction. This is the single highest-leverage change available in HiGHS, which has no SOS branching.
- **How to measure.** Node count and time against pulse, with identical everything else. Check that the root LP bound is equal, which validates the model.

### 2. Strengthen the root with problem-specific cuts in an outer cut loop
- **Implement.** A Python separation loop before the final HiGHS call: solve the LP, separate, add rows, repeat, then solve the MIP with the strengthened rows.
  - **(a) Lifted GUB cover cuts** on each cumulative budget row `t`. Items are (project, start) pairs, and the GUB set is one start per project. Separate exactly with a small knapsack DP, since there are about 100 items per row.
  - **(b) Cover cuts on aggregated windows.** Use differences of cumulative rows (spend in years `[t1,t2]` ≤ funding in `[t1,t2]` plus the balance carried in), only where valid with the `net >= 0` structure. Use the incumbent-implied bound on the balance at `t1`.
  - **(c) Conflict-graph clique cuts.** Pairs of (project, start) that cannot coexist because they jointly exceed early cumulative funding.
  - Use the read-only cut-pool callback to inspect what HiGHS already generates, and avoid duplicates.
- **Backed by.** #4 (lifted GUB covers), #2 (Sousa-Wolsey knapsack-GUB inequalities), #3 (lifted covers, cliques and preprocessing closed 754 open RCPSP instances).
- **Expected effect.** This is the only cheap way past the LP and Lagrangian bound. If the root gap is meaningful (check first), closing 30-60% of it typically cuts B&B time several-fold.
- **How to measure.** Root gap closed; total time including separation; node count.

### 3. 20-thread racing portfolio with shared incumbents
- **Implement.** Launch about 10-16 independent processes (Python `multiprocessing` on Windows needs spawn-safe entry points), mixing:
  - seeds (`random_seed`);
  - formulation (pulse, step, with or without the idea-2 cuts);
  - HiGHS parallel versus serial;
  - `mip_heuristic_effort` in {0.05, 0.2};
  - optionally one SCIP 10 and one CP-SAT worker.

  Broadcast every improving incumbent through the HiGHS **MIP user solution** callback and the improving-solution callback, via a shared queue. Stop all workers as soon as one proves optimality. Optionally use "bet-and-run": short runs, then continue the most promising.
- **Backed by.** #15 (erraticism, bet-and-run), #16 and HiGHS 1.15 parallel (Mittelmann: 5.44 parallel vs 7.55 serial), #18 (seeded parallel heuristic workers).
- **Expected effect.** Time-to-optimal becomes roughly the minimum over k runs. With typical log-normal run-time spread, expect 1.5-3x on the shifted geometric mean and a large reduction in worst cases.
- **How to measure.** Distribution of the first-proof time against the single best configuration. Report the median and the 90th percentile.

### 4. Problem-specific presolve, start-window tightening and numerics clean-up
- **Implement.**
  - **(a) Earliest feasible start per project.** The smallest `s` such that the project alone fits under cumulative funding for all `t`. Better still, account for mandatory or fixed projects.
  - **(b) Latest useful start.** From the benefit decay and holding cost, and from an incumbent-based reduced-cost bound.
  - **(c) Dominance.** If two projects have identical profiles and benefits, add symmetry-breaking order constraints.
  - **(d) Penalty bands.** Scale the objective so the coefficients span fewer orders of magnitude. Where a 1e6 band is provably never entered at the optimum (check with an LP or an incumbent bound), replace it by a hard bound on `net_t`. Hard bounds propagate; penalties do not. Set `mip_abs_gap` consistently with the scaled objective.
- **Backed by.** #3 (preprocessing of resource rows), #30 (numerics), and SCIP 10's (#17) implied-integer presolve illustrating the value of presolve.
- **Expected effect.** Fewer variables (often 20-50% of `x[p,s]` removable in such models), tighter LP rows, and fewer numerical troubles, for roughly 1.2-2x.
- **How to measure.** Variable count after presolve, LP iteration counts, numerical warnings, time.

### 5. Fast high-quality incumbent before branch-and-bound (warm start plus cutoff)
- **Implement.**
  - **(i) Construction.** An LP-guided constructive heuristic: sort projects by LP mean start, or by benefit and cost density, then list-schedule each at the earliest feasible year (the "LP then topological or order rounding" of #11, and #7's relaxation-based list scheduling).
  - **(ii) Improvement.** An outer ALNS that calls HiGHS on sub-MIPs, with 1-3 s limits:
    - time-window neighbourhoods (free all projects starting in years `[a,a+w]`);
    - project-subset neighbourhoods (free k random or most-correlated projects);
    - RINS (incumbent and LP agree);
    - local branching ("at most k starts change");
    - proximity search (#28).

    Select neighbourhoods with a bandit (#24).
  - **(iii) Handover.** Pass the best solution to the main solve with `setSolution`, plus an objective cutoff.
- **Backed by.** #24, #25, #26, #28, #11, #7, #19 and #21.
- **Expected effect.** Near-optimal incumbents in seconds. On proof-dominated instances, typically a 10-30% time saving through earlier pruning, more if HiGHS currently finds the optimum late.
- **How to measure.** Time at which the optimal incumbent is found, primal integral, and total time with versus without the warm start.

### 6. Tune HiGHS parameters on the instance family
- **Implement.** A small random or Bayesian sweep (SMAC-style, or plain random search over about 50 configs × 5 seeds) over:
  - `mip_pscost_minreliable` (0, 4, 8, 16);
  - `mip_heuristic_effort`;
  - `mip_lp_age_limit`;
  - `mip_pool_soft_limit`;
  - `mip_allow_restart`;
  - `mip_detect_symmetry`;
  - `mip_heuristic_run_zi_round` and `mip_heuristic_run_shifting`;
  - `mip_lp_solver` (simplex against ipm).

  Pick the best 3-5 configurations as racing members (idea 3).
- **Backed by.** #31 (automated configuration gives large gains on homogeneous families) and #29 (reliability branching parameter).
- **Expected effect.** 1.2-2x from the best configuration, plus diversity for the race.
- **How to measure.** Cross-validated geometric-mean time on held-out variants.

### 7. Use the patched-HiGHS primal heuristic stack (FJ, FPR, Local-MIP)
- **Implement.**
  - Build HiGHS with the heuristics from #18, if its code is public. That availability is not confirmed.
  - Otherwise, port a specialised Local-MIP or FJ in Python/numba with "shift project p by ±k years" moves and weighted violation of the cumulative rows. Feed results in through the user-solution callback.
- **Backed by.** #18, #19, #20, #21.
- **Expected effect.** Primal integral improves by about 10-25% (#18). Time-to-optimal improves only modestly, because the proof dominates.
- **How to measure.** Primal integral and time to optimal incumbent.

### 8. Lagrangian relaxation of budget and balance links for reduced-cost fixing and heuristics
- **Implement.**
  - Dualise the cumulative rows (and the `net_t` definition, with the convex penalty on copy variables). Subproblems separate into a per-project best start and a per-year 1-D convex problem in `net_t`.
  - Run the subgradient or bundle method in numpy, which takes milliseconds.
  - Use the multipliers for **Lagrangian reduced-cost fixing** of `x[p,s]` against the incumbent, and for Lagrangian list-scheduling heuristics.
- **Backed by.** #7, #8, #9.
- **Expected effect.** The bound equals the LP bound because of the integrality property, so **no bound gain**. Fixing is similar to HiGHS's root reduced-cost fixing, so the speed-up is modest (around 1.1-1.3x). The main value is a cheap, solver-independent heuristic and pre-fixing for the race.
- **How to measure.** The number of variables fixed, and the time with and without pre-fixing.

### 9. Dantzig-Wolfe over groups of consecutive years for a stronger bound
- **Implement.**
  - **Prototype.** Split the horizon into blocks of years. The pricing problem for a block is a small MIP over (project, start) pairs whose spend falls in the block, solved by HiGHS. The master links blocks via each project's GUB and the carried-over balance. Compute the DW bound only, and compare it with the LP bound.
  - **Follow-on.** If the gap closed is large, use it for bounding or as cuts derived from the DW solution. Full branch-and-price in Python is a big project.
- **Backed by.** #5 (grouped-constraint DW for the temporal knapsack), #12, and #10 (BZ as column generation).
- **Expected effect.** Potentially the strongest bound available, but uncertain and high effort. The test is cheap: one DW root solve.
- **How to measure.** DW bound against LP bound against optimum, and the time to compute.

### 10. Reuse cuts and solutions across scenario variants and runs
- **Implement.** After each solve, harvest the root cut pool (HiGHS cut-pool callback) and the optimal starts. For a sibling variant (different penalty bands, envelope F, or the PV-only variant):
  - re-add the cuts that are still valid (budget-row covers stay valid if F is unchanged);
  - warm-start from the sibling's optimum, repaired by local search.

  Solve the no-penalty variant first if it is quicker, and use it as the incumbent seed.
- **Backed by.** HiGHS callbacks docs, and the warm-start effect in #5 and #38 (predict-and-search shows how valuable a good prediction is).
- **Expected effect.** Large for batches of related runs; none for one-off solves.
- **How to measure.** Total time across a sweep of variants.

### 11. CP-SAT worker in the race
- **Implement.** Model each project start as an integer variable with element or table constraints, giving the cumulative spend as a sum of interval-dependent demands. Scale the objective to integers, carefully, because of the 1e6 weights. Run 4-6 CP-SAT workers (LP, ViolationLS and LNS) inside the portfolio.
- **Backed by.** #22 and #41, and #9's lazy-clause NPV line.
- **Expected effect.** Uncertain. CP-SAT is strong for scheduling feasibility and LNS, but our objective is LP-driven, which favours MIP bounds. It is mostly useful for diversity and incumbents.
- **How to measure.** Win rate in the race and time to optimal incumbent.

### 12. SCIP 10 with a custom step-dichotomy branching rule and cut callback
- **Implement.** In PySCIPOpt, keep the pulse model but branch on `sum_{s<=t} x[p,s]` (a GUB split at the LP-weighted median start). Add the idea-2 separators as a SCIP separator. Enable SCIP's concurrent mode or UG on 20 threads.
- **Backed by.** #17, #29, #1.
- **Expected effect.** It may match the step reformulation without changing the model, but SCIP's baseline is about 1.8x slower than HiGHS-parallel on Mittelmann. Worth it only if ideas 1 and 2 show that branching and cuts dominate.
- **How to measure.** Time against the best HiGHS configuration.

### 13. Coarse-time (bucket or aggregated) relaxation for warm start and bounds
- **Implement.** Aggregate years into 2-3-year buckets. Solve the coarse MIP fast, then disaggregate with a sub-MIP or local search, giving an incumbent and window restrictions.
- **Backed by.** #13 and #11 (aggregation in mine scheduling).
- **Expected effect.** Small to moderate. Mostly a primal tool.
- **How to measure.** Incumbent quality and time.

### 14. ML-guided search (predict-and-search, learned LNS, learned branching): only if many solved variants accumulate
- **Implement.** Train a simple model (gradient boosting on project features and the LP solution) to predict start years. Use it for a trust-region sub-MIP warm start, as in #38.
- **Backed by.** #36-#40.
- **Expected effect.** Primal side only. A low priority for proof time with 100 projects.
- **How to measure.** As in idea 5.

### 15. GPU and PDLP-based heuristics (not recommended)
- **Rationale.** Our LP (about 6k columns × about 200 rows) solves in milliseconds by dual simplex. The GPU and first-order methods (#32-#35) target LPs millions of times larger. List only to rule out.

## Gaps and caveats

- **MindOpt comparison.** I found no published MindOpt-vs-HiGHS comparison after MindOpt's 2024 withdrawal from Mittelmann's benchmarks.
- **Problem-specific literature.** Little exact-method literature exists for *capital-programme* scheduling with a cumulative budget balance plus a holding cost. The closest exact-method work is in open-pit mining (NPV with per-period capacity), the temporal or incremental knapsack, and RCPSP-DC. Finance-based scheduling papers are mostly heuristic.
- **Patched HiGHS code.** Whether Spoorendonk's patched HiGHS (#18) is publicly available was not verified.
- **Effect estimates.** The expected-effect figures above are estimates, not measured results. Validate ideas 1-4 first, since they target the proof, which is where the time goes.
