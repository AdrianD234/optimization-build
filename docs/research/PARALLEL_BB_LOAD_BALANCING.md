# Parallel B&B load balancing and the E27 work-stealing audit

**Date:** 2026-10-16
**Question:** What do the parallel branch-and-bound literature and HiGHS' own semantics say a work-stealing layer must guarantee? Does E27 (`steal_after` in `solve_parallel` / `_solve_leaf`) keep those guarantees? What should the next iteration of load balancing look like?

## Setup

- **Code audited.** The uncommitted E27 working tree on `exp/e27-work-stealing`, as of 2026-10-16. That is `src/capopt/parallel.py` (`_init`, `_solve_leaf`, `solve_parallel`) and the plumbing in `src/capopt/solver.py`.
  - It includes the later guard `best_val.value < np.inf`, so there is no stealing before an incumbent exists.
  - It also includes `tests/test_work_stealing.py`.
  - Surrounding code read for context: `_finish`, `_branch`, `_sb_pick`, `_lp_node`, `ramp_up` and `split_bounds`.
- **Compute.**
  - Single-threaded HiGHS 1.15.1 (highspy), with `_solve_leaf` called in-process. The worker globals `_W` were filled by hand, with no pool and no multiprocessing.
  - A timing benchmark was running on the machine, so no `capopt campaign` or multi-process runs were made.
  - The scratch scripts are not in the repo. Each check is described well enough to re-implement.
- **Labels.**
  - **[M]** = measured here.
  - **[I]** = inferred, from code reading or argument.
  - **[L]** = from literature, with URL.
  - **[unverified]** = could not be confirmed from a primary source.
- **Data rule.** Only aggregates and statuses appear here. There are no project identifiers or schedules.

## Headline

1. **[I/M] No soundness bug.**
   - A stolen leaf is never closed with a partial bound.
   - Every stolen subtree is covered exactly once by its children.
   - The inherited bound is valid.
   - Global-bound reporting and termination stay correct.
   - All four invariants below pass.
2. **[M] Found: one liveness bug, a restart loop on LP-integral leaves.**
   - Trigger: a stolen leaf whose master LP has no fractional start candidate (`not node.frac`).
   - What happens: it is re-queued *unchanged*, with `sl=None`, and it can be stolen again. With a small `steal_after`, and in particular `steal_after=0.0` (which the new test uses), it restarts until the global time limit. Then it is closed at its start bound and the run comes back **unproven**.
   - Measured: such a leaf, dispatched with its flag set, returns `"stolen"` after 0 nodes. With the flag clear it is `"Optimal"` after 1 node.
   - The fix (below) is a few lines.
3. **[M] HiGHS' dual bound after a steal interrupt is valid, and steals land early.**
   - Checks: 15 forced interrupts on 3 ramp-up leaves with a known subtree optimum, plus 15 on leaves without one. No returned bound exceeded the subtree optimum.
   - The interrupt callback fires during root processing. A steal after 100 callback reads still had only 0–3 B&B nodes.
   - So a young leaf is stolen *inside its root*. Its root cut loop and heuristics are thrown away, and each child repeats them.
4. **[L] E27's design matches UG/ParaSCIP's choice of *which* leaf to take, but not *how*.**
   - UG takes nodes from the solvers with the weakest bound, as E27 does.
   - UG then *keeps the donor running* and ships a few open nodes. It throttles how many solvers collect, and adapts the trigger thresholds.
   - E27 kills the donor's whole HiGHS search and gets back only 2 children per steal. The highest-value change: split a stolen leaf into as many children as there are idle workers.

## 1. Literature [L]

Sources were checked against publisher, ZIB, Optimization Online or COR@L records. Full text was read where marked.

### 1.1 Surveys

- **Gendron & Crainic (1994)**, "Parallel branch-and-bound algorithms: survey and synthesis", *Operations Research* 42(6):1042–1066. https://doi.org/10.1287/opre.42.6.1042
  - The classic taxonomy of parallel B&B, by type of parallelism and by synchronous vs asynchronous designs.
  - Only the abstract was reached. Specific load-balancing content is [unverified].
- **Crainic, Le Cun & Roucairol (2006)**, "Parallel branch-and-bound algorithms", ch. 1 of *Parallel Combinatorial Optimization*, Wiley, pp. 1–28. https://doi.org/10.1002/9780470053928.ch1
  - Metadata verified. Full text not reached, so its content is [unverified].
- **Ralphs, Shinano, Berthold & Koch (2018)**, "Parallel solvers for mixed integer linear optimization", *Handbook of Parallel Constraint Reasoning*, Springer, pp. 283–336. https://doi.org/10.1007/978-3-319-63516-3_8. Free: https://optimization-online.org/wp-content/uploads/2017/05/5993.pdf. Full text read.
  - In an asynchronous run, no process knows the exact global lower bound, because nodes in progress or in transit are not in any queue.
  - Pruning needs only a node's own bound and *any* valid upper bound. The global lower bound is a progress measure, and making it exact needs synchronisation.
  - A child processed right after its parent can warm-start from it, so rebalancing too eagerly costs that warm start.
  - Separates work *sharing* (centralised) from work *stealing* (started by the processor with too much or too little work). Discusses ramp-up and ramp-down, where ramp-down starts once the first processor is permanently idle.
- **Koch, Ralphs & Shinano (2012)**, "Could we use a million cores to solve an integer program?", *Math. Methods of OR* 76(1):67–93. https://doi.org/10.1007/s00186-012-0390-9. Free: ZIB-Report 11-40, https://optimization-online.org/wp-content/uploads/2012/03/3408.pdf. Full text read.
  - Parallel overhead comes in four kinds:
    - communication;
    - idle time in ramp-up and ramp-down;
    - idle time from latency and contention;
    - redundant work, which comes mostly from changed node order and from knowledge not yet known locally.
  - A *racing* ramp-up runs the root with different settings, keeps the winner's tree, and discards everything else except primal solutions. As of 2012 it had not paid back the throughput it lost early on.
  - Ramp-up wants wide, balanced trees.
  - Moving open nodes back to a coordinator is expensive at scale. Checkpointing only subtree roots loses the work done below them.

### 1.2 UG / ParaSCIP / FiberSCIP (Shinano, Achterberg, Berthold, Heinz, Koch et al.)

- **ParaSCIP**: Shinano et al., *Competence in HPC 2010*, Springer, pp. 135–148. https://doi.org/10.1007/978-3-642-24025-6_12. ZIB-Report 10-27: https://opus4.kobv.de/opus4-matheon/files/971/ZR-10-27.pdf. Full text read.
  - **Invariant.** A subproblem is sent as bound changes against the presolved model. The LoadCoordinator *keeps every node it has handed out until that solver reports it finished*, so no subtree can be lost.
  - **Invariant.** New incumbents go through the coordinator to all solvers, which prune at once.
  - **Invariant.** The coordinator's best dual bound covers only pool nodes, not nodes being solved. Termination: the pool is empty and every solver is idle.
  - **Collecting mode.**
    - A pool node is "good" if its relative gap to the global dual bound, (NodeDualBound − GlobalDualBound)/max(|GlobalDualBound|, 1), is below a threshold.
    - When fewer than p good nodes are in the pool, selected solvers switch to best-bound node order. They then alternate between solving nodes and shipping them to the coordinator, and stop once there are more than mp·p good nodes.
    - A solver whose subtree bound is much worse than the pool's best hands its own subtree back ("delays" it) and takes a better node.
  - Checkpoints store only *primitive* nodes, those with no ancestor in the coordinator. This takes far less I/O, but work below them is lost on restart.
- **Solving open MIP instances with ParaSCIP on up to 80,000 cores**: Shinano et al., IPDPS 2016, pp. 770–779. https://doi.org/10.1109/IPDPS.2016.56. ZIB-Report 15-53: https://opus4.kobv.de/opus4-zib/files/5640/ZR-15-53-rev.pdf. Full text read.
  - Solvers enter collecting mode **in ascending order of their lowest open-node bound** (weakest first, as E27 picks).
  - At most one solver collects at first. That cap rises by one each time the pool stays empty for 10 s, and p doubles if collecting modes recur within 10 s. Collecting stops above 1.5·p heavy nodes.
  - In collecting mode, nodes are sent lowest bound first, with ties going to the node with the fewest bound changes (the largest subtree).
  - "Bulk sending": the coordinator says how many nodes to send.
  - Ramp-down rule: if fewer than 90% of solvers are active for 10 s, the coordinator collects only, then redistributes.
- **FiberSCIP**: Shinano, Heinz, Vigerske & Winkler, *INFORMS J. Computing* 30(1):11–30, 2018. https://doi.org/10.1287/ijoc.2017.0762. Free: https://optimization-online.org/2017/04/5944/. Full text read.
  - At first, solvers could talk to the coordinator only at node selection, so expensive nodes could not be interrupted. Communication points were added after bound changes, LP solves and new incumbents.
  - The **solver**, not the coordinator, decides to swap out a weak subtree, because the coordinator's information is stale.
- **UG overview**: Shinano, "The Ubiquity Generator framework: 7 years of progress in parallelizing branch-and-bound", *OR Proceedings 2017*, Springer 2018, pp. 143–149. https://doi.org/10.1007/978-3-319-89920-6_20. ZIB-Report 17-60.
  - Supervisor-worker design. Base solvers keep their trees, and UG pulls only a few subproblems out of them for balancing.
- **Hard MIPLIB2003 problems with ParaSCIP: an update**: Shinano et al., IPDPSW 2014, pp. 1552–1561. https://doi.org/10.1109/IPDPSW.2014.174
  - Defines three phases: ramp-up, primary, ramp-down.
  - Racing can move to its second stage without waiting for every solver, and falls back to normal ramp-up if the winner has too few nodes.

### 1.3 CHiPPS / ALPS (Xu, Ralphs, Ladányi, Saltzman)

- Xu, Ralphs, Ladányi & Saltzman, "ALPS: a framework for implementing parallel tree search algorithms", Springer ORCS 2005, pp. 319–334. https://doi.org/10.1007/0-387-23529-9_21
- Same authors, "Computational experience with a software framework for parallel integer programming", *INFORMS J. Computing* 21(3):383–397, 2009. https://doi.org/10.1287/ijoc.1090.0347. Free: https://optimization-online.org/2007/09/1786/
- Xu, *Scalable Algorithms for Parallel Tree Search*, PhD thesis, Lehigh 2007. https://coral.ise.lehigh.edu/~ted/files/papers/YanXuDissertation07.pdf
- Points:
  - Master-hub-worker structure. Load balancing has three tiers: static (initialisation), intra-cluster (run by the hub) and inter-cluster (run by the master).
  - Balancing looks at both the **quantity** of work and its **quality** (node bounds).
  - Work moves as whole subtrees. A donor with a single subtree **splits it** by backtracking from its best node to a root holding about half the nodes.
  - **Invariant.** Termination uses Mattern's four-counter method:
    - the system looks idle;
    - every message sent has been received;
    - the counts are unchanged over a second sweep.
- **PEBBL** (successor of PICO): Eckstein, Hart & Phillips, *Math. Prog. Computation* 7(4):429–469, 2015. https://doi.org/10.1007/s12532-015-0087-1. Free: https://optimization-online.org/wp-content/uploads/2013/10/4077.pdf
  - Termination needs true quiescence. Matching message counts is necessary but not sufficient, because the counts are not measured at the same moment.
  - A cluster's load is weighted by how far each subproblem's bound sits from the incumbent.

### 1.4 Work stealing, termination detection, restarts

- **Blumofe & Leiserson (1999)**, "Scheduling multithreaded computations by work stealing", *JACM* 46(5):720–748. https://doi.org/10.1145/324133.324234
  - A thief takes the *oldest* (topmost) item from a random victim's deque. The oldest item tends to be the biggest piece of work, so steals are rare: expected time T1/P + O(T∞).
- **Karp & Zhang (1993)**, *JACM* 40(3):765–789. https://doi.org/10.1145/174130.174145
  - Randomised local best-first B&B runs within a constant factor of an inherent lower bound. Details beyond the abstract are [unverified].
- **Mattern (1987)**, "Algorithms for distributed termination detection", *Distributed Computing* 2(3):161–175. https://doi.org/10.1007/BF01782776
- **Dijkstra, Feijen & van Gasteren (1983)**, *IPL* 16(5):217–219. https://doi.org/10.1016/0020-0190(83)90092-3. Token-ring details are [unverified from the paper].
- **Kumar, Grama & Vempaty (1994)**, "Scalable load balancing techniques for parallel computers", *JPDC* 22(1):60–79. https://doi.org/10.1006/jpdc.1994.1070
  - Round-robin, nearest-neighbour and random-polling schemes. Content [unverified]; the points come through Ralphs et al. 2018.
- **Fischetti, Monaci & Salvagnin (2014)**, "Self-splitting of workload in parallel computation", CPAIOR 2014. https://doi.org/10.1007/978-3-319-07046-9_28
  - Coverage without communication: every worker runs the same deterministic sampling, then keeps its own colour class of nodes.
- **Anderson, Hendel, Le Bodic & Viernickel (2019)**, "Clairvoyant restarts in branch-and-bound search using online tree-size estimation", AAAI-19, 33(01):1427–1434. https://doi.org/10.1609/aaai.v33i01.33011427
  - **The cost of a restart.** Forcing a SCIP restart after 1000 nodes costs 4.7% on average.
  - Restarts that are triggered only when a tree-size estimate stays high gain 7–18% on instances needing 50k–500k nodes.
- **Hendel, Anderson, Le Bodic & Pfetsch (2022)**, "Estimating the size of branch-and-bound trees", *INFORMS J. Computing* 34(2):934–952. https://doi.org/10.1287/ijoc.2021.1103
  - Tree size cannot be approximated within a factor of 2 unless P=NP.
  - Learned estimates get within a factor of 3 early on. So "steal the largest subtree" must rely on rough estimates.

### 1.5 What the literature implies

**(a) Correctness invariants for a work-stealing B&B**

| # | Invariant | Literature basis |
|---|---|---|
| I1 | **Global bound validity.** The reported dual bound is the minimum over *every* open subtree: queued, running, in transit and closed. A subtree in transit must still be counted. | Ralphs et al. 2018 (exact global LB needs synchronisation); ParaSCIP (the coordinator keeps handed-out nodes) |
| I2 | **Conservation.** Every subtree is at all times in exactly one of {queue, running, closed}. A steal moves it running → queue as a set of children that **partition** it. Nothing is lost and nothing is duplicated. | ParaSCIP (nodes kept until the solver reports); ALPS (whole subtrees move) |
| I3 | **No premature closing.** A subtree is closed only with a bound proven *for the whole subtree*: optimal, infeasible or pruned. A partial bound from an interrupted solve must reopen it, or count as a lower bound only. | ParaSCIP/UG ("delayed" subtrees go back to the pool) |
| I4 | **Termination.** All work is accounted for: queue empty, nothing running, no message in flight. Centralised designs get this for free. Distributed ones need Mattern/Dijkstra-style detection. | Mattern 1987; ALPS; PEBBL |
| I5 | **Incumbent and cutoff consistency.** The incumbent only improves. Any bound a node derived under a cutoff U is valid for solutions better than U, so it stays valid when the incumbent falls below U. | ParaSCIP (incumbent broadcast); standard B&B argument |
| I6 | **Liveness.** Each steal must make progress on the subtree, by shrinking it or keeping its search state. A steal that hands back the same subtree unchanged can loop. | [I], no direct source; implicit in UG's "collect nodes, keep solving" design |

**(b) Design guidance**

- **When to steal.**
  - Steal when the queue is empty or holds no "good" nodes, and workers are idle (UG: below p good nodes; ramp-down when fewer than 90% of solvers are active for 10 s).
  - Throttle it: start with one collector and add more only if starvation persists. Make the thresholds adaptive (UG doubles p).
  - Do not rebalance too eagerly, because the warm start and search state are lost (Ralphs et al.).
- **Which node to steal.**
  - Weakest bound first, i.e. the subtree holding the global bound (UG collecting order).
  - Among equals, take the largest subtree, meaning the fewest bound changes (UG) or the oldest deque item (Blumofe-Leiserson).
  - Size estimates are rough, within about 3x at best (Hendel et al.).
- **How much to steal.**
  - Enough to fill the idle workers in one go: UG's bulk sending, and ALPS splitting a subtree at about half its nodes.
- **The cost of a restart.**
  - Losing the tree costs a few percent per restart in SCIP (Anderson et al.).
  - Racing and checkpoint restarts discard the losers' trees and the work below primitive nodes (Koch et al.; ParaSCIP).
  - UG avoids most of this by *not* stopping the donor.

## 2. E27 audit against the invariants

### 2.1 Status paths in `_solve_leaf` (question 1) [I, M]

The checks run in order: pruned → Optimal → stolen → raw HiGHS status.

| HiGHS ends with | Steal flag | `_solve_leaf` returns | Master does | OK? |
|---|---|---|---|---|
| Infeasible | any | `pruned`, bound +inf | close | yes. Infeasible under the leaf's cutoff means no better solution in the subtree (I5) |
| Any non-optimal status, bound ≥ best − gap | any | `pruned`, bound max(bound, best) | close | yes. The prune test runs before the steal test, so a prune/steal race is resolved as a prune, which is sound |
| Optimal | any | `Optimal`, bound max(bound, obj) | close | yes. The flag is ignored, which is correct because the subtree is solved |
| Interrupted by user (the steal fired), bound < best − gap | 1 | `stolen` | re-split | yes |
| Time limit reached on a slice | 1 | `stolen` | re-split, same as a sliced leaf | yes. Only the `steals` counter is inflated |
| Time limit reached at the global limit (unsliced) | 1 | `stolen` | re-split *after* the deadline; children close at dispatch (`remaining <= 0`) with max(child LP, parent bound) | yes for soundness. Costs one `_lp_node` + `_branch` after the deadline (§2.5) |
| Time limit reached at the global limit | 0 | raw status | close with the partial bound | as before E27. Valid as a lower bound; the run is reported unproven |
| Interrupted by user (prune fired in the callback, but the post-run test fails) | 0 | raw status | close with the partial bound | as before E27. Unproven, not unsound. It should not happen: the callback and post-run bounds come from the same `lower_bound` (§2.4), and `best` only decreases. See R5 for hardening |

- The flag is read *after* `h.run()`. It cannot change in between: only the master writes `steal[slot]`, and it resets it only when dispatching a new leaf into that slot. A slot returns to `free_slots` only after the master has processed this leaf's result.
- **[M]** Measured: 30 forced steals across 6 ramp-up leaves of `P100_raw_nopenalty` (L=59). The flag flipped after 1, 2, 5, 20 or 100 callback reads, and every run returned `stolen`.
- **[M]** An unflagged slot never returned `stolen`; the new unit test covers this.
- **I3 holds. Verdict: PASS.**

### 2.2 Coverage of a stolen subtree (question 2) [I]

- `_lp_node(splits)` returns `None` only on proven LP infeasibility, after a cold retry for any other status. An LP with cuts is a relaxation of the subtree, so closing at +inf is correct.
- `not node.frac`: the same `splits` go back to the queue. That is coverage by itself, so nothing is lost or duplicated. **But see the liveness bug, §2.6.**
- Otherwise `_branch` returns two children `(p, s, 0)` and `(p, s, 1)`, i.e. start ≤ s and start > s.
  - `split_bounds` maps both to complementary option ranges (`hi = j` and `lo = j + 1` for the same j), so they **partition** the parent.
  - A child that is `None` is LP-infeasible and correctly dropped.
  - `_sb_pick` always returns a pair, because scores are ≥ eps² > −1 and `node.frac` is non-empty.
- Every path ends in `continue` or pushes. No path both pushes and closes.
- **I2 holds. Verdict: PASS.**

### 2.3 Steal-flag races (question 3) [I]

| Race | Analysis | OK? |
|---|---|---|
| The flag is set just as the leaf finishes naturally | The flag goes up after the worker computed its status: the result is unaffected. The flag goes up before HiGHS stops: the leaf ends `stolen` (re-split) or `Optimal`/`pruned` (closed). All of these are sound. | yes |
| The victim is chosen from `running` after its future completed but before it was processed | The flag is set on a finished leaf, with no effect. The same iteration's `done` loop discards it from `stealing`, and the next dispatch into that slot resets the flag. | yes |
| Slot reuse | `steal[slot] = 0` happens before `pool.submit` and in the same process, so it is visible to the worker before the task starts. Every submit goes through that line. | yes |
| `free_slots` running out (slot −1) | Not reachable. `running` is at most `workers − len(lns)`, which is at most `workers` = the number of slots, and slots are freed when their futures complete. A slot −1 leaf would simply never be stealable. | yes |
| Stale `stealing` | The entry is cleared when the victim's future completes. The victim always completes, at the latest at its HiGHS time limit. While HiGHS is inside a sub-MIP heuristic the interrupt is not checked (`!mipsolver.submip` in `checkLimits`), so a steal can be delayed. That only blocks further steals; it cannot deadlock. | yes (latency only) |
| `live[slot]` written after the master resets it | Callbacks stop before `h.run()` returns, so before the future is done. | yes |

**Verdict: PASS.**

### 2.4 Bound inheritance (question 4) [L, M, I]

**HiGHS source** (master branch; the same structure as in 1.15.1 is [I]):

- In `HighsMipSolver::run()`, every iteration flushes each worker's open dive nodes back to the global queue (`search_ptr_->openNodesToQueue(nodequeue)`).
- *Then* it updates `lower_bound = min(upper_bound, nodequeue.getBestLowerBound())`.
- *Then* it calls `checkLimits()`, which is where `kCallbackMipInterrupt` fires and sets `kInterrupt`.
- `cleanupSolve()` sets `dual_bound_ = lower_bound`, rounds it up if the objective is integral, adds the offset, and caps it at the primal bound.
- Sources: https://github.com/ERGO-Code/HiGHS/blob/master/highs/mip/HighsMipSolver.cpp and https://github.com/ERGO-Code/HiGHS/blob/master/highs/mip/HighsMipSolverData.cpp (`checkLimits`).

So after a user interrupt, `mip_dual_bound` is the minimum over all open nodes, dive nodes included, capped by the leaf's own incumbent. That is a valid lower bound on the subtree.

**[M]** Steals were forced at 1, 2, 5, 20 or 100 callback reads on the six leaves of a 6-leaf ramp-up of `P100_raw_nopenalty`.

- On the 3 leaves solved to optimality (`Optimal`/`pruned` in about 100 s): all 15 stolen bounds were ≤ the subtree optimum.
- On the other 3 leaves (time-limited reference): all 15 stolen bounds were ≤ the reference's 120 s bound, which is consistent.
- Stolen bounds rose with the read count. That is root cut progress, which the children inherit.

**Children's bound** = max(child LP, parent's returned bound).

- Both terms are valid for the child, because the child is a subset of the parent.
- With `leaf_cutoff`, the parent's bound β is only *conditionally* valid: it holds for solutions better than the cutoff U the leaf started with.
- β is returned only when β < best (otherwise the leaf is `pruned`), and best ≤ U only ever decreases. By I5 the bound therefore remains valid for pruning.
- It is also valid for reporting, because `_finish` caps the dual bound at `best`.

**Verdict: PASS.**

### 2.5 Termination and the global time limit (question 5) [I]

- The master is a central coordinator. Every subtree is always in `heap`, in `running` (as a future), or in `closed`, and futures are the only "messages". So `while heap or running` is exact termination detection, and no Mattern-style counting is needed (I4).
- `bound_now` takes the minimum over closed, the heap minimum, and max(start, live) of each running leaf. A victim in flight is still in `running`, and its children are pushed in the same step that pops it (I1).
- **Time limit.** Leaves get `time_limit = remaining`. Past the deadline, queued children close at dispatch with their bound. The only overrun comes from re-splitting leaves that came back `stolen` at the deadline (one LP plus a strong-branching round). That overrun existed before E27 for sliced leaves too, and is about a second.
- A steal issued in the last seconds wastes the victim's work. Its children are dispatched with `max(0.5, remaining)`, so they can overrun by 0.5 s (also as before E27). See R4.
- Every steal either shrinks the subtree, by adding a split on a fractional candidate, or hits the integral case in §2.6. The number of splits along a path is finite, so **with a finite time limit the loop terminates**.
- **With `time_limit = inf`, the §2.6 loop does not terminate.**

**Verdict: PASS for a finite time limit. The proof-completion part depends on fixing §2.6.**

### 2.6 BUG (liveness): the restart loop on LP-integral leaves (question 6) [M + I]

**Mechanism** (`solve_parallel`, the re-split branch):

```python
if not node.frac:  # LP integral yet the MIP is open: let it run to the end
    heapq.heappush(heap, (max(b, r["bound"]), seq, splits, None)); seq += 1; continue
```

1. Before E27, this branch was reached only from a time slice, and `sl=None` really did mean "run to the end".
2. Under E27, `sl=None` leaves are still stealable. The same `splits` come back, are re-dispatched from scratch, and once they are `steal_after` old they are stolen again.
3. Stealing only happens when the tree is at most half busy. That is the end game, exactly when such a leaf is likely to be the last one running.
4. With `steal_after=0.0` (as in `test_aggressive_stealing_reaches_the_same_proven_optimum`), the steal check that runs right after dispatch flags the leaf within microseconds. Its first interrupt callback then stops it.
5. The cycle repeats until `remaining <= 0`. The leaf is then closed at its start bound, below the incumbent, and the run reports **proven = False**.

**[M] Minimal reproduction (worker side), single-threaded:**

1. Use `P100_growth_penalised` at L=18. Get an incumbent from a 10 s root solve.
2. Draw random leaves that fix every project at the incumbent except k = 1–5 projects (`default_rng(0)`). Keep the leaves whose master LP has `frac == []`.
3. Results:
   - Three such leaves are solved by HiGHS presolve (`Optimal`, 0 nodes) even with the flag set. They are safe.
   - The fourth (2 free projects) returns **`stolen` after 0 nodes** with the flag set, and `Optimal` after 1 node with it clear.
4. The same leaf dispatched by the master as the only running leaf (≤ half busy, empty queue) with `steal_after=0.0` follows the cycle above. That master-side step is by code reading: it is exactly the `not node.frac` branch.

Why `frac` can be empty while HiGHS still branches:

- `frac` looks only at the start columns, with a 1e-6 threshold on the master's LP (with its cuts, its warm basis and no presolve).
- HiGHS solves a presolved model whose LP optimum can be a different, fractional vertex.
- **[I]** On the 233-project models with `steal_after` of a few seconds, this needs an LP-integral leaf that HiGHS cannot finish within `steal_after`. That is rare, but it is the end-game case.

**Fix** (proposed only; `src/` is not edited by this study): pin such leaves so they are never stolen.

```python
# with the other E27 state
pinned: set[int] = set()  # seq ids of leaves that must run to the end (LP-integral re-queues)

# dispatch: carry the seq id k
running[fut] = (b, splits, sl, sl is not None and tl < remaining, slot, time.time(), k)

# steal candidates
cands = [(max(rv[0], live[rv[4]]), rv[4]) for rv in running.values()
         if rv[4] >= 0 and rv[6] not in pinned and now - rv[5] >= steal_after]

# completion
for fut in done:
    b, splits, sl, sliced, slot, started, k = running.pop(fut)
    ...
    if not node.frac:  # LP integral yet the MIP is open: let it run to the end, never steal it again
        pinned.add(seq)
        heapq.heappush(heap, (max(b, r["bound"]), seq, splits, None)); seq += 1; continue
```

- Optionally, also `pinned.discard(k)` when the future completes, to keep the set small.
- A cheaper alternative with no tuple change: re-queue these leaves with `sl = np.inf` and skip `rv[2] == np.inf` in `cands`. That works because `min(remaining, inf) = remaining` and `sliced` stays False. It is more obscure, though.
- **Regression test:**
  - Run `_solve_leaf` with the flag set on an LP-integral leaf that HiGHS does not presolve away. It returns `stolen`, which shows the hazard.
  - Then run a master-level check that a pinned entry is never in `cands`.
  - The existing `steal_after=0.0` end-to-end test only passes on instances where no such leaf arises.

**Verdict: I6 FAIL until fixed.**

### 2.7 Other observations [I]

- **Steal storms.**
  - A steal replaces the victim with children younger than `steal_after`, and only one steal is in flight at a time (`not stealing`). So apart from §2.6, the same subtree cannot be stolen back to back.
  - Small `steal_after` values do turn the end game into a master-driven LP B&B. It is sound and terminates, but each child redoes HiGHS' root. **[M]** 100 callback reads in, a leaf still had ≤ 3 nodes, so a young leaf is stolen inside its root.
- **Starvation.**
  - The weakest-bound victim is the one holding the global bound, which is the right target for proving. A strong-bound leaf with a huge subtree is never stolen, but it cannot hold back the proof either.
  - One steal yields only 2 children. With w idle workers, it takes about log2(w) rounds of at least `steal_after` each to fill them (R1).
- **`leaf_cutoff` / `cutoff_after`.**
  - Children dispatched after `cutoff_after` get the shared incumbent as `objective_bound`, so restarts pick up the latest cutoff, which helps.
  - The bound semantics stay valid (§2.4, I5).
  - Restarting under a cutoff changes the HiGHS search path, which exp05 found can hurt. This is a performance question, not a correctness one.
- **Utilization accounting.**
  - `busy_s += dt * len(running)` counts the leaves dispatched *at the start of this iteration* over a `dt` that includes the previous iteration's re-split work, while those workers were idle. It also counts finished but unprocessed futures as busy.
  - Both bias utilization upward when the master is slow, e.g. during strong branching on the master.
  - The exact figure is Σ over the census of (leaf wall time) / cap_s, since `census` already records `time.time() − started`.
  - Minor: `low_s` uses `< tree_workers / 2`, but the steal trigger uses `<= tree_workers // 2`.
- **`steals` counter.** It also counts leaves that hit a slice or the global time limit while flagged. Log `stolen` separately from those, plus the HiGHS node count discarded per steal (`r["nodes"]`), to measure the restart cost directly.

## 3. Invariants checklist for E27

| Invariant | Verdict | Evidence |
|---|---|---|
| I1 global bound validity (incl. in transit) | **PASS** | §2.4, §2.5 [I, M, L] |
| I2 conservation / partition, no lost or duplicated subtree | **PASS** | §2.2 [I] |
| I3 no premature closing of a stolen leaf | **PASS** | §2.1 [I, M] |
| I4 termination detection | **PASS** (central coordinator) | §2.5 [I] |
| I5 incumbent/cutoff consistency | **PASS** | §2.4 [I] |
| I6 liveness: every steal makes progress | **FAIL** (LP-integral re-queue loop; only the time limit ends it) | §2.6 [M + I] |
| Global time limit honoured | **PASS** (about 1 s re-split overrun at the deadline, 0.5 s child minimum, as before E27) | §2.5 [I] |
| Flag races / slot reuse / slot −1 / stale `stealing` | **PASS** | §2.3 [I] |

## 4. Design recommendations, ranked by expected value

1. **R0: Fix §2.6. Must do before any E27 timing campaign.**
   - Pin LP-integral re-queues as unstealable (code above).
   - Cost: about 5 lines. Without it, a run can lose its proof in the end game. `steal_after=0.0` in the test is the worst case.
2. **R1: Split a stolen leaf into enough children to fill the idle workers. [I, L]**
   - **Why.** E27 pays a full HiGHS restart but gets back only 2 children. With 10 idle workers, filling them takes about 3–4 steal rounds, each at least `steal_after` long.
   - **How.** Run a small best-first LP expansion of the victim's node (`ramp_up` started from `node` instead of the root, with strong branching on the pool) until it has about `tree_workers − len(running) + 1` open leaves. Every child inherits max(LP, r["bound"]).
   - This matches UG's bulk sending and ALPS's split-to-half. It turns one restart into a full refill. It is also the cheapest way to amortise the root-restart cost seen in §2.7.
3. **R2: Make the steal trigger adaptive and progress-aware. [L, I]**
   - Steal only a victim whose live bound has stalled: little bound movement over the last `steal_after` seconds. Workers can also publish their node count in a shared `nodes[slot]` array (from `e.data_out.mip_node_count`), so a leaf is never stolen inside its root (nodes == 0). The root is where a restart loses the most per second.
   - Adapt like UG: double `steal_after` when a steal's children come back `stolen` or sliced again, and halve it when idle time persists.
   - Keep weakest-bound-first as the victim rule (UG's collecting order). Break ties by fewest splits, i.e. the larger subtree.
   - A steal only pays if the victim's remaining work is well above one root restart. The restart-cost literature puts that cost at a few percent per restart (Anderson et al. 2019).
4. **R3: Measure the restart cost before tuning further. [I]**
   - Log, per steal: the victim's age and nodes (`r["nodes"]`); its bound gain over its LP start; and how long its children took to close.
   - Switch utilization to the exact census-based figure (§2.7).
   - Without these, E27's benchmark result cannot be told apart from the slicing it duplicates.
5. **R4: No steals near the deadline. [I]**
   - Skip the steal check when `remaining < max(2 * steal_after, 5 s)`. A late steal throws away the victim's search, and its children cannot finish anyway.
   - Also, when a `stolen` leaf returns at or after the deadline, close it with `r["bound"]` instead of re-splitting. That removes the post-deadline LP and strong-branching round.
6. **R5: Treat any unpruned "Interrupted by user" as open (hardening). [I]**
   - Map every unpruned `"Interrupted by user"` to `"stolen"`, so it is re-split and never closed with a partial bound.
   - It should be unreachable today (§2.1). It costs one line and closes the last path that could leave a proof incomplete.
7. **R6 (longer term): steal without killing. [L]**
   - UG and FiberSCIP keep the donor running and ship *some* of its open nodes. HiGHS exposes no API for exporting open nodes, so the nearest equivalent is to restart the victim *on one child* and queue the rest.
   - Dispatch first the child that contains the parent's best LP point, and pass the shared incumbent to every child as a solution hint (`setSolution`). `ProcessPoolExecutor` cannot pin a task to a particular worker, so locality is lost either way.
   - The HiGHS search tree is still lost. Worth trying only after R1–R3 show restarts are the bottleneck.

---

# E28 racing audit

**Date:** 2026-10-16
**Question:** Is E28 (`race_after` / `max_racers` in `solve_parallel`) sound? It runs several copies of one open leaf on idle workers, closes the leaf when any copy finishes it, and takes the best bound over the copies. Does it still keep the invariants of §1.5, and how does it interact with E27 stealing?

## 5.1 Setup

- **Code audited.** Commit `051f224` on `exp/e28-racing`: the group bookkeeping and the racing block in `solve_parallel`, the per-group live bound in `bound_now`, and the group-aware completion loop. `_solve_leaf` is unchanged from E27 (with the E27 audit's pinning fix, `sl = np.inf`, already in).
- **Compute.** Same rules as §Setup: single-threaded, in-process, no pool. A timing campaign was running.
- **Two checks.**
  - **Master harness [M].** The real `solve_parallel` master loop runs with an in-process thread-pool stand-in for `ProcessPoolExecutor` and a scripted `_solve_leaf`. The script decides, per (group, copy), when the copy ends, with what status and bound, whether it obeys the stop flag, and whether it raises. Instance `P100_growth_penalised`, L = 18, 3 ramp-up leaves, the best-known schedule as incumbent. Only the master's own LPs (ramp-up, re-split) run HiGHS. The `closed` list is captured at `_finish`. Seven scenarios, S1–S6b (§5.4).
  - **HiGHS check [M].** `_solve_leaf` was called directly on the 6 ramp-up leaves of the same model, with different seeds and different `objective_bound` cutoffs (§5.3).
- **Scratch scripts** are not in the repo. Each scenario below is described well enough to re-implement.

## 5.2 Headline

1. **[I/M] No soundness bug.**
   - The group bound max(copies) is valid, even though copies ran under different cutoffs (§5.3).
   - Each group is closed, re-split or closed at its bound exactly once, in every interaction tried (§5.4).
   - The per-group live bound in `bound_now` is valid.
   - With `race_after=None`, E28 behaves exactly as E27: every group has one member, so the code paths reduce to the old ones.
2. **Four robustness and liveness bugs, none of which can give a wrong answer.**
   - **B1 [M]** A copy whose result would be ignored can still crash the solve, by raising.
   - **B2 [M]** The run waits for stopped copies whose results it will ignore.
   - **B3 [M]** E27 and E28 work against each other: E27 kills raced copies one at a time, and can pick an already-stopped copy as its victim.
   - **B4 [I]** Racers never yield to queued work. This matters only when racing is combined with slicing or E27.
3. **B5:** three counters and the docstring are slightly off (§5.7).
4. **[L]** Racing is the classic ramp-up of UG/ParaSCIP and of the commercial solvers' concurrent mode. The literature says it pays only modestly, and mostly through variability. E28 uses it only at ramp-down, on one subtree. "Best bound over configurations" is the "light communication" rule of Carvajal et al. (2014), so E28's bound combination has a direct precedent.

## 5.3 Bound validity (question 1) [I, M, L]

**Argument [I].**

- Notation:
  - z\* is the optimum of the group's subtree.
  - Copy i started with HiGHS `objective_bound` U_i: the shared incumbent when *that copy* started, or +inf when `cut` is False.
  - β_i is the bound copy i returned or wrote to `live`.
- For a single copy, HiGHS gives **z\* ≥ min(β_i, U_i)**.
  - β_i is the minimum bound over open nodes (§2.4).
  - Every node pruned against U_i holds no solution below U_i.
- The incumbent only decreases, and each U_i is a past value of it. So at any later time t, best_t ≤ U_i for every copy. Hence:
  - z\* ≥ max_i min(β_i, U_i) ≥ max_i min(β_i, best_t) = **min(max_i β_i, best_t)**.
- So max over copies is a valid bound in exactly the sense every bound in this code is valid: *for solutions better than the incumbent*.
  - `_finish` reports min(closed + [best]).
  - Pruning compares against `tol()` ≤ best.
  - `bound_now` includes `best_val`.
  - Re-split children compare max(child LP, group bound) against `tol()`.
  - All of these are uses of min(·, best).
- Different cutoffs across copies do not matter, because only the *smallest* U enters, and best is below all of them.

**The winning copy was "pruned" against a later, better incumbent.**

- `pruned` means β_w ≥ best_ret − gap, where best_ret is the incumbent when the copy returned. So z\* ≥ min(β_w, U_w) ≥ best_ret − gap.
- The closed value max(r.bound, group bound) ≥ best_ret ≥ best_final. So it never pulls the reported dual below best. **Sound.**

**The winner is "Optimal" while the group bound is above its objective.**

- This needs some copy j with β_j > z\*. By the inequality above, that forces U_j ≤ z\*, so the incumbent was already as good as anything in the subtree.
- The closed value then exceeds z\* but is ≥ best, and min(closed + [best]) ignores it. **Sound.**

**`bound_now`.**

- Per open group it takes max(group bound, max over members of max(start, live[slot])).
- Closed groups are skipped, because their value is already in `closed`.
- A just-dispatched racer's slot reads −inf until the worker writes its start bound. The master resets `live[slot]` when the slot's previous future is processed, so that read is conservative.
- **Valid.**

**Literature [L].**

- Carvajal et al. (2014) run several solver configurations on the same MILP. In their "light communication" mode they record the best lower bound and the best upper bound over all configurations, so "one configuration [can provide] the optimal solution and another the best dual bound". That is E28's max rule.

**Measured [M]: HiGHS, 6 ramp-up leaves of `P100_growth_penalised`, L = 18.**

- **Subtree optima.** Each leaf was solved to the end with no cutoff and 4 seeds. All 4 seeds returned the same final bound per leaf, so the subtree optimum z\* is known for each.
- **Cutoff test.** On the 2 leaves whose seed-0 run ended `Optimal`, copies were run:
  - with cutoffs U ∈ {+inf, global optimum, z\* + 10⁻³|z\*|, z\* − 10⁻⁴|z\*|}, i.e. cutoffs above, at and *below* the subtree optimum;
  - with time limits of 0.3 s and 1.0 s, and seeds 0 and 1;
  - 32 interrupted or finished copies in all.
- **Result: 0 violations of z\* ≥ min(β_i, U_i)**, the per-copy inequality the max rule rests on.

**Verdict: PASS.**

## 5.4 No lost subtrees: exactly once (question 2) [I, M]

**Group life cycle (code reading).**

- A group is created at dispatch with one member. Members leave only in the completion loop, *after* `running.pop`.
- On a finishing status (`Optimal`, `pruned`, `empty`, `Infeasible`), the group closes once: `closed=True`, and later members hit the `if grp["closed"]` branch.
- On an open status, the group is re-split or closed at its bound only when `members` is empty.
- The two branches are mutually exclusive. The closed flag is set before any later member is processed. The open branch deletes the group before it pushes children.
- `del groups[g]` happens only when `members` is empty, so every running future's group exists. No `KeyError` is possible in `run_bounds`, the racing loop or the completion loop.
- Group keys are heap `seq` ids, which are unique.

**Scenarios (harness) [M].** Group 0 is the scripted leaf; the other two leaves close at once. "closed entries" counts the `closed` list at `_finish`.

| # | Scenario | Result | OK? |
|---|---|---|---|
| S1 | The original runs 5 s and obeys stop. A racer (started at 0.57 s) finishes `Optimal` at 0.88 s | 3 closed entries (one per group). The original is stopped at 0.89 s and ignored. `races: 1 (1 won by a later copy)`. proven, 0.9 s wall | yes |
| S2 | As S1, but the original ignores stop for 3 s, then returns `Optimal` | 3 closed entries, and the late `Optimal` is ignored. **Wall 3.1 s instead of 0.9 s** (B2) | sound; latency |
| S3 | Slices of 2 s. Original and racer both time out on the slice | `re-splits: 1`, i.e. exactly one re-split of group 0, from the last member, with the max bound. Children close; 4 closed entries | yes |
| S4 | E27 (`steal_after=1`) with racing (`max_racers=2`). The original's live bound is lower than the racer's | E27 stops the original at 1.1 s (open; the racer remains, so nothing happens), then stops the racer at 1.6 s, which re-splits the group. Log: `steals: 1` although two copies were killed | sound; B3, B5 |
| S5 | A racer wins. The stopped original *raises* when it sees the stop flag | **`solve_parallel` raises `RuntimeError`**. The run is lost, although that copy's result would have been ignored (B1) | robustness |
| S6 | The original finishes before `race_after` is reached | no racer, 3 closed entries | yes |
| S6b | The master is slowed by 0.3 s per iteration, as during a re-split LP. A racer starts 0.3 s before the original finishes `Optimal` | the group is closed once by the original. The racer is stopped at its next poll and ignored | yes |

**Interactions asked about.**

- **A racer starts just as the original finishes.** The racing block can see a group whose futures are done but not yet processed, because `running` still holds them. It then starts a racer on a finished subtree. In the same iteration's completion loop, the original closes the group and sets the racer's flag, and the racer's result is ignored (S6b is the near-miss version). Sound, but it wastes one start-up. A cheap filter: skip groups with `any(f.done() for f in grp["members"])`.
- **The original is stolen by E27 while racers run.** It returns `stolen`. Members remain, so nothing happens, and the racers carry the subtree (S4). Sound. The steal is pure waste, though, and `steals` does not count it (B3, B5).
- **A stopped copy returns `Optimal` or `pruned` after the group closed.** It is ignored, which is fine. The group was closed with a whole-subtree proof. The ignored copy's incumbents are not lost: they went through `_offer` into shared memory during the solve, and `trace.extend(r["found"])` runs *before* the closed check.
- **A member raises.** `fut.result()` propagates out of `solve_parallel`, as it did before E28. E28 adds exposure: a copy that was *told to stop*, and whose result is irrelevant, can now kill the run (S5, B1). Whether HiGHS can actually raise after a user interrupt is [unverified]. None was seen in §2's 30 forced interrupts or in the harness runs, which use a fake leaf.

**Verdict: PASS (exactly once). B1 is a robustness gap.**

## 5.5 Slot and steal-flag hygiene (question 3) [I]

| Item | Analysis | OK? |
|---|---|---|
| Flag reset at dispatch | Both the queue dispatch and the racer dispatch set `steal[slot] = 0` before `pool.submit`. | yes |
| Stop is set only on live members | The closing loop flags `running[f][4]` for f in `members`. All members are still in `running`, because a member leaves `members` only after `running.pop`. So the slot still belongs to that member. | yes |
| A flag set on a done-but-unprocessed member | No effect. The slot is freed when that member is processed, and the next dispatch resets the flag. | yes |
| The flag is read *after* `h.run()` (E27 audit §2.1) | E28 adds a writer: a copy that ended naturally (for example a slice timeout) just before its group closed can come back `stolen` instead of `Time limit reached`. Its group is closed, so it is ignored. | yes |
| `free_slots` | Racing requires `free_slots` non-empty *and* `len(running) < tree_workers`. Stopped copies stay in `running` and keep their slot until they return, so the count of slots in use equals the number of running tree tasks. Slot −1 stays unreachable. | yes |
| E27 `stealing` | Cleared when the victim's future completes, as before. **But** E27's candidate list does not exclude stopped copies of closed groups. The weakest live bound often *is* such a copy, because racing targets the weakest group, so E27 can "steal" a dead copy and then block real steals until that copy returns. | B3 |
| `live[slot]` | Reset to −inf when a slot's future is processed. The worker writes its start bound when it starts. No stale value from another task can be read. | yes |

**Verdict: PASS, with B3 as an inefficiency when E27 and E28 are combined.**

## 5.6 Termination and the time limit (question 4) [I, M]

- **A stale group cannot block `while heap or running`.** The loop condition does not use `groups`, and `del groups[g]` happens as each group's last member returns. The only way the loop waits is a live future in `running`, which always ends, at the latest at its HiGHS time limit.
- **But the loop also waits for copies whose results it will ignore (B2).**
  - The stop flag is honoured only at HiGHS' interrupt callback, which is skipped inside sub-MIPs (§2.3).
  - The project notes also record HiGHS running far past `time_limit` on one model.
  - S2 measured a 2.2 s delay from a copy that ignored stop for 3 s. When the last open group is closed by a racer, the run's wall time becomes that of the slowest-stopping loser.
  - For a timing benchmark this is a direct, avoidable cost.
- **Racers' deadlines.**
  - A racer gets `max(0.5, grp["deadline"] − now)` and is started only if at least 1 s remains. So it never outlives its group's slice, or the global deadline, by more than its start-up time. Originals have the same start-up overrun.
  - An unsliced group's racers run to the global deadline. At the deadline, the last member to return closes the group at the best bound (`Time limit reached`, not sliced), which is valid (§5.3).
  - A pinned group (`sl = inf`) can be raced. E27 still never steals any of its members, because `rv[2]` is copied to the racers.
- **Liveness of queued work (B4) [I].**
  - Racers occupy workers until their group closes or their deadline passes. They are never pre-empted when the queue refills.
  - With racing alone (no slicing, no E27), the queue never refills after the initial dispatch, apart from rare unexpected interrupts. So B4 cannot arise in that configuration.
  - With `slice_seconds`, a re-split's children wait up to a racer's remaining slice.
  - With E27, the children of a stolen leaf can wait behind racers of an *unsliced* group until the global deadline. E27 itself rarely fires, because racers keep the tree more than half busy.
  - The queued children partition real work, while racers duplicate it.
  - UG ends racing as soon as it switches to normal work distribution, so racers should yield.

**Verdict: PASS for termination and the time limit. B2 is a wall-time cost; B4 needs a fix before racing is combined with slicing or E27.**

## 5.7 Accounting (question 5) [I, M]

- **`races`**
  - Counts copies started, including racers started on an already-finished group (S6b) or killed by E27.
  - This is fine as a count of starts. Add the counts of racers stopped and racers stolen to make it interpretable.
- **`racer_wins`**
  - Computed as `started > grp["first"] + 1e-6`, which compares two separate `time.time()` reads.
  - It is correct here, because `time.time()` uses `GetSystemTimePreciseAsFileTime` at 1e-7 s resolution on this machine (Python 3.13). With a coarse clock and `race_after≈0`, a racer could compare equal.
  - It counts a "later copy" win even when the original had been stolen, which is probably intended.
  - Fix: record racers explicitly, e.g. a `racers: set` of futures, and use `racer_wins += fut in racers`.
- **`steals`**
  - Incremented only when the group's *last* member returns `stolen`. E27 steals of non-last members are not counted (S4: 2 kills, `steals: 1`).
  - Count steals at flag time (victims) and outcomes per member.
- **`census`**
  - Includes ignored copies, which have status `stolen` and a partial bound, so "worst bounds" can list subtrees that were already closed.
  - Tag them (`status + "/ignored"`) or skip them.
- **Utilization**
  - Stopped copies count as busy, which is correct (the process is busy). The E27 audit's caveats (§2.7) still apply.
- **Docstring**
  - It says "whose first copy has run at least `race_after`". The code uses the oldest *still-running* member, so after the original is stolen the clock restarts at the oldest racer.
  - Either reading is defensible. Make the two agree.

## 5.8 Other risks (question 6) [I]

- **Racers inherit `cut` from the group.** A group dispatched before `cutoff_after` has `cut=False`, so its racers also run without a cutoff, even when `cutoff_after` has passed. That is a performance choice, not a correctness one (§5.3 holds for any cutoffs). Consider `leaf_cutoff or (cutoff_after is not None and now - t0 >= cutoff_after)` for racers.
- **Racer start rate.**
  - One racer per master iteration, and an iteration can block in `wait` for 0.5 s.
  - In the harness, racers started about 0.5 s after becoming eligible (S1: original at 0.06 s, racer at 0.57 s with `race_after=0.2`).
  - Filling k idle workers takes up to k × 0.5 s. Loop the racing block while slots, idle workers and candidates remain.
- **What racing buys.**
  - Every copy repeats the subtree's root work: presolve, root LP and cuts.
  - It pays only if seed-to-seed variability in the subtree's solve time is large compared with that cost.
  - **[M] Measured spread.** The 6 ramp-up leaves of `P100_growth_penalised`, L = 18, were each solved to the end with seeds 0–3. This was single-threaded and sequential, while a benchmark campaign was loading the machine, so the timings are noisy.
    - HiGHS `random_seed` does change the search: node counts differ by up to 4.2x on a leaf.
    - Per leaf, slowest/fastest seed time was 1.1x–1.9x.
    - Seed 0 versus the best of 4 seeds: 1.0x–1.8x, geometric mean 1.31x (1.18x for the best of 2).
    - The slowest leaf (about 30–39 s) was already fastest with seed 0, so racing every leaf would cut the critical path from 32.1 s to 29.8 s (−7%), with 2 copies or with 4.
  - So on this model, racing helps a leaf by about 24% on average when there are idle workers to spare. The run's critical path improves far less. Two copies get most of the gain.
  - The literature (§5.9) agrees: the gain from multiple seeds or bases is real but modest, and saturates after K = 2 in CPLEX's root cut-loop racing.
- **Pre-existing, outside E28 [unverified].**
  - `_highs` sets `mip_abs_gap = 1e-6` in *solver* (scaled) units, and `_solve_leaf` closes `Optimal` leaves at max(dual, obj).
  - If auto-scaling makes 1e-6 solver units larger than `rel_gap · |obj|` in user units, a leaf is closed at a bound up to that much above what HiGHS proved.
  - Not checked here; worth a one-line check against `MipBuilder.auto_scale`.

## 5.9 Literature on racing in parallel MIP [L]

- **UG/ParaSCIP racing ramp-up.** Shinano et al., "Solving open MIP instances with ParaSCIP on supercomputers using up to 80,000 cores", IPDPS 2016, https://doi.org/10.1109/IPDPS.2016.56. ZIB-Report 15-53: https://opus4.kobv.de/opus4-zib/files/5640/ZR-15-53-rev.pdf. Full text read for this section.
  - Every solver starts on the root, with different parameter settings and permutations of variables and constraints, so that the search trees differ.
  - After a set time, a winner is chosen by a combination of its lower bound and its number of open nodes. Its open nodes are collected and redistributed, and the other trees are discarded. **Only their feasible solutions are kept.**
  - The benefit: all solvers start at once, with no load balancing needed early on. Racing statistics can also seed the branching information ("deep probing").
  - The paper likens the commercial solvers' concurrent mode to running only ParaSCIP's racing stage. In ParaSCIP, when the racing stage ends is a run-time parameter.
- **Koch, Ralphs & Shinano (2012)** (§1.1). By 2012, racing ramp-up had not yet made up for the throughput it loses. **Carvajal et al. (2014)** report that racing ramp-up "has not proven to be very effective" (citing ParaSCIP).
- **Shinano et al. (2014)**, IPDPSW (§1.2). Racing can end without waiting for every solver. If the winner has too few nodes, it falls back to normal ramp-up.
- **Carvajal, Ahmed, Nemhauser, Furman, Goel & Shao (2014)**, "Using diversification, communication and parallelism to solve mixed-integer linear programs", *Operations Research Letters* 42(2):186–189. https://doi.org/10.1016/j.orl.2013.12.012. Preprint: https://optimization-online.org/2013/05/3900/. Full text read.
  - Several CPLEX configurations solve the same MILP in parallel.
  - "Light communication" records the best lower and upper bounds over all configurations. This is E28's bound rule, and it means one configuration can supply the solution and another the bound.
  - Sharing solutions and fathomed paths helped most. Sharing cuts helped least.
  - Sharing lower bounds through objective cuts was worse, because of dual degeneracy. E28 shares bounds only in the master, not as cuts, so this does not apply.
- **Fischetti & Monaci (2014)**, "Exploiting erraticism in search", *Operations Research* 62(1):114–122. https://doi.org/10.1287/opre.2013.1231. Abstract only.
  - Erraticism is presented as an unavoidable "chaotic amplifier" effect of exponential tree search, to be exploited rather than avoided.
  - Method, "bet-and-run": several short randomized runs, then the most promising one is run to completion.
  - E28 is a run-them-all variant applied at a leaf. It never picks a winner early.
- **Fischetti, Lodi, Monaci, Salvagnin & Tramontani (2016)**, "Improving branch-and-cut performance by random sampling", *Math. Prog. Computation* 8(1):113–132. https://doi.org/10.1007/s12532-015-0096-0. Full text read.
  - Variability from the choice among degenerate optimal LP bases. K root cut loops from different bases pool their cuts and solutions into a final run.
  - Adopted in CPLEX 12.5.1 with K = 2 concurrent cut loops: about 1–4% faster on its test set.
  - **K > 2 degraded performance**, because overhead grows with K while diversification saturates. This suggests that E28's default `max_racers = 4` is high.
  - The paper notes that similar ideas drive ParaSCIP's racing ramp-up.
- **Fischetti, Monaci & Salvagnin (2018)**, "SelfSplit parallelization for mixed-integer linear programming", *Computers & OR* 93:101–112. https://doi.org/10.1016/j.cor.2018.01.011. Preprint: https://www.dei.unipd.it/~salvagni/pdf/selfsplit2.pdf. Full text read.
  - Performance variability, modelled as heavy-tailed (Pareto) subproblem times, is what limits the scalability of static splitting.
  - Reducing variability can be worth it in a distributed setting "even if" it costs sequential performance.
  - This is the case *for* E28: when one leaf's solve time is heavy-tailed, the minimum over k independent copies trims the tail. It helps exactly at ramp-down, where one straggler holds the bound.
- **Lodi & Tramontani (2013)**, "Performance variability in mixed-integer programming", *INFORMS TutORials in OR*, pp. 1–12. https://doi.org/10.1287/educ.2013.0112. Abstract only. Variability from platform changes, row and column permutations and other "neutral" changes. Benchmarks must use several seeds.

**What this implies for E28.**

- Keep the losers' *solutions*, as UG does. E28 already gets these through `_offer`.
- Use few copies: K = 2 was CPLEX's sweet spot.
- Race only where the variability pays: the straggler that holds the global bound. That is E28's weakest-group rule.
- Stop racing as soon as partition work exists (UG's switch to normal ramp-up), which is B4's fix.

## 5.10 Bugs and fixes (proposed only; `src/` not edited)

**B1: a member's exception kills the run, even when its result is irrelevant.** Move the group lookup before `fut.result()` and tolerate a failure the group can absorb:

```python
for fut in done:
    b, splits, sl, sliced, slot, started, g = running.pop(fut)
    if slot >= 0:
        live[slot] = -np.inf
        free_slots.append(slot)
        stealing.discard(slot)
    grp = groups[g]
    grp["members"].discard(fut)
    try:
        r = fut.result()
    except Exception as exc:
        if grp["closed"] or grp["members"]:  # subtree already proven, or still covered by another copy
            log.append(f"leaf copy failed and was dropped: {type(exc).__name__}: {exc}")
            if not grp["members"]:
                del groups[g]
            continue
        raise  # the only copy of an open subtree: as before E28
    census.append(...)
    trace.extend(r["found"])
    if grp["closed"]:
        ...  # unchanged from here, minus the old `grp = groups[g]; grp["members"].discard(fut)`
```

**B2: waiting for ignored copies.** End the loop when only stopped copies remain, and stop late incumbent writes:

```python
def tree_open() -> bool:
    return bool(heap) or any(not groups[rv[6]]["closed"] for rv in running.values())

while tree_open():
    ...
with lock:
    stop.value = 1  # under the lock, so no _offer lands between this and _finish's read

# _offer (worker side):
with _W["lock"]:
    if obj < _W["best_val"].value and not _W["stop"].value:
        ...
```

- Without the `stop` guard, a leftover copy could write `best_starts` while `_finish` reads it. Its flag is already set and `pool.shutdown(wait=False, cancel_futures=True)` is unchanged, so leftover copies exit at their next callback.
- LNS tasks already end on `stop`. An improvement they find after the tree is proven lies within the gap, so dropping it is harmless.
- Alternative if the early exit is unwelcome: keep the loop and log the seconds spent waiting on closed groups, to measure B2 first.

**B3: E27 and E28 work against each other.** Steal only single-copy, open groups. Do not race a group with a pending steal:

```python
cands = [(max(rv[0], live[rv[4]]), rv[4]) for rv in running.values()
         if rv[4] >= 0 and rv[2] != np.inf and now - rv[5] >= steal_after
         and not groups[rv[6]]["closed"] and len(groups[rv[6]]["members"]) == 1]
...
# racing candidates:
if (grp["closed"] or grp["copies"] >= max_racers or grp["deadline"] - now < 1.0
        or any(f.done() or running[f][4] in stealing for f in grp["members"])):
    continue
```

**B4: racers yield to queued work** (needed only with slicing or E27). Keep a set of racer futures, `racers`, filled at racer dispatch and discarded on completion, plus a set `yielding`. Before the dispatch loop:

```python
if heap and len(running) >= workers - len(lns):  # queued partition work, no free worker: stop the youngest racer
    extra = [(running[f][5], f) for f in racers - yielding
             if not groups[running[f][6]]["closed"] and len(groups[running[f][6]]["members"]) > 1]
    if extra:
        f = max(extra)[1]
        steal[running[f][4]] = 1
        yielding.add(f)
```

- This is sound by §5.4. A yielded racer ends open. If by then it is its group's last member, the normal `stolen` re-split path covers the subtree.

**B5: accounting.**

- `racer_wins += fut in racers`.
- Count E27 steals at flag time.
- Tag ignored copies in `census`.
- Make the docstring describe the "oldest running copy" rule.
- Optionally loop the racing block, so all idle workers fill in one iteration.

**Regression tests (no pool needed).** The harness pattern of §5.1 works as a unit test: monkeypatch `P.ProcessPoolExecutor` with a thread-pool wrapper and `P._solve_leaf` with a scripted function, then assert on `closed` (captured by wrapping `P._finish`) and on the log. Suggested tests:

- S1: exactly one closed entry per group, and `racer_wins == 1`.
- S3: `re-splits: 1`.
- S5: no exception after the B1 fix.
- S2: wall time under 1.5 s after the B2 fix.

They run in a few seconds with no processes. The existing end-to-end `test_racing_happens_and_the_proven_optimum_is_unchanged` uses 6 processes and cannot hit S2 or S5 deterministically.

## 5.11 Invariants checklist for E28

| Invariant | Verdict | Evidence |
|---|---|---|
| I1 global bound validity, with the per-group max over live copies | **PASS** | §5.3 [I, M, L] |
| I1′ group bound = max over copies with different cutoffs | **PASS** (valid as min(·, best), which is how every consumer uses it) | §5.3 [I, M] |
| I2 conservation: each group closed, re-split, or closed at its bound exactly once | **PASS** | §5.4 [I, M: S1–S6b] |
| I3 no premature closing: a group closes only on a whole-subtree proof, and open copies never close a group while others run | **PASS** | §5.4 [I, M: S3, S4] |
| I4 termination | **PASS** (`groups` cannot block the loop) | §5.6 [I] |
| I4′ prompt termination | **FAIL (B2)**: the run waits for copies whose results it ignores | §5.6 [M: S2] |
| I5 incumbent/cutoff consistency, including ignored copies' incumbents | **PASS** (`_offer` plus `found` traced before the closed check) | §5.3, §5.4 [I] |
| I6 liveness of queued work | **PASS** with racing alone. **FAIL (B4)** with slicing or E27: racers are never pre-empted | §5.6 [I] |
| Robustness: an irrelevant copy cannot crash the run | **FAIL (B1)** | §5.4 [M: S5] |
| Slot and flag hygiene, `free_slots` | **PASS** | §5.5 [I] |
| E27 interplay | **PASS** for soundness. **Inefficient (B3)**: E27 kills raced copies and can pick dead ones | §5.4, §5.5 [M: S4] |
| Global time limit honoured | **PASS** (racers stay within the group deadline; at least 1 s left to start) | §5.6 [I] |
| Counters `races` / `racer_wins` / `steals` | **Partly**: `steals` undercounts, `census` includes ignored copies (B5) | §5.7 [I, M: S4] |

## 5.12 Summary

- **Soundness.** No bug. The max over copies' bounds is valid despite different per-copy cutoffs: z\* ≥ min(max β_i, best). Each group closes or re-splits exactly once in every interaction tried. `bound_now`'s per-group live max is valid. With `race_after=None` the behaviour is identical to E27.
- **Bugs to fix before an E28 timing campaign:**
  - **B2:** the run waits for ignored copies, measured at +2.2 s in a 3 s scenario. Fix: end the loop when only closed groups run, and guard `_offer` with `stop`.
  - **B1:** a stopped copy's exception kills the run. Fix: absorb the exception when the group is closed or still covered.
- **Before combining racing with E27 or slicing:**
  - **B3:** E27 must skip multi-copy and closed groups, and racing must skip groups being stolen.
  - **B4:** racers must yield to queued work.
- **Cosmetic (B5):** the counters and the docstring.
- **Tuning from the literature and measurement.**
  - Racing pays through variability and saturates quickly (CPLEX: K = 2).
  - Here, the best of 4 seeds was 1.31x faster per leaf than seed 0 (geometric mean), and the best of 2 was 1.18x faster. The critical path improved by only 7%.
  - So try `max_racers=2`. Racers should start for all idle workers at once rather than one per 0.5 s.
