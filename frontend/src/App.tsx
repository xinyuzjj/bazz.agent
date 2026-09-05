import React, { useEffect, useState } from "react";
import { api } from "./api";
import { Shell, NavId } from "./components/Shell";
import { ChatView } from "./views/ChatView";
import { MarketsView } from "./views/MarketsView";
import { WalletView } from "./views/WalletView";
import { Web3SkillsView } from "./views/Web3SkillsView";
import { ExchangeView } from "./views/ExchangeView";
import { SquarePostView } from "./views/SquarePostView";
import { SettingsView } from "./views/SettingsView";
import { MemoryOverlay } from "./views/MemoryOverlay";
import { PanicHaltModal } from "./views/PanicHaltModal";
import { I18nProvider } from "./i18n/i18n";
import { ThemeProvider } from "./theme/theme";

// 兜底：捕获子树渲染错误，渲染降级提示而不让整页崩
class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { err?: any }> {
  state = { err: undefined as any };
  static getDerivedStateFromError(err: any) { return { err }; }
  componentDidCatch(err: any, info: any) { (window as any).__boot_err__ = (window as any).__boot_err__ || []; (window as any).__boot_err__.push({ err, info }); }
  render() {
    if (this.state.err) {
      return (
        <div style={{ position: "fixed", inset: "60px 16px 16px 16px", padding: 16, background: "#1a0d0d", border: "1px solid #f6465d", color: "#ff9aa8", borderRadius: 8, font: "12px/1.5 ui-monospace, monospace", whiteSpace: "pre-wrap", overflow: "auto", zIndex: 99998 }}>
          <div style={{ color: "#fff", fontWeight: 700, marginBottom: 8 }}>渲染错误（已捕获）</div>
          {String(this.state.err?.message ?? this.state.err)}
          {"\n\n"}{this.state.err?.stack ?? ""}
        </div>
      );
    }
    return this.props.children;
  }
}

function AppInner() {
  const [nav, setNav] = useState<NavId>("chat");
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [settings, setSettings] = useState<any>(null);
  const [conversations, setConversations] = useState<any[]>([]);
  const [convId, setConvId] = useState<string | null>(null);
  const [status, setStatus] = useState<any>(null);
  // 全局熔断（PANIC HALT）
  const [halted, setHalted] = useState(false);
  const [panicOpen, setPanicOpen] = useState(false);
  // 跨视图下单标的（行情/妖币/对话 → CEX；妖币可带现货/合约+方向预设）
  const [tradeSymbol, setTradeSymbol] = useState<string | undefined>(undefined);
  const [tradeMode, setTradeMode] = useState<"spot-long" | "futures-long" | "futures-short" | undefined>(undefined);

  useEffect(() => {
    api.settings().then(setSettings).catch(() => {});
    api.conversations(true).then((d: any) => setConversations(Array.isArray(d) ? d : d?.items ?? [])).catch(() => {});
    const tick = () => api.status().then(setStatus).catch(() => {});
    tick();
    const t = setInterval(tick, 8000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (settings && !settings?.llm?.configured && !sessionStorage.getItem("llm-hint-shown")) {
      sessionStorage.setItem("llm-hint-shown", "1");
    }
  }, [settings]);

  // 从行情/妖币/对话带 symbol 跳到 CEX；妖币模式决定现货还是合约、方向
  const goTrade = (symbol: string, mode?: "spot-long" | "futures-long" | "futures-short") => {
    setTradeSymbol(symbol);
    setTradeMode(mode);
    setNav("cex");
  };

  const openMemory = () => { setNav("memory"); setMemoryOpen(true); };
  const closeMemory = () => { setMemoryOpen(false); setNav("chat"); };

  const telemetry = {
    cpu: status?.cpu ?? "28.4%",
    mem: status?.mem ?? "4.12 / 16 GB",
    agents: status?.agents ?? "9 AGENTS",
    load: status?.load ?? "NOMINAL",
    build: status?.build ?? "BUILD 2025.04.1-PROD",
  };
  void telemetry; // 之前传给 Shell.LeftRail（已删除），保留供未来使用

  const renderView = () => {
    try {
      if (nav === "chat") return <ChatView convId={convId} conversations={conversations} setConversations={setConversations} setConvId={setConvId} onTrade={goTrade} onNav={(n) => setNav(n as any)} />;
      if (nav === "markets") return <MarketsView onTrade={goTrade} onOrder={goTrade} />;
      if (nav === "wallet") return <WalletView />;
      if (nav === "skills") return <Web3SkillsView />;
      if (nav === "cex") return (
        <ExchangeView key={`${tradeSymbol ?? "cex"}|${tradeMode ?? "def"}`}
          initialSymbol={tradeSymbol}
          initialTab={tradeMode?.startsWith("spot") ? "spot" : "futures"}
          initialSide={tradeMode === "futures-short" ? "SHORT" : "LONG"}
          halted={halted} onPanicHalt={() => setPanicOpen(true)} />
      );
      if (nav === "council") return <SquarePostView />;
      if (nav === "settings") return <SettingsView settings={settings} onSaved={setSettings} onNav={(n: any) => setNav(n)} />;
      if (nav === "memory") return <MemoryOverlay open={memoryOpen} full onClose={closeMemory} />;
      return null;
    } catch (e: any) {
      return (
        <div style={{ padding: 16, color: "#ff9aa8", fontFamily: "monospace", fontSize: 12 }}>
          <b>View render failed:</b> {String(e?.message ?? e)}
          <pre style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>{e?.stack ?? ""}</pre>
        </div>
      );
    }
  };

  return (
    <>
      <Shell nav={nav} setNav={setNav}
        llmReady={!!settings?.llm?.configured}
        onMemory={openMemory}>
        <ErrorBoundary>{renderView()}</ErrorBoundary>
      </Shell>
      <PanicHaltModal
        isOpen={panicOpen}
        onClose={() => setPanicOpen(false)}
        halted={halted}
        onConfirm={() => { setHalted(true); setPanicOpen(false); }}
        onResume={() => { setHalted(false); setPanicOpen(false); }}
      />
    </>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <I18nProvider>
        <AppInner />
      </I18nProvider>
    </ThemeProvider>
  );
}
