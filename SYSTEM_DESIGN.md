# System Design & Architecture

## 1. Design Philosophy: "Premium Financial Terminal"

The RLD Dashboard is designed to evoke the aesthetic of high-frequency trading terminals (e.g., Bloomberg, Reuters) but with a modern, Web3-native polish.

**Core Principles:**
*   **Data Density**: Maximize information per pixel without clutter.
*   **High Contrast**: Deep black backgrounds with neon data points for readability in dark environments.
*   **Monospace First**: All numerical data and technical labels use monospaced fonts for precise alignment.
*   **Fluidity**: Real-time updates, smooth transitions, and hardware-accelerated charts.

---

## 2. Visual Design System

### A. Color Palette
We use a strictly curated dark mode palette.

**Backgrounds**
*   **Canvas**: `#050505` (Deepest Black - Main Background)
*   **Surface**: `#0a0a0a` (Card / Table Background)
*   **Overlay**: `#0f0f0f` (Dropdowns / Modals)

**Borders & Separators**
*   **Subtle**: `rgba(255, 255, 255, 0.1)` (Standard Dividers)
*   **Hover**: `rgba(255, 255, 255, 0.2)` (Interactive Elements)

**Functional Colors (Assets)**
*   ![#22d3ee](https://via.placeholder.com/10/22d3ee/000000?text=+) **USDC**: `#22d3ee` (Cyan-400) - Represents Stability/Tech.
*   ![#facc15](https://via.placeholder.com/10/facc15/000000?text=+) **DAI**: `#facc15` (Yellow-400) - Represents Decentralization/Caution.
*   ![#4ade80](https://via.placeholder.com/10/4ade80/000000?text=+) **USDT**: `#4ade80` (Green-400) - Represents Volume/Cash.
*   ![#c084fc](https://via.placeholder.com/10/c084fc/000000?text=+) **SOFR**: `#c084fc` (Purple-400) - Represents Institutional/Risk-Free.
*   ![#a1a1aa](https://via.placeholder.com/10/a1a1aa/000000?text=+) **ETH**: `#a1a1aa` (Zinc-400) - Represents the Benchmark/Base Layer.

**Brand Accents**
*   **Primary**: `#00f2ff` (Electric Cyan) - Used for active states, logos, and critical highlights.
*   **Secondary**: `#ff0055` (Neon Pink) - Used for emphasis and selection backgrounds.

### B. Typography
*   **Primary Font**: `Inter` (System UI) - Used for Headers and navigational text.
*   **Data Font**: `Monospace` (System Mono) - Used for ALL numbers, rates, and technical labels.
*   **Styling**:
    *   **Labels**: Uppercase, `text-[10px]` to `text-xs`, Tracking Widest (`tracking-[0.2em]`).
    *   **Values**: Large, Light weight (`font-light`), Tracking Tight (`tracking-tight`).

---

## 3. Component Library

### Metric Box
A standardized container for displaying high-level KPIs.
*   **Layout**: Flex column, spread vertically.
*   **Iconography**: `lucide-react` icons, 15px size, opacity 90%.
*   **Visuals**: No background by default, thin border separation.

### Interactive Charts (`RLDChart.jsx`)
A bespoke wrapper around `recharts` optimized for performance.
*   **Level of Detail (LOD)**: Automatically downsamples 100k+ data points into ~1000 visible points based on zoom level to maintain 60FPS.
*   **Interaction**: Custom implementation of "Touchpad Pan" (two-finger scroll) and "Wheel Zoom".
*   **Visuals**: Gradient fills (Opacity 0.33 -> 0), custom "Glass" tooltip.

### Data Table
*   **Look**: Terminal style.
*   **Headers**: Sticky, uppercase, dimmed (`text-gray-500`).
*   **Rows**: Hover effect (`bg-white/[0.03]`), transition duration 300ms.
*   **Cells**: Monospace numbers, aligned for comparison.

---

## 4. Technical Architecture

### Frontend (Client)
*   **Framework**: React + Vite (Fast Build).
*   **Styling**: Tailwind CSS (Utility-first).
*   **State Management**: `SWR` (Stale-While-Revalidate) for automatic background polling and caching.
*   **Data Flow**:
    1.  Polls `/rates` API every X seconds.
    2.  Merges data streams (USDC, SOFR, ETH) into a time-aligned bucket map.
    3.  Calculates derived stats (Dominance, Avg APY) client-side.

### Backend (Server)
*   **Runtime**: Python 3.10 + FastAPI (ASGI).
*   **Database**: SQLite (WAL Enabled) for single-file simplicity but high concurrent read performance.
*   **Indexer Daemon**:
    *   **Main Thread**: Polls Ethereum RPC (Aave V3 Contracts) every 12s.
    *   **SOFR Thread**: Polls NY Fed API every 1h.
    *   **Gap Filler**: Runs on startup to patch missing blocks.

### Data Pipeline
1.  **Block 12345 Mined** -> RPC Node.
2.  **Indexer** detects new block height.
3.  **Fetch**: Queries Aave `getReserveData` multicast.
4.  **Store**: Inserts into SQLite `rates` table.
5.  **API**: Serves cached JSON (TTL 20s) to Frontend.
6.  **Frontend**: SWR revalidates and updates Chart/Table.

---

## 5. Directory Structure
```
/frontend
  /src
    /components
       RLDChart.jsx       # The heavy-lifting visualization engine
       Markets.jsx        # Main Dashboard Page
       SettingsButton.jsx # Reusable UI control
    index.css             # Tailwind @apply rules & Global Styles
    tailwind.config.js    # Design Token Registry
```
