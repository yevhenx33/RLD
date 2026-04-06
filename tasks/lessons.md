# Core Operational Lessons

## 1. EVM Stack Too Deep Isolation
- **Pattern:** When an overloaded Solidity function hits the 16-variable limit, do not use naive generic wrappers.
- **Rule:** Extract core calculations into pure standalone helper functions. Pass aggregate structs by state reference across scopes (e.g. `JITState storage state`) instead of individual literal values to prune local bindings from the root EVM frame.

## 2. Global Debt Normalization Indexing
- **Pattern:** Frequent per-user state updates on global factor changes (like interest rates or funding) are O(N) and bottleneck the indexer.
- **Rule:** Store raw `debt_principal` per-broker and the global `normalization_factor` per-market. Delegate "true debt" calculation (`principal * indexing_factor / 1e18`) to the consumer/frontend layer to maintain O(1) processing complexity per event.

## 3. Investigatory Requests ≠ Implementation
- **Pattern:** CEO asks to "check," "review," "report," or "analyze" something. This is Phase 1 only.
- **Rule:** Deliver the report/audit artifact and STOP. Do not enter Phase 2 (code changes, infra fixes) without explicit "Approved." Even if issues are found, the CEO decides what to fix and when.
