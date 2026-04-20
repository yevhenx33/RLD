# Architectural Lessons

## 1. Global Hub Netting takes Precedence over Local Netting
Never allow Spokes to only match their own internal directional flow when a central Hub exists. Global Ghost Flow must be aggregated and crossed at the Hub *before* ANY Spoke acts, enabling zero-slippage cross-strategy intercepts (e.g. TWAMM natively clearing against a Limit Engine).

## 2. The Cumulative Sweep ensures 0-Dust Fractional Math
Using strict Greedy Arrays introduces Strategy Hierarchy (e.g. Engine 0 front-running Engine 1). To achieve flat Pro-Rata filling safely, use the **Cumulative Proportion** pattern. Never dump `sum - matches` remainder dust onto the terminal engine (as it might have 0 balance and revert). Distribute step-by-step using: `cum_expected = (total_match * cum_balance) // total_balance`, with `step_match = cum_expected - cum_already_matched`.

## 3. Strict Secrets Isolation (Poka-Yoke)
Never expose API keys, database credentials, or proprietary RPC URLs directly into source control, including as hardcoded default fallbacks in `os.getenv`, Python files, or `docker-compose.yml`. Assume ANY unique hash appended to an otherwise "public" URL (e.g., load balancer URLs) acts as a bearer token. Force scripts to crash safely by defaulting to `http://localhost:8545` when `.env` is absent, rather than providing graceful but fundamentally insecure cloud fallbacks. All secrets MUST strictly remain inside `.env` configurations.
