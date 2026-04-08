# Autoresearch: Cointegration Model Optimization

## Objective
The agent is tasked with discovering the mathematical optimum structural relationship (Cointegration model) between Decentralized Finance (DeFi) interest rates (AAVE V3 USDC) and Cryptoeconomic asset prices (Ethereum) to formalize in the RLD Whitepaper. Note: the mathematical derivative index is modeled as $P = 100 \times r$.

## Rules of Engagement
1. **The Constraints:** You may solely modify `model.py`. Do NOT alter `prepare.py` or the underlying dataset.
2. **Maximum Lag Limits:** Any lookback or lag parameter $L$ must strictly NOT exceed `30` days.
3. **No Spurious Polynomials:** Do not introduce arbitrary mathematical transformations, such as $n$-degree moving averages or random polynomial variables without pure economic backing.
4. **Target Metric:** The primary objective is minimizing the Augmented Dickey-Fuller (ADF) p-value. However, the evaluation framework applies an Akaike Information Criterion (AIC) penalty. Model complexity is penalized if it fails to yield significant predictive power.

## The Loop
At each block iteration:
1. Open `model.py`.
2. Propose a new geometric methodology (e.g., Vector Error Correction Models [VECM], VAR, Simple Engle-Granger, or dynamic smoothing).
3. Execute `python3 train.py`.
4. Check the `fitness_score` metric.
5. If the metric improves (lower is better), keep the method. If not, revert and test a new hypothesis.

*May the purest math win.*
