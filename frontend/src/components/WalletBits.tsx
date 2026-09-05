import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { I } from "./icons";

/* =====================================================================
 * 钱包共享组件（WalletView 全页 与 ChatView 弹层复用）
 *   1) AgentSigninCard —— Agent 钱包（baw MPC）：扫码登录卡
 *   2) ChainWalletPanel —— 链上钱包（Binance Web3 Wallet API，BX- Key）：连接 / 链上地址持仓
 *   3) Spin —— 加载圈
 *
 * 交易所账户（HMAC Key）已迁出钱包 Hub，搬到顶导「Binance CEX」页面（ExchangeView），
 *     该页直接绑定本地 binance-cli profile，可真实执行现货 / 合约 / 闪兑（须 CONFIRM）。
 * ===================================================================== */

export type WalletState = {
  connected?: boolean;
  configured?: boolean;
};

function errText(e: any): string {
  const m = (e?.message ?? String(e ?? "")) as string;
  return m || "请求失败";
}

const fmtUSDT = (n: number | undefined | null) => {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
};

const trimZero = (n: number | string | undefined | null) => {
  if (n == null) return "0";
  const s = String(n);
  if (!s.includes(".")) return s;
  return s.replace(/\.?0+$/, "");
};

/* ---------------- Agent 钱包：扫码登录卡 ---------------- */

export function AgentSigninCard({ compact, onDone, onOpenPage }: {
  compact?: boolean;
  onDone?: () => void;
  onOpenPage?: () => void;
}) {
  const [state, setState] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [qr, setQr] = useState<{ qrCodeId: string; qrImage: string } | null>(null);
  const [pending, setPending] = useState(false);
  const pollRef = useRef<number | null>(null);

  const load = async () => {
    try {
      const s: any = await api.wallet(true);
      setState(s);
      if (s?.status?.connected && onDone) onDone();
    } catch (e: any) { setErr(errText(e)); }
  };
  useEffect(() => { load(); return () => { if (pollRef.current) window.clearInterval(pollRef.current); }; }, []);

  const connected = !!state?.status?.connected;
  const installed = !!state?.cli?.installed;

  const install = async () => {
    setBusy("install"); setErr("");
    try { await api.walletInstall(); await load(); }
    catch (e: any) { setErr(errText(e)); } finally { setBusy(null); }
  };

  const startSignin = async () => {
    setBusy("signin"); setErr("");
    try {
      const r: any = await api.walletSignin();
      const qrCodeId: string | undefined = r?.qrCodeId ?? r?.qr_code_id;
      if (!qrCodeId) throw new Error("未返回 qrCodeId");
      const qrUrl = r?.qrUrl || r?.qr_url || r?.qrString || r?.qr_string || "";
      const qrImage = qrUrl ? await api.walletQr(qrUrl).catch(() => "") : "";
      setQr({ qrCodeId, qrImage });
      setPending(true);
      pollRef.current = window.setInterval(async () => {
        try {
          const v: any = await api.walletVerify(qrCodeId, 10);
          if (v?.status === "ok" || v?.connected) {
            if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
            setPending(false); setQr(null); await load();
          }
        } catch { /* 忽略轮询错误 */ }
      }, 11000);
    } catch (e: any) { setErr(errText(e)); }
    finally { setBusy(null); }
  };

  const cancelSignin = () => {
    if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    setPending(false); setQr(null);
  };

  const signout = async () => {
    setBusy("signout"); setErr("");
    try { await api.walletSignout(); await load(); }
    catch (e: any) { setErr(errText(e)); }
    finally { setBusy(null); }
  };

  if (connected) {
    return (
      <div className="rounded-lg border border-green/40 bg-green/5 px-4 py-3 flex items-center gap-2 font-mono text-[12px]">
        <span className="dot dot-green live" /> Agent 钱包已登录，可调用 baw 钱包/交易/DeFi/x402 命令。
        {!compact && (
          <button onClick={signout} disabled={!!busy} className="ml-auto btn-ghost text-[11px] py-1 text-ink-mute hover:text-red">
            <I.X size={10} /> 退出
          </button>
        )}
      </div>
    );
  }

  if (!installed) {
    return (
      <div className="space-y-3">
        <div className="rounded-lg border border-gold/40 bg-gold/5 px-4 py-3 font-mono text-[12px] text-ink">
          ⚠ 未检测到 Agentic Wallet（baw）CLI。可一键安装（依赖 npm 可用）。
        </div>
        <button onClick={install} disabled={!!busy} className="btn-gold">
          {busy === "install" ? <span className="dot dot-gold live" /> : <I.Qr size={12} />} 安装 Agentic Wallet
        </button>
        {err && <div className="rounded-lg border border-red/40 bg-red/5 px-4 py-2 font-mono text-[11px] text-red">{err}</div>}
      </div>
    );
  }

  if (pending && qr) {
    return (
      <div className="space-y-3">
        <div className="rounded-lg border border-line bg-elevated/30 p-4 flex flex-col md:flex-row gap-4 items-center">
          <div className="rounded-md border border-gold/40 bg-canvas p-2 flex items-center justify-center" style={{ width: 192, height: 192 }}>
            {qr.qrImage
              ? <img src={qr.qrImage} alt="qr" className="w-full h-full object-contain" />
              : <span className="text-[10px] font-mono text-ink-mute">无图像（前端请用 Binance App 扫码登录）</span>}
          </div>
          <div className="flex-1 space-y-1.5 font-mono text-[12px]">
            <div className="text-ink">📱 用 Binance App 扫一扫完成配对</div>
            <div className="text-ink-mute text-[11px]">配对码：<code className="text-gold">{qr.qrCodeId}</code></div>
            <div className="pill pill-gold mt-2 inline-flex items-center gap-1"><span className="dot dot-gold live" /> 等待 App 确认</div>
          </div>
          <button onClick={cancelSignin} className="btn-ghost text-[11px] py-1.5">取消</button>
        </div>
        {err && <div className="rounded-lg border border-red/40 bg-red/5 px-4 py-2 font-mono text-[11px] text-red">{err}</div>}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-line bg-elevated/20 px-4 py-3 font-mono text-[12px] text-ink-dim leading-relaxed">
        点击下方按钮，弹出 Binance App 扫码配对二维码。登录后将自动同步地址 / 链 / 限额 / 历史交易到本应用。
      </div>
      <div className="flex items-center gap-2">
        <button onClick={startSignin} disabled={!!busy} className="btn-gold">
          {busy === "signin" ? <span className="dot dot-gold live" /> : <I.Qr size={12} />} 扫码登录 Agent 钱包
        </button>
        {onOpenPage && (
          <button onClick={onOpenPage} className="btn-ghost text-[11px] py-1.5">打开钱包页</button>
        )}
      </div>
      {err && <div className="rounded-lg border border-red/40 bg-red/5 px-4 py-2 font-mono text-[11px] text-red">{err}</div>}
    </div>
  );
}

/* ---------------- 链上钱包（Binance Web3 Wallet API，BX- Key） ---------------- */

const CHAIN_OPTIONS: { id: string; name: string }[] = [
  { id: "56", name: "BSC" },
  { id: "1", name: "Ethereum" },
  { id: "8453", name: "Base" },
  { id: "solana", name: "Solana" },
  { id: "137", name: "Polygon" },
  { id: "42161", name: "Arbitrum" },
];

export function ChainWalletPanel({ compact, onChanged }: {
  compact?: boolean;
  onChanged?: (connected: boolean) => void;
}) {
  const [status, setStatus] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [hint, setHint] = useState("");
  const [k, setK] = useState("");
  const [s, setS] = useState("");
  const [showSec, setShowSec] = useState(false);
  const [addr, setAddr] = useState("");
  const [chains, setChains] = useState<string[]>(["56", "1"]);
  const [bal, setBal] = useState<any>(null);
  const [agentAddrs, setAgentAddrs] = useState<any>(null);

  const load = useCallback(async () => {
    try {
      const st: any = await api.web3Status();
      setStatus(st);
      try { const aa: any = await api.web3AgentAddresses(); setAgentAddrs(aa); } catch { /* 静默 */ }
    } catch (e: any) { setErr(errText(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const configured = !!status?.configured;
  const masked = status?.masked_key || "";

  const connect = async () => {
    if (!k.trim() || !s.trim()) { setErr("BX- API Key 与 Secret 都不能为空。"); return; }
    setBusy(true); setErr(""); setOk(""); setHint("");
    try {
      const r: any = await api.web3Connect(k.trim(), s.trim());
      if (r?.status && r.status !== "ok") {
        setErr(r?.message || "连接失败（未保存）。");
        setHint(r?.hint || "");
        return;
      }
      setOk(r?.message || "已连接"); setK(""); setS("");
      await load(); onChanged?.(true);
    } catch (e: any) { setErr(errText(e)); }
    finally { setBusy(false); }
  };
  const disconnect = async () => {
    setBusy(true); setErr(""); setOk("");
    try { await api.web3Disconnect(); setStatus({ configured: false, masked_key: "" }); setBal(null); onChanged?.(false); }
    catch (e: any) { setErr(errText(e)); }
    finally { setBusy(false); }
  };
  const query = async () => {
    if (!addr.trim()) { setErr("请输入链上地址。"); return; }
    if (!chains.length) { setErr("至少选择一条链。"); return; }
    setBusy(true); setErr(""); setBal(null);
    try { const r: any = await api.web3Balance(addr.trim(), chains); setBal(r); }
    catch (e: any) { setErr(errText(e)); }
    finally { setBusy(false); }
  };
  const useAgentAddr = (a: string) => { setAddr(a); };

  if (!configured) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <I.Key size={14} className="text-gold" />
          <span className="font-mono text-[12.5px] text-ink">连接链上钱包（Binance Web3 Wallet）</span>
          <span className="pill pill-dim">BX- API Key · 链上地址查询</span>
        </div>
        <div className="grid gap-2">
          <input value={k} onChange={(e) => setK(e.target.value)} placeholder="BX- API Key（web3.binance.com）"
            className="field font-mono w-full" autoComplete="off" spellCheck={false} />
          <div className="relative">
            <input type={showSec ? "text" : "password"} value={s} onChange={(e) => setS(e.target.value)}
              placeholder="Secret"
              className="field font-mono w-full pr-14" autoComplete="off" spellCheck={false} />
            <button type="button" onClick={() => setShowSec((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] font-mono text-ink-mute hover:text-gold">
              {showSec ? "隐藏" : "显示"}
            </button>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={connect} disabled={busy || !k.trim() || !s.trim()} className="btn-gold">
            {busy ? <Spin /> : <I.Key size={12} />} 连接并验证
          </button>
        </div>
        {err && <div className="rounded-md border border-red/30 bg-red/5 px-3 py-2 font-mono text-[11px] text-red leading-relaxed">{err}</div>}
        {ok && <div className="rounded-md border border-green/40 bg-green/5 px-3 py-2 font-mono text-[11px] text-green leading-relaxed">{ok}</div>}
        <div className="rounded-md border border-line bg-elevated/20 px-3 py-2 font-mono text-[10.5px] text-ink-mute leading-relaxed">
          BX- 密钥属于 Binance Web3 Wallet API（web3.binance.com），与交易所 HMAC Key 互不通用；用于查链上地址 / 持仓，不会执行链上操作以外的任何动作。
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="pill pill-green"><span className="dot dot-green live" /> 已连接</span>
        <code className="rounded border border-line bg-canvas px-2 py-0.5 font-mono text-[10.5px] text-gold">{masked}</code>
        <button onClick={disconnect} disabled={busy} className="ml-auto btn-ghost text-[11px] py-1 text-ink-mute hover:text-red">
          <I.X size={10} /> 断开
        </button>
      </div>

      <div className="grid gap-2">
        <div className="flex items-center gap-2">
          <input value={addr} onChange={(e) => setAddr(e.target.value)} placeholder="0x... / SoL... 链上地址"
            className="field font-mono w-full" spellCheck={false} />
          <button onClick={query} disabled={busy} className="btn-ghost text-[11px] py-1.5">
            {busy ? <Spin /> : <I.Refresh size={11} />} 查询持仓
          </button>
        </div>
        {agentAddrs && (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(agentAddrs?.addresses || {}).slice(0, 6).map(([chain, a]) => (
              <button key={chain} onClick={() => useAgentAddr(String(a))}
                className="font-mono text-[10.5px] px-2 py-1 rounded border border-line hover:border-gold text-ink-dim hover:text-gold">
                {chain.slice(0, 4)}: {String(a).slice(0, 6)}…{String(a).slice(-4)}
              </button>
            ))}
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          {CHAIN_OPTIONS.map((c) => (
            <button key={c.id} onClick={() => setChains((prev) => prev.includes(c.id) ? prev.filter((x) => x !== c.id) : [...prev, c.id])}
              className={`pill ${chains.includes(c.id) ? "pill-gold" : "pill-dim"}`}>
              {c.name}
            </button>
          ))}
        </div>
      </div>

      {err && <div className="rounded-md border border-red/30 bg-red/5 px-3 py-2 font-mono text-[11px] text-red leading-relaxed">{err}</div>}
      {ok && <div className="rounded-md border border-green/40 bg-green/5 px-3 py-2 font-mono text-[11px] text-green leading-relaxed">{ok}</div>}

      {bal && (
        <div className="glass">
          <div className="px-4 py-2 flex items-center gap-2 border-b border-line">
            <span className="prefix">链上持仓</span>
            <span className="pill pill-dim">{bal?.totalTokens ?? bal?.tokens?.length ?? 0} 项</span>
          </div>
          <pre className="px-4 py-2 font-mono text-[10.5px] text-ink-dim whitespace-pre-wrap break-all max-h-[360px] overflow-auto">
            {JSON.stringify(bal, null, 2)}
          </pre>
        </div>
      )}

      <div className="font-mono text-[10px] text-ink-mute">
        参考：<a className="text-gold hover:underline" href="https://web3.binance.com/zh-CN/dev-docs/products/wallet-api/error-codes" target="_blank" rel="noreferrer">Binance Wallet API 错误码</a>
        （若使用 CEX 现货 HMAC Key，可忽略此链接）
      </div>
    </div>
  );
}

/* ---------------- 小工具 ---------------- */

export function Spin({ className }: { className?: string }) {
  return (
    <span className={`inline-block w-3 h-3 rounded-full border-2 border-gold border-t-transparent animate-spin align-[-2px] ${className || ""}`} />
  );
}