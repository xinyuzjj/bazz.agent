/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Hermes Quant_Node v2.4.9 palette
        canvas: "#0B0E11",
        card: "#181A20",
        elevated: "#1E2329",
        line: "#2B313A",
        active: "#474D57",
        ink: {
          DEFAULT: "#EAECEF",
          dim: "#9AA4B2",
          mute: "#6E7A8A",
        },
        gold: {
          DEFAULT: "#F0B90B",
          soft: "#FCD535",
          deep: "#FFD87F",
        },
        green: "#0ECB81",
        red: "#F6465D",
      },
      fontFamily: {
        sans: ['"Inter"', '"PingFang SC"', '"Microsoft YaHei"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        // typographic scale per DESIGN.md
        "display-xl": ["40px", { lineHeight: "48px", letterSpacing: "-0.02em", fontWeight: "700" }],
        "headline-lg": ["24px", { lineHeight: "32px", letterSpacing: "-0.01em", fontWeight: "600" }],
        "headline-md": ["20px", { lineHeight: "28px", fontWeight: "600" }],
        "headline-sm": ["16px", { lineHeight: "24px", fontWeight: "600" }],
        "body-lg": ["16px", { lineHeight: "24px" }],
        "body-md": ["14px", { lineHeight: "20px" }],
        "body-sm": ["12px", { lineHeight: "16px" }],
        "metric-lg": ["28px", { lineHeight: "36px", letterSpacing: "-0.03em", fontWeight: "700" }],
        "metric-md": ["18px", { lineHeight: "24px", letterSpacing: "-0.02em", fontWeight: "600" }],
        "code-body": ["13px", { lineHeight: "18px" }],
        "label-md": ["12px", { lineHeight: "16px", letterSpacing: "0.04em", fontWeight: "500" }],
        "label-xs": ["10px", { lineHeight: "14px", letterSpacing: "0.06em", fontWeight: "600" }],
      },
      borderRadius: {
        sm: "4px",
        md: "6px",
        lg: "8px",
        xl: "12px",
        "2xl": "16px",
      },
      backdropBlur: { xs: "8px" },
      boxShadow: {
        elevated: "0 12px 32px rgba(0,0,0,0.65), 0 0 1px rgba(240,185,11,0.2)",
        glow: "0 0 16px rgba(240,185,11,0.15)",
        "gold-cta": "0 0 12px rgba(240,185,11,0.35)",
      },
    },
  },
  plugins: [],
};