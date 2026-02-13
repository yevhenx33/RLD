# React + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and [`typescript-eslint`](https://typescript-eslint.io) in your project.

---

## Close Short Feature

### Hook: `useSwapExecution.js`

Added `executeCloseShort(amountIn, onSuccess)` callback:

- Takes waUSDC amount (human-readable, 6 decimals)
- Checks operator status, switches chain ID for Anvil, signs via MetaMask
- Calls `router.closeShort(broker, amountInWei, poolKey)`
- ABI entry added for `closeShort(address,uint256,PoolKey)`

### UI: `SimulationTerminal.jsx`

- **OPEN/CLOSE toggle** — now active for both LONG and SHORT sides
- **CLOSE SHORT input panel** — `Spend_waUSDC` input + `Est._wRLP_Repaid` (read-only, from V4Quoter)
- **Quote direction** — Close Short uses BUY direction (buying wRLP with waUSDC)
- **Action button** — dynamic label: "Close Short", disabled when no amount or quoting
- **Confirm modal handler** — calls `executeCloseShort` with success toast

### Modal: `SwapConfirmModal.jsx`

Fixed to distinguish OPEN SHORT vs CLOSE SHORT:

- Added `isOpenShort` / `isCloseShort` flags
- CLOSE SHORT shows "You Pay waUSDC → You Receive wRLP" layout
- Header: `CLOSE_SHORT`, Button: `Close Short`, Side label: `CLOSE SHORT`
- OPEN SHORT layout unchanged (Collateral → Borrow/Debt)

---

## Close Short Panel Polish

### `SimulationTerminal.jsx` — PAY_WITH Custom Dropdown

- Replaced native `<select>` with a custom dropdown matching Markets page `FilterDropdown` style
- **Trigger button**: Boxed (`border border-white/20 bg-black`), mono font, `ChevronDown` icon with rotation
- **Options panel**: `bg-[#0a0a0a] border border-white/20`, rows with cyan highlight for selected, hover effects
- Click-outside handler closes dropdown; selecting an option clears inputs
- Default mode: `wRLP` (Direct Repay); options: `wRLP — Direct Repay`, `waUSDC — Swap & Repay`

### `SimulationTerminal.jsx` — Compact Balance Formatting

- All balance sublabels use `.toFixed(1)` — no thousand separators, 1 decimal place
- Example: `12252.1 waUSDC` instead of `12 252,129 waUSDC`
- Applied to: Collateral Broker, Sell_wRLP Available, Total Debt, Amount_To_Pay Broker, Broker_wRLP, Short Collateral Broker

### `SimulationTerminal.jsx` — Quote Refresh

- Replaced static "quoting..." text with a clickable `RefreshCw` icon button
- Icon spins (`animate-spin`) while a quote is loading
- Users can manually trigger a quote refresh by clicking the icon
- Auto-refresh interval changed from 5s → 12s to match production cadence

### `useSwapQuote.js`

- Auto-refresh interval: `5000ms` → `12000ms`
- Exposed `refresh` callback for manual quote triggering

### `index.css` — Global Styles

- Added CSS to hide number input spinner arrows globally (all browsers)
