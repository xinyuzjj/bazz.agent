/** @type {import('tailwindcss').Config} */
export default {
  content: { relative: true, files: ["./index.html", "./preview.html", "./src/**/*.{ts,tsx}"] },
  theme: {
    extend: {
      colors: {
        // Hermes Quant_Node v2.4.9 palette
        // 颜色走 CSS 变量（rgb 分量），让 Tailwind 工具类 + opacity 修饰符随 data-theme 联动
        canvas: "rgb(var(--canvas-rgb) / <alpha-value>)",
        card: "rgb(var(--card-rgb) / <alpha-value>)",
        elevated: "rgb(var(--elevated-rgb) / <alpha-value>)",
        line: "rgb(var(--line-rgb) / <alpha-value>)",
        active: "rgb(var(--active-rgb) / <alpha-value>)",
        ink: {
          DEFAULT: "rgb(var(--ink-rgb) / <alpha-value>)",
          dim: "rgb(var(--ink-dim-rgb) / <alpha-value>)",
          mute: "rgb(var(--ink-mute-rgb) / <alpha-value>)",
        },
        gold: {
          DEFAULT: "rgb(var(--gold-rgb) / <alpha-value>)",
          soft: "rgb(var(--gold-soft-rgb) / <alpha-value>)",
          deep: "rgb(var(--gold-deep-rgb, 255 216 127) / <alpha-value>)",
        },
        green: "rgb(var(--green-rgb) / <alpha-value>)",
        red: "rgb(var(--red-rgb) / <alpha-value>)",
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