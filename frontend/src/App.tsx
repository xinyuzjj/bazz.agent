import React, { useCallback, useEffect, useState } from "react";
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
import Toasts from "./components/Toasts";
import AppDialogHost, { showAppDialog } from "./components/ConfirmDialog";
import { useT } from "./i18n/i18n";
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

  // v1.5.27：点 X 的「托盘 / 退出」询问——主进程转发到渲染层，用应用内美化弹窗替代系统原生弹窗
  const t = useT();
  useEffect(() => {
    const bw = (window as any).bazzWindow;
    if (!bw?.onAskClose || !bw?.answerClose) return;
    return bw.onAskClose(() => {
      showAppDialog({
        title: t("dialog.closeTitle"),
        detail: t("dialog.closeDetail"),
        choices: [
          { id: "tray", label: t("dialog.closeTray") },
          { id: "quit", label: t("dialog.closeQuit"), variant: "danger" },
        ],
        layout: "list",
        checkbox: { label: t("dialog.closeRemember") },
        cancelId: "cancel",   // Esc / 点遮罩 / 关闭按钮 = 取消，留在当前窗口
      }).then((r) => bw.answerClose(r));
    });
  }, [t]);

  // 从行情/妖币/对话带 symbol 跳到 CEX；妖币模式决定现货还是合约、方向
  // useCallback：保持引用稳定，让 MarketsView 的 memo 行组件不至于随状态轮询整表重渲
  const goTrade = useCallback((symbol: string, mode?: "spot-long" | "futures-long" | "futures-short") => {
    setTradeSymbol(symbol);
    setTradeMode(mode);
    setNav("cex");
  }, []);

  // 从行情按钮（现货买入 / 合约做多 / 合约做空）跳到对话，并向 Agent 提问仓位建议
  const goChatOrder = useCallback((symbol: string, mode: "spot-long" | "futures-long" | "futures-short") => {
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
  }, [curLocale]);

  // 从行情 / 妖币页「一键分析」跳到对话，让 Agent 解读某标的（走势 / 位置 / 风险 / 入场）
  const goAnalyze = useCallback((symbol: string) => {
    const isEn = curLocale === "en";
    const msg = isEn
      ? `Analyze ${symbol}: FIRST call run_skill with skill_name="coin-report" (args="report ${symbol}") to pull the full local dataset (90d klines, momentum, range percentile, funding, OI, top-trader long/short, fear & greed), then combine with market_quote if needed. Give trend direction, where price sits in its 90-day range, key risk warnings, and an entry plan with clear invalidation conditions. Clearly separate facts from speculation. Do NOT call Binance REST directly.`
      : `帮我分析 ${symbol}：请先用 run_skill 调用 coin-report 技能（args="report ${symbol}"）获取本机全维度数据（90日K线、动量、区间分位、资金费率、OI、大户多空比、恐惧贪婪），需要补项时再用 market-data 技能。基于真实数据给出趋势方向、当前价位在 90 日区间的位置、关键风险提示，以及入场计划（含失效条件）。请明确区分事实与推测。不要用 run_command 直连币安 API。`;
    setPendingChatMsg(msg);
    setTradeSymbol(undefined);
    setTradeMode(undefined);
    setNav("chat");
  }, [curLocale]);

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
      <Toasts />   {/* v1.4.0：全局事件通知（订单状态 / SL·TP 提醒）——应用内 toast + 系统通知 */}
      <AppDialogHost />   {/* v1.5.27：全局美化弹窗（替代 window.confirm / 系统原生 dialog） */}
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
