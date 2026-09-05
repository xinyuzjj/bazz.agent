import React from "react";
import { I } from "./icons";
import { useI18n } from "../i18n/i18n";
import { useTheme } from "../theme/theme";

export type NavId = "chat" | "markets" | "wallet" | "skills" | "cex" | "council" | "settings" | "memory";

export function TopBar({
  nav, setNav, onMemory, llmReady,
}: {
  nav: NavId; setNav: (n: NavId) => void; onMemory: () => void;
  llmReady: boolean;
}) {
  const { t, locale, toggle: toggleLocale } = useI18n();
  const { theme, toggle: toggleTheme } = useTheme();

  const NAV: { id: NavId; label: string; Icon: React.FC<any> }[] = [
    { id: "chat", label: t("nav.chat"), Icon: I.Chat },
    { id: "markets", label: t("nav.markets"), Icon: I.Market },
    { id: "wallet", label: t("nav.wallet"), Icon: I.Wallet },
    { id: "cex", label: t("nav.cex"), Icon: I.Cex },
    { id: "council", label: t("nav.council"), Icon: I.Megaphone },
    { id: "skills", label: t("nav.skills"), Icon: I.Grid },
    { id: "settings", label: t("nav.settings"), Icon: I.Gear },
  ];

  // 切语言按钮上显示「另一个」语言：中文时显示 EN，英文时显示 中
  const otherLocaleLabel = locale === "zh" ? "EN" : "中";

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-canvas/85 backdrop-blur-md">
      <div className="flex items-center gap-3 px-4 h-14">
        {/* Brand left */}
        <div className="flex items-center gap-2.5 shrink-0">
          <I.Hex className="text-gold" size={22} />
          <div className="leading-tight">
            <div className="font-mono font-bold tracking-wide text-[16px] text-ink">BAZZ<span className="text-gold">.</span>AGENT</div>
            <div className="font-mono text-[10px] tracking-[0.12em] text-ink-dim">BINANCE AGENT OS // v2.4.9</div>
          </div>
        </div>

        {/* Nav center */}
        <nav className="flex items-center gap-0.5 mx-auto px-2 py-1 rounded-lg bg-elevated/40 border border-line">
          {NAV.map(n => (
            <button key={n.id}
              onClick={() => setNav(n.id)}
              className={`px-3 py-1.5 rounded-md font-mono text-[12px] tracking-wide transition-colors whitespace-nowrap
                ${nav === n.id
                  ? "bg-card text-gold border border-line"
                  : "text-ink-dim hover:text-ink hover:bg-card/60 border border-transparent"}`}>
              {n.label}
            </button>
          ))}
        </nav>

        {/* Tools right */}
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={onMemory}
            className={`pill ${nav === "memory" ? "pill-gold" : "pill-dim hover:bg-elevated"} transition-colors`}>
            <I.Memory size={12} /> {t("topbar.memory")}
          </button>
          <span className={`pill ${llmReady ? "pill-green" : "pill-red"}`}>
            <span className={`dot ${llmReady ? "dot-green live" : "dot-red"}`} />
            {llmReady ? t("topbar.llmArmed") : t("topbar.llmOffline")}
          </span>

          {/* 主题切换：当前是 dark 时显示太阳（点击切到 light），反之亦然 */}
          <button
            onClick={toggleTheme}
            title={theme === "dark" ? t("topbar.theme.dark") : t("topbar.theme.light")}
            className="pill pill-dim hover:bg-elevated transition-colors"
            aria-label={theme === "dark" ? t("topbar.theme.dark") : t("topbar.theme.light")}
          >
            {theme === "dark" ? <I.Sun size={12} /> : <I.Moon size={12} />}
          </button>

          {/* 语言切换：按钮上显示「另一个」语言名，点击即切换 */}
          <button
            onClick={toggleLocale}
            title={t("topbar.lang.tooltip")}
            className="pill pill-dim hover:bg-elevated transition-colors font-mono"
            aria-label={t("topbar.lang.tooltip")}
          >
            <I.Globe size={12} />
            <span className="ml-0.5">{otherLocaleLabel}</span>
          </button>

          <div className="w-7 h-7 rounded-full bg-elevated border border-line flex items-center justify-center font-mono text-[11px] text-gold">Q</div>
        </div>
      </div>
    </header>
  );
}

export function Shell({
  nav, setNav, llmReady, onMemory, children,
}: {
  nav: NavId; setNav: (n: NavId) => void;
  llmReady: boolean;
  onMemory: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="h-screen flex flex-col text-ink">
      <TopBar nav={nav} setNav={setNav} onMemory={onMemory} llmReady={llmReady} />
      <main className="flex-1 min-h-0 min-w-0 overflow-auto">{children}</main>
    </div>
  );
}