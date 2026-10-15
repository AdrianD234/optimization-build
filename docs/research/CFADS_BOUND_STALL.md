# Why the bound stalls on the CFADS-after-debt-service scenarios

**Date:** 2026-10-16
**Question:** On `CPO233_cfadsds_{P50,P50high,P95,P95high}`, capopt's parallel tree finds the CPO's schedule early but cannot close the bound (3e-4 to 1.9e-3 left after 400-900 s, KPI pass 1). Why do "start ≤ s" splits move the bound so little? Is the cause structural? Which disjunctions or bounds work here? How do the candidate branching rules compare in an LP-only tree?
**Method template:** [GAP_ANATOMY_AND_DISJUNCTIONS.md](GAP_ANATOMY_AND_DISJUNCTIONS.md), the same study on s2000_P50high.

## Setup

- **Instances.** `CPO233_cfadsds_P50`, `_P50high`, `_P95`, `_P95high` (no start cap: `max_starts_per_year` equals the project count). Comparison: `CPO233_s2000_P50`.
- **Model.** `last_year_model(inst, L=min_last_year(), "step", "cumulative")`, which is capopt's default. L = 37 for P50/P50high, L = 42 for P95/P95high and L = 30 for s2000_P50.
  - P50: 7,784 columns × 7,515 rows (including the step chain rows).
  - P95: 8,964 × 8,690.
- **Incumbent.** The best of the CPO reference and the KPI pass 1 schedules (`private/cpo/results/kpi1`).
  - **Note [M]:** on P95 and P95high, the best-known schedule's last build year is **43**, not 42. The L = 42 tree therefore only has to *prune* against an incumbent that does not fit its own model. That is why KPI pass 1 also had to solve L = 43 on those two scenarios.
- **Compute.**
  - HiGHS 1.15.1 with `threads=1` and one process at a time, on a machine running a timing benchmark, so the seconds are noisy.
  - Every MIP was capped at 30 s. No `capopt` campaign commands were run.
  - The scratch scripts live outside the repo, and each experiment below is described well enough to re-implement.
- **Data rule.** Only aggregates appear here: ratios, counts and percentiles.
- **Conventions.** Gaps are relative to |z_inc|. "Closes X%" means (bound − z_LP)/(z_inc − z_LP), or the same share of a *node's* own gap where stated. A **tolerance unit** is 1e-5·|z_inc|, the KPI target.
- **Labels.** [M] measured here; [I] inferred; [L] literature, with URL; [unverified] means the details could not be checked against the primary source in this session.

## Headline

1. **[M] cfadsds is a different combinatorial regime from s2000. The difference comes from the funding stream, not from symmetry or tiers.**
   - Year-0 funding is ≤ 0, and funding varies from 0.5× to 1.64× its mean, while s2000's is flat.
   - As a result, **220 of 233 projects** are not at their first allowed start in the best-known schedule, against **66** on s2000, where 167 projects pile up in year 0.
   - The LP has **29 of 38** cash rows binding (s2000: 16 of 31). It has **21 fractional projects** (s2000: 11), and most of them are smeared over *adjacent* years (spreads 1-4).
2. **[M] The gap is diffuse.**

   | Measure | cfadsds_P50 | s2000_P50 |
   |---|---|---|
   | Projects carrying 80% of the start-column gap term | 12 | 4 |
   | LP-integral projects that sit at another year in the incumbent | 145 of 212 | 62 of 222 |
   | Median shift of those projects | 2 years | 4.5 years |
   | Root gap, in tolerance units | 141 | 563 |
   | Best single split at the root, in tolerance units | 24 | 191 |

   - The root gap is smaller in tolerance units, yet the best single split moves the bound **8× less in absolute terms**.
   - That is the direct answer to "why splits barely move the bound": no single project carries much of the gap.
3. **[M] Splits are not useless at depth, but the rules pick badly.**
   - At depth 3-9, the best "start ≤ s" split closes 16-41% (P50) and 34-91% (P95) of the node gap, comparable to s2000.
   - capopt's frac × spend pick closes only 1-5% of the node gap at most measured P95 nodes.
   - The best split by product score sits at rank 10/4/2/0 (P50) and 5/3/3/5 (P95) of capopt's list, so **k = 4 strong branching misses it at most nodes**.
4. **[M] The LP tree is wide, whatever the rule.**
   - In an LP-only best-first tree with the incumbent as cutoff, the current rule reproduces KPI pass 1's ramp-up exactly: **60 leaves, 9 pruned, 44% of the gap closed**.
   - After 389 expansions it has 326 open leaves and 59.5% closed; 92% of children survive.
   - Strong branching with a **gap-capped product score over 16 candidates** reaches 68% closed after 55 expansions, with 36 open leaves.
   - No rule reaches a 3e-4 gap within 240 s on one core.
5. **[M] Integrality structure exists in short year blocks and at the end of the horizon, unlike on s2000.**
   - Lagrangian year blocks at the root duals close up to **21% with one 5-year block** on P50 (years 6-10). That is better than s2000_P50high's 16.6%.
   - On P95, a **single year, L−1, closes 10.2%** (s2000's single-year ceiling was 1.5%). The 10-year block 32-41 closes 24.8%.
   - capopt's existing root cover cuts close **6.8% (P50) and 5.1% (P95)**, against 1.7% on s2000_P50.
6. **[M] Ruled out.**
   - **Symmetry:** 5 groups of identical projects (11 projects, largest group 3). Ordering rows change the root LP by 0% and leave the tree trajectory unchanged.
   - **Tier penalties:** holding plus tier costs are about 2e-5 of |z| on P50 and 5e-4 on P95. The incumbents sit in the base tier in 36-38 of 37-42 years. The tier cost is convex piecewise-linear, so the LP already represents it exactly.
   - **Reduced-cost fixing:** at most 0.6% of nonbasic start columns are fixable.
   - **Counts of all starts by year:** ≤ 0.004%.

## 1. Why "start ≤ s" splits barely move the bound (question 1)

### 1.1 Structure of the root LP [M]

| Item | s2000_P50 (L = 30) | cfadsds_P50 / P50high (L = 37) | cfadsds_P95 / P95high (L = 42) |
|---|---|---|---|
| Root gap | 0.563% | 0.141% / 0.141% | 0.254% / 0.245% |
| Fractional projects | 11 | 21 | 22 |
| Support sizes | 2-6 | 2 (×15), 3 (×2), 4 (×4) | 2 (×15), 3 (×5), 5 (×2) |
| Start-year spreads | 1-20; 6 of 11 spread ≥ 4 | 1-11; 16 of 21 spread ≤ 3 | 1-12; 18 of 22 spread ≤ 3 |
| Spend percentile of fractional projects (median) | 0.97 | 0.92 | 0.95 |
| Cash rows binding (non-zero dual, zero slack) | 16 of 31 | 29 of 38 | 31 of 43 |
| Band rows with non-zero dual | 3 | 2 | 2 |
| Final-year row (year L) | slack 0.8%, dual 0 | slack 2.9%, dual 0 | slack **0.12%**, dual 0 |
| Projects not at their first allowed start (incumbent) | 66 | 220 | 220 |
| Distinct start years (incumbent) | 19 | 29-30 | 33-34 |
| Years with closing cash < 1% of that year's funding (incumbent) | 10 | 18-20 | 18 |

- The final-year row itself is constant, because every project finishes by L. Its neighbours are not: the row for L−1 is effectively a **covering** knapsack, since the spend in year L must cover total spend − cumF_{L−1}. P95 has only 0.12% funding slack at L, so this binds hard there (§3.3).

**Exact gap decomposition.** As in the template: c·x̂ − z_LP = d·(x̂ − x*) + y·(r̂ − r*), evaluated at the incumbent at the same L (for P95, at L = 43, whose root LP gap is 0.2537%). The terms sum to 1.000000.

| Term | s2000_P50 | cfadsds_P50 | cfadsds_P50high | cfadsds_P95 (L = 43) |
|---|---|---|---|---|
| Reduced costs of start columns | 77.2% | 72.1% | 70.5% | 51.5% |
| Band columns | 6.6% | 1.2% | 1.8% | **19.4%** |
| Cash rows (dual × incumbent slack) | 9.0% | **18.1%** | **19.2%** | **23.9%** |
| Band rows | 3.6% | 3.5% | 4.0% | 1.0% |
| Chain rows | 3.7% | 5.1% | 4.6% | 4.3% |
| Projects carrying 50 / 80 / 95% of the start term | 2 / 4 / 16 | 3 / 12 / 39 | 3 / 11 / 36 | 4 / 12 / 36 |
| LP-integral projects at another year in the incumbent | 62 of 222 | 145 of 212 | 148 of 212 | 118 of 211 |
| Median / upper-quartile shift of those projects | 4.5 / 17 years | 2 / 7 years | 2 / 6 years | 3 / 5 years |

- **[I] Reading.** On s2000 the optimum *re-sequences a few big projects by many years*; one split on one of them is a thin direction. On cfadsds the optimum makes *many small shifts* (median 2 years) across 3× as many projects, and leaves twice the share of cash slack in years where the LP fills funding exactly.
  - So the template's rejected hypothesis H1 (smearing across adjacent years to fill cash rows exactly) **is the right picture here**.
  - A split on one smeared project lets the LP re-smear a neighbour across the 29 tight rows.
- **[M] P95's tier term.** On P95, 19% of the gap is band columns. The incumbent holds cash in the top tier in one year (year 9), which the LP avoids. This is the only scenario where tiers matter, and they matter only through integrality (§3.4).
- **Caveat [I]:** the LP is dual-degenerate, so the split between terms depends on which dual vector HiGHS returns. The total is exact.

### 1.2 Per-split child gains, root and depth 3-9 [M]

**Method.**
- Nodes are on the path that follows capopt's rule (frac × spend, top candidate), always taking the lower-bound child.
- At each node, every fractional "start ≤ s" candidate is evaluated with both child LPs, warm-started from the node's basis.
- "Best" means the best min-child bound as a share of the node's gap. "Product pick" is the candidate with the best product score. "capopt pick" is rank 0 of capopt's list.

| Scenario, depth | Node gap (share of root) | Candidates | Best min-child closes | Product pick: min-child / max-child | capopt pick: min-child | Product-best rank in capopt's list | Splits closing ≥ 5% |
|---|---|---|---|---|---|---|---|
| s2000_P50, 0 | 1.00 | 82 | **34.0%** | 27.8% / 52% | 1.1% | 3 | 8 |
| s2000_P50, 3 | 0.73 | 55 | 39.0% | 33.4% / 67% | 2.8% | 6 | 7 |
| s2000_P50, 6 | 0.43 | 56 | 54.2% | 54.2% / 105% | 24.6% | 1 | 9 |
| s2000_P50, 9 | 0.18 | 54 | 135% (node pruned) | 86% / 467% | 38.2% | 3 | 5 |
| cfadsds_P50, 0 | 1.00 | 68 | **16.9%** | 5.4% / 69% | 16.9% | 10 | 3 |
| cfadsds_P50, 3 | 0.64 | 65 | 15.8% | 6.0% / 822% | 15.3% | 4 | 13 |
| cfadsds_P50, 6 | 0.34 | 60 | 29.9% | 29.9% / 1513% | 4.7% | 2 | 18 |
| cfadsds_P50, 9 | 0.29 | 62 | 40.6% | 40.6% / 1801% | 40.6% | 0 | 18 |
| cfadsds_P95, 0 | 1.00 | 62 | **6.6%** | 4.3% / 51% | 1.0% | 5 | 3 |
| cfadsds_P95, 3 | 0.86 | 62 | 33.6% | 18.9% / 445% | 3.5% | 3 | 6 |
| cfadsds_P95, 6 | 0.80 | 64 | 41.7% | 22.2% / 478% | 2.3% | 3 | 6 |
| cfadsds_P95, 9 | 0.26 | 66 | 91.0% | 29.4% / 2700% | 18.1% | 5 | 14 |

- No one-sided infeasibility was found in about 1,500 child LPs, so strong-branching fixings will not happen.
- **[M] At the root, the best cfadsds split closes half (P50) or a fifth (P95) of what it closes on s2000.** In absolute terms the gap per split is 8× smaller (Headline 2).
- **[M] At depth ≥ 3, good splits exist on cfadsds, but they look different.**
  - The product-best split pairs a small gain on one side with a child that jumps **4-27× the node gap**, well past the incumbent.
  - In effect, one side is pruned and the other is a near-fixing. The uncapped product score overvalues that jump, so it often picks a split with a weak min-child (e.g. P50 depth 0: 5.4% against 16.9% available).
  - **Capping each gain at the node's distance to the cutoff fixes this** (§4).
- **[M] The capopt rule's top candidate is often poor on P95** (1-3.5% at depths 0-6), and the product-best is beyond rank 4 in 5 of 8 cfadsds nodes. KPI pass 1's k = 4 strong branching therefore often does not see the best split.

### 1.3 LP degeneracy and alternative optima [M]

| Measure | s2000_P50 | cfadsds_P50 | cfadsds_P95 |
|---|---|---|---|
| Basic start columns | 2,762 of 6,276 | 1,717 of 7,673 | 1,925 of 8,838 |
| Basic start columns at a bound (primal degeneracy) | 2,680 (97%) | 1,649 (96%) | 1,863 (97%) |
| Nonbasic start columns with \|rc\| ≤ 1e-6 × gap (dual degeneracy) | 50 | 73 | 74 |
| \|rc\|/gap, nonbasic start columns: median / 90th percentile | 0.0005 / 0.0097 | 0.0014 / 0.031 | 0.0009 / 0.020 |
| Nonbasic columns fixable by reduced cost (\|rc\| > gap) | 0% | 0.6% | 0.1% |
| Feasible single-project moves from the incumbent within 1e-5 / 1e-4 of \|z\| | 1,078 / 2,999 | 1,202 / 2,707 | n/a |
| Pair moves (±1-2 years) within 1e-5 / 1e-4 of \|z\| | 13,825 / 54,977 | **35,011 / 76,933** | n/a |
| Single moves that improve the incumbent | 1 (by 1.4e-6, below tolerance) | 0 | n/a |

- **[M] The LP is no more degenerate on cfadsds than on s2000.** Both are about 97% primal-degenerate with similar dual degeneracy, and reduced-cost fixing is useless on both.
- **[M] The integer plateau is wider:** 2.5× more pair moves land within the 1e-5 tolerance. **[I]** This matters only for the last tolerance units. It does not explain the 3e-4 to 1.9e-3 stalls, which are 30-190 tolerance units.

### 1.4 Where the gap sits by year (year-block test) [M]

**Method.** As in the template (§3.2 there):
- Lagrangian bound at the root duals y*. Keep the chain rows plus the cash and band rows of the years in K, with integrality; dualise every other row.
- Sanity check: the LP of the empty block equals z_LP to 2e-15.
- Each block MIP is capped at 30 s. A block that hits the cap reports its MIP dual bound, which is still valid but understated (marked "TL").

| Block | cfadsds_P50 (L = 37) | cfadsds_P95 (L = 42) | s2000_P50high (template) |
|---|---|---|---|
| Single years | max **4.1%** (year 35 = L−2); median 0.01% | max **10.2%** (year 41 = L−1); 2.3% (39); 1.8% (38) | max 1.5% |
| 3-year blocks | max **10.4%** (10-12); 9.2% (7-9); 6.0% (33-35); median 0.9% | max 10.8% (39-41); 10.4% (40-42); 4.0% (8-10) | max 6.2% |
| 5-year blocks (stride 2) | max **21.1%** (6-10); 15.7% (8-12); 15.1% (10-14) | max 10.8% (38-42); 8.4% (8-12); 7.7% (6-10) | max 16.6% |
| 10-year blocks (stride 4) | 19.2% TL (4-13); 18.2% (0-9) | **24.8% TL (32-41)**; 14.1% TL (4-13) | 54.5% (years 4-16) |
| Halves | 16.9% TL (0-18); 2.9% TL (19-37) | 23.6% TL (0-20); 9.5% TL (21-42) | n/a |

- **[M] Two hot spots.**
  - **Years 4-14** (both scenarios): the early, low-funding ramp where most projects compete.
  - **The last 3-5 build years** (strongly on P95, mildly on P50): the covering knapsack of §1.1, i.e. which projects finish in years L−1 and L.
- On P95, one year (L−1) holds as much integrality gap as any 5-year block.
- **[I]** The large blocks hit 30 s, so their numbers are lower bounds on what those blocks hold. The years 19-37 / 21-42 halves are mostly loose.

## 2. Structural causes (question 2)

| Candidate | Verdict | Evidence |
|---|---|---|
| Year-varying funding with near-zero-slack years | **Primary cause.** | [M] Year-0 funding is ≤ 0 and funding ranges 0.5-1.64× its mean. 29-31 cash rows bind (16 on s2000). The incumbent has 18-20 years below 1% cash. 220 of 233 projects are "in play" (66 on s2000). The gap is spread over 12 projects at 80% (4 on s2000), and the cash-slack term doubles to 18-24%. **[I]** Every year is a tight knapsack and the LP smears small and medium projects by 1-2 years to fill each one exactly. Any one split is cheaply repaired by re-smearing a neighbour. |
| Symmetric or near-identical projects | **Rejected.** | [M] Identical spend, allowed starts and PV: 5 groups, 11 projects, largest group 3. Identical spend only: 8 groups, 19 projects. Same normalised shape and duration: one group of 45 (mostly 1-year projects of different sizes, so not symmetric). 3 of the 21 fractional root projects lie in identical groups. Ordering rows (start_p ≤ start_q, 203 rows) close **0.0%** at the root, and in the tree (§4) give 59.5% against 60.3% without them, a trajectory identical to noise. |
| Holding-cash tier penalty | **Rejected for P50; minor for P95.** | [M] Holding plus tiers is 2e-5 of \|z\| on P50 and 5e-4 on P95. The incumbent is in the base tier in 36 of 37 years (P50) and 38 of 42 (P95). **[I]** The tiers are convex piecewise-linear, so the LP is exact given x. Tiers can only add gap through integrality, which is the 19% band-column term on P95, all in one year inside the years 4-14 hot spot. |
| Larger last year L | **Contributes.** | [M] 35-40 options per project (29 on s2000) and 7.8-9.0k columns (6.4k on s2000). The final rows create the L−1/L covering knapsack: 10.2% in one year on P95, 4.1% on P50. On P95 the incumbent's last year is 43, so the L = 42 subtree carries an extra "cannot finish by 42 cheaply" gap. That gap is pure end-of-horizon structure. |
| LP degeneracy | **Not distinctive.** | [M] §1.3: the same as s2000. |

## 3. Disjunctions and bounds that work here (question 3)

### 3.1 Disjunction families, best child closure [M]

These are on cfadsds_P50, at the same path nodes as §1.2, as a share of the node gap. Hyperplanes were added as free rows; each child changes one row bound.

| Family | Root | Depth 3 | Depth 6 | s2000 (template, root) |
|---|---|---|---|---|
| "start ≤ s" (variable) | **16.9%** | **15.8%** | **29.9%** | 33.9% |
| Count of starts by year t, all projects | 0.004% | 0.001% | 0.004% | 0.00% |
| Count of starts by year t, top 10% by spend | 8.4% | 9.2% | 13.0% | 0.02% |
| Rounded cumulative spend, unit 1.6e-4 of funding | 0.18% | 0.24% | 0.58% | 0.01% |
| Rounded cumulative spend, unit 1.6e-3 | 7.2% | 13.8% | 24.9% | 0.14% |
| Rounded cumulative spend, unit 1.6e-2 | 5.4% | 12.2% | 29.9% (the same split as the best variable split) | 14.4% |
| Rounded cumulative spend, unit 5e-2 | integral (no split) | | | |
| Band/tier variables | **vacuous** [I]: the band columns are continuous and the tier cost is convex, so "cash_t ≤ θ ∨ cash_t ≥ θ" never excludes the LP point | | | |

- **[M] Unlike s2000, the aggregate families are not flat on cfadsds.** Counts over big projects and moderately rounded cumulative spend close 7-25%, because the gap is a many-project smear.
- **They never beat the best variable split**, though, so their value is as extra strong-branching candidates at best. **[I]** That is a small expected gain, at the cost of dense rows in every node LP.

### 3.2 Symmetry handling [M]

- Ordering rows for the 5 identical groups: 0.0% at the root.
- LP tree with SB8-capped scoring plus ordering rows: 59.5% closed after 111 expansions, against 60.3% after 113 without them.
- Orbital fixing would act on the same 11 projects. The tree-size reduction is bounded by the group orbits (at most 2⁴·3! = 96 for the full orbit product, and far less in practice, because only 3 of 21 fractional projects are in a group). **[I]** It is not worth implementing for this data.

### 3.3 Cuts: per-year covers [M]

- capopt's root cover loop (`cuts.root_loop`, 30 rounds) closes **6.8% (P50, 262 cuts) and 5.1% (P95, 528 cuts)**, against **1.7%** on s2000_P50 (693 cuts). Five rounds give 6.7% / 4.5%.
- On P50 this is above the best single-year Lagrangian ceiling (4.1%), because the cuts act in several years at once. On P95 it is half of year L−1's 10.2% ceiling. **[I]** The covering form of that row (spend after year t ≥ total − cumF_t, over projects still running) is probably not what the started-by cover separator finds.

### 3.4 A stronger relaxation of the cash-tier structure [I]

- The tier cost is exact in the LP. Its gap share on P95 (19%) is integrality in one year, which the years 4-14 block bound already captures (the 8-12 block closes 8.4% on P95).
- No separate tier relaxation is needed. Year-block bounds and covers subsume it.

### 3.5 Lagrangian or year-block bounds [M]

- §1.4: one 5-year block closes 21% (P50). One 10-year block closes ≥ 24.8% (P95, end of horizon). Both are about the size of the best single split at the root, and both are certificates only.
- The template found that block cuts did not shrink the strong-branching tree on s2000 (227 against 219 nodes).
- **[I]** Combining the two hot spots, e.g. years 4-13 plus 32-41 on P95, needs multiplier re-optimisation. That is the H16 Dantzig-Wolfe; the bounds above use fixed root duals.

## 4. LP-only best-first tree on cfadsds_P50 (question 4) [M]

**Protocol.**
- Pure LP B&B in Python, best-first, single thread, on a busy machine. The cutoff is z_inc − 1e-5·|z_inc| (capopt's target).
- 240 s per rule. Child LPs are warm-started from the parent basis.
- "Ramp-up" is the moment the tree first has 60 open leaves, capopt's default `n_leaves`.
- Rules:
  - capopt is `_branch` with `sb_candidates=0`;
  - sbK is strong branching over capopt's top K candidates with the product score `_sb_pick`;
  - sbKcap is the same with each gain capped at (cutoff − node bound), so a child that is pruned counts as closing the whole node gap.

| Rule | At 60 open leaves: expansions / pruned / closed / leaf depth (median) | After 50-60 expansions: closed / open | At 240 s: expansions / LPs / closed / open / pruned | Gap at 240 s |
|---|---|---|---|---|
| capopt (frac × spend) | 68 / **9** / 43.9% / 8 | 43.2% / 53 | 389 / 1,168 / 59.5% / 326 / 64 | 5.7e-4 |
| sb4 (KPI pass 1's setting) | 68 / 9 / 42.8% / 8 | 41.3% / 53 | 210 / 1,891 / 56.6% / 158 / 53 | 6.1e-4 |
| sb8cap | 82 / 23 / 53.8% / 9 | 50.4% / 48 | 113 / 1,922 / 60.3% / 78 / 36 | 5.6e-4 |
| **sb16cap** | n/a (never reached 60 open) | **65.8% / 33** | 55 / 1,816 / **68.1%** / 36 / 20 | **4.5e-4** |
| sb8cap + symmetry ordering | 82 / 23 / 53.8% / 9 | 50.4% / 48 | 111 / 1,888 / 59.5% / 75 / 37 | 5.7e-4 |

- **[M] The capopt rule's tree reproduces KPI pass 1's ramp-up exactly** (60 leaves, 9 pruned). Its open-leaf count grows by about 0.85 per expansion, since 92% of children survive the cutoff. This is the "flat tree".
- **[M] k = 4 strong branching gives nothing here** (sb4 ≈ capopt). This matches §1.2: the good splits sit beyond rank 4.
- **[M] sb16cap is the best rule.**
  - Per expansion: 66% closed after 50 expansions, against 42% for capopt.
  - Per LP solved: 68.1% after 1,816 LPs, against 59.5% after 1,168 for capopt, and capopt's curve was flattening at about +2% per 50 expansions.
  - Per open leaf: 36 leaves at a 4.5e-4 gap, against 53 leaves at 8.0e-4 when capopt has expanded 60 nodes.
- **[I] No rule is anywhere near proving 1e-5 in an LP-only tree.** Extrapolating capopt's curve (about +3% closure per 100 expansions near 60%), 3e-4 would need thousands of LP expansions. The leaves capopt hands to HiGHS start about 0.8e-3 from the incumbent. Under sb16cap they would start about 0.45e-3 away.
- **[I] Wall-clock caveat.** About 130 ms per child LP on one busy core. sb16cap costs about 33 LPs per expansion, so a 60-expansion ramp-up is about 4 minutes serial, or about 15 s on 16 pool workers (E12's batched mechanism).

## 5. Ranked experiments for capopt

| # | Experiment | Mechanism | Implementation | Evidence | Expected effect / cost |
|---|---|---|---|---|---|
| **1** | **Gap-capped product score plus more strong-branching candidates (k = 16) in ramp-up and re-split** | The cfadsds splits that matter have one child far past the cutoff (4-27× the node gap). The uncapped product overvalues that and picks weak min-children. k = 4 misses the best split in 5 of 8 nodes. | `parallel._sb_pick`: take a `cutoff` argument and use `gains = min(c.bound, cutoff) - node.bound`, so a pruned child counts as the full gap. Pass `best_val.value - rel_gap*|best|` from `ramp_up` / `_ramp_up_batched` and from the re-split in `solve_parallel`. `_lp_node` already keeps 32 candidates. Default `sb_candidates=16` when the root LP has more than about 15 fractional projects. | [M] §4: 68% against 44% closed at the ramp-up point; open leaves 36 against 53; root-to-leaf gap 4.5e-4 against 8e-4. §1.2: the uncapped product pick left a 3× better min-child unused at P50's root. | **[I]** Leaves start about 45% closer to the incumbent, with fewer of them. About 1,800 child LPs, roughly 15 s on 16 workers via `_ramp_up_batched`. About 10 lines plus a default. A 1-seed KPI rerun on cfadsds_P50 and P95 decides it. |
| **2** | **Deeper outer tree on cfadsds: more leaves, and work stealing for bound-holding leaves** | The KPI census shows 2-5 depth 5-9 leaves holding the bound for the whole run after 3,000-10,000 HiGHS nodes. The LP tree is wide, so 60 leaves is too coarse. | (a) `solve.solve` / `solve_parallel`: `n_leaves` scaled by the root fractional count, e.g. 200 when there are more than 15 fractional projects. (b) In the `solve_parallel` loop: when the queue is empty and fewer than about half the workers are busy, set a per-slot stop flag on the running leaf with the lowest `live[slot]` bound. `_solve_leaf`'s interrupt callback checks it. Re-split that leaf with rule #1 and requeue the children with `max(child.bound, live bound)` (the E1 inheritance already exists). | [M] KPI census; §4 tree width. [I] Time-sliced re-splitting (I4) was rejected on raw_penalised because restarts discard HiGHS state. Stealing only the bound-holders, and only when the other workers are idle, avoids paying that everywhere. | Medium (about 60 lines). Expected to turn the tail from "2 workers busy, 18 idle" into a spread search. Tree-side only, so it cannot hurt the primal side. |
| **3** | **End-of-horizon covering cuts, plus the existing cover loop on cfadsds** | P95's L−1 row alone holds 10.2% of the gap, and the last 3-5 years 10.8-24.8%. The row says "projects still running after t must spend ≥ total − cumF_t", a covering knapsack. The existing loop already closes 6.8% / 5.1% (against 1.7% on s2000). | (a) Turn on `cut_rounds=5` for scenarios whose root closure is above 3%, as a quick screen. 5 rounds give 6.7% / 4.5%. (b) `cuts.separate`: for t ≥ L−4, complement the row (spend in years > t of the projects whose start allows spend after t ≥ total − cumF_t). Separate the minimal-cover form in "started after s" variables (1 − z) and lift over the chain. | [M] §1.4, §3.3. [I] exp04 found that cuts trade node count against LP speed; here there are 5 rounds and few rows. | Small for (a), a screen. Medium for (b), about 50 lines. Expected root closure 5-10%, and more on P95's L = 42 subtree, which is exactly the "cannot finish by L" structure. |
| 4 | **Prune P95's L = 42 against the L = 43 incumbent with a final-years block bound** | The L = 42 model must be *pruned*, not solved. Its gap is concentrated in the last 10 years (24.8% in one block MIP). | `cuts.py` `block_cut(model, years, duals)` (the template's #3) applied to years L−10..L−1, used as an objective cut for the L = 42 tree and in `solver.idle` LP trees. | [M] §1.4 P95 numbers; the KPI ran L = 42 and L = 43 back to back on P95. | Small (about 60 lines plus a 30 s MIP). A certificate; it moves the L = 42 root bound by about 25%. |
| 5 | **Aggregate hyperplanes as extra strong-branching candidates (counts of big projects; cumulative spend rounded to 1.6e-3)** | On cfadsds these are not flat (7-25%), but never better than the best variable split. | Needs `Split` to carry rows (the template's #4 infrastructure). | [M] §3.1. | Low priority: a tie-breaker at depth ≥ 6 at best. |
| 6 | **Drop:** symmetry handling, tier-variable branching or tier relaxations, reduced-cost fixing, all-project count disjunctions, fine cumulative-spend splits | Small orbits; tiers exact and nearly inactive; rc ≪ gap; flat directions. | Nothing. | [M] §2, §3.1-3.2, §1.3. | Saves effort. |

**Suggested order:** #1, which is cheap and directly measured. Then #3(a), a one-flag screen. Then #2 if the KPI tail is still held by a few leaves. Then #4 for P95/P95high specifically.

## 6. Measured versus inferred

- **Measured:**
  - all tables in §1-§4;
  - the gap decompositions;
  - the move counts;
  - the block Lagrangian bounds (with "TL" marking bounds limited by the 30 s cap);
  - the cover-loop closures;
  - the LP-tree trajectories (one run each, on a busy machine; the trees are deterministic, but times are noisy).
- **Inferred:**
  - the "smear" reading of the decomposition (the LP is dual-degenerate);
  - that the covering form of the late rows is missed by the current separator;
  - the wall-clock estimates on the pool;
  - the extrapolation of tree size to 3e-4;
  - that work stealing helps where I4 failed.
- **Not done:**
  - LP trees on P95 (only per-node splits and blocks were measured there);
  - an HiGHS leaf-level comparison (the 30 s cap makes leaf MIPs uninformative at these gaps);
  - Gomory splits (the template found them below variable splits);
  - multiplier re-optimisation for the blocks.

## Literature [L]

- Achterberg, Koch, Martin, "Branching rules revisited", *Oper. Res. Lett.* 33 (2005): strong, pseudocost and reliability branching, and product scoring. https://doi.org/10.1016/j.orl.2004.04.002
- Ostrowski, Linderoth, Rossi, Smriglio, "Orbital branching", *Math. Prog.* 126 (2011). This is the symmetry method rejected here as not worth it for orbits of size ≤ 3. https://doi.org/10.1007/s10107-009-0273-x
- Gamrath, Berthold, Salvagnin, "An exploratory computational analysis of dual degeneracy in mixed-integer programming", *EURO J. Comput. Optim.* 8 (2020) [unverified details]. https://doi.org/10.1007/s13675-020-00130-z
- Gamrath, Melchiori, Berthold, Gleixner, Salvagnin, "Branching on multi-aggregated variables", CPAIOR 2015. Branching on aggregates such as counts and cumulative quantities [unverified details]. https://doi.org/10.1007/978-3-319-18008-3_10
- Caprara, Furini, Malaguti, "Uncommon Dantzig-Wolfe reformulation for the temporal knapsack problem", *INFORMS J. Comput.* 25 (2013). Year-block DW. https://pubsonline.informs.org/doi/10.1287/ijoc.1120.0521
- Related capopt notes: [GAP_ANATOMY_AND_DISJUNCTIONS.md](GAP_ANATOMY_AND_DISJUNCTIONS.md) (the method and the s2000 baselines), [BRANCHING_AND_TREE.md](BRANCHING_AND_TREE.md), [../EXPERIMENTS.md](../EXPERIMENTS.md) (E11/E12 strong branching, E1 leaf census, I4 re-splitting, KPI pass 1).
