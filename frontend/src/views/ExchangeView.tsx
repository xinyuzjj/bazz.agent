import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";

/* Binance CEX 账户管理页（精简版）
 * - 顶部 KPI：账户净值 / 可交易 / 可提现 / 交易 CLI 状态 —— 来自 /api/wallet/cex/summary 与 status.cli
 * - KEY_VAULT：HMAC API Key + Secret 填入 / 状态展示 / 断开（未连接时显示表单，已连接时显示掩码）
 * - 活跃挂单：来自签名代理 /api/wallet/cex/openorders（8s 自动刷新）
 *
 * 原交易面板（左中右三大块：行情/盘口/下单表单/风控/熔断）已全部移除——
 * 真实下单改走 binance-cli（profile main/prod）由 Agent 调用，订单/资金/风险各自独立处理。
 */

export function ExchangeView({ initialSymbol, initialTab, initialSide, halted, onPanicHalt }: {
  initialSymbol?: string;
  initialTab?: "spot" | "futures";
  initialSide?: "LONG" | "SHORT";
  halted?: boolean;
  onPanicHalt?: () => void;
}) {
  const [symbol, setSymbol] = useState<string>(initialSymbol || "BTCUSDT");

  // ---- 交易所账户真实状态 ----
  const [cexStatus, setCexStatus] = useState<any>(null);
  const [cexSummary, setCexSummary] = useState<any>(null);
  const [cexBusy, setCexBusy] = useState(false);
  const [cexErr, setCexErr] = useState("");
  const [cexHint, setCexHint] = useState("");
  const [cexOk, setCexOk] = useState("");
  const [k, setK] = useState("");
  const [s, setS] = useState("");
  const [showSec, setShowSec] = useState(false);
  const loadCex = useCallback(async () => {
    try {
      const st: any = await api.cexStatus();
      setCexStatus(st);
      if (st?.configured) {
        const sm: any = await api.cexSummary();
        setCexSummary(sm);
      } else { setCexSummary(null); }
    } catch { /* 静默 */ }
  }, []);
  useEffect(() => { loadCex(); }, [loadCex]);
  const connectCex = async () => {
    if (!k.trim() || !s.trim()) { setCexErr("API Key 与 Secret 都不能为空。"); return; }
    setCexBusy(true); setCexErr(""); setCexHint(""); setCexOk("");
    try {
      const r: any = await api.cexConnect(k.trim(), s.trim());
      if (r?.status !== "ok") { setCexErr(r?.message || "连接失败（API Key 校验未通过，未保存）。"); setCexHint(r?.hint || ""); return; }
      setCexOk(r?.message || "已连接"); setK(""); setS(""); await loadCex();
    } catch (e: any) { setCexErr(e?.message || String(e)); }
    finally { setCexBusy(false); }
  };
  const disconnectCex = async () => {
    setCexBusy(true); setCexErr(""); setCexOk("");
    try { await api.cexDisconnect(); await loadCex(); setCexOk("已断开。"); }
    catch (e: any) { setCexErr(e?.message || String(e)); }
    finally { setCexBusy(false); }
  };
  const refreshCex = async () => { setCexBusy(true); try { await loadCex(); } finally { setCexBusy(false); } };
  const cexConfigured = !!cexStatus?.configured;
  const acc = cexSummary?.status === "ok" ? cexSummary.account : null;
  const cli = cexStatus?.cli;

  // ---- 真实挂单（签名 openOrders） ----
  const [openOrders, setOpenOrders] = useState<any[] | null>(null);
  const [ordersErr, setOrdersErr] = useState("");
  const loadOrders = useCallback(async () => {
    if (!cexConfigured) { setOpenOrders(null); return; }
    try {
      const r: any = await api.cexOpenOrders();
      setOrdersErr(r?.status === "ok" ? "" : (r?.message || "挂单接口无权限"));
      setOpenOrders(r?.orders ?? []);
    } catch (e: any) { setOrdersErr(e?.message || String(e)); setOpenOrders([]); }
  }, [cexConfigured]);
  useEffect(() => { loadOrders(); const t = setInterval(loadOrders, 8000); return () => clearInterval(t); }, [loadOrders]);

  // ---- 历史成交（myTrades 签名） ----
  const [tradeSym, setTradeSym] = useState<string>("BTCUSDT");
  const [trades, setTrades] = useState<any[] | null>(null);
  const [tradesErr, setTradesErr] = useState("");
  const [tradesNeedSym, setTradesNeedSym] = useState(false);
  const loadTrades = useCallback(async () => {
    if (!cexConfigured) { setTrades(null); return; }
    try {
      const r: any = await api.cexTrades(tradeSym, 50);
      setTradesNeedSym(!!r?.need_symbol);
      setTradesErr(r?.status === "ok" ? "" : (r?.message || "历史成交接口无权限"));
      setTrades(r?.trades ?? []);
    } catch (e: any) { setTradesErr(e?.message || String(e)); setTrades([]); }
  }, [cexConfigured, tradeSym]);
  useEffect(() => { loadTrades(); }, [loadTrades]);

  useEffect(() => {
    if (initialSymbol) setSymbol(initialSymbol);
  }, [initialSymbol]);

  const fmt = (n: number | string | null | undefined, d = 2) => {
    if (n == null || n === "") return "—";
    const x = typeof n === "string" ? parseFloat(n) : n;
    if (Number.isNaN(x)) return "—";
    return x.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
  };
  const fmtTs = (ts: number | undefined) => {
    if (!ts) return "—";
    const d = new Date(ts);
    return d.toLocaleString();
  };

  return (
    <div className="p-5 space-y-4">
      {/* 顶部 KPI —— 真实账户数据 */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <Kpi label="账户净值 (USDT 现价)" value={acc ? `$${fmt(acc.total_usdt, 2)}` : "—"} sub={acc ? (() => {
          const total = (acc.assets || []).length;
          const val = (acc.assets || []).filter((r: any) => !r.no_usdt_pair).length;
          return val === total ? `币种 ${total}` : `已估值 ${val}/${total}（${total - val} 项无 USDT 对）`;
        })() : "连接后展示"} up={acc ? acc.total_usdt > 0 : false} />
        <Kpi label="可交易" value={acc ? (acc.can_trade ? "✓ ENABLED" : "✗ DISABLED") : "—"} sub={acc ? (acc.can_trade ? "可下现货/合约/闪兑" : "API Key 权限受限") : "未连接"} up={acc?.can_trade} badge={acc?.can_trade ? "TRADE" : (acc ? "READ-ONLY" : undefined)} />
        <Kpi label="可提现" value={acc ? (acc.can_withdraw ? "✓ ON ⚠" : "✓ OFF") : "—"}
             sub={acc ? (acc.can_withdraw
                 ? "已开启提现权限（建议去 API 管理关闭）"
                 : "提现已锁定（推荐）") : "未连接"}
             up={acc?.can_withdraw !== undefined ? !acc.can_withdraw : undefined}
             badge={acc ? (acc.can_withdraw ? "WITHDRAW-RISK" : "LOCKED") : undefined} />
        <Kpi label="交易 CLI" value={cli ? (cli.profile ? `binance-cli v${cli.version || "?"}` : "已安装") : "未安装"} sub={cli?.profile ? `profile ${cli.profile}/${cli.env}` : (cli ? "连接后自动生成 profile" : "npm i -g @binance/binance-cli")} up={!!cli?.profile} badge={cli?.profile ? "READY" : (cli ? "NEED-CONNECT" : "MISSING")} />
      </div>

      {/* KEY_VAULT —— 填入 + 状态 + 账户资产快照 */}
      <div className="glass p-3.5 space-y-2.5">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="pill pill-gold">BINANCE_CEX_DEDICATED_RELAY</span>
          <span className="font-mono text-[10px] text-ink-dim">[HMAC-SHA256 · SHA256(urlencode(payload, RFC3986))]</span>
          {cexConfigured ? (
            <>
              <span className="pill pill-green"><span className="dot dot-green live" /> 已连接交易所账户</span>
              <span className="pill pill-dim font-mono">KEY {cexStatus?.masked_key}</span>
              {cexStatus?.cli?.profile && (
                <span className="pill pill-green font-mono">binance-cli v{cexStatus.cli.version} · {cexStatus.cli.profile}/{cexStatus.cli.env}</span>
              )}
              <div className="ml-auto flex items-center gap-2 font-mono text-[10px]">
                <span className="pill pill-red"><I.Lock size={10} /> WITHDRAW_LOCKED</span>
                <button onClick={refreshCex} disabled={cexBusy} className="btn-ghost text-[11px] py-1"><I.Refresh size={11} className={cexBusy ? "animate-spin" : ""} /> 刷新</button>
                <button onClick={disconnectCex} disabled={cexBusy} className="btn-ghost text-[11px] py-1 text-ink-mute hover:text-red"><I.X size={10} /> 断开</button>
              </div>
            </>
          ) : (
            <>
              <span className="pill pill-red">未连接交易所账户</span>
              <span className="pill pill-dim">需填入 HMAC Key + Secret 以启用真实下单通道</span>
              <div className="ml-auto flex items-center gap-2 font-mono text-[10px]">
                <span className="pill pill-dim"><I.Lock size={10} /> AES-256</span>
              </div>
            </>
          )}
        </div>

        {!cexConfigured && (
          <div className="rounded-md border border-gold/30 bg-gold/[0.04] p-3 space-y-2">
            <div className="font-mono text-[11px] text-ink-dim flex items-center gap-2">
              <I.Key size={12} className="text-gold" /> 填入币安交易所 API Key + Secret —— 校验通过后自动同步到本地 binance-cli profile（main · prod），Agent 可真实执行现货 / 合约 / 闪兑。
            </div>
            <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto] gap-2">
              <input value={k} onChange={(e) => setK(e.target.value)} placeholder="API Key（binance.com → 账户 → API 管理）"
                className="field font-mono text-[12px]" autoComplete="off" spellCheck={false} />
              <div className="relative">
                <input type={showSec ? "text" : "password"} value={s} onChange={(e) => setS(e.target.value)}
                  placeholder="Secret Key（HMAC 私钥）"
                  className="field font-mono text-[12px] pr-14" autoComplete="off" spellCheck={false} />
                <button type="button" onClick={() => setShowSec((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] font-mono text-ink-mute hover:text-gold">
                  {showSec ? "隐藏" : "显示"}
                </button>
              </div>
              <button onClick={connectCex} disabled={cexBusy || !k.trim() || !s.trim()} className="btn-gold">
                {cexBusy ? <I.Refresh size={12} className="animate-spin" /> : <I.Key size={12} />} 连接并验证
              </button>
            </div>
            {cexErr && (
              <div className="rounded-md border border-red/30 bg-red/5 px-3 py-2 font-mono text-[11px] text-red leading-relaxed">
                {cexErr}
                {cexHint && <div className="mt-1 text-gold">💡 {cexHint.split('\n')[0]}</div>}
              </div>
            )}
            {cexOk && <div className="rounded-md border border-green/40 bg-green/5 px-3 py-2 font-mono text-[11px] text-green leading-relaxed">{cexOk}</div>}
            <div className="font-mono text-[10px] text-ink-mute leading-relaxed">
              ⚠ 密钥仅保存本机 settings，提交前先用 <code className="text-gold">/api/v3/account</code> 真实校验；建议 API Key 绑定受信 IP（出口 <code className="text-gold">117.170.200.178</code>）、<b className="text-ink-dim">保持提现权限关闭</b>，只勾选需要的现货/合约/闪兑权限。
            </div>
          </div>
        )}

        {/* 账户资产快照（非零余额） */}
        {acc && acc.assets?.length > 0 && (
          <div className="rounded-md border border-line bg-elevated/30">
            <div className="px-4 py-2.5 flex items-center gap-3 border-b border-line">
              <span className="prefix">非零持仓</span>
              <span className="pill pill-dim">{acc.assets.length} 项</span>
              <span className="pill pill-gold ml-auto">总估值 ${fmt(acc.total_usdt, 2)}</span>
            </div>
            <div className="grid items-center px-4 py-1.5 border-b border-line/60 text-[10px] font-mono tracking-[0.08em] text-ink-dim"
              style={{ gridTemplateColumns: "1fr 1fr 1fr 1fr" }}>
              <div>资产</div><div className="text-right">可用</div><div className="text-right">锁定</div><div className="text-right">估值 (USDT)</div>
            </div>
            {acc.assets.slice(0, 12).map((r: any) => (
              <div key={r.asset} className="grid items-center px-4 py-1.5 border-b border-line/40 hover:bg-elevated/40 transition-colors font-mono text-[12px]"
                style={{ gridTemplateColumns: "1fr 1fr 1fr 1fr" }}>
                <div className="font-semibold">{r.asset}{r.no_usdt_pair && <span className="ml-1 text-[9px] text-ink-mute" title="未找到 USDT 对，无法估值">N/A</span>}</div>
                <div className="tabular text-right">{fmt(r.free, 4)}</div>
                <div className="tabular text-right text-ink-dim">{fmt(r.locked, 4)}</div>
                <div className="tabular text-right text-gold">{r.no_usdt_pair ? "—" : `$${fmt(r.usdt, 2)}`}</div>
              </div>
            ))}
            {acc.assets.length > 12 && (
              <div className="px-4 py-2 text-center font-mono text-[10.5px] text-ink-mute">… 另有 {acc.assets.length - 12} 项小余额</div>
            )}
          </div>
        )}
      </div>

      {/* 活跃挂单 —— /api/v3/openOrders 真实数据 */}
      <div className="glass">
        <div className="px-4 py-3 flex items-center gap-4 border-b border-line">
          <button className="font-mono text-[13px] text-ink"><span className="text-gold">▣</span> 活跃挂单 (Open Orders) <span className="pill pill-gold ml-1">{openOrders?.length ?? 0}</span></button>
          <button onClick={() => loadOrders()} className="btn-ghost"><I.Refresh size={12} /> 刷新</button>
          <div className="ml-auto font-mono text-[10.5px] text-ink-mute">
            {!cexConfigured ? "未连接 Key · 无法读取挂单" : ordersErr ? ordersErr : "每 8 秒自动刷新"}
          </div>
        </div>
        {openOrders === null ? (
          <div className="px-4 py-6 text-center font-mono text-[12px] text-ink-mute">{cexConfigured ? "加载中…" : "请先在顶部填入 Key 并连接"}</div>
        ) : openOrders.length === 0 ? (
          <div className="px-4 py-6 text-center font-mono text-[12px] text-ink-mute">暂无活跃挂单</div>
        ) : (
          <>
            <div className="grid items-center px-4 py-2 border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim"
              style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1.2fr 1fr 1.2fr 1.2fr" }}>
              <div>标的</div><div>方向</div><div>类型</div><div>价格</div><div>数量</div><div>原始/已成交</div><div>时间</div>
            </div>
            {openOrders.map((o: any) => (
              <div key={o.orderId} className="grid items-center px-4 py-2.5 border-b border-line/60 hover:bg-elevated/40 transition-colors font-mono text-[12px]"
                style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1.2fr 1fr 1.2fr 1.2fr" }}>
                <div className="font-semibold">{o.symbol}</div>
                <div><span className={`pill ${o.side === "BUY" ? "pill-green" : "pill-red"}`}>{o.side}</span></div>
                <div className="text-ink-dim">{o.type}</div>
                <div className="tabular">${fmt(o.price, o.type === "MARKET" ? 4 : 2)}</div>
                <div className="tabular">{fmt(o.origQty, 4)} {o.symbol.replace(/USDT$|BTC$|FDUSD$|USDC$/i, "")}</div>
                <div className="tabular text-ink-dim">{fmt(o.executedQty, 4)} / {parseFloat(o.origQty) > 0 ? ((parseFloat(o.executedQty) / parseFloat(o.origQty)) * 100).toFixed(1) : "0"}%</div>
                <div className="text-ink-dim text-[10.5px]">{fmtTs(o.time || o.updateTime)}</div>
              </div>
            ))}
          </>
        )}
      </div>

      {/* 历史成交 —— /api/v3/myTrades 真实数据 */}
      <div className="glass">
        <div className="px-4 py-3 flex items-center gap-3 border-b border-line">
          <span className="font-mono text-[13px] text-ink"><span className="text-gold">▣</span> 历史成交 (Trade History)</span>
          <input value={tradeSym} onChange={(e) => setTradeSym(e.target.value.toUpperCase())}
            placeholder="BTCUSDT" className="field font-mono text-[11px] w-[140px] py-1" spellCheck={false} />
          <button onClick={() => loadTrades()} className="btn-ghost"><I.Refresh size={12} /> 查询</button>
          <span className="pill pill-dim">最近 50 条</span>
          <div className="ml-auto font-mono text-[10.5px] text-ink-mute">
            {!cexConfigured ? "未连接 Key · 无法读取历史成交" : tradesErr ? tradesErr : (trades?.length !== undefined ? `共 ${trades.length} 条` : "")}
          </div>
        </div>
        {trades === null ? (
          <div className="px-4 py-6 text-center font-mono text-[12px] text-ink-mute">{cexConfigured ? "加载中…" : "请先在顶部填入 Key 并连接"}</div>
        ) : trades.length === 0 ? (
          <div className="px-4 py-6 text-center font-mono text-[12px] text-ink-mute">{tradesNeedSym ? `请输入交易对（如 BTCUSDT）后查询（/api/v3/myTrades 要求必填 symbol）` : `该交易对 (${tradeSym}) 暂无成交`}</div>
        ) : (
          <>
            <div className="grid items-center px-4 py-2 border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim"
              style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1.1fr 1.1fr 1fr 1.2fr" }}>
              <div>时间</div><div>交易对</div><div>方向</div><div>价格</div><div>数量</div><div>成交额</div><div>手续费</div>
            </div>
            {trades.map((t: any) => (
              <div key={t.id} className="grid items-center px-4 py-2.5 border-b border-line/60 hover:bg-elevated/40 transition-colors font-mono text-[12px]"
                style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1.1fr 1.1fr 1fr 1.2fr" }}>
                <div className="text-ink-dim text-[10.5px]">{fmtTs(t.time)}</div>
                <div className="font-semibold">{t.symbol}</div>
                <div><span className={`pill ${t.isBuyer ? "pill-green" : "pill-red"}`}>{t.isBuyer ? "BUY" : "SELL"}</span></div>
                <div className="tabular">${fmt(t.price, 4)}</div>
                <div className="tabular">{fmt(t.qty, 4)}</div>
                <div className="tabular text-ink-dim">${fmt(parseFloat(t.price) * parseFloat(t.qty), 2)}</div>
                <div className="tabular text-ink-dim text-[10.5px]">{t.commissionAsset} {fmt(t.commission, 6)}</div>
              </div>
            ))}
          </>
        )}
      </div>

      <div className="font-mono text-[10.5px] text-ink-mute px-1">
        💡 真实下单改走 <code className="text-gold">binance-cli</code>（profile <b>main/prod</b>）：在对话里跟 Agent 说「下个 0.001 BTC 的现货限价买单」之类，或在终端直接跑 <code className="text-gold">binance-cli spot order ...</code>。执行前会强制让你确认。
      </div>
    </div>
  );
}

function Kpi({ label, value, sub, up, badge }: { label: string; value: string; sub: string; up?: boolean; badge?: string }) {
  return (
    <div className="glass p-3.5">
      <div className="flex items-center justify-between">
        <span className="prefix">{label}</span>
        {badge && <span className={`pill ${up ? "pill-green" : "pill-dim"}`}>{badge}</span>}
      </div>
      <div className={`mt-2 font-mono tabular text-[26px] font-bold leading-none ${up ? "up" : "text-ink"}`}>{value}</div>
      <div className={`mt-1.5 font-mono text-[10px] ${up ? "up" : "text-ink-mute"}`}>{sub}</div>
    </div>
  );
}