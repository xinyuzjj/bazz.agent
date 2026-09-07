import React from "react";
import { I } from "./icons";
import { useI18n } from "../i18n/i18n";
import { useTheme } from "../theme/theme";

export type NavId = "chat" | "markets" | "wallet" | "skills" | "cex" | "council" | "settings" | "memory";

// 仅当 desktop（preload 暴露了 bazzWindow）时显示窗口控制按钮
function WindowControls() {
  const w: any = (typeof window !== "undefined") ? (window as any) : null;
  const { t } = useI18n();
  if (!w?.bazzWindow) return null;
  const btn = "w-9 h-9 flex items-center justify-center rounded-md text-ink-dim hover:bg-elevated hover:text-ink transition-colors app-no-drag";
  const close = "w-9 h-9 flex items-center justify-center rounded-md text-ink-dim hover:bg-red-500/85 hover:text-white transition-colors app-no-drag";
  return (
    <div className="flex items-center gap-0.5 ml-1">
      <button onClick={() => w.bazzWindow.minimize()} className={btn} title={t("shell.min")} aria-label={t("shell.min")}>
        <svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="5" width="8" height="1" fill="currentColor"/></svg>
      </button>
      <button onClick={() => w.bazzWindow.toggleMaximize()} className={btn} title={t("shell.max")} aria-label={t("shell.max")}>
        <svg width="10" height="10" viewBox="0 0 10 10"><rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="1"/></svg>
      </button>
      <button onClick={() => w.bazzWindow.close()} className={close} title={t("shell.close")} aria-label={t("shell.close")}>
        <svg width="10" height="10" viewBox="0 0 10 10">
          <path d="M1.5 1.5 L8.5 8.5 M8.5 1.5 L1.5 8.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" fill="none"/>
        </svg>
      </button>
    </div>
  );
}

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
    <header className="sticky top-0 z-40 border-b border-line bg-canvas/85 backdrop-blur-md app-drag">
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
        <nav className="flex items-center gap-0.5 mx-auto px-2 py-1 rounded-lg bg-elevated/40 border border-line app-no-drag">
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
        <div className="flex items-center gap-2 shrink-0 app-no-drag">
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

        {/* 窗口控制按钮（仅桌面版显示） */}
        <WindowControls />
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