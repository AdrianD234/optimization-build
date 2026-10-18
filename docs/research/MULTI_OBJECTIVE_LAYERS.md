# Multi-objective layers: PV sacrifice, activity-class adherence and regional fairness

**Date:** 2026-10-18.

**Question:** what mathematics and algorithms suit the three "distribution" layers being added to capopt?

- **Dimension scoring:** maximise a re-weighted benefit dimension subject to Total PV ≥ f·PV\*, with f swept from 100% to 85%.
- **Activity-class adherence:** spend shares by class in 3-year funding windows.
- **Regional fairness:** spend shares by region at cumulative checkpoints.

The question also covers how to run the resulting sweeps quickly and report them credibly.

**Method:** four research agents (academic sources only) worked on one theme each, and their findings are consolidated here:

1. PV-sacrifice frontiers;
2. fairness in public capital allocation;
3. category / time-window spend targets;
4. fast parametric re-solves.

**Labels:**
- [L] is literature (with a URL). The agents checked some references against abstracts or listing pages; any marked unverified in their reports are flagged here.
- [I] is inference or our own derivation.
- No CPO data appears in this document.

**Context:** capopt already reaches a certified 1% / 0.5% gap in seconds when warm-started (E37-E43). This is what makes sweeps of dozens of solves affordable.

## Headline

1. **Lumpy mega-projects make soft quotas nearly invisible to the LP bound.** [L, I]
   - The LP relaxation can spread a mega-project's spend across start years and hit every class or regional target almost exactly, while any integer schedule pays the full lumpy shortfall.
   - Convex penalties make this worse: the LP pays the penalty of the *average*, which is less than the average penalty (Jensen's inequality).
   - So the quota and fairness terms add little to the bound, and only decisions on the mega-projects' start years close that part of the gap.
   - Remedies:
     - enumerate the start years of the 2-4 largest projects as an outer loop, like the existing last-build-year (L) loop, with LP pruning;
     - a small deadband;
     - carve the mega-projects out of the targets, or attribute their spend to regions by benefit incidence;
     - region-specific tolerance floors.
2. **For the PV-sacrifice sweep, use an augmented ε-constraint plus a lexicographic polish, not a weighted sum.** [L]
   - A weighted sum misses unsupported points and spreads its points unevenly.
   - The augmentation term (AUGMECON) protects against weakly efficient points only when each solve is optimal. At a 0.5% gap the augmentation term sits inside the gap, so a quick second stage is needed: fix D ≥ D̂ − gap, then maximise PV (and then minimise carry).
3. **Sweep in the direction that makes reuse free, and certify the whole curve, not just the points.** [L, I]
   - **Lowering f** enlarges the feasible set, so the previous schedule is a valid warm start.
   - **λ-sweeps** change only the objective, so a pool of past schedules can be re-scored for free.
   - Monotonicity of the ε-frontier and concavity of z(λ) turn certified point solves into a certified band around the whole trade-off curve.
   - Choose points adaptively (chord / knee): about 8-15 solves instead of 30 or more, stopping once the band is narrower than the MIP gap.
4. **The regional design needs four refinements.** [L]
   - **Run an endogenous base next to the fixed entitlement base.** A fixed base confuses under-spending with unfairness under an envelope that is not fully used, and duplicates the holding-cash term.
   - **Use a region-specific lumpiness floor** (the largest project *in that region*), backed by fair-division theory (weighted PROP1; Tijdeman's chairman assignment problem). A global largest-project floor leaves small regions unconstrained.
   - **Add a worst-case term** (k-sum or max-min) so small regions count.
   - **Use denser (5-yearly) checkpoints** to stop the solver bunching spend just before each checkpoint.
5. **Report trade-offs as intervals, with the gap matched to the step.** [L, I]
   - The PV-per-fairness rate between two sweep points lies within an interval set by their primal and dual bounds.
   - At a relative gap g, neighbouring points must differ by more than about 4g·|z|, or the rate is noise.
   - Prefer frontier rates over LP duals. Never quote last-node duals.

## 1. PV-sacrifice frontier (dimension scoring)

- **Formulation.**
  - Row: `PV(x) − s = f·PV_ref`, with s ≥ 0.
  - Objective: `max D(x) − λ_c·carry(x) + ρ·s/R_PV`.
  - Keep λ_c fixed across the sweep, or move carry into a cap row (`carry ≤ (1+κ)·carry_ref`). Otherwise the problem is silently three-objective.
  - Scale the PV row to O(1) coefficients, because PV in the billions plus solver feasibility tolerances can admit violations of the floor.
  - Sources: Mavrotas 2009 AUGMECON, https://doi.org/10.1016/j.amc.2009.03.037; Mavrotas & Florios 2013 AUGMECON2, https://mpra.ub.uni-muenchen.de/105034; Ehrgott & Ruzika 2008, https://doi.org/10.1007/s10957-008-9394-2.
- **Lexicographic polish (stage 2).** Fix D ≥ D̂ − δ, maximise PV, then optionally minimise carry, warm-started from stage 1. One extra quick solve per point removes dominated points that the gap would otherwise hide. [I]
- **PV\* uncertainty.**
  - Use PV_ref = the incumbent of the max-PV solve, so f = 1 is always feasible.
  - Report the certified range f_true ∈ [f·PV_inc/PV_bound, f].
  - If a sweep solve finds a higher PV, update PV\*.
  - Expect the tight floor at f = 1 to be the slowest point.
- **Sweep.**
  - Anchors: f = 1 (lexicographic); the unconstrained max-D solve, whose f_free marks where the floor stops binding (no solves are needed below it); and max(0.85, f_free).
  - Refinement: split the interval with the largest chord deviation (Rote 1992, https://link.springer.com/doi/10.1007/BF02238642), or by coverage gap (Ceyhan, Köksalan & Lokman 2019, https://doi.org/10.1016/j.ejor.2018.06.012).
  - Bypass: if the solution at f_k has PV = f′·PV_ref with f′ > f_k, it is optimal on [f_k, f′], so jump past f′ (AUGMECON2 bypass).
  - Stop when the band is narrower than the gap.
- **Avoid:** exact full-frontier algorithms (balanced box, triangle splitting, boxed line, L-shape). There are likely thousands of non-dominated points, and our points are only 0.5%-optimal. Borrow their ideas instead: box splitting, and the early approximate frontiers L-shape produces. [L, I]
  - Boland, Charkhgard & Savelsbergh, https://doi.org/10.1287/ijoc.2015.0646 and https://doi.org/10.1287/ijoc.2015.0657.
  - Perini et al. 2020, https://doi.org/10.1287/ijoc.2019.0887.
- **Presentation:**
  - x-axis PV retained (%), y-axis dimension D, with the certified band shaded;
  - a marginal-rate table ("each 1% of PV buys ΔD"), with the knee marked;
  - the projects that change start year between neighbouring points (churn).
  - For three or more objectives, use value paths or parallel coordinates (Miettinen 2014, https://jyx.jyu.fi/handle/123456789/44819).

## 2. Activity-class adherence (category spend targets)

- **The closest analogue is open-pit mine scheduling:**
  - the same "started-by" time-indexed variables;
  - per-period capacity rows;
  - an NPV objective;
  - grade or blend share rows that behave like class shares.

  References: Chicoisne et al. 2012, TopoSort LP rounding, https://doi.org/10.1287/opre.1120.1050; Rivera Letelier et al. 2020, https://doi.org/10.1287/opre.2019.1965; Bienstock & Zuckerberg 2010. [L]
- **The encoding is already right.** Pulse and step variables give the same LP bound, and on/off formulations are weaker (Artigues, https://hal-univ-tlse3.archives-ouvertes.fr/hal-01461447). [L]
- **Penalty.**
  - Soft, shortfall-only, piecewise-linear and convex, with window weights.
  - Prefer gentler slopes plus a **2-5% deadband**, so integer schedules can also reach zero penalty. Steep penalties widen the Jensen gap.
  - Keep slopes within about 10³ of the PV coefficients.
  - Add a hard backstop only where a subset-sum check shows an integer schedule can meet it.

  References: Tamiz, Jones & Romero 1998 (goal programming: normalise deviations, beware dominated solutions); Brown, Dell & Wood 1997, elastic constraints and persistence, https://apps.dtic.mil/sti/pdfs/ADA576179.pdf. [L]
- **Branch on the lumps first.** [I]
  - Enumerate the start years of the k = 2-4 largest projects, each within ±2 years of its LP or incumbent start, as an outer product with the L loop, with LP pruning.
  - This is the best route to a certified 0.5% gap when quotas bind.
- **Primal.**
  - Class × window LNS: free a class's projects in windows w−1..w+1 plus small projects of other classes.
  - Greedy repair: shift or swap the worst-shortfall class.
  - Both are MIP starts. Seed from the no-quota optimum or TopoSort rounding. (Related methods: RINS, local branching, kernel search.)
- **Decomposition.**
  - **By project:** gains nothing, since the "one start year" polytope is integral, so the Lagrangian bound equals the LP bound (Geoffrion 1974).
  - **By class:** column generation could remove the within-class Jensen gap, but HiGHS has no branch-and-price, so keep it as a last resort.
  - **Lagrangian pricing of the quota rows:** useful as a diagnostic and a price-steered heuristic (Kimms 2001; Möhring et al. 2003, https://doi.org/10.1287/mnsc.49.3.330.12737).
- **Cuts (optional):** c-MIR on the 5-20 lumpiest quota rows, with the dominant mega-project's spend as the divisor (Marchand & Wolsey 2001).
- **Practice:** NZTA's Investment Prioritisation Method prioritises nationally *by activity class*, so class adherence matches current practice
  (https://www.nzta.govt.nz/planning-and-investment/planning-and-investment-knowledge-base/202427-nltp-investment-requirements/202427-nltp-investment-prioritisation-method/investment-prioritisation-method-document).

## 3. Regional fairness

- **Framing.** Regions with different targets are a *balance* problem, not an equitability problem (Karsu & Morton 2015, https://doi.org/10.1016/j.ejor.2015.02.035). The price of fairness should be read off our own frontier: Bertsimas, Farias & Trichakis 2011, https://doi.org/10.1287/opre.1100.0865, whose worst-case bounds are too loose to be informative here. [L]
- **NZ context.** NZTA's method has no regional formula. Regional fairness is therefore a policy overlay; international precedents are the US Highway Trust Fund minimum guarantee (a shortfall-from-entitlement floor of 90.5-92%) and EU cohesion need-based formulas. [L]
- **Linear, solver-friendly measures:**
  - shortfall from entitlement;
  - min-max and max-min shares;
  - mean absolute deviation;
  - k-sum / OWA (Ogryczak & Tamir 2003);
  - weighted EF1 / PROP1 tolerances;
  - Gini with a *fixed* normaliser.

  Not linear: Gini over an endogenous total, Theil, Atkinson, α-fairness and Σ log. [L]
- **Design refinements, all linear:**
  - **Base.** Run both the fixed entitlement base and an endogenous base (`s_S·Σ_R spend_R(w)`). The difference measures how much "unfairness" is really under-spending. With the endogenous base, shortfall-only is exactly half the L1 deviation.
  - **Tolerance.** `δ_{S,w} = max(τ·s_S·B_w, κ·c^max_S(w))`, where c^max_S(w) is the largest single project's possible cumulative spend in region S by w. This is the weighted PROP1 / Tijdeman result: tracking within one item is always achievable.
  - **Aggregation.** Normalise shortfalls by entitlement, and add a worst-case term (k-sum with k = 2-3, or a sum + max-min blend) so small regions count. A pure max term gives a flat, degenerate LP, so blend it.
  - **Checkpoints.** Use 5-yearly or annual checkpoints with geometric decay instead of three cumulative checkpoints, which invite bunching just before years 10, 20 and 30. Cumulative weights of 4:2:1 also double-count early spend.
  - **Catch-up fairness.** A near-hard final checkpoint (with a large-penalty slack) and soft earlier ones (Lodi et al. 2024, https://arxiv.org/abs/2101.03716).
  - **Mega-projects.** Carve-out, or a benefit-incidence attribution matrix `spend_S(w) = Σ_j a_{jS}·cash_j(w)`.
  - **Measure choice.** Spend share is an input measure; appraisal theory favours outcomes such as accessibility and benefit (Martens 2012; Pereira et al. 2017; Meng & Yang 2002). Optimise against spend or a blend, and *report* per-capita spend, benefit share and a need index after the fact. Cost-share targets are circular: the pipeline sets its own target.
  - **Calibration.** Pick the operating point from a price-of-fairness frontier (an ε-constraint on the maximum relative shortfall; Camporeale et al. 2019, https://doi.org/10.1016/j.tra.2018.04.006), not from preset weights.

## 4. Fast sweeps, shadow prices and alternatives

- **Reuse.**
  - Lowering f: warm-start from the previous optimum and the pool (primal reuse).
  - Raising f: the previous dual bound stays valid.
  - λ: re-score the whole pool at the new λ before solving.
  - HiGHS cannot carry its tree between solves, and it has no solution pool, so the pool must live in capopt's B&B layer.

  References: Gamrath, Hiller & Witzig 2015, https://optimization-online.org/2015/05/4898/; SYMPHONY warm solves; Ralphs, Saltzman & Wiecek 2006, https://optimization-online.org/?p=9615. [L]
- **Certified curve.**
  - z(λ) = min over x of [PV-loss(x) + λ·unfairness(x)] is concave and piecewise linear.
  - The pool's lower envelope bounds it from above, and certified lower bounds at the λ grid points interpolate linearly.
  - So a few certified solves certify a band around the *whole* curve.
  - Solve next where the envelope lines cross (Eisner-Severance).
  - For ε-sweeps, use a staircase sandwich (Ehrgott & Gandibleux bound sets, https://hal.archives-ouvertes.fr/hal-00462004). [L, I]
- **Shadow prices, labelled by type.**
  - (a) Frontier rates as intervals: the primary measure.
  - (b) λ stability ranges for each schedule: reaches supported points only.
  - (c) Conditional LP duals with the start years fixed: "marginal cost with the schedule frozen" (O'Neill et al. 2005).
  - Never last-node duals: integer programs need price *functions* (Wolsey 1981). [L]
- **Diverse near-optimal schedules (MGA).**
  - Use the linear distance `d(x, ŝ) = n − Σ_p x_{p,ŝ_p}`.
  - Run a Hop-Skip-Jump / max-min loop under `objective ≤ (1+δ)·z*` with δ ∈ {1, 2, 5}%; or pick k = 3-5 schedules by max-min dispersion from the pool, which costs no extra solves.
  - Report each alternative's regret across the sweep grid. Neumann & Brown found a wide range of alternatives within 0.5% of the optimal cost.

  References: Brill et al. 1982; Neumann & Brown 2021, https://arxiv.org/abs/1910.01891; Danna & Woodruff 2009; Ahanor et al. DiversiTree, https://arxiv.org/abs/2204.03822. [L]

## 5. Ranked experiments, mapped to the layered plan (fail-fast: screen to 1% / 0.5%)

| Stage | Experiment | What it decides |
|---|---|---|
| A1 dimensions | Anchors: unconstrained max-D (f_free), plus f ∈ {1, 0.99, 0.95, 0.90, 0.85}. Time, D, slack and gap at each | Is f = 1 pathologically slow; is the curve flat below f_free |
| A1 | Plain ε vs augmented (ρ = 1e-3, 1e-4) vs augmented + lexicographic polish | How much weak efficiency the gap hides |
| A1 | Downward sweep with warm starts and bypass vs independent cold solves; adaptive (chord / knee) vs uniform grid at 10 solves | Sweep cost per frontier |
| A2 class | Gap diagnostic: root-LP quota penalty vs the incumbent's | Confirms the Jensen / lumpiness gap |
| A2 | Penalty shape: linear / 3-segment / 3-segment + 3% deadband / soft + −30% backstop / hard ±10% | Fastest adherent shape |
| A2 | Mega-project start-year enumeration (k = 2, 3) with LP pruning vs monolithic | Route to a certified 0.5% gap |
| A2 | Class × window LNS + greedy repair as a MIP start | Primal speed |
| A3 region | 2×2: fixed vs endogenous base × mega-projects in vs carved out | Under-spending vs distribution; carve-out value |
| A3 | Floor: global largest-project vs region-specific vs none | Small-region protection, PV cost |
| A3 | Aggregator: sum / sum + max-min / k-sum (k = 3); checkpoints: 3 cumulative vs 5-yearly | Fairness quality, gaming, solve time |
| B/C | Pool re-scoring for λ; gap vs rate fidelity (g = 1%, 0.5%, 0.1%); MGA diversity at δ = 2% | Sweep engineering and decision outputs |
