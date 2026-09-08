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
import { I18nProvider, useI18n } from "./i18n/i18n";
import UpdateNotifier from "./components/UpdateNotifier";
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
          <div style={{ color: "#fff", fontWeight: 700, marginBottom: 8 }}>渲染错误（已捕获） / Render error (caught)</div>
          {String(this.state.err?.message ?? this.state.err)}
          {"\n\n"}{this.state.err?.stack ?? ""}
        </div>
      );
    }
    return this.props.children;
  }
}

function AppInner() {
  const { locale: curLocale } = useI18n();
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
  // 跨视图「提问 Agent」消息：行情页下单按钮 → 切到对话并自动发问
  const [pendingChatMsg, setPendingChatMsg] = useState<string | undefined>(undefined);

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

  // 从行情按钮（现货买入 / 合约做多 / 合约做空）跳到对话，并向 Agent 提问仓位建议
  const goChatOrder = (symbol: string, mode: "spot-long" | "futures-long" | "futures-short") => {
    const isEn = curLocale === "en";
    const tag =
      mode === "spot-long"      ? (isEn ? "spot-buy" : "现货买入")
      : mode === "futures-long" ? (isEn ? "futures-long" : "合约做多")
      : (isEn ? "futures-short" : "合约做空");
    const msg = isEn
      ? `I want to ${tag} ${symbol}. Based on the current price, the 7-day and 30-day trends, volume and open-interest changes on the Markets page, give me a position-sizing plan: ` +
        (mode === "spot-long"
          ? "suggested % of total capital, entry strategy (market/limit/DCA), target price, stop-loss and take-profit batches."
          : "suggested conservative leverage, position %, liquidation price, stop-loss, take-profit batches, and risk-reward ratio.")
      : `我想对 ${symbol} 做${tag}，请结合行情页当前价位 / 7日与30日趋势 / 成交量与持仓变化，给出仓位建议：` +
        (mode === "spot-long"
          ? "建议占总资金比例、入场策略（市价/限价/分批）、目标价、止损价、止盈分批。"
          : "建议杠杆倍数（保守）、仓位占比、强平价、止损、止盈分批、风险收益比。");
    setPendingChatMsg(msg);
    setTradeSymbol(undefined);
    setTradeMode(undefined);
    setNav("chat");
  };

  // 从行情 / 妖币页「一键分析」跳到对话，让 Agent 解读某标的（走势 / 位置 / 风险 / 入场）
  const goAnalyze = (symbol: string) => {
    const isEn = curLocale === "en";
    const msg = isEn
      ? `Analyze ${symbol} for me: using the current price, recent price action, volume and funding rate shown on the Markets page, give the trend direction, where the price sits in its 90-day range, the key risk warnings, and an entry plan with clear invalidation conditions. Clearly separate facts from speculation.`
      : `帮我分析 ${symbol}：结合行情页当前价位 / 近期走势 / 成交量 / 资金费率，给出趋势方向、当前价位在 90 日区间的位置、关键风险提示，以及入场计划（含失效条件）。请明确区分事实与推测。`;
    setPendingChatMsg(msg);
    setTradeSymbol(undefined);
    setTradeMode(undefined);
    setNav("chat");
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
      // 除 chat 外的活动视图（chat 常驻挂载，见下方）
      let active: React.ReactNode = null;
      if (nav === "markets") active = <MarketsView onTrade={goTrade} onOrder={goChatOrder} onAnalyze={goAnalyze} />;
      else if (nav === "wallet") active = <WalletView />;
      else if (nav === "skills") active = <Web3SkillsView />;
      else if (nav === "cex") active = (
        <ExchangeView key={`${tradeSymbol ?? "cex"}|${tradeMode ?? "def"}`}
          initialSymbol={tradeSymbol}
          initialTab={tradeMode?.startsWith("spot") ? "spot" : "futures"}
          initialSide={tradeMode === "futures-short" ? "SHORT" : "LONG"}
          halted={halted} onPanicHalt={() => setPanicOpen(true)} />
      );
      else if (nav === "council") active = <SquarePostView />;
      else if (nav === "settings") active = <SettingsView settings={settings} onSaved={setSettings} onNav={(n: any) => setNav(n)} />;
      else if (nav === "memory") active = <MemoryOverlay open={memoryOpen} full onClose={closeMemory} />;
      return (
        <>
          {/* ChatView 常驻挂载：切到行情/钱包等视图仅隐藏（display:none）而不卸载，
              正在进行的流式生成、消息列表与 streaming 状态全部保留，切回即可继续看到完整回复 */}
          <div style={{ display: nav === "chat" ? undefined : "none", height: "100%" }}>
            <ChatView convId={convId} conversations={conversations} setConversations={setConversations} setConvId={setConvId} onTrade={goTrade} onNav={(n) => setNav(n as any)} pendingMsg={pendingChatMsg} onPendingConsumed={() => setPendingChatMsg(undefined)} />
          </div>
          {active}
        </>
      );
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
      <UpdateNotifier />
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
