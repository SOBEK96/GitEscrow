import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#07070b", 900: "#0c0c13", 850: "#11111a", 800: "#171722", 700: "#232331", 600: "#34344a" },
        violet: { glow: "#a78bfa" },
        gl: { DEFAULT: "#8b5cf6", soft: "#c4b5fd", deep: "#5b21b6", orange: "#fb923c" },
        ok: "#34d399",
        warn: "#fbbf24",
        bad: "#f87171",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "-apple-system", "BlinkMacSystemFont", "Inter", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "monospace"],
      },
      boxShadow: {
        card: "0 0 0 1px rgba(255,255,255,0.06), 0 12px 40px rgba(0,0,0,0.45)",
        glow: "0 0 32px rgba(139,92,246,0.25)",
      },
      keyframes: {
        rise: { "0%": { opacity: "0", transform: "translateY(6px)" }, "100%": { opacity: "1", transform: "none" } },
        pulseDot: { "0%,100%": { opacity: "0.4" }, "50%": { opacity: "1" } },
      },
      animation: { rise: "rise .35s ease-out both", pulseDot: "pulseDot 1.6s ease-in-out infinite" },
    },
  },
  plugins: [],
};
export default config;
