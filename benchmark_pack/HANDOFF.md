# Handoff - starter context for the new optimiser

## Goal

Build an optimiser that beats the MindOpt benchmark on these instances: reach the best-known objective (or within 1% / 0.1%) faster, or find better schedules within the same time. Same instances, same scoring (`reference/evaluate.py`).

## Where the problem comes from

A capital programme optimiser for a transport investment portfolio: projects have real cost profiles (P50, $M by build year) and 40-year benefit streams; a fixed annual envelope funds them; MindOpt schedules start years. The research question behind these instances was **benefit growth**: when benefits grow faster than the discount rate (1.5-2%), a pure PV objective defers the best projects to capture later, larger benefits.

## What is already known

* **No-penalty instances are easy to solve well**: simulated annealing gets within 0.0-0.3% in 30 s. Proving optimality is another matter: many near-equal schedules.
* **Penalised instances are the real test.** The steep holding-cash bands (1,000 to 1,000,000 per $0.01M-year) make the landscape cliff-like: most single moves either break feasibility (negative balance) or push cash into an expensive band. The baseline heuristic stays 100-430% above the optimum.
* In effect the penalised objective asks for **cumulative spend to track cumulative funding within about $35M every year** until the last build year. Projects have lumpy profiles (a few have zero-spend years mid-build) and durations of 1-16 years.
* Growth instances defer fast-growing, high-BCR projects; raw instances build best-BCR-first. A "growth-neutral schedule" (solve the raw instance, then value it with growth) captures about 96-97% of the growth-optimised PV.

## Ideas worth trying

* Time-indexed structure: each year's spend should be close to the envelope - think bin-packing / scheduling with a cumulative resource rather than a generic MIP.
* Large-neighbourhood search: free 10-20 projects (or a 5-10 year window), re-optimise exactly (DP or a small sub-MIP), repeat.
* Use the LP relaxation (or the no-penalty optimum) to guide rounding / fix-and-optimise.
* Warm-starting MindOpt from the weak baseline heuristic did not help: a warm start has to be good to pay for itself. Most of MindOpt's time on the penalised instances goes into finding the last fraction of a percent, then proving it.

## Practical notes

* MindOpt uses a floating licence: one solve at a time, keep all solves inside one process; killing a process mid-solve can lock the seat for a while.
* Reference times were measured on a 20-thread Windows machine (MindOpt 2.3). Re-run `reference/run_benchmark.py` on your own machine for like-for-like times.
* `excel/` holds the original workbook (live formulas, the full 100-project model) as a cross-check; it is not needed to run anything.
