// FILE: frontend/src/fixtures/shared.ts — place at this path in the Culprit repo
//
// One consistent demo story, reused across every fixtures/*.ts state file,
// so switching states in /dev/states looks like one job's timeline rather
// than eight unrelated snapshots. The diagnosis text below is lifted
// straight from docs/AGENT_SPECS.md §2's own worked Diagnoser output
// example (an N+1 query) — using the spec's own example as fixture data
// means rendering it correctly *is* a conformance check, not just a demo.

import type { Diagnosis, JobState, TimelineEntry } from "@/lib/types";

export const REPO_URL = "https://github.com/demo-org/orders-service";
export const BENCHMARK_COMMAND = "pytest benchmarks/test_order_summary.py --benchmark-only";
export const COMMIT_RANGE: [string, string] = [
  "1f6b2a9d3e7c4485b9a1d6f2c8e0a3b7d4f9c1e6",
  "91e2a27fbaf42cfddd29fe7a543ea8cbf17d9e06",
];

const T0 = Date.parse("2026-09-20T14:00:00Z");
const minutes = (n: number) => new Date(T0 + n * 60_000).toISOString();

// Nine candidates the Bisector's binary search evaluated, in chronological
// (commit) order — a clean pre/post cliff for the timeline chart.
export const FULL_TIMELINE: TimelineEntry[] = [
  { commit: "1f6b2a9d3e7c4485b9a1d6f2c8e0a3b7d4f9c1e6", score: 101.2, timestamp: minutes(0) },
  { commit: "2a7c3b0e4f8d5596cab2e7038d9f1b4e5a0c2d7f", score: 99.8, timestamp: minutes(6) },
  { commit: "3b8d4c1f5a9e66a7dbc3f8149eaf2c5f6b1d3e80", score: 102.5, timestamp: minutes(13) },
  { commit: "4c9e5d2a6bafd7b8ecd4a9250fba3d6a7c2e4f91", score: 100.1, timestamp: minutes(19) },
  { commit: "5daf6e3b7cb0e8c9fde5ba361a0c4e7b8d3f5aa2", score: 124.6, timestamp: minutes(26) },
  { commit: "6ebf7f4c8dc1f9daaef6cb47210bd5f8c9e4a6b3", score: 126.0, timestamp: minutes(33) },
  { commit: "7fc0805d9ed20aebbf07dc58321ce6a9daf5b7c4", score: 123.9, timestamp: minutes(40) },
  { commit: "80d1916eafe31bfccc18ed69432df7bae0c6d8d5", score: 125.4, timestamp: minutes(47) },
  { commit: "91e2a27fbaf42cfddd29fe7a543ea8cbf17d9e06", score: 127.1, timestamp: minutes(54) },
];

export const REGRESSION_COMMIT = "5daf6e3b7cb0e8c9fde5ba361a0c4e7b8d3f5aa2";

export const GUILTY_DIFF = `--- a/backend/orders/summary.py
+++ b/backend/orders/summary.py
@@ -10,15 +10,10 @@ def get_order_summary(customer_id):
 def get_order_summary(customer_id):
     orders = Order.objects.filter(customer_id=customer_id)
-    order_ids = [o.id for o in orders]
-    line_items = LineItem.objects.filter(order_id__in=order_ids)
-    items_by_order = defaultdict(list)
-    for item in line_items:
-        items_by_order[item.order_id].append(item)
-    for order in orders:
-        order.line_items = items_by_order.get(order.id, [])
+    for order in orders:
+        order.line_items = LineItem.objects.filter(order_id=order.id)
 
     return [serialize_order(o) for o in orders]`;

export const DIAGNOSIS: Diagnosis = {
  category: "n_plus_one",
  explanation:
    "The new code fetches each order's line items individually inside the loop over orders, instead of the previous single batched query. With N orders this now issues N+1 queries.",
  cited_lines: [
    "for order in orders:",
    "    order.line_items = LineItem.objects.filter(order_id=order.id)",
  ],
  confidence: "high",
  tavily_refs: [
    {
      title: "Django QuerySet API — select_related() and prefetch_related()",
      url: "https://docs.djangoproject.com/en/stable/ref/models/querysets/#select-related",
    },
  ],
  diff: GUILTY_DIFF,
};

export const FIX_PATCH_ATTEMPT_1 = `--- a/backend/orders/summary.py
+++ b/backend/orders/summary.py
@@ -10,10 +10,12 @@ def get_order_summary(customer_id):
 def get_order_summary(customer_id):
     orders = Order.objects.filter(customer_id=customer_id)
-    for order in orders:
-        order.line_items = LineItem.objects.filter(order_id=order.id)
+    order_ids = [o.id for o in orders]
+    line_items = LineItem.objects.filter(order_id__in=order_ids).select_related("sku")
+    for order in orders:
+        order.line_items = [li for li in line_items if li.order_id == order.id]
 
     return [serialize_order(o) for o in orders]`;

export const FIX_PATCH_ATTEMPT_2 = `--- a/backend/orders/summary.py
+++ b/backend/orders/summary.py
@@ -10,10 +10,15 @@ def get_order_summary(customer_id):
 def get_order_summary(customer_id):
     orders = Order.objects.filter(customer_id=customer_id)
-    for order in orders:
-        order.line_items = LineItem.objects.filter(order_id=order.id)
+    order_ids = [o.id for o in orders]
+    line_items = LineItem.objects.filter(order_id__in=order_ids)
+    items_by_order = defaultdict(list)
+    for item in line_items:
+        items_by_order[item.order_id].append(item)
+    for order in orders:
+        order.line_items = items_by_order.get(order.id, [])
 
     return [serialize_order(o) for o in orders]`;

function baseDefaults(): Omit<JobState, "job_id" | "status" | "created_at" | "updated_at" | "timeline"> {
  return {
    repo_url: REPO_URL,
    benchmark_command: BENCHMARK_COMMAND,
    commit_range: COMMIT_RANGE,
    regression_commit: null,
    diagnosis: null,
    fix_attempts: [],
    fix: null,
    final_result: null,
    error: null,
  };
}

/** Builds a complete, schema-valid JobState from overrides — every fixture
 * file is just this plus the fields that differ for that state, so drift
 * between fixtures (and against lib/types.ts) shows up as a type error. */
export function buildJob(overrides: Partial<JobState> & Pick<JobState, "job_id" | "status" | "created_at" | "updated_at">): JobState {
  return {
    ...baseDefaults(),
    timeline: [],
    ...overrides,
  };
}
