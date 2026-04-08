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

## 4. Read the Existing Design Before Proposing Fixes
- **Pattern:** Reviewer raises an attack vector. Agent proposes a fix that is already present in the existing design.
- **Rule:** Before proposing any mitigation, verify whether the existing spec already addresses the concern. Cross-reference all sections. Example: TWAR was already specified in §2.2.1 as the oracle mechanism, making the "24h trigger reset" attack inert — but both the reviewer and the agent missed this.

## 5. Academic Paper Submission — Venue Matching
- **Pattern:** A practitioner protocol-design paper submitted to an academic conference gets rejected for lack of formal proofs, derivations, and literature survey.
- **Rule:** Match the artifact to the venue. Engineering blueprints → practitioner venues (ETHDenver, Flashbots forum, Paradigm-style blog). Formal proofs → academic conferences. If targeting academic venues, either rewrite to theorem-proof-QED style or find a co-author who handles the formalism.

## 6. Academic Paper Submission — Tone Calibration
- **Pattern:** Reviewers flag "AI-generated content" based on rhetorical style: bold-everything emphasis, superlatives ("unbreakable", "massive safety margin"), symmetrical list structures.
- **Rule:** Let the math be impressive. Never tell the reader something is impressive. Use understated language. "The max LTV remained below 30%" beats "the strategy maintains a MASSIVE safety margin." Same data, different credibility.

## 7. Academic Paper Submission — Data Consistency
- **Pattern:** Two sections cite different numbers for the same event (6.7%→40% vs 7.6%→45.5%). Once a reviewer finds one inconsistency, trust collapses for the entire paper.
- **Rule:** Before any submission, do a single consistency pass. Every number cited more than once must match exactly. Every empirical claim needs a source URL or on-chain tx reference.

## 8. Scope ≠ Depth — One Paper, One Contribution
- **Pattern:** A paper covering 5 distinct products (bonds, fixed-rate borrowing, vol trading, CDS, RWA) gets rejected because validation is shallow on every front.
- **Rule:** Each standalone contribution is a separate paper. Breadth of claims must be matched by depth of validation. If covering multiple products, the paper must be explicitly framed as an architecture overview, not a deep dive on each.
