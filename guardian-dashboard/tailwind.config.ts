import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        guardian: {
          bg: "var(--guardian-bg)",
          card: "var(--guardian-card)",
          border: "var(--guardian-border)",
          text: "var(--guardian-text)",
          muted: "var(--guardian-muted)",
          teal: "var(--guardian-teal)",
          blue: "var(--guardian-blue)",
          green: "var(--guardian-green)",
          red: "var(--guardian-red)",
          amber: "var(--guardian-amber)",
        },
      },
      animation: {
        "pulse-glow": "pulse-glow 2s ease-in-out infinite",
      },
      keyframes: {
        "pulse-glow": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.5" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
