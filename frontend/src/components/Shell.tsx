import React from "react";
import { I } from "./icons";
import { useI18n } from "../i18n/i18n";
import { useTheme } from "../theme/theme";

export type NavId = "chat" | "markets" | "wallet" | "skills" | "cex" | "council" | "settings" | "memory";

// vite.config.ts / preview.config.ts 的 define 注入的包版本号
declare const __APP_VERSION__: string;

type ShellProps = {
  nav: NavId; setNav: (n: NavId) => void; onMemory: () => void; llmReady: boolean;
};

// 仅当 desktop（preload 暴露了 bazzWindow）时显示窗口控制按钮
function WindowControls() {
  const w = (window as any).bazzWindow;
  const { t } = useI18n();
  if (!w) return null;
  return <div className="window-controls app-no-drag">
    <button className="icon-button" onClick={() => w.minimize()} title={t("shell.min")} aria-label={t("shell.min")}><I.Minus size={15} /></button>
    <button className="icon-button" onClick={() => w.toggleMaximize()} title={t("shell.max")} aria-label={t("shell.max")}><svg width="14" height="14" viewBox="0 0 14 14" fill="none"><rect x="2.5" y="2.5" width="9" height="9" rx="1" stroke="currentColor" /></svg></button>
    <button className="icon-button window-close" onClick={() => w.close()} title={t("shell.close")} aria-label={t("shell.close")}><I.X size={15} /></button>
  </div>;
}

function useNavigation() {
  const { t, locale } = useI18n();
  // 记忆入口也在导航里：点击走 App 的 onMemory（打开记忆浮层），不是普通 setNav
  return {
    locale,
    items: [
      { id: "chat", label: t("nav.chat"), Icon: I.Chat },
      { id: "markets", label: t("nav.markets"), Icon: I.Market },
      { id: "wallet", label: t("nav.wallet"), Icon: I.Wallet },
      { id: "cex", label: t("nav.cex"), Icon: I.Cex },
      { id: "council", label: t("nav.council"), Icon: I.Megaphone },
      { id: "skills", label: t("nav.skills"), Icon: I.Grid },
      { id: "memory", label: t("topbar.memory"), Icon: I.Memory },
      { id: "settings", label: t("nav.settings"), Icon: I.Gear },
    ] as { id: NavId; label: string; Icon: React.FC<any> }[],
  };
}

/** 顶部导航条：品牌 | 导航 | 工具。导航不再占左侧一整列。 */
export function TopBar({ nav, setNav, llmReady, onMemory }: ShellProps) {
  const { t, locale, toggle: toggleLocale } = useI18n();
  const { theme, toggle: toggleTheme } = useTheme();
  const { items } = useNavigation();
  const activate = (id: NavId) => id === "memory" ? onMemory() : setNav(id);
  const openCommands = () => {
    setNav("chat");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }));
  };
  return <header className="app-topbar app-drag">
    <div className="topbar-brand">
      <span className="brand-symbol"><I.Hex size={24} /></span>
      <div className="brand-wordmark"><strong>BAZZ<span>.</span>AGENT</strong><small>BINANCE AGENT OS · v{__APP_VERSION__}</small></div>
    </div>
    <nav className="topbar-nav app-no-drag" aria-label={locale === "zh" ? "主导航" : "Main navigation"}>
      {items.map(n => (
        <button key={n.id} className={`topbar-nav-item ${nav === n.id ? "is-active" : ""}`} onClick={() => activate(n.id)}
          aria-current={nav === n.id ? "page" : undefined} title={n.label}>
          <n.Icon size={15} /><span>{n.label}</span>
        </button>
      ))}
    </nav>
    <div className="topbar-tools app-no-drag">
      <button className="icon-button" onClick={openCommands}
        title={locale === "zh" ? "搜索与快捷操作 (Ctrl K)" : "Search and commands (Ctrl K)"}
        aria-label={locale === "zh" ? "搜索与快捷操作" : "Search and commands"}><I.Search size={16} /></button>
      <span className="connection-status">
        <span className={`dot ${llmReady ? "dot-green live" : "dot-red"}`} />
        <span className="connection-label">{llmReady ? t("topbar.llmArmed") : t("topbar.llmOffline")}</span>
      </span>
      <span className="tool-divider" />
      <button className="icon-button" onClick={toggleTheme} title={theme === "dark" ? t("topbar.theme.dark") : t("topbar.theme.light")} aria-label={theme === "dark" ? t("topbar.theme.dark") : t("topbar.theme.light")}>
        {theme === "dark" ? <I.Sun size={17} /> : <I.Moon size={17} />}
      </button>
      <button className="icon-button language-button" onClick={toggleLocale} title={t("topbar.lang.tooltip")} aria-label={t("topbar.lang.tooltip")}>{locale === "zh" ? "EN" : "中"}</button>
      <WindowControls />
    </div>
  </header>;
}

export function Shell({ nav, setNav, llmReady, onMemory, children }: ShellProps & { children: React.ReactNode }) {
  return <div className="app-shell text-ink">
    <TopBar nav={nav} setNav={setNav} llmReady={llmReady} onMemory={onMemory} />
    <main className={`workspace-content view-${nav}`} id="workspace-content">{children}</main>
  </div>;
}
