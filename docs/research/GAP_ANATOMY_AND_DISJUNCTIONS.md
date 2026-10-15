# Gap anatomy, general disjunctions and polyhedral structure

**Date:** 2026-10-15
**Question:** Where does the root LP gap of the fixed-last-year model live on the 233-project CPO data? Which branching disjunctions close it fastest? Are there inequality classes or relaxations that beat the LP? What should capopt try next?

## Setup

- **Instance.** `CPO233_s2000_P50high_startcap100`, i.e. the CPO model with the 100-starts cap that the commercial reference solved.
  - Partway through this study the default instance `CPO233_s2000_P50high` was re-extracted without the start cap. This was a user decision recorded in `docs/ITERATION_LOOP.md`.
  - The capped copy is the one whose reference schedule is a certified optimum, so all gap figures use it unless marked **uncapped**.
- **Model.** `last_year_model(inst, L=min_last_year()=30, "pulse", "cumulative")`. Pulse and step encodings have the same LP, and a "start ≤ s" split means the same thing in both.
  - 6,599 columns × 325 rows (294 rows uncapped).
  - Root LP takes 0.07 s single-threaded with presolve off.
- **Compute.**
  - Single-threaded HiGHS 1.15.1 only, run while a benchmark campaign was timing on the machine. Wall-clock seconds below are therefore noisy.
  - Every MIP was capped at 30 s. No multiprocessing was used.
  - The scripts were scratch files and are not in the repo. Each experiment is described well enough to re-implement.
- **Data rule.** Only aggregates appear here: ratios, counts and percentiles. There are no project identifiers, objective values or cash amounts.
- **Gap convention.** "Closes X%" means (bound − z_LP)/(z_opt − z_LP). The root gap is (z_opt − z_LP)/|z_opt| = **0.5653%**, or 0.5678% uncapped (measured against the same reference schedule).
- **Labels.**
  - **[M]** = measured here.
  - **[I]** = inferred or estimated.
  - **[L]** = from literature, with URL.
  - **[unverified]** = could not be confirmed from a primary source.

## Headline

1. **[M] The gap is an inter-year, many-project phenomenon, not a "few smeared projects" one.**
   - The exact LP-dual decomposition of z_opt − z_LP puts **80%** of the gap on reduced costs of the optimum's start choices, and **5 projects carry 80% of that**. Four of those 5 are LP-*integral*. All 5 are in the top 8% by spend and 7-20 years long, and the optimum moves them 1-19 years (three of them by 6 years or more).
   - Only **10%** of the gap is "cash slack the optimum leaves in years where the LP fills funding exactly". 3% is band rows and 0% is the year-0 cap.
   - The LP's fractional projects are large (spend percentile 0.60-1.00, durations 2-19 years against a median of 3). However, 8 of the 13 mix start years **3-20 years apart**, not adjacent years.
2. **[M] Single-variable "start ≤ s" splits are by far the best disjunction family.**

   | Disjunction family | Best child closes, root | Best child closes, depth 3 / 6 (of node gap) |
   |---|---|---|
   | "Start ≤ s" splits | **34%** | 39% / 70% |
   | Gomory (Karamanov-Cornuéjols) splits | 15% | 37% / 51% |
   | Pair order | ≤ 2.3% | |
   | Counts of starts by year t (all, fractional subset, big projects) | ≤ 0.03% | |
   | Starts in year t | ≤ 0.03% | |
   | Cumulative spend (unit split) | **0%** | |
   | Cumulative spend rounded to 1.6% of funding (KP-style hyperplane) | 14% | |
3. **[M] Strong branching is the lever. In an LP-only best-first tree with the optimum as cutoff:**

   | Rule | Nodes to reach a 1e-4 relative gap | Global bound after 60 expansions |
   |---|---|---|
   | Strong branching on the top-30 splits (product score) | **219** | 90% closed |
   | capopt's current ramp-up rule | 849 | 71% closed |

   - Adding Gomory splits or a strong year-block cut does not shrink the strong-branching tree.
   - All rules stall near a 1.0-1.2e-5 relative gap within budget, a near-optimal plateau.
4. **[M] Single-row structure is exhausted; multi-year structure is not.**
   - Keeping *one* year's cash row integral, with every other row priced at the LP duals, closes ≤ 1.5%. That matches the 1.6% from per-year GUB covers.
   - Keeping consecutive years integral closes more:

     | Years kept integral | Gap closed |
     |---|---|
     | 3-year block | ≤ 6.2% |
     | 5-year block | ≤ 16.6% |
     | Years 4-16 | **54.5%**, in one 25 s block MIP |

   - So no per-year facet class (lifted covers, precedence-constrained knapsack induced covers) can do much at these multipliers. Only multi-period structure can.
5. **[M] Aggregated-state DP or surrogate relaxations are worthless here.** A surrogate knapsack (all cash rows aggregated by the LP duals, with GUBs kept and solved to integrality) closes **0.00%**.

## 1. Gap anatomy (task 1)

### 1.1 Root LP (capped) [M]

| Item | Value |
|---|---|
| Gap split by objective term | PV 92.3%, holding/band penalties 7.7% |
| Fractional projects | 13 of 233; support sizes 2 (×8), 3 (×4), 6 (×1); start-year spreads 1,1,2,2,3,3,4,8,11,11,20,20,20 |
| Fractional projects' size | spend percentile 0.60-1.00 (median 0.92); durations 2-19 years (median 9; all-project median 3) |
| Binding cash rows (non-zero dual) | 17 of 31, in years 6, 8-11, 14, 18-25, 27-29; LP slack exactly 0 there |
| Year-0 cap | binds; its dual is 115 × the largest cash-row dual |
| Years carrying cash-dual weight (largest first) | 6, 18, 14, 8, 28, 19 |
| Band rows with non-zero dual | 3 (years 1-3) |
| LP-integral projects at another year in the optimum | 97 of 220: 53 earlier, 44 later; median shift 5 years, upper quartile 18 |
| Year-0 starters | LP-integral at year 0: 97; optimum: 100; overlap 83 |

**Exact decomposition of the gap.** For any feasible x̂ with row activities r̂, using the LP's reduced costs d and row duals y:

c·x̂ − z_LP = d·(x̂ − x*) + y·(r̂ − r*)

Evaluated at the reference optimum, the terms split the gap as follows (they sum to 1.000000):

| Term | Share of gap |
|---|---|
| Reduced costs of start columns | **80.0%** |
| Reduced costs of band columns | 7.5% |
| Cash rows (dual × slack the optimum leaves) | 9.6% |
| Band rows | 2.9% |
| Start-cap rows | 0.0% |
| GUB rows | 0.0% |

- The start-column term is concentrated: 2 projects carry 50%, 5 carry 80% and 16 carry 95%.
- **Caveat [I]:** the LP is dual-degenerate (254 of 325 rows have non-zero duals), so this split depends on which dual vector HiGHS returns. The *total* is exact.

**Where the LP and the optimum differ in cumulative spend**, measured as |Δ| / cumulative funding:

| Year | Δ | Direction |
|---|---|---|
| 0 | 7.6% | LP spends more; the optimum keeps 13.4% slack against the LP's 5.8% |
| 3 | 2.6% | optimum spends more |
| 1 | 2.1% | |
| 10, 8, 11 | ≈ 1% each | |

Start counts differ most in two places:
- **year 1:** LP 30.1 against the optimum's 47;
- **years 18-21:** the LP starts about 2 projects in year 18 and about 87 across 19-21, while the optimum starts 22 in year 18 and 19 across 19-21.

### 1.2 Deeper nodes [M]

These nodes come from capopt's ramp-up rule (`_lp_node`: most fractional × total spend), always following the lower-bound child.

| Node | Gap to optimum | Fractional projects (spreads) | Binding cash rows | Cap dual / max cash dual | Top dual years |
|---|---|---|---|---|---|
| Root | 0.565% | 13 (1-20) | 17 | 115 | 6, 18, 14, 8 |
| Depth 3 | 0.414% | 12 (1-22) | 14 | 33 | 6, 14, 28, 19 |
| Depth 6 | 0.318% | 17 (1-22) | 15 | 66 | 6, 10, 19, 27 |

Branching does not reduce the number of fractional projects. Fixing one big project makes 1-5 others fractional. The binding years stay in the same two clusters, years 6-14 and 18-29.

### 1.3 Hypotheses tested

| Hypothesis | Verdict | Evidence |
|---|---|---|
| H1. The gap comes from smearing a few large projects across *adjacent* years so cumulative spend matches funding exactly. | **Mostly rejected.** | [M] Fractional projects are large, and the LP has zero slack in the binding years where the optimum keeps 0.1-1% slack. But that slack effect is only 10% of the gap, and 8 of 13 fractional projects split across starts 3-20 years apart. The bulk (80%) is the optimum *re-sequencing* LP-integral big projects. |
| H2. The LP exploits integrality of a small set of x variables. | **Rejected (no small set exists).** | [M] Making the 13 fractional projects integral closes at least 34% (one split on one of them already does that). But 13-15 *new* projects then turn fractional; iterating 6 times grows the set to 58 projects. With other projects continuous, the bound stays low (HiGHS cannot even bracket these partial-integrality MIPs in 25 s; the bounds are wide). Requiring integrality only on the 5 projects that carry 80% of the reduced-cost gap closes 0.0% (solved to optimality): 4 of them are already integral in the LP. |
| H3. The year-0 cap drives the gap. | **Rejected.** | [M] The cap's term in the decomposition is 0%. Uncapped, the root gap is 0.5678% against 0.5653%, the fractional count is 11 against 13, and the disjunction ranking is unchanged (§2.3). |
| H4. The gap is a single-year knapsack effect that per-year cuts can fix. | **Rejected.** | [M] §3.2: single-year integrality at the LP duals closes ≤ 1.5%; blocks of years close up to 54.5%. |
| H5. The gap is concentrated in the mid-horizon interaction (years about 4-16) between long, large projects. | **Supported.** | [M] Block 4-16 closes 54.5%, the best 5-year blocks are 6-10 and 8-12 (12.6% and 16.6%), and the dual weight sits in years 6-14. [I] The optimum moves a few long, big projects by up to 19 years across exactly this window. |

## 2. General-disjunction branching (task 2)

### 2.1 Literature [L]

- **Owen and Mehrotra (2001)** were the first to show that general disjunctions πx ≤ π0 ∨ πx ≥ π0+1 can shrink trees. This is cited in the Karamanov-Cornuéjols abstract; the primary paper was not found in this session **[unverified details]**.
- **Karamanov and Cornuéjols**, "Branching on general disjunctions", *Math. Prog.* 128 (2011), doi 10.1007/s10107-009-0332-3 ([abstract](https://academicnewsletter.sufe.edu.cn/info/399033); [Karamanov thesis PDF](https://www.cmu.edu/tepper/programs/phd/program/assets/dissertations/2006-operations-research-karamanov-dissertation.pdf)).
  - They branch on the split disjunctions that define GMI cuts at the optimal basis, ranked through the link to intersection cuts.
  - Their trees are usually smaller than with full strong branching on variables.
- **Cornuéjols, Liberti and Nannicini**, "Improved strategies for branching on general disjunctions", *Math. Prog.* 130 (2011) 225-247 ([Springer](https://link.springer.com/article/10.1007/s10107-009-0333-2); [preprint](https://www.lix.polytechnique.fr/~liberti/branchimp.pdf)).
  - Reduced-lattice combinations of tableau rows.
  - More than 2× fewer nodes on mixed instances, with comparable time.
- **Mahajan and Ralphs.**
  - "On the complexity of selecting disjunctions in integer programming" ([SIAM, doi 10.1137/080737587](https://epubs.siam.org/doi/10.1137/080737587)): choosing the best split disjunction is NP-hard, even for binary programs.
  - "Experiments with branching using general disjunctions" ([Optimization Online](https://optimization-online.org/?p=10468)): selection posed as a MILP.
- **Krishnamoorthy and Pataki**, "Column basis reduction and decomposable knapsack problems", *Discrete Optim.* 6(3) 2009 ([arXiv 0807.1317](https://arxiv.org/abs/0807.1317)). This is branching on hyperplanes (Lenstra / Aardal-Hurkens-Lenstra):
  - knapsacks with a = pM + r are exponentially hard for variable branching, but easy when branching on px;
  - Krishnamoorthy's "cascade knapsack" paper ([arXiv 1612.08588](https://arxiv.org/abs/1612.08588)) warns that a thin direction is not always a good branching direction.

### 2.2 Which disjunctions suit this structure? (theory) [I]

A disjunction helps only if **both** children exclude the LP point **and** the LP cannot cheaply re-route around the hyperplane. The second condition means the region of near-optimal LP solutions must be thin along π.

- **Cumulative spend S_t.**
  - The coefficients are integers in cash units. Each cash row's gcd is 1, and the median coefficient is 1.4e-4 of total funding.
  - At a binding year, S_t* = cumulative funding exactly, an integer, so *no* split excludes the point.
  - At non-binding years a unit-width split is removed by a change of 1e-7 of funding: useless.
  - The data are not "decomposable" in the Krishnamoorthy-Pataki sense. The rounded variant πx = round(C/M)·x is a valid hyperplane at any unit M. It only starts to bite when M is coarse, about 1.6% of funding, where it becomes a weighted count of big projects.
- **Counts by year, N_t.**
  - Integer-valued, but the LP is flat along π: dozens of small projects can each shift a little start mass at almost zero reduced cost.
  - N_0 is already integral because the cap binds.
- **Pair order, start_p − start_q.** Excludes the point, but the LP re-routes through the other projects.
- **"Start ≤ s" for a long, large project.**
  - This is thin where it matters: the project's cash profile is many years long, so the LP cannot replace its mid-horizon spend cheaply.
  - It is the natural SOS1/GUB split, and the step encoding already makes it a single-variable branch.
- **Gomory / Karamanov-Cornuéjols splits.** Valid, and π mixes the start columns with integer slacks of the cash and cap rows. In theory they dominate the simple split on the same basic variable. In practice (§2.3) they match variable splits only at depth ≥ 3.

### 2.3 Root and deep-node strong-branching comparison [M]

**Method.**
- One LP per child.
- Candidate rows are pre-added as free rows. Each child changes one row's (or column's) bounds and is warm-started from the parent basis.
- "Best" is the best min-child bound as a share of the *node's* gap to the optimum. "Product" is the SCIP-style product score.
- All 106 fractional "start ≤ s" splits were tested at the root (the top 30 would have sufficed; see §2.4).
- Gomory splits use GMI rounding on the tableau rows of the fractional basic start columns. Integer slacks of the cash and cap rows are treated as integer variables. The split is mapped back to x-space and checked against the LP value.

| Family | Root: n / best / top-3 mean | Depth 3: best / top-3 | Depth 6: best / top-3 | One side infeasible |
|---|---|---|---|---|
| "start ≤ s" (variable) | 106 / **33.9%** / 28.8% | **39.5%** / 38.3% | **70.4%** / 54.3% | 0 |
| Gomory (KC) splits | 34 / 15.2% / 6.9% | 37.4% / 18.5% | 50.6% / 37.3% | 0 |
| Pair order (fractional pairs) | 78 / 0.67% / 0.41% | 0.33% | 2.31% | 0 |
| Count of starts by t, big projects | 23 / 0.02% | 0.01% | 0.02% | 0 |
| Count by t, fractional set | 22 / 0.00% | 0.01% | 0.03% | 0 |
| Count by t, all projects | 22 / 0.00% | 0.00% | 0.00% | 0 |
| Starts in year t | 18 / 0.00% | 0.00% | 0.00% | 0 |
| Cumulative spend, unit split | 10 / 0.00% | 0.00% | 0.00% | 0 |
| Rounded cumulative spend, unit = 1.6e-4 / 1.6e-3 / 1.6e-2 of funding | 30 each / 0.01% / 0.14% / **14.4%** | n/a | n/a | n/a |

- **Uncapped root:** variable splits 33.7%, Gomory 19.7%, count and cumulative-spend families ≤ 0.01%. One all-project count candidate and one pair candidate had a child LP that returned a non-optimal status (the script scored them as 100%); they are discarded as artefacts.
- **Per branch:** variable splits close 2.2× (root), 1.06× (depth 3) and 1.4× (depth 6) more than the best Gomory split, and 15-1000× more than any count, pair or cumulative-spend family.
- **No one-sided infeasibility** was found among about 1,800 child LPs. Strong-branching *fixings* will therefore be rare on this model.

### 2.4 Tree-level effect (LP-only best-first B&B) [M]

**Protocol.** Pure LP B&B in Python with the reference optimum as cutoff (prune tolerance 1e-9 relative), best-first. "Nodes" counts the child LPs created. Single thread, on a busy machine.

| Rule | 1e-3 gap: expansions / nodes / s | 1e-4 gap | 3e-5 gap | Closed after 60 expansions (open leaves) |
|---|---|---|---|---|
| capopt ramp-up rule (frac × spend) | 157 / 315 / 51 | 424 / **849** / 142 | 465 / 931 / 154 | 70.7% (48) |
| SB product score, top 30 candidates by that score | 40 / 81 / 38 | 109 / **219** / 99 | 129 / 259 / 115 | **89.7%** (33) |
| SB, all candidates | 40 / 81 / 74 | 109 / 219 / 182 | 129 / 259 / 214 | identical trajectory: the product-best was always in the top 30 |
| SB top 30 + 20 Gomory splits | 39 / 79 / 71 | 112 / 225 / 187 | 127 / 255 / 211 | reached 1e-5 at 295 nodes |
| SB top 30 + 2 year-block Lagrangian cuts (§3.3; root already 54.5% closed) | 45 / 91 / 92 | 113 / 227 / 161 | 132 / 265 / 179 | n/a |

- Every rule except SB + Gomory stopped at 1.0-1.2e-5 within the budget: capopt rule 1,500 expansions, SB 300. **[I]** This is a plateau of near-optimal alternatives. MindOpt's/COPT's 1e-5 target sits right at it, and HiGHS's default 1e-4 sits well above it.
- **[I]** An LP-only tree with strong branching proves 1e-4 in about 220 nodes. That is the same order as the commercial solver's roughly 1,600 nodes. HiGHS, single-threaded on the same full model, closed only 8.4% of the gap in 28 s (§3.3, all rows kept).
- **[I] Caveat:** these trees had the optimal value as cutoff. With a cutoff 1e-4 worse, the 1e-4 proof is free, but the 1e-5 proof needs the true optimum. Tree size against incumbent quality is untested.

## 3. Polyhedral structure (task 3)

### 3.1 Literature and mapping [L]/[I]

- **Capital budgeting.**
  - Lorie and Savage, "Three problems in rationing capital", *J. Business* 28 (1955) ([RePEc listing](https://ideas.repec.org/r/ucp/jnlbus/v28y1955p229.html)).
  - Weingartner, *Mathematical Programming and the Analysis of Capital Budgeting Problems* (1963) ([WorldCat](https://search.worldcat.org/title/Mathematical-programming-and-the-analysis-of-capital-budgeting-problems/oclc/408653)).
  - These are the multi-period 0-1 knapsack/LP origin of the problem. Weingartner's analysis of fractional LP projects is the same "at most one fractional item per binding row" vertex structure seen here **[book not read; from catalogue summaries]**.
- **Step encoding = chain precedence-constrained knapsack (PCKP) [I].**
  - Year t's cash row is Σ_{p,s≤t} z[p,s]·spend_p(t−s) ≤ F_t, with chains z[p,s] ≤ z[p,s+1].
  - Choosing item (p,s) forces (p,s+1..t), so per-year GUB covers are exactly PCKP *induced covers*.
  - Known PCKP classes:
    - lifted induced covers: van de Leensel, van Hoesel and van de Klundert, *Math. Prog.* 86 (1999) ([IAOR](https://iaorifors.com/paper/28960));
    - Park and Park, *DAM* 72 (1997) ([KAIST](https://koasas.kaist.ac.kr/handle/10203/7579?mode=full));
    - clique-based facets: Boland, Bley, Fricke, Froyland and Sotirov, *Math. Prog.* (2012) ([Optimization Online](https://optimization-online.org/2006/01/1322/)).
  - All of these are **single-row** classes.
- **Multi-row.**
  - Kaparis and Letchford's *global* lifted covers for the multidimensional knapsack ([EJOR 186, 2008](https://ideas.repec.org/a/eee/ejores/v186y2008i1p91-103.html); [PDF](https://www.lancaster.ac.uk/people/letchfoa/articles/2008-mkp.pdf)) use the whole matrix.
  - Incremental knapsack (nested capacities, items persist):
    - Bienstock, Sethuraman and Ye ([arXiv 1311.4563](https://arxiv.org/abs/1311.4563));
    - Faenza and Malinovic ([arXiv 1701.07299](https://arxiv.org/abs/1701.07299)).
  - These use **disjunctive** formulations over periods (approximation results, not facets).
  - The temporal-knapsack year-block Dantzig-Wolfe is Caprara, Furini and Malaguti ([IJOC 25, 2013](https://pubsonline.informs.org/doi/10.1287/ijoc.1120.0521)).
- **Time-indexed scheduling.**
  - Sousa and Wolsey, *Math. Prog.* 54 (1992) ([IAOR](https://iaorifors.com/paper/8382)).
  - van den Akker, van Hoesel and Savelsbergh, *Math. Prog.* 85 (1999) ([Maastricht](https://cris.maastrichtuniversity.nl/en/publications/a-polyhedral-approach-to-single-machine-scheduling-problems-2)). They characterise all facets with right-hand side 1 or 2.
  - These are unit-capacity (single-machine) results. Our cumulative cash resource has arbitrary integer demands, so the right-hand side 1/2 facets do not apply directly **[I]**.
- **Do known facet classes for nested knapsack rows with GUBs miss anything?** [I+M]
  - We found **no published facet class for nested cumulative knapsacks with GUB chains beyond single-row PCKP/GUB covers and their lifting**.
  - §3.2 shows single-row classes cannot help much here, whatever their strength.

### 3.2 How much can any single-row or block inequality class give? [M]

**Method.** Lagrangian bound at the root LP duals y*:

L_K(y*) = min { (c − y_D A_D)x : x ∈ X_K } + y_D·r*_D

- X_K keeps the GUBs plus the cash, band and cap rows of the years in K, with integrality. All other rows D are dualised.
- Sanity check: the LP of L_∅ equals z_LP to 1e-14.
- L_K(y*) is a **lower estimate** of the year-block Dantzig-Wolfe bound for K, because the multipliers are not re-optimised. It is also an upper bound on what any inequality class *derived from the rows in K alone* can add at these duals.

| Years kept integral | Gap closed | Solve time |
|---|---|---|
| None (GUBs only) | 0.00% | |
| One binding year (each of 17) | max **1.50%** (year 28); 14 of 17 years ≤ 0.04% | ≤ 0.5 s |
| 3-year blocks (15 windows) | max 6.2% (years 8-10); median 1.0% | ≤ 5 s |
| 5-year blocks (14 windows) | max **16.6%** (8-12); 12.6% (6-10); 7.7% (26-30) | ≤ 20 s (one hit 25 s) |
| Years 6-10 + 26-30 | 20.5% | 20 s |
| Years 6-14 | ≥ 36.0% | time limit, 28 s |
| Years 4-16 (13 years) | **54.5%** | 25 s, optimal |
| All years (= the full MIP, HiGHS 1 thread) | 8.4% | time limit, 28 s |

- The per-year GUB covers already in `cuts.py` (1.6%) sit at the single-row ceiling.
- **Lifting, PCKP facets or exact single-row separation cannot do materially better** at the root.
- Gap closure needs inequalities over **≥ 5 consecutive years**.

### 3.3 One family tested as rows: year-block Lagrangian (objective) cuts [M]

The cut is (c − y_D A_D)·x ≥ L_K(y*) − const: one dense row, valid for every feasible schedule.

- **Eight 5-year blocks:** round 1 closes **16.6%**. Rounds 2-5 add nothing: with the new duals (weight on the cut row), the block subproblems reproduce the same bound.
- **Two big blocks (4-16, 20-30):** closes **54.5%** in one round.
- **Effect on the strong-branching tree:** none (§2.4: 227 against 219 nodes to 1e-4).
  - **[I]** Strong branching closes that part of the gap in the first 3-5 levels anyway. The cut does not touch the tail.
  - Its value is as a cheap **certificate**, e.g. for pruning other last years L or for reporting a bound, not as a tree accelerator.
- **[I]** Getting past "max over blocks" needs multiplier re-optimisation: a subgradient or bundle method over overlapping blocks, i.e. Lagrangian decomposition or the H16 Dantzig-Wolfe.

## 4. DP / relaxation bounds (task 4)

- **[M] Surrogate knapsack:** all cash rows aggregated with weights −y*, GUBs kept, solved to integrality. Closes **0.00%** (the LP version also 0.00%). Any DP whose state aggregates cash into a scalar (cumulative spend, slack) is a relaxation of such a surrogate, or weaker. **It cannot beat the LP at the LP multipliers.**
- **[I] Exact DP over years** needs the committed future spend vector of started projects as state. Projects run up to 23 years, so the state is a vector of up to 23 cumulative amounts: intractable.
- **[I] What the evidence supports instead:**
  - The block MIPs of §3.2 are the "right" DP: integrality over 5-13 consecutive years, priced elsewhere.
  - A Lagrangian decomposition with 2-3 overlapping blocks and subgradient updates would need about 50-200 block MIPs of 5-25 s each, i.e. 5-60 core-minutes for a root bound likely above 55% closure **[estimate]**.
  - That is worthwhile only as a bound/certificate (H16). Per §2.4, it will not speed the proof.

## 5. Ranked experiments

| # | Experiment | Mechanism | Implementation in capopt | Evidence (closure) | Cost |
|---|---|---|---|---|---|
| **1** | **Strong-branching ramp-up and re-split** (product score, top 30) | Branch on the "start ≤ s" split with the best product of child gains. These are thin directions on long, big projects. | `parallel._lp_node`: keep the top 30 candidates (now 8). In `ramp_up` and the slice re-split, for each candidate `changeColsBounds` on the cached LP, run warm from the parent basis (`getBasis`/`setBasis`), restore, and score `max(Δ0,ε)·max(Δ1,ε)`. **Push the two evaluated children with their already-computed bounds** (this saves the current child LP pair). Optional: parallelise candidate evaluation over the idle pool during ramp-up. | **[M]** Root split 27.8% (product pick) against 1.1% for the current rule. After 60 expansions the global bound is 89.7% closed against 70.7%, with 33 open leaves against 48. 1e-4 is reached in 219 LP nodes against 849. | About 0.8 s per expansion single-threaded (about 60 warm LPs) against about 0.3 s, so a 60-expansion ramp-up takes about 50 s on one core, or under 5 s spread over 20 workers **[I]**. About 80 lines. |
| **2** | **Deeper outer LP tree as the proof engine; HiGHS only for the tail** | The Python strong-branching LP tree reaches 1e-4 in about 220 nodes. Use it for the bulk of the proof, then hand leaves to HiGHS only once their LP gap is below about 1e-4 or the node is LP-integral. | Extend `ramp_up` into a persistent best-first loop on the master. Workers evaluate strong-branching candidates or child LPs (each worker holds a warm `_lp_solver`). Leaf hand-off rule: (LP gap < τ) or (depth > D). The cutoff is the shared incumbent. E1 live bounds are reused. | **[M]** LP-only: about 220 nodes / about 100 s on one busy core to 1e-4, given the optimal cutoff. **[I]** About 5-15 s wall on 20 workers; the 1e-5 tail (plateau) still needs HiGHS. | Medium (a new loop plus task protocol). First run a **cheap screen: the same LP tree with cutoff = capopt's 0.01% and 0.07% incumbents** (tree-size sensitivity), on one core in about 5 min. |
| **3** | **Year-block Lagrangian bound** (years 4-16, plus a second block) as a certificate | Integrality over consecutive years is where the gap lives (§3.2). | `cuts.py`: `block_cut(model, years, duals)` builds the reduced-cost MIP (GUBs plus the block's cash, band and cap rows), solves it with a 25 s cap, and returns the dense objective cut. Use it in the `idle` hook to bound the other last years L, and to report a stronger root bound. | **[M]** 54.5% root closure in one 25 s MIP. 5-year blocks give up to 16.6% in ≤ 6 s. **No tree speed-up** (227 against 219 nodes). | Small (about 60 lines); 25 s of one core per call. Upgrade to subgradient Lagrangian decomposition (H16) only if certificates matter. |
| **4** | **Gomory (Karamanov-Cornuéjols) splits as strong-branching candidates at depth ≥ 3 only** | General splits from tableau rows; at depth 3 they match variable splits. | Needs `Split` to carry `(cols, vals, lo, hi)` rows, `addRow` in leaves, and a 325×325 dense basis solve per node. | **[M]** Tree: 225 against 219 nodes to 1e-4 at 2× strong-branching cost; only SB + Gomory reached 1e-5 within its budget (295 nodes). | Medium. **Low priority:** run only after #1/#2, as a tie-breaker in the tail. |
| **5** | **Drop:** count, starts-in-year, cumulative-spend and pair-order disjunctions; per-year lifted-cover/PCKP facet work; scalar-state DP or surrogate bounds | They are flat or thick directions, or capped by single-row integrality. | Remove from the backlog (BRANCHING_AND_TREE E7's count/pair part; H5 beyond the existing covers). | **[M]** ≤ 2.3%, ≤ 0.03%, 0%, 0.7%; single-row ceiling 1.5%; surrogate 0%. | None. Saves effort. |

**Suggested order:** #1, then #2's screen, then #2. Keep #3 as a side project for certificates and L pruning. Stop investing in disjunction families and per-row cuts.

## 6. Measured versus inferred (summary)

- **Measured:**
  - every number in the tables of §1.1-1.2, §2.3-2.4, §3.2-3.3 and §4's surrogate;
  - the exact dual decomposition of the gap;
  - the H1-H5 verdicts' quantitative parts.
- **Inferred:**
  - the "plateau" reading of the 1e-5 stall;
  - wall-clock estimates on 20 workers;
  - comparability of the 220-node LP tree with the commercial solver's roughly 1,600 nodes (different node definitions and cutoffs);
  - that the dual decomposition's attribution reflects structure rather than one arbitrary dual (it is degenerate);
  - the Lagrangian-decomposition estimate;
  - the absence of published facet classes for nested cumulative knapsacks with GUB chains (we did not find one; that is not proof it does not exist).
- **Not done:**
  - full-text reading of the Karamanov-Cornuéjols and Cornuéjols-Liberti-Nannicini papers (abstracts and preprint listings only);
  - Owen and Mehrotra's primary source;
  - Weingartner's book.

## Sources

- Karamanov, Cornuéjols, "Branching on general disjunctions" (abstract): https://academicnewsletter.sufe.edu.cn/info/399033 ; thesis: https://www.cmu.edu/tepper/programs/phd/program/assets/dissertations/2006-operations-research-karamanov-dissertation.pdf
- Cornuéjols, Liberti, Nannicini, "Improved strategies for branching on general disjunctions": https://link.springer.com/article/10.1007/s10107-009-0333-2 ; preprint https://www.lix.polytechnique.fr/~liberti/branchimp.pdf
- Mahajan, Ralphs, "On the complexity of selecting disjunctions in integer programming": https://epubs.siam.org/doi/10.1137/080737587
- Mahajan, Ralphs, "Experiments with branching using general disjunctions": https://optimization-online.org/?p=10468
- Krishnamoorthy, Pataki, "Column basis reduction and decomposable knapsack problems": https://arxiv.org/abs/0807.1317
- Krishnamoorthy, "Thinner is not always better: cascade knapsack problems": https://arxiv.org/abs/1612.08588
- Lorie, Savage, "Three problems in rationing capital" (citation listing): https://ideas.repec.org/r/ucp/jnlbus/v28y1955p229.html
- Weingartner, *Mathematical Programming and the Analysis of Capital Budgeting Problems* (catalogue): https://search.worldcat.org/title/Mathematical-programming-and-the-analysis-of-capital-budgeting-problems/oclc/408653
- van de Leensel, van Hoesel, van de Klundert, "Lifting valid inequalities for the precedence constrained knapsack problem": https://iaorifors.com/paper/28960
- Park, Park, "Lifting cover inequalities for the precedence-constrained knapsack problem": https://koasas.kaist.ac.kr/handle/10203/7579?mode=full
- Boland et al., "Clique-based facets for the precedence constrained knapsack problem": https://optimization-online.org/2006/01/1322/
- Kaparis, Letchford, "Local and global lifted cover inequalities for the 0-1 multidimensional knapsack problem": https://ideas.repec.org/a/eee/ejores/v186y2008i1p91-103.html ; PDF https://www.lancaster.ac.uk/people/letchfoa/articles/2008-mkp.pdf
- Bienstock, Sethuraman, Ye, "Approximation algorithms for the incremental knapsack problem via disjunctive programming": https://arxiv.org/abs/1311.4563
- Faenza, Malinovic, "A PTAS for the time-invariant incremental knapsack problem": https://arxiv.org/abs/1701.07299
- Caprara, Furini, Malaguti, "Uncommon Dantzig-Wolfe reformulation for the temporal knapsack problem": https://pubsonline.informs.org/doi/10.1287/ijoc.1120.0521
- de Sousa, Wolsey, "A time indexed formulation of non-preemptive single machine scheduling problems": https://iaorifors.com/paper/8382
- van den Akker, van Hoesel, Savelsbergh, "A polyhedral approach to single-machine scheduling problems": https://cris.maastrichtuniversity.nl/en/publications/a-polyhedral-approach-to-single-machine-scheduling-problems-2
- Related capopt notes: [BRANCHING_AND_TREE.md](BRANCHING_AND_TREE.md) (E3 strong-branching ramp-up, E7 general disjunctions), [MILP_FRONTIER.md](MILP_FRONTIER.md), [HYBRID_AND_CROSSFIELD.md](HYBRID_AND_CROSSFIELD.md) (H16 year-block DW).
