/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        dark: {
          900: "#0e1117", // Main Background
          800: "#161b22", // Card Background
          700: "#21262d", // Borders
        },
        brand: {
          cyan: "#00f2ff",
          pink: "#ff0055",
        },
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
};
