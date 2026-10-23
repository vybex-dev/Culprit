# A real regression in a real open-source project: `tabulate`

Everything on this page was measured, not assumed. The history is upstream's, unmodified.

- **Repo:** https://github.com/astanin/python-tabulate (pure Python, no dependencies)
- **Range:** `v0.9.0` (`bf58e37e6b35e3cc9a0bd740f752abfd32b6e6f8`) → `v0.10.0` (`3b4cd509820e4c45cd2aaba833aa585ea6308b94`), 205 commits
- **What got slower:** formatting a table of text cells took **~49 ms → ~95-100 ms (about 1.9x)**
- **Introduced by:** `8c9c9e75` "Support better type deduction" (Perry Kundert, PR #243). `_type()` now also tries a thousands-separator regex (`_isnumber_with_thousands_separator`) for every cell that isn't already a number, and does so twice per text cell.

## Benchmark (one shell command, no files needed in the repo)

```
python -c "import time; from tabulate import tabulate; rows=[['item-%d'%i,'user%d@example.com'%(i%97),'north' if i%2 else 'south','SKU-%05d'%i,'note %d'%(i%13),'ok'] for i in range(2000)]; tabulate(rows[:50]); f=lambda: (lambda t: (tabulate(rows), time.perf_counter()-t)[1])(time.perf_counter()); print(round(min(f() for _ in range(25))*1000,3))"
```

It reports the best of 25 timed calls, a standard way to filter out noisy-neighbour spikes. Run it with commit range `bf58e37e6b35e3cc9a0bd740f752abfd32b6e6f8` → `3b4cd509820e4c45cd2aaba833aa585ea6308b94`.

## What we measured

| Check | Result |
|---|---|
| Parent of the guilty commit (`cecb08e9`), 12 runs of a best-of-7 variant | 50.0-53.9 ms |
| Guilty commit (`8c9c9e75`), same | 94.0-110.0 ms |
| `git bisect run` with a min-of-7 threshold between the tags | lands on `8c9c9e75` |
| Culprit (offline stand-in models), 205 commits | 9 commits benchmarked; baseline 48.3 ms; blames `f0437c12` |
| Culprit's profile of the blamed commit | `_isnumber_with_thousands_separator`: 0 → 600,600 calls |

## Reading the result honestly

- Culprit blames **`f0437c12`, the merge of PR #243**, not `8c9c9e75` itself. That is the correct mainline answer: the merge's first parent (`048a22c5`) is fast, the merge is slow, and the merge brings in exactly the guilty commit. The diff Culprit shows the Diagnoser is the merge's diff against its first parent, which contains it.
- The run above used `CULPRIT_OFFLINE=1`: bisection, sandboxes, benchmark numbers and profiling are real, but the models are the labelled stand-ins, so the diagnosis came back `other`. We have not run this case with live Nemotron.
- On a noisy shared machine a naive 3-iteration benchmark was inconclusive in some runs (and Culprit correctly refused to guess); the best-of-25 form is what makes it stable.
- This is a feature commit that cost speed, not a bug. A good fix keeps the feature and avoids the regex for cells that cannot contain a thousands separator.
