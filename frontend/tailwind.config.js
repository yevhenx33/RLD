/* global require */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      // ── Design Tokens ──
      fontFamily: {
        'Space_Grotesk': ['Space Grotesk', 'sans-serif'],
      },
      fontSize: {
        micro: ["9px", { lineHeight: "12px" }],
        tiny: ["10px", { lineHeight: "14px" }],
        label: ["11px", { lineHeight: "16px" }],
      },
      letterSpacing: {
        terminal: "0.15em",
        wide: "0.2em",
        ultra: "0.25em",
        extreme: "0.4em",
      },
      colors: {
        dark: {
          900: "#0e1117",
          800: "#161b22",
          700: "#21262d",
        },
        surface: {
          DEFAULT: "#0a0a0a",
          base: "#050505",
          raised: "#111111",
        },
        brand: {
          cyan: "#00f2ff",
          pink: "#ff0055",
        },
        accent: {
          bond: "#22d3ee",    // cyan-400
          perp: "#22c55e",    // green-500
          cds: "#ec4899",     // pink-500
          warning: "#f59e0b",
        },
        border: {
          DEFAULT: "rgba(255,255,255,0.08)",
          hover: "rgba(255,255,255,0.15)",
        },
      },
      borderWidth: {
        px: "1px",
      },
      animation: {
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
