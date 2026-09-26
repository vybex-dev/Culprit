// FILE: frontend/src/fixtures/done-unresolved.ts — place at this path in the Culprit repo
//
// AGENT_SPECS.md's Fixer retry policy caps at 3 attempts; when none
// resolve it, the honest result is "unresolved_diagnosis_only" — never a
// fabricated "resolved: true" (AGENTS.md rule 1). This fixture exists
// specifically because BUILD_03 calls this state "easy to forget and easy
// to fake-demo around."

import { buildJob, DIAGNOSIS, FIX_PATCH_ATTEMPT_1, FULL_TIMELINE, REGRESSION_COMMIT } from "./shared";

const attempt3Patch = `--- a/backend/orders/summary.py
+++ b/backend/orders/summary.py
@@ -10,10 +10,13 @@ def get_order_summary(customer_id):
 def get_order_summary(customer_id):
     orders = Order.objects.filter(customer_id=customer_id)
-    for order in orders:
-        order.line_items = LineItem.objects.filter(order_id=order.id)
+    order_ids = list(orders.values_list("id", flat=True))
+    line_items = list(LineItem.objects.filter(order_id__in=order_ids))
+    for order in orders:
+        order.line_items = [li for li in line_items if li.order_id == order.id]
 
     return [serialize_order(o) for o in orders]`;

export const doneUnresolvedJob = buildJob({
  job_id: "demo-done-unresolved",
  status: "done",
  created_at: "2026-09-20T14:00:00Z",
  updated_at: "2026-09-20T14:52:00Z",
  timeline: FULL_TIMELINE,
  regression_commit: REGRESSION_COMMIT,
  diagnosis: DIAGNOSIS,
  fix_attempts: [
    {
      attempt: 1,
      patch: FIX_PATCH_ATTEMPT_1,
      rationale:
        "Batches the line-item lookup with a single filter(order_id__in=...) call instead of querying per order.",
      score_after: 119.8,
      resolved: false,
    },
    {
      attempt: 2,
      patch: attempt3Patch,
      rationale:
        "Attempt 1 still re-scanned the full line_items list per order after fetching it. This materializes it once as a plain list up front.",
      score_after: 116.2,
      resolved: false,
    },
    {
      attempt: 3,
      patch: attempt3Patch,
      rationale:
        "Re-applied the same shape with values_list(flat=True) instead of a queryset slice, on the chance the ORM's lazy evaluation was still re-querying — did not change the measured result meaningfully.",
      score_after: 117.0,
      resolved: false,
    },
  ],
  fix: {
    patch_diff: attempt3Patch,
    verified: false,
    before_score: 100.9,
    after_score: 117.0,
  },
  final_result: "unresolved_diagnosis_only",
});
