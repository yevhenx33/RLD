# Frontend Visual Redesign

## Phase 1: Standalone Landing Page Folder ✅

- [x] Create `new-front/` at project root (sibling to `frontend/`)
- [x] Scaffold minimal Vite + React project (no router, no contexts, no wallet)
- [x] Create clean landing page skeleton in `src/pages/Landing.jsx`
- [x] Verify it runs independently with `npm run dev` on port 5174
- [x] Verify `frontend/` is **FULLY UNTOUCHED** (git diff = clean)

### Structure
```
new-front/
├── index.html          (Google Fonts preload)
├── package.json        (React 19 + Vite 7 + Tailwind 3)
├── vite.config.js      (port 5174)
├── tailwind.config.js
├── postcss.config.js
├── public/
└── src/
    ├── index.css       (Tailwind directives + dark base)
    ├── main.jsx        (bare React render, no router/providers)
    └── pages/
        └── Landing.jsx (minimal hero — starting point for redesign)
```

## Phase 2: Iterative UI/UX Redesign
- [ ] Design hero section
- [ ] Design product sections
- [ ] Design footer
- [ ] Polish animations & micro-interactions
