# SOP — Restock & Inventory Risk

## Restock thresholds
- stock_days below 3 days is HIGH risk: create restock ticket immediately.
- Auto-approved restock quantity is capped at 500 units (policy: restock_auto_threshold).
- Any restock above 500 units requires human approval before execution.

## Verification
- Every restock ticket must carry a correlation_id and be closed after verification.
