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
