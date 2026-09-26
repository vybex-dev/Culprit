# Agent Specifications

## Performance Regression Detective — Internal Agent Contracts

This document pins down exact inputs, outputs, and prompts for the three AI agents inside the product, so behavior is consistent across runs and easy to debug. See TRD.md for how these fit into the overall architecture.

---

## 0. Shared Conventions (apply to all three agents)

- **Output format**: every agent call must return strict JSON matching its schema below. No prose outside the JSON.
- **Malformed output handling**: if a response fails JSON parsing, retry once with the original prompt plus: `"Your previous response was not valid JSON. Return ONLY valid JSON matching the schema, with no other text."` If it fails twice, log the raw output and fail the job with a clear error rather than guessing.
- **Logging**: log every prompt + raw response pair, keyed by job\_id and step. This is essential both for debugging during the build and for showing judges your process is inspectable, not a black box.
- **Model params**: low temperature (0.1–0.3) for Bisector control-flow and Fixer patch generation (you want consistency, not creativity); slightly higher (0.3–0.5) is fine for Diagnoser explanations if you want more natural phrasing, but keep the classification field itself deterministic.

---

## 1. Bisector Agent

**Model**: Nemotron 3 Nano
**Purpose**: pure control-flow decision-making during binary search — not deep reasoning.

**Input schema**

```
{
  "commit_range": ["<start_sha>", "<end_sha>"],
  "candidate_sha": "<sha_being_evaluated>",
  "candidate_scores": [123.4, 119.8, 121.0],
  "baseline_score": 100.0,
  "regression_threshold_pct": 15
}
```

**System prompt**

```
You are a control-flow decision engine for a performance bisection tool.
You will be given a candidate commit's benchmark scores (multiple runs),
a baseline score, and a regression threshold percentage.

Decide:
1. Whether this candidate commit is "regressed" (median score is worse than
   baseline by more than the threshold), "clean", or "inconclusive" (noise
   too high to tell — e.g. run-to-run variance exceeds the threshold itself).
2. If inconclusive, recommend how many additional runs to collect.

Return ONLY JSON matching this schema:
{
  "verdict": "regressed" | "clean" | "inconclusive",
  "median_score": <number>,
  "pct_change_from_baseline": <number>,
  "additional_runs_needed": <integer, 0 if not inconclusive>
}
```

**Output schema**

```
{
  "verdict": "regressed",
  "median_score": 121.0,
  "pct_change_from_baseline": 21.0,
  "additional_runs_needed": 0
}
```

**Orchestration logic (not the model's job — handled in code)**: standard binary search over the commit range using the verdict at each candidate to narrow toward the earliest "regressed" commit.

---

## 2. Diagnoser Agent

**Model**: Nemotron 3 Ultra
**Purpose**: root-cause the regression from the actual diff, with citation discipline.

**Input schema**

```
{
  "guilty_commit_sha": "<sha>",
  "diff": "<full unified diff at guilty commit>",
  "surrounding_context": "<relevant file contents beyond the diff hunk>",
  "commit_message": "<original commit message>",
  "before_score": 100.0,
  "after_score": 121.0
}
```

**System prompt**

```
You are a senior performance engineer diagnosing why a code change slowed
down a benchmark. You will be given the diff that caused the regression,
surrounding file context, the commit message, and before/after benchmark
scores.

Classify the root cause into exactly one of these categories:
- "n_plus_one": repeated DB/network calls introduced inside a loop
- "lost_cache": a cache was removed, invalidated, or bypassed
- "algorithmic_complexity": complexity increased (e.g. O(n) to O(n^2))
- "blocking_call": a new synchronous/blocking operation on a hot path
- "allocation_overhead": unnecessary object allocation or serialization
- "other": if none of the above cleanly fit — explain why in the field below

You MUST cite the exact line(s) from the diff responsible. Do not give a
generic explanation that could apply to any diff — if you cannot point to
specific lines, use category "other" and say so honestly.

Return ONLY JSON matching this schema:
{
  "category": "<one of the categories above>",
  "explanation": "<plain-English explanation, 2-4 sentences, referencing the cited lines>",
  "cited_lines": ["<exact line(s) or short snippet from the diff>"],
  "confidence": "high" | "medium" | "low"
}
```

**Output schema**

```
{
  "category": "n_plus_one",
  "explanation": "The new code fetches each order's line items individually inside the loop over orders, instead of the previous single batched query. With N orders this now issues N+1 queries.",
  "cited_lines": ["for order in orders:", "    order.line_items = LineItem.objects.filter(order_id=order.id)"],
  "confidence": "high"
}
```

**Tavily grounding step (runs after the above)**

- Query pattern: `"{category} performance issue {relevant library/function name}"` or `"is {cited pattern} a known anti-pattern"`.
- Purpose: attach a citation/confidence booster to the diagnosis, and this is your basis for the Tavily bonus prize — make sure the README explicitly explains this usage.
- If Tavily returns nothing relevant, don't force a citation — leave `tavily_refs` empty rather than fabricating relevance.

---

## 3. Fixer Agent

**Model**: Nemotron 3 Ultra
**Purpose**: propose a patch addressing the diagnosed root cause, then hand off to the sandbox for verification (verification itself is not the model's job).

**Input schema**

```
{
  "diagnosis": { "...": "output of Diagnoser agent" },
  "full_file_contents": "<contents of the file(s) needing changes>",
  "attempt_number": 1,
  "previous_attempt_result": null
}
```

On retries, `previous_attempt_result` is populated:

```
{
  "previous_attempt_result": {
    "patch_applied": "<diff of what was tried>",
    "score_after_fix": 118.0,
    "still_regressed": true
  }
}
```

**System prompt**

```
You are a senior engineer fixing a diagnosed performance regression.
You will be given the diagnosis (root cause + cited lines), the full
contents of the affected file(s), and — on retries — the result of your
previous attempt.

Propose a minimal patch that addresses the specific root cause. Do not
make unrelated changes. If this is a retry and your previous attempt did
not resolve the regression, explain what you're changing about your
approach before proposing the new patch.

Return ONLY JSON matching this schema:
{
  "patch": "<unified diff format>",
  "rationale": "<1-3 sentences on why this fixes the root cause>",
  "confidence_will_resolve": "high" | "medium" | "low"
}
```

**Retry policy**: max 3 attempts. After 3 failed attempts, stop and report the diagnosis plus all attempted patches transparently — do not claim success on an unresolved case. An honest "diagnosed but not auto-fixed" result is a legitimate and defensible outcome for the demo.

---

## 4. End-to-End Job State (for the dashboard/API)

```
{
  "job_id": "abc123",
  "status": "diagnosing" | "bisecting" | "fixing" | "done" | "failed",
  "timeline": [{"commit": "sha", "score": 100.0}],
  "regression_commit": "sha",
  "diagnosis": { "...": "Diagnoser output" },
  "tavily_refs": [{"title": "...", "url": "..."}],
  "fix_attempts": [{"attempt": 1, "patch": "...", "score_after": 118.0, "resolved": false}],
  "final_result": "resolved" | "unresolved_diagnosis_only"
}
```
