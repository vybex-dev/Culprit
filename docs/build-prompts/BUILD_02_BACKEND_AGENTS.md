# Build Prompt — Backend: AI Reasoning Agents (Diagnoser + Fixer)

**You are building the two agents that do actual reasoning**: the Diagnoser (root-causes a regression from its diff, with citation discipline) and the Fixer (proposes and iterates on a patch). Both use Nemotron 3 Ultra via the shared `models.py` wrapper built in the Core Orchestration stack — don't reimplement model-calling here, import it.

## Read first

- `AGENTS.md` (whole file, especially rule 5: "the Diagnoser must cite specific diff lines")
- `BUILD_00_OVERVIEW.md` — canonical job-state schema; your outputs slot into `diagnosis`, `tavily_refs`, and `fix_attempts`/`fix`
- `AGENT_SPECS.md §0` (shared conventions), `§2` (Diagnoser), `§3` (Fixer)
- `PRD.md §11` risk table, row "Diagnosis is generic/hand-wavy on unseen cases"

## Install first

```bash
npx skills add akmalovaa/python-skill
# same Python conventions as the other backend stack — pydantic v2 for the
# schemas below, fail-loud error handling for the citation-verification
# check described in section 1.

/plugin marketplace add obra/superpowers-marketplace
/plugin install superpowers@superpowers-marketplace
# systematic-debugging is genuinely useful here: when the Diagnoser gets a
# demo case's category wrong, you want a structured way to trace whether
# the prompt, the context window, or the category taxonomy itself is at fault
# — not a guess-and-reprompt loop.
```

## What you own

```
backend/diagnoser.py      # root-cause classification, calls Ultra + Tavily
backend/fixer.py          # patch generation + retry loop, calls Ultra
backend/tavily_client.py
```

## Non-negotiables specific to this stack

1. **Never fabricate a "resolved" result.** After 3 failed fix attempts, report the diagnosis and every attempted patch transparently. An honest "diagnosed but not auto-fixed" is a legitimate outcome for the demo — a fake success is not.
2. **A diagnosis with no real citation gets classified `"other"`, honestly** — it is never dressed up as a confident specific category just because that would look better on the dashboard.
3. **Don't fabricate a Tavily citation.** If nothing relevant comes back, `tavily_refs` stays empty.

## 1. Diagnoser (`diagnoser.py`)

System prompt (verbatim — don't rephrase this, the specific instruction to cite exact lines is load-bearing):

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

Input schema:
```json
{
  "guilty_commit_sha": "<sha>",
  "diff": "<full unified diff at guilty commit>",
  "surrounding_context": "<relevant file contents beyond the diff hunk>",
  "commit_message": "<original commit message>",
  "before_score": 100.0,
  "after_score": 121.0
}
```

**Build this check, don't rely on the model's honesty alone**: after parsing the Diagnoser's JSON, verify every string in `cited_lines` is an actual substring of the `diff` you sent. If any citation doesn't verify, treat it the same as a malformed-schema response — re-prompt once with an explicit note that the citation didn't match the diff, and if it still doesn't verify, downgrade the result to `category: "other"` yourself rather than passing along an uncited claim. This turns `AGENTS.md` rule 5 from a prompt-only hope into an enforced invariant.

Tavily grounding step (runs after the above, only if a real category was assigned):
- Query pattern: `"{category} performance issue {relevant library/function name}"` or `"is {cited pattern} a known anti-pattern"`.
- Purpose: attach a citation/confidence signal to the diagnosis — this is your basis for the Tavily bonus prize, and the README needs to spell out this usage explicitly (see `BUILD_04_DEMO_AND_SUBMISSION.md`).
- If Tavily returns nothing relevant, leave `tavily_refs` empty. Do not force a citation.

## 2. Fixer (`fixer.py`)

System prompt (verbatim):

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

Input schema (first attempt):
```json
{
  "diagnosis": { "...": "output of Diagnoser agent" },
  "full_file_contents": "<contents of the file(s) needing changes>",
  "attempt_number": 1,
  "previous_attempt_result": null
}
```

On retries, populate:
```json
{
  "previous_attempt_result": {
    "patch_applied": "<diff of what was tried>",
    "score_after_fix": 118.0,
    "still_regressed": true
  }
}
```

**Retry policy**: max 3 attempts, hard cap. Verification (applying the patch in a sandbox and re-running the benchmark) is the Core Orchestration stack's job via `sandbox_client.py`, not this file's — `fixer.py` proposes, it never marks itself as verified. After 3 failed attempts, stop and surface the diagnosis plus every attempted patch through the job state — don't retry silently past the cap.

## Shared conventions (recap — implement identically to the other backend stack)

- Malformed JSON: retry once with the original prompt plus `"Your previous response was not valid JSON. Return ONLY valid JSON matching the schema, with no other text."` Fail the job with a clear error on a second failure — don't guess.
- Log every prompt + raw response pair, keyed by `job_id` and step.
- Temperature 0.1–0.3 for the Fixer's patch generation; 0.3–0.5 acceptable for the Diagnoser's explanation *phrasing*, never for the `category` field's determinism.

## Testing & validation

- Validate the Diagnoser against your 2–3 curated real-world demo cases (built in `BUILD_04_DEMO_AND_SUBMISSION.md`): output `category` must match the known ground-truth cause, and `cited_lines` must actually appear in the provided diff (this is now an automated check, not a manual eyeball).
- Fixer success bar: on at least 2 of 3 curated demo cases, produce a patch that brings the benchmark back within ~10% of baseline, **verified by an actual sandbox re-run** — never assumed from the model's own `confidence_will_resolve` field.

## Definition of done

- **Diagnoser**: on all curated demo cases, output category matches the known ground-truth cause, and `cited_lines` actually appears in the provided diff.
- **Fixer**: on at least 2 of 3 curated demo cases, produces a patch that brings the benchmark back within ~10% of baseline, verified by an actual sandbox re-run (not assumed).

## Hand back

The citation-verification check's pass/fail output against each demo case, the Fixer's attempt history (including failed attempts, not just the winning one) for at least one case, and confirmation of which model/temperature was used at each step.
