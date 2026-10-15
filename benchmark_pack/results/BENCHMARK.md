# Benchmark results

Measured 2026-10-15T11:58:57 on 20 threads (Windows-11-10.0.26300-SP0), MindOpt 2.3, gap target 0, time limit 900 s per MindOpt solve; heuristic budget 30 s. Times in seconds. Objective: minimise (lower is better).

| Instance | Best-known objective | Method | Status | Gap | Time to within 1% | Time to within 0.1% | Time to best-known | Total time |
|---|---|---|---|---|---|---|---|---|
| P100_raw_penalised | -7.408929e+09 | MindOpt cold | OPTIMAL | 0.0000% | 5 | 32 | 211 | 232 |
| | | heuristic (25 s, +431.19%) then MindOpt | OPTIMAL | 0.0000% | 84 | 120 | 332 | 356 |
| | | original pipeline (greedy start then MindOpt) | OPTIMAL | 0.0000% | | | | 329 |
| P100_growth_penalised | -1.362491e+10 | MindOpt cold | OPTIMAL | 0.0000% | 94 | 105 | 177 | 179 |
| | | heuristic (24 s, +103.81%) then MindOpt | OPTIMAL | 0.0000% | 94 | 155 | 187 | 192 |
| | | original pipeline (greedy start then MindOpt) | OPTIMAL | 0.0000% | | | | 152 |
| P100_raw_nopenalty | -7.411953e+13 | MindOpt cold | TIME LIMIT | 0.0007% | 8 | 28 | 704 | 903 |
| | | heuristic (24 s, +0.21%) then MindOpt | TIME LIMIT | 0.0018% | 28 | 49 | - | 928 |
| | | original pipeline (greedy start then MindOpt) | TIME LIMIT | 0.0044% | | | | 302 |
| P100_growth_nopenalty | -1.389382e+14 | MindOpt cold | OPTIMAL | 0.0000% | 10 | 10 | 107 | 132 |
| | | heuristic (24 s, +0.00%) then MindOpt | OPTIMAL | 0.0000% | 28 | 28 | 148 | 200 |
| | | original pipeline (greedy start then MindOpt) | OPTIMAL | 0.0000% | | | | 59 |

Notes
* "Time to within x%" = first time MindOpt's incumbent was within x% of the best-known objective (from the solver log, whole seconds).
  "Time to best-known" uses the log's precision (within 1e-6 relative); the rest of the run is proving optimality.
  For the heuristic-then-MindOpt rows, times include the heuristic's run time.
* MindOpt run times vary between runs of the same instance (P100_growth_nopenalty proved optimal in under 30 s in a smoke test, 132 s here).
* P100_raw_nopenalty has not been proven optimal by any run (best gap 0.0007% after 900 s): many near-equal schedules make the final proof slow.
* All returned schedules were re-scored with reference/evaluate.py and match the MindOpt objective.
