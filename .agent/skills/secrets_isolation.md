# Skill: Strict Secrets Isolation

## Goal
Prevent accidental exposure of private endpoints, connection strings, or API tokens directly into version control boundaries.

## Rules

1. **Zero Hardcoded Secrets Strategy:** 
   Never place an API key, RPC hash, or database password directly as a string literal in Python logic, bash files, or Docker Compose structures.
   
2. **Beware of Default Leakage:**
   When using environmental masking modules like `os.getenv` or shell parameter expansion like `${VAR:-default}`, **never** include a real API string as the fallback parameter. 

   **BAD:** 
   ```python
   # Leaks the key into version-controlled python scripts if pushed!
   RPC_URL = os.getenv("RPC_URL", "https://lb.drpc.live/ethereum/key")
   ```

   **GOOD:** 
   ```python
   # Defaults securely to a local stub, forcing a manual .env config for cloud nodes.
   RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
   ```

3. **Poka-Yoke Enforcements:**
   - Any endpoint containing an arbitrary block of high-entropy base64/hex characters is an API key until proven otherwise.
   - It is infinitely better for an orchestration script to violently crash demanding a valid `.env` file than to connect smoothly using an insecurely hardcoded fallback token.
   - Restrict all secret credentials exclusively to `.env` files that trigger `.gitignore` protections.
