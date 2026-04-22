# Frontend Runtime Guide

## Architecture

- App composition is split across `src/app/providers.jsx`, `src/app/routes.jsx`, and `src/app/AppShell.jsx`.
- Route entry modules live under `src/pages`.
- Explore-domain routes are hosted under `src/features/explore/pages`.
- Shared UI modules live under `src/components/shared`.
- Reusable chart primitives live under `src/charts/primitives`.
- Canonical server-state access is centralized in `src/api` and `src/hooks/queries`.

## Build and Guardrail Commands

```bash
npm run lint
npm run build
npm run check:boundaries
npm run check:bundle
npm run check:perf-smoke
```

## Additional Frontend Notes

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
