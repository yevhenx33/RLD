# Rate Level Derivatives (RLD) Dashboard

**A High-Performance Analytics Terminal for On-Chain Interest Rates.**

The **RLD Dashboard** provides institutional-grade visibility into DeFi lending markets. It indexes real-time borrow rates from Aave V3, benchmarks them against the risk-free SOFR rate, and visualizes the data in a responsive, high-frequency interface.

---

## ✨ Key Features

### 📊 Real-Time Market Analytics
*   **Multi-Asset Tracking**: Monitor borrowing rates for **USDC**, **DAI**, and **USDT** simultaneously.
*   **Live Borrow APY**: Streamed directly from Ethereum Mainnet (via local fork or RPC) with 12-second latency.
*   **Total Debt & Utilization**: Track billions in active liquidity across Aave V3 markets.

### 📈 Advanced Visualization
*   **Interactive Charts**: 
    *   **Zoom & Pan**: Smooth 60FPS navigation through years of historical data.
    *   **Smart Downsampling**: Renders 1M+ data points instantly without browser lag.
    *   **Custom Resolution**: Switch between Raw (Block-level), Hourly, Daily, and Weekly aggregates.
*   **Risk-Free Benchmarking**: Automatically overlays the **Secured Overnight Financing Rate (SOFR)** (sourced from NY Fed) to assess basis/spread.
*   **ETH Price Overlay**: Correlate interest rate spikes with ETH price action.

### 🛡️ Data Integrity & Ops
*   **Auto-Healing Database**: The system detects missing blocks (e.g., after downtime) on startup and automatically backfills history from the blockchain.
*   **Zero-Maintenance SOFR**: A background daemon polls the NY Fed API hourly to keep risk-free rates up to date.
*   **Hot Backups**: Automated, non-blocking SQLite snapshots ensure data safety.

---

## 🚀 Quick Start (Local Dev)

Launch the entire stack (Blockchain Fork, Indexer, API, Frontend) with a single command.

### Prerequisites
*   **Docker** (Optional, for future use) or **Python 3.10+** & **Node.js 18+**
*   **Foundry** (`forge`, `anvil`)
*   **Ethereum RPC URL** (Alchemy/Infura)

### 1. Configure Environment
Create a `.env` file in the root directory:
```env
MAINNET_RPC_URL="https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY"
```

### 2. Run System
```bash
./dev_start.sh
```

**That's it.** The script will:
1.  Fork Mainnet via Anvil.
2.  Deploy RLD Oracle Contracts.
3.  **Check & Repair Data Gaps** (indexes missing history).
4.  Launch the API (`localhost:8000`) and Frontend (`localhost:5173`).

---

## 🖥️ System Architecture
For a deep dive into the visual language and component design, see [SYSTEM_DESIGN.md](./SYSTEM_DESIGN.md).

*   **Indexer**: Python-based, multi-threaded (Aave RPC + NY Fed API).
*   **Database**: SQLite (WAL Mode) for high-concurrency performance.
*   **Frontend**: React + Vite + Tailwind (Premium Terminal Theme).

## 🛠️ Manual Data Tools

| Task | Command | Description |
| :--- | :--- | :--- |
| **Check Gap Status** | `python3 backend/scripts/check_gaps.py` | Scan database for missing blocks. |
| **Manual Backfill** | `python3 backend/scripts/batched_backfill.py` | Force-fetch history for a range. |
| **Backup DB** | `python3 backend/backup.py` | Trigger an immediate hot backup. |

---

## 📜 License
Private / Proprietary.
