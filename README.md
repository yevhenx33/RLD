```markdown
# RLD Protocol: Local Development Environment

This repository contains the full stack for the **RLD Protocol**—a decentralized, Symbiotic-powered oracle network and synthetic rate dashboard.

This guide automates the deployment of the entire stack:
1.  **Blockchain:** Local Anvil node (Mainnet Fork).
2.  **Contracts:** Solidity Oracle & Gateway contracts.
3.  **Operator Network:** Python bot (Symbiotic Operator) feeding real-time data.
4.  **Backend:** FastAPI server for historical data.
5.  **Frontend:** React Dashboard streaming live on-chain updates.

---

## 🛠️ Prerequisites

Ensure you have the following installed:

* **[Foundry](https://getfoundry.sh/):** For local blockchain & contract deployment.
* **[Python 3.10+](https://www.python.org/):** For the Operator Bot & API.
* **[Node.js 18+](https://nodejs.org/):** For the Frontend.
* **[Git](https://git-scm.com/):** Version control.

---

## 📦 Installation

Run these commands once to set up dependencies for all layers.

### 1. Contracts (Foundry)
```bash
cd contracts
forge install
cd ..

```

### 2. Python Environment (Operator & API)

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install web3 eth-account python-dotenv fastapi uvicorn

```

### 3. Frontend (React)

```bash
cd frontend
npm install
cd ..

```

---

## ⚙️ Configuration

Create a `.env` file in the `contracts/` directory to configure your local fork.

**File:** `contracts/.env`

```env
# Your Alchemy/Infura Mainnet URL (Required for forking state)
MAINNET_RPC_URL=[https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY](https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY)

# Anvil Default Private Key (Do not change for local dev - Account #0)
PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80

```

---

## 🚀 Usage (Launch Everything)

We have created a master script `dev_start.sh` that orchestrates the entire system. It spins up the chain, deploys contracts, auto-configures addresses, and launches the UI.

### 1. Make the script executable (First time only)

```bash
chmod +x dev_start.sh

```

### 2. Run the System

```bash
./dev_start.sh

```

**What happens next?**

1. **Anvil** starts a local blockchain (forked from Mainnet) on port `8545`.
2. **Forge** deploys your Oracle contracts to this local chain.
3. **Auto-Config:** The script grabs the newly deployed address and updates your Python Bot and React Frontend automatically.
4. **Backend API** starts on port `8000`.
5. **Symbiotic Operator** starts feeding live TWAR data to the contract.
6. **Frontend** launches at `http://localhost:5173`.

---

## 🏗️ Architecture

* **Symbiotic Operator (Python):** Reads Spot rates from Aave V3 (on the fork), calculates Time-Weighted Average Rate (TWAR), signs it, and pushes it to the Oracle Contract.
* **Oracle Contract (Solidity):** Verifies the operator's signature and ensures the data is fresh before updating the on-chain state.
* **Frontend (React/SWR):** Connects to the local node (`localhost:8545`) to listen for `TwarUpdated` events and streams them to the chart in real-time.

---

## 🔧 Troubleshooting

* **`Address already in use`**: You have old processes running.
* Run: `pkill anvil && pkill -f uvicorn && pkill -f python`


* **`MAINNET_RPC_URL not set`**: Check your `contracts/.env` file. Ensure there are **no spaces** around the `=` sign.
* **Frontend shows "SYNCING..."**:
* Check if the Operator is running (look at `operator_logs.txt`).
* Ensure your `EVENT_TOPIC` in `useSymbioticOracle.js` matches the deployed contract (use `cast logs` to verify).


* **Chart is flat:** You are on a local fork, so Aave rates don't change unless you manipulate the chain. The Operator Bot adds tiny "jitter" to the price to visually demonstrate liveness.

```

```
