````markdown
# RLD Protocol Design System (v1.0)

> **Philosophy:** "Industrial DeFi Terminal."
> **Aesthetic:** Bloomberg Terminal meets Cyberpunk/Sci-Fi.
> **Core Rules:** Data-dense, high contrast, strict geometry (no curves), monospaced typography.

---

## 1. Color Palette

### Background Layers

- **Page Root:** `bg-[#050505]` (Deepest black).
- **Panel/Card:** `bg-[#080808]` (Primary container background).
- **Header/Action:** `bg-[#0a0a0a]` (Slightly lighter, used for headers or input areas).
- **Hover/Active:** `bg-white/5` (Interactive states).

### Borders & Dividers

- **Base Border:** `border-white/10` (Used for grid lines, panel edges).
- **Focus Border:** `border-white/20` or `border-white/40` (Active inputs).
- **Separators:** `divide-white/10` or `border-b border-white/5`.

### Semantic Accents

- **RLD Brand / Short / Bond / Fixed-Yield:**
  - Color: `text-pink-500` (`#ec4899`)
  - Background: `bg-pink-500`
  - Glow: `shadow-[0_0_10px_#ec4899]`
- **Spot / Long / Variable / Lending:**
  - Color: `text-cyan-400` (`#22d3ee`)
  - Background: `bg-cyan-400`
  - Glow: `shadow-[0_0_10px_#22d3ee]`
- **Success / Solvency / System Status:**
  - Color: `text-green-500` (`#22c55e`)
  - Indicator: `bg-green-500` (often used with `animate-pulse`)
- **Error / Danger:**
  - Color: `text-red-500` (`#ef4444`)

### Typography Colors

- **Primary Text:** `text-[#e0e0e0]` (Off-white).
- **Labels/Micro:** `text-gray-500` (`#6b7280`).
- **Muted/Disabled:** `text-gray-700` (`#374151`).

---

## 2. Typography

**Global Font:** `font-mono` (Apply to root).

### Hierarchy

1.  **Micro Labels (The "Terminal" Look):**

    - Classes: `text-[10px]` or `text-[11px]`, `uppercase`, `tracking-[0.2em]` (or `tracking-widest`), `font-bold`, `text-gray-500`.
    - _Usage:_ Input labels, axis labels, status indicators, table headers.

2.  **Standard Text:**

    - Classes: `text-xs`, `font-mono`.
    - _Usage:_ Body copy, button text, informative descriptions.

3.  **Data / Metrics:**

    - Classes: `text-xl` to `text-3xl`, `font-light` (or `font-normal`), `tracking-tight`, `text-white`.
    - _Usage:_ APY %, Prices, TVL, Balances.

4.  **Section Headers:**
    - Classes: `text-xs`, `font-bold`, `uppercase`, `tracking-widest`, `text-white`.
    - _Usage:_ Panel titles (often accompanied by an Icon).

---

## 3. UI Primitives

### A. Buttons

**Rule:** Strictly square corners (`rounded-none`).

- **Primary Action (Pink/Brand):**
  `w-full py-4 bg-pink-500 hover:bg-pink-400 text-black text-xs font-bold tracking-[0.2em] uppercase transition-all`
- **Secondary Action (Cyan/Spot):**
  `w-full py-4 bg-cyan-900 text-cyan-100 hover:bg-cyan-800 border border-cyan-500/30 uppercase tracking-[0.2em] text-xs font-bold`
- **Ghost / Cancel:**
  `py-3 text-gray-500 hover:text-white text-xs font-bold tracking-[0.2em] uppercase transition-all`
- **Outline:**
  `border border-white/20 hover:border-white text-gray-400 hover:text-white uppercase`

### B. Inputs (The "Underline" Style)

**Rule:** No background box. Underline only.

```jsx
<div className="relative group">
  <input
    type="number"
    className="w-full bg-transparent border-b border-white/20 text-lg font-mono text-white py-2 focus:outline-none focus:border-white transition-colors placeholder-gray-800 rounded-none"
    placeholder="0.00"
  />
  <span className="absolute right-0 top-3 text-xs text-gray-600 font-bold">
    USDC
  </span>
</div>
```
````

### C. Panels & Cards

**Rule:** Thin borders, dark backgrounds, uniform padding.

```jsx
<div className="border border-white/10 bg-[#080808] p-6">{/* Content */}</div>
```

### D. Navigation Header

**Rule:** Sticky, blurred, minimalist.

- Container: `sticky top-0 bg-[#050505]/95 backdrop-blur-sm z-50 border-b border-white/10`.
- Links: Text-based, separated by `|` or `//`.
- Active State: `border-b border-pink-500 text-white`.

---

## 4. Layout & Grid

- **Max Width:** `max-w-[1800px]`.
- **Grid System:** `grid-cols-12`.
- **Dashboard Split:** Typically `col-span-8` (Chart/Data) vs `col-span-4` (Terminal/Action).

- **Spacing:** `gap-6` is the standard distance between panels.

---

## 5. Animation & Effects

- **Pulsing Status:**
  `<div className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse shadow-[0_0_8px_#22c55e]"></div>`
- **Page Entry:**
  `animate-in fade-in zoom-in-95 duration-300`
- **Selection Highlight:**
  `selection:bg-pink-500/30 selection:text-white`

---

## 6. Icons

- **Library:** `lucide-react`.
- **Size:** Small (`size={12}` to `size={16}`).
- **Color:** Muted (`text-gray-500`) unless part of an active state.

---

## 7. Component Snippets

### Standard Metric Box

```jsx
<div className="p-6 border border-white/10 bg-[#080808] flex flex-col justify-between h-full">
  <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2 flex justify-between">
    Label <Icon size={14} />
  </div>
  <div>
    <div className="text-3xl font-light text-white mb-1 tracking-tight">
      Value
    </div>
    <div className="text-[10px] text-gray-500 uppercase tracking-widest">
      Sub-label / Change
    </div>
  </div>
</div>
```

### Section Header

```jsx
<div className="p-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center">
  <h3 className="text-xs font-bold tracking-widest text-white uppercase flex items-center gap-2">
    <Terminal size={14} className="text-gray-500" />
    Section_Title
  </h3>
  <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></div>
</div>
```

```

```
