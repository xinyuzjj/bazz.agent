import React, { useEffect, useState, useCallback } from "react";
import { useT } from "../i18n/i18n";
import { api } from "../api";
import { I } from "../components/icons";
import { AgentSigninCard, ChainWalletPanel } from "../components/WalletBits";

/* 钱包 Hub —— 两个相互独立的钱包体系：
 *   Tab「Agent 钱包」：Binance Agentic Wallet（baw MPC，Binance App 扫码登录）
 *      数据源：GET /api/wallet（cli/status/daily_caps/commands）
 *             POST /api/wallet/signin|verify|signout（扫码登录真实流程）
 *             POST /api/wallet/run（仅 baw 前缀命令）
 *   Tab「链上钱包」：Binance Web3 Wallet API（BX- Key）——链上地址 / 持仓查询
 *      数据源：GET /api/wallet/web3/status / POST connect|disconnect|balance
 *
 * 注意——交易所账户（HMAC API Key）已迁出钱包 Hub，迁移到顶导「Binance CEX」页：
 *      该页直接绑定本地 binance-cli profile，可真实执行现货 / 合约 / 闪兑（须 CONFIRM）。
 *      交易所密钥与 BX- 钱包密钥互不通用，请勿混填。
 */

type RunResult = { status: string; code?: string; stdout?: string; stderr?: string; returncode?: number; detail?: string } | null;

export function WalletView() {
  const t = useT();
  const [mode, setMode] = useState<"agent" | "chain">("agent");
  const [state, setState] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string>("");
  const [live, setLive] = useState<Record<string, RunResult>>({});
  const [copied, setCopied] = useState<string>("");

  const installed = !!state?.cli?.installed;
  const version = state?.cli?.version ?? null;
  const connected = !!state?.status?.connected;
  const npmOk = !!state?.npm_available;

  const load = async () => {
    try { const s = await api.wallet(true); setState(s); setErr(""); }
    catch (e: any) { setErr(e?.message ?? String(e)); }
  };
  useEffect(() => { load(); }, []);

  // 已连接时拉真实数据（balance/address/chains/left-quota 全部 --json）
  const liveLoad = useCallback(async (names?: string[]) => {
    if (!connected) return;
    const cmds = names && names.length
      ? names
      : ["balance", "address", "chains", "left-quota"];
    setBusy("live");
    try {
      const entries = await Promise.all(cmds.map(async (name) => {
        const r = await api.walletRun(`baw wallet ${name} --json`).catch((e: any) => ({ status: "error", detail: String(e?.message ?? e) }));
        return [name, r] as const;
      }));
      setLive((prev) => ({ ...prev, ...Object.fromEntries(entries) }));
    } finally { setBusy(null); }
  }, [connected]);
  useEffect(() => { liveLoad(); }, [connected, liveLoad]);

  const refreshAll = async () => { setBusy("refresh"); try { await load(); await liveLoad(); } finally { setBusy(null); } };
  const install = async () => {
    setBusy("install");
    try { const r: any = await api.walletInstall(); setErr(r?.detail ?? (r?.status === "ok" ? "" : "安装失败")); await load(); }
    catch (e: any) { setErr(e?.message ?? String(e)); } finally { setBusy(null); }
  };
  const signout = async () => {
    setBusy("signout");
    try { await api.walletSignout(); setLive({}); } catch (e: any) { setErr(e?.message ?? String(e)); }
    finally { setBusy(null); await load(); }
  };
  const copy = async (key: string, text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(key); setTimeout(() => setCopied(""), 1200); } catch {}
  };

  const parse = (name: string) => {
    const r = live[name];
    if (!r) return null;
    if (r.status !== "ok") return { error: true, text: r.detail || r.stderr || r.stdout || "命令失败" };
    const raw = (r.stdout ?? "").trim();
    if (!raw) return { error: true, text: "（空输出）" };
    try { const j = JSON.parse(raw); return { error: false, json: j, text: raw }; }
    catch { return { error: false, json: null, text: raw }; }
  };

  const TabBtn = ({ id, label, icon }: { id: "agent" | "chain"; label: React.ReactNode; icon: React.ReactNode }) => (
    <button onClick={() => setMode(id)}
      className={`flex items-center gap-1.5 px-3.5 py-2 font-mono text-[12px] tracking-wide rounded-t-lg border border-b-0 transition-colors ${mode === id
        ? "text-gold border-line bg-card/70 shadow-[0_-1px_0_0_rgba(212,175,55,0.25)]"
        : "text-ink-mute border-transparent hover:text-ink hover:bg-elevated/40"}`}>
      {icon}{label}
    </button>
  );

  return (
    <div className="p-5 grid grid-cols-1 xl:grid-cols-[1fr_340px] gap-5 items-start">
      {/* 主列：所有现有面板 */}
      <div className="space-y-4 min-w-0">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2.5">
          <I.Hex className="text-gold" size={24} />
          <span className="font-mono text-[15px] font-bold tracking-wide text-ink">钱包</span>
          <span className="prefix">{mode === "agent" ? "Agentic Wallet · baw MPC"
            : "链上钱包 · Binance Web3 Wallet API (BX-)"}</span>
        </div>
        {mode === "agent" && (
          <div className="ml-auto flex items-center gap-2">
            <button onClick={refreshAll} disabled={!!busy} className="btn-ghost py-1.5 px-3 text-[12px]">
              <I.Refresh size={12} className={busy ? "animate-spin" : ""} /> {busy ? "刷新中…" : "刷新"}
            </button>
            {connected && (
              <button onClick={signout} disabled={!!busy} className="btn-ghost py-1.5 px-3 text-[12px] text-ink-mute hover:text-red" title="退出登录并清除会话">
                <I.X size={11} /> 登出
              </button>
            )}
            {state && (
              <span className={`pill ${connected ? "pill-green" : "pill-red"}`}>
                <span className={`dot ${connected ? "dot-green live" : "dot-red"}`} />
                {connected ? "LIVE · 已登录" : "未登录"}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Tab 切换 */}
      <div className="flex items-end gap-1 border-b border-line/60">
        <TabBtn id="agent" icon={<I.Hex size={12} className="text-gold" />} label={<>Agent 钱包<sup className="text-[8.5px] text-gold ml-1">MPC</sup></>} />
        <TabBtn id="chain" icon={<I.Cex size={12} className="text-gold" />} label={<>链上钱包<sup className="text-[8.5px] text-gold ml-1">BX</sup></>} />
      </div>

      {/* ============ Agent 钱包 Tab ============ */}
      {mode === "agent" && (
        <>
          {err && (
            <div className="rounded-lg border border-red/40 bg-red/5 px-4 py-2.5 text-[13px] text-red font-mono">{err}</div>
          )}

          {!state ? (
            <div className="glass p-6 space-y-2" style={{ borderRadius: 12 }}>
              {Array.from({ length: 3 }).map((_, i) => <div key={i} className="shimmer h-10" />)}
            </div>
          ) : (
            <>
              {/* 状态总览 —— 全部真实 */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <StatusTile label="CLI 安装" ok={installed} text={installed ? `baw v${version ?? "?"}` : "未安装"} />
                <StatusTile label="Node / npm" ok={npmOk} text={npmOk ? "npm 可用" : "npm 缺失"} />
                <StatusTile label="登录状态" ok={connected} text={connected ? "已登录" : "未登录"} warn={!connected && installed} />
                <StatusTile label="密钥方式" ok text="MPC · 无本地私钥" sub="Binance App 扫码" />
              </div>

              {state?.status?.detail && !connected && (
                <div className="rounded-lg border border-line bg-elevated/30 px-4 py-2.5 font-mono text-[11.5px] text-ink-dim">
                  {state.status.detail}
                </div>
              )}

              {/* 安装 / 登录引导 —— 按真实状态分支 */}
              {!installed ? (
                <div className="glass p-5" style={{ borderRadius: 12 }}>
                  <div className="font-mono text-[13px] text-ink mb-1">需要安装 Agentic Wallet CLI</div>
                  <p className="text-[12.5px] text-ink-dim leading-relaxed mb-3">MPC 无密钥钱包（Binance Agentic Wallet）。要求 Node ≥ 18。</p>
                  <div className="flex items-center gap-3 flex-wrap">
                    <code className="rounded-md border border-line bg-canvas px-3 py-2 font-mono text-[12px] text-gold">{state.install_cmd ?? "npm i -g @binance/agentic-wallet"}</code>
                    <button onClick={() => copy("install", state.install_cmd ?? "")} className="btn-ghost text-[12px] py-1.5">{copied === "install" ? "已复制" : "复制命令"}</button>
                    <button onClick={install} disabled={!!busy} className="btn-gold"><I.Download size={12} /> 一键安装</button>
                  </div>
                </div>
              ) : !connected ? (
                /* 未登录：内嵌真实扫码登录（QR + pairingCode + 轮询 verify） */
                <div className="glass p-5" style={{ borderRadius: 12 }}>
                  <div className="flex items-center gap-2 mb-3">
                    <I.Qr size={15} className="text-gold" />
                    <span className="font-mono text-[13px] text-ink">用 Binance App 扫码登录</span>
                    <span className="pill pill-dim ml-1">MPC · 官方流程</span>
                  </div>
                  <p className="text-[12.5px] text-ink-dim leading-relaxed mb-3">
                    点下方按钮生成二维码，用<b className="text-gold"> Binance App</b> 扫码并在 App 内确认配对码，即完成登录（无需任何私钥）。
                    首次登录会顺带完成 Agentic Wallet 创建流程。
                  </p>
                  <AgentSigninCard onDone={refreshAll} />
                </div>
              ) : (
                /* 已连接：真实数据区 */
                <>
                  {/* 头部状态 */}
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="prefix">真实数据 · 来自 baw wallet --json</span>
                    <span className="pill pill-green"><span className="dot dot-green live" /> CONNECTED</span>
                    {busy === "live" && <span className="pill pill-dim">同步中…</span>}
                    <button onClick={refreshAll} className="ml-auto btn-ghost py-1 px-2.5 text-[11.5px]" disabled={!!busy}>
                      <I.Refresh size={11} className={busy === "live" ? "animate-spin" : ""} /> 一键刷新全部
                    </button>
                  </div>

                  {/* 多链地址总览（合并 + 美化） */}
                  <div className="glass p-4" style={{ borderRadius: 12 }}>
                    <div className="flex items-center gap-2 mb-3">
                      <I.Wallet size={13} className="text-gold" />
                      <span className="font-mono text-[12px] tracking-wider text-ink">多链地址总览</span>
                      <span className="pill pill-dim text-[10px]">MPC · 同一身份多链地址</span>
                      <button onClick={() => liveLoad(["address", "chains"])} className="ml-auto text-[11px] font-mono text-gold hover:underline">重新获取</button>
                    </div>
                    <MultiChainGrid addressResult={parse("address")} chainsResult={parse("chains")} onCopy={copy} copied={copied} />
                  </div>

                  {/* 余额 + 额度 */}
                  <div className="grid grid-cols-12 gap-4">
                    <div className="col-span-12 lg:col-span-7 glass p-4" style={{ borderRadius: 12 }}>
                      <PanelHead icon={<I.Hex size={13} className="text-gold" />} title="代币余额" extra={
                        <button onClick={() => liveLoad(["balance"])} className="text-[11px] font-mono text-gold hover:underline">重新获取</button>
                      } />
                      <JsonView result={parse("balance")} mode="table" onCopy={copy} copied={copied} copyKey="balance" />
                    </div>
                    <div className="col-span-12 lg:col-span-5 glass p-4" style={{ borderRadius: 12 }}>
                      <PanelHead icon={<I.Lock size={13} className="text-gold" />} title="每日剩余额度 (Quota)" extra={
                        <button onClick={() => liveLoad(["left-quota"])} className="text-[11px] font-mono text-gold hover:underline">重新获取</button>
                      } />
                      <JsonView result={parse("left-quota")} mode="kv" onCopy={copy} copied={copied} copyKey="quota" />
                    </div>
                  </div>

                  {/* 新增：历史交易 / 预测 PnL */}
                  <div className="grid grid-cols-12 gap-4">
                    <LivePanel
                      className="col-span-12 lg:col-span-7" title="历史交易" icon={<I.Arrow size={13} className="text-gold" />}
                      cmd="baw wallet tx-history --json"
                      live={live} setLive={setLive} copy={copy} copied={copied}
                      busy={busy} setBusy={setBusy}
                      render={(res) => <TxHistoryView result={res} onCopy={copy} copied={copied} />}
                      emptyHint="暂无交易（活动期内若操作了 bStock，tx-history 应有记录；空属正常）。"
                    />
                    <LivePanel
                      className="col-span-12 lg:col-span-5" title="预测 PnL（Prediction）" icon={<I.Bolt size={13} className="text-gold" />}
                      cmd="baw prediction position pnl --json"
                      live={live} setLive={setLive} copy={copy} copied={copied}
                      busy={busy} setBusy={setBusy}
                      render={(res) => <PnlView result={res} onCopy={copy} copied={copied} />}
                      emptyHint="暂无 PnL 记录。"
                    />
                  </div>

                  {/* 新增：DeFi 协议 / 机会 */}
                  <div className="grid grid-cols-12 gap-4">
                    <LivePanel
                      className="col-span-12 lg:col-span-7" title="DeFi 协议排行（TVL / APY）" icon={<I.Grid size={13} className="text-gold" />}
                      cmd="baw defi protocol-list --json"
                      live={live} setLive={setLive} copy={copy} copied={copied}
                      busy={busy} setBusy={setBusy}
                      render={(res) => <DefiProtocolView result={res} />}
                      emptyHint="暂无 DeFi 协议数据（接口或网络不可用时会如实显示错误）。"
                    />
                    <DefiOppPanel />
                  </div>

                  {/* 新增：我的 DeFi 持仓 */}
                  <LivePanel
                    className="" title="我的 DeFi 持仓（健康因子 / LP / 质押）" icon={<I.Shield size={13} className="text-gold" />}
                    cmd="baw defi position --json"
                    live={live} setLive={setLive} copy={copy} copied={copied}
                    busy={busy} setBusy={setBusy}
                    render={(res) => <DefiPositionView result={res} />}
                    emptyHint="暂无 DeFi 持仓。"
                  />

                  {/* 新增：Campaign / bStock 大赛 */}
                  <CampaignPanel />
                </>
              )}
            </>
          )}

          {/* 每日限额策略（后端真实配置 DAILY_CAPS） */}
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <PanelHead icon={<I.Shield size={13} className="text-gold" />} title="Agent 每日限额策略 (Binance 设定)" extra={<span className="pill pill-dim">只读配置</span>} />
            <div className="grid grid-cols-3 gap-3 mt-3">
              {Object.entries(state?.daily_caps ?? { swap: "$50,000", defi: "$100,000", x402: "$20" }).map(([k, v]) => (
                <div key={k} className="rounded-lg border border-line bg-elevated/30 p-3">
                  <div className="prefix">{k.toUpperCase()}</div>
                  <div className="mt-1 font-mono tabular text-[20px] font-bold text-gold">{v as string}</div>
                </div>
              ))}
            </div>
          </div>

          {/* 官方命令参考（后端真实 COMMAND_TREE） */}
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <details>
              <summary className="cursor-pointer flex items-center gap-2 list-none">
                <I.Cpu size={13} className="text-gold" />
                <span className="prefix">baw CLI 命令参考（官方）</span>
                <span className="pill pill-dim ml-2">{state?.commands?.length ?? 0} 条</span>
                <span className="ml-auto text-gold font-mono text-[12px]">▾ 展开</span>
              </summary>
              <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2">
                {(state?.commands ?? []).map((c: any, i: number) => (
                  <div key={i} className="rounded-md border border-line bg-elevated/30 px-3 py-2">
                    <div className="flex items-center justify-between">
                      <code className="font-mono text-[12px] text-gold">{c.cmd}</code>
                      <span className="pill pill-dim text-[9.5px]">{c.group}</span>
                    </div>
                    <div className="font-mono text-[10.5px] text-ink-mute mt-1">{c.desc}</div>
                  </div>
                ))}
              </div>
            </details>
          </div>

          <div className="font-mono text-[10.5px] text-ink-mute flex items-center justify-between px-1">
            <span>数据源: baw CLI 真实输出 · 沙箱网络受限时命令会如实显示超时/错误，不伪造资产</span>
            <span>{version ? `baw v${version}` : ""}</span>
          </div>
        </>
      )}

      {/* ============ 链上钱包（BX- Web3 Wallet 网关）Tab ============ */}
      {mode === "chain" && (
        <>
          {err && (
            <div className="rounded-lg border border-red/40 bg-red/5 px-4 py-2.5 text-[13px] text-red font-mono">{err}</div>
          )}
          <ChainWalletPanel />
          <WalletSkillsGrid />
          <div className="font-mono text-[10.5px] text-ink-mute px-1">
            链上钱包使用 Binance Web3 Wallet API（BX- Key）查链上地址 / 持仓；如需查看 / 下单 <b className="text-ink-dim">币安交易所账户</b>，请到顶导的「Binance CEX」页面填交易所 HMAC Key（两套密钥不通用）。
          </div>
        </>
      )}
      </div>{/* /主列 */}

      {/* 侧栏：行情快照 / 限额 / Skills 快捷（仅 Agent 钱包模式有意义） */}
      {mode === "agent" && <WalletSidebar />}
    </div>
  );
}

/* ---------- 展示组件 ---------- */

function StatusTile({ label, ok, text, sub, warn }: { label: string; ok: boolean; text: string; sub?: string; warn?: boolean }) {
  return (
    <div className="glass p-3.5" style={{ borderRadius: 10 }}>
      <div className="prefix">{label}</div>
      <div className={`mt-1.5 flex items-center gap-2`}>
        <span className={`dot ${warn && !ok ? "dot-red" : ok ? "dot-green" : "dot-red"} ${ok ? "live" : ""}`} />
        <span className={`font-mono tabular text-[15px] font-bold ${warn && !ok ? "text-red" : ok ? "text-ink" : "text-red"}`}>{text}</span>
      </div>
      {sub && <div className="font-mono text-[10px] text-ink-mute mt-1">{sub}</div>}
    </div>
  );
}

function PanelHead({ icon, title, extra }: { icon: React.ReactNode; title: string; extra?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      {icon}
      <span className="font-mono text-[12px] tracking-wider text-ink">{title}</span>
      <div className="ml-auto">{extra}</div>
    </div>
  );
}

/* 通用 JSON 展示：kv / list / table，容错展示 baw 真实返回 */
function JsonView({ result, mode, onCopy, copied, copyKey }: {
  result: any;
  mode: "kv" | "list" | "table";
  onCopy: (k: string, v: string) => void;
  copied: string;
  copyKey: string;
}) {
  if (!result) return <div className="py-6 text-center font-mono text-[12px] text-ink-mute">未获取，点击上方「重新获取」或等连接后自动同步。</div>;
  if (result.error) {
    return (
      <div className="rounded-md border border-red/30 bg-red/5 p-3 font-mono text-[11px] text-red leading-relaxed break-all">
        {result.text}
      </div>
    );
  }
  const j = result.json;

  // 定位到核心数据容器
  const unwrap = (x: any): any => {
    if (!x || typeof x !== "object") return x;
    if (Array.isArray(x)) return x;
    const keys = Object.keys(x);
    // {success:true, data: ...} → data
    if ("data" in x) return unwrap(x.data);
    if (keys.length === 1 && typeof x[keys[0]] === "object") return x[keys[0]];
    return x;
  };
  const data = unwrap(j);
  const pretty = (v: unknown) => typeof v === "string" ? v : JSON.stringify(v, null, 2);

  if (mode === "list") {
    if (Array.isArray(data)) {
      return (
        <div className="flex flex-wrap gap-2">
          {data.map((c, i) => (
            <span key={i} className="rounded-md border border-line bg-elevated/40 px-3 py-1.5 font-mono text-[12px] text-ink">
              {typeof c === "object" ? (c.chain ?? c.name ?? c.id ?? JSON.stringify(c)) : String(c)}
            </span>
          ))}
        </div>
      );
    }
    return <RawBox text={pretty(data)} />;
  }

  if (mode === "kv" && data && !Array.isArray(data) && typeof data === "object") {
    return (
      <div className="space-y-2">
        {Object.entries(data).map(([k, v]) => {
          const val = typeof v === "object" ? JSON.stringify(v) : String(v);
          return (
            <div key={k} className="rounded-md border border-line bg-elevated/30 px-3 py-2 flex items-start gap-2">
              <span className="font-mono text-[11px] text-gold w-32 shrink-0 break-all">{k}</span>
              <span className="font-mono text-[12px] text-ink break-all flex-1">{val}</span>
              <button onClick={() => onCopy(`${copyKey}:${k}`, val)}
                className="text-[10px] font-mono text-ink-dim hover:text-gold shrink-0 pt-0.5">
                {copied === `${copyKey}:${k}` ? "已复制 ✓" : "复制"}
              </button>
            </div>
          );
        })}
      </div>
    );
  }

  // table 模式：尽量识别行数组
  if (mode === "table" && Array.isArray(data)) {
    const rows = data as Record<string, any>[];
    if (rows.length && rows.every((r) => r && typeof r === "object")) {
      const cols = Array.from(new Set(rows.flatMap((r) => Object.keys(r)))).filter((c) => !["network", "chain_id"].includes(c));
      return (
        <div className="overflow-x-auto">
          <div className="min-w-[480px]">
            <div className="grid font-mono text-[10px] tracking-wider text-ink-mute px-2 py-1.5 border-b border-line"
              style={{ gridTemplateColumns: `repeat(${Math.min(cols.length, 5)}, 1fr)` }}>
              {cols.slice(0, 5).map((c) => <div key={c}>{c.toUpperCase()}</div>)}
            </div>
            {rows.map((r, i) => (
              <div key={i} className="grid font-mono text-[12px] text-ink px-2 py-2 border-b border-line/50 items-center"
                style={{ gridTemplateColumns: `repeat(${Math.min(cols.length, 5)}, 1fr)` }}>
                {cols.slice(0, 5).map((c) => (
                  <div key={c} className="truncate pr-1">{typeof r[c] === "object" ? JSON.stringify(r[c]) : String(r[c] ?? "—")}</div>
                ))}
              </div>
            ))}
            {rows.length === 0 && <div className="text-center py-4 font-mono text-[12px] text-ink-mute">账户为空（无代币余额）</div>}
          </div>
        </div>
      );
    }
    if (rows.length === 0) return <div className="text-center py-4 font-mono text-[12px] text-ink-mute">余额为空</div>;
  }

  return <RawBox text={pretty(data)} />;
}

function RawBox({ text }: { text: string }) {
  return (
    <pre className="rounded-md border border-line bg-canvas p-3 font-mono text-[11px] text-ink-dim overflow-auto max-h-56 whitespace-pre-wrap break-all">
      {text}
    </pre>
  );
}

/* ============ Agent 钱包 Tab · 新增面板组件 ============ */

/* 把链名映射到品牌色与浏览器链接（覆盖 baw chains 列表里常见链） */
const CHAIN_VISUAL: Record<string, { color: string; bg: string; explorer: (a: string) => string; native: string }> = {
  "Solana":         { color: "#9945FF", bg: "rgba(153,69,255,0.12)", explorer: (a) => `https://solscan.io/account/${a}`, native: "SOL" },
  "Ethereum":       { color: "#627EEA", bg: "rgba(98,126,234,0.12)", explorer: (a) => `https://etherscan.io/address/${a}`, native: "ETH" },
  "BNB Smart Chain":{ color: "#F0B90B", bg: "rgba(240,185,11,0.12)", explorer: (a) => `https://bscscan.com/address/${a}`, native: "BNB" },
  "Base":           { color: "#0052FF", bg: "rgba(0,82,255,0.12)",   explorer: (a) => `https://basescan.org/address/${a}`, native: "ETH" },
  "Polygon":        { color: "#8247E5", bg: "rgba(130,71,229,0.12)", explorer: (a) => `https://polygonscan.com/address/${a}`, native: "POL" },
  "Arbitrum One":   { color: "#28A0F0", bg: "rgba(40,160,240,0.12)", explorer: (a) => `https://arbiscan.io/address/${a}`, native: "ETH" },
  "Robinhood":      { color: "#1FB55A", bg: "rgba(31,181,90,0.12)",  explorer: () => "#", native: "RON" },
};
const CHAIN_FALLBACK = { color: "#9aa4b2", bg: "rgba(154,164,178,0.10)", explorer: () => "#", native: "—" };

/* 多链地址总览：合并 address + chains，渲染每条链一个精美卡片行 */
function MultiChainGrid({ addressResult, chainsResult, onCopy, copied }: {
  addressResult: any; chainsResult: any;
  onCopy: (k: string, v: string) => void; copied: string;
}) {
  // 解析 address：可能是 [{binanceChainId, chainName, address}, ...] 或 {data: [...]}
  const unwrap = (x: any): any => {
    if (!x || typeof x !== "object") return x;
    if (Array.isArray(x)) return x;
    if ("data" in x) return unwrap(x.data);
    if (Object.keys(x).length === 1 && typeof x[Object.keys(x)[0]] === "object") return x[Object.keys(x)[0]];
    return x;
  };
  const addrs: Array<{ name: string; address: string }> = [];
  if (addressResult && !addressResult.error) {
    const d = unwrap(addressResult.json);
    if (Array.isArray(d)) {
      d.forEach((r: any) => {
        if (r && (r.address || r.addr)) {
          addrs.push({ name: r.chainName ?? r.name ?? r.chain ?? "Unknown", address: r.address ?? r.addr });
        }
      });
    } else if (d && typeof d === "object") {
      Object.entries(d).forEach(([k, v]) => {
        if (typeof v === "string") addrs.push({ name: k, address: v });
      });
    }
  }
  const chainsList: string[] = [];
  if (chainsResult && !chainsResult.error) {
    const d = unwrap(chainsResult.json);
    if (Array.isArray(d)) d.forEach((c: any) => typeof c === "string" ? chainsList.push(c) : chainsList.push(c.chain ?? c.name ?? ""));
  }
  const merged = addrs.length > 0
    ? addrs
    : chainsList.map((c) => ({ name: c, address: "" }));

  if (addrs.length === 0 && (chainsList.length === 0 || addressResult === null)) {
    return <div className="py-6 text-center font-mono text-[12px] text-ink-mute">未获取，点击「重新获取」或等连接后自动同步。</div>;
  }
  if (addressResult?.error || chainsResult?.error) {
    const err = (addressResult?.error ? addressResult.text : "") || (chainsResult?.error ? chainsResult.text : "");
    return <div className="rounded-md border border-red/30 bg-red/5 p-3 font-mono text-[11px] text-red break-all">{err}</div>;
  }

  const short = (a: string) => a.length > 14 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5">
      {merged.map((row, i) => {
        const v = CHAIN_VISUAL[row.name] ?? CHAIN_FALLBACK;
        const noAddr = !row.address;
        return (
          <div key={i} className="group rounded-lg border border-line bg-elevated/30 hover:border-gold/40 transition-colors p-3 flex items-start gap-2.5">
            <div className="w-9 h-9 rounded-md flex items-center justify-center shrink-0 font-mono text-[11px] font-bold"
              style={{ background: v.bg, color: v.color, border: `1px solid ${v.color}55` }}>
              {row.name.slice(0, 3).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-[12.5px] font-semibold text-ink truncate">{row.name}</span>
                <span className="font-mono text-[9px] text-ink-mute">· {v.native}</span>
              </div>
              {noAddr ? (
                <div className="font-mono text-[11px] text-ink-mute mt-0.5">（该链未分配地址）</div>
              ) : (
                <div className="flex items-center gap-1.5 mt-0.5">
                  <code className="font-mono text-[11.5px] text-ink-dim tabular truncate" title={row.address}>{short(row.address)}</code>
                  <button onClick={() => onCopy(`mc:${row.name}`, row.address)} className="text-ink-dim hover:text-gold shrink-0" title="复制完整地址">
                    {copied === `mc:${row.name}` ? <I.Check size={11} className="text-green" /> : <I.Copy size={11} />}
                  </button>
                  {v.explorer(row.address) !== "#" && (
                    <a href={v.explorer(row.address)} target="_blank" rel="noreferrer" className="text-ink-dim hover:text-gold shrink-0" title="在区块浏览器查看">
                      <I.Link size={11} />
                    </a>
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* 通用「带独立刷新按钮 + 实时数据」面板 */
function LivePanel({ className, title, icon, cmd, live, setLive, copy, copied, busy, setBusy, render, emptyHint }: {
  className?: string; title: string; icon: React.ReactNode;
  cmd: string;
  live: Record<string, RunResult>; setLive: React.Dispatch<React.SetStateAction<Record<string, RunResult>>>;
  copy: (k: string, v: string) => void; copied: string;
  busy: string | null; setBusy: React.Dispatch<React.SetStateAction<string | null>>;
  render: (res: any) => React.ReactNode; emptyHint: string;
}) {
  const key = cmd.replace(/\s*--json\s*$/i, "").replace(/\s+/g, ":");
  const r = live[key];
  const refreshing = busy === key;
  const refresh = async () => {
    setBusy(key);
    try {
      const x: any = await api.walletRun(cmd).catch((e: any) => ({ status: "error", detail: String(e?.message ?? e) }));
      setLive((prev) => ({ ...prev, [key]: x }));
    } finally { setBusy(null); }
  };
  useEffect(() => { if (!r) refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [cmd]);

  // 通用 parse
  const parsed = r ? (
    r.status !== "ok"
      ? { error: true, text: r.detail || r.stderr || r.stdout || "命令失败" }
      : (() => {
        const raw = (r.stdout ?? "").trim();
        if (!raw) return { error: true, text: "（空输出）" };
        try { return { error: false, json: JSON.parse(raw), text: raw }; }
        catch { return { error: false, json: null, text: raw }; }
      })()
  ) : null;

  return (
    <div className={`glass p-4 ${className ?? ""}`} style={{ borderRadius: 12 }}>
      <div className="flex items-center gap-2 mb-3">
        {icon}
        <span className="font-mono text-[12px] tracking-wider text-ink">{title}</span>
        <code className="font-mono text-[10px] text-ink-mute truncate ml-1" title={cmd}>{cmd}</code>
        <button onClick={refresh} disabled={!!refreshing} className="ml-auto text-[11px] font-mono text-gold hover:underline disabled:opacity-50">
          <I.Refresh size={11} className={`inline ${refreshing ? "animate-spin" : ""}`} /> {refreshing ? "刷新中…" : "刷新"}
        </button>
      </div>
      {!parsed ? (
        <div className="p-2 space-y-1.5">{Array.from({ length: 4 }).map((_, i) => <div key={i} className="shimmer h-6" />)}</div>
      ) : parsed.error ? (
        <div className="rounded-md border border-red/30 bg-red/5 p-3 font-mono text-[11px] text-red break-all">{parsed.text}</div>
      ) : (
        <div>{render(parsed)}</div>
      )}
      {parsed && !parsed.error && (() => {
        // 检测空数组并提示
        const d = (() => {
          const x = (parsed as any).json; if (!x) return null;
          if (Array.isArray(x)) return x;
          if (x && typeof x === "object" && "data" in x && Array.isArray((x as any).data)) return (x as any).data;
          return null;
        })();
        if (d && d.length === 0) return <div className="text-center py-3 font-mono text-[11px] text-ink-mute mt-2">{emptyHint}</div>;
        return null;
      })()}
    </div>
  );
}

/* ---- 表格视图：通用「列自适应」---- */
function KVTable({ rows, cols, keyOf, dense }: {
  rows: Record<string, any>[];
  cols: { key: string; label: string; min?: number; render?: (v: any, r: any, i: number) => React.ReactNode; cls?: string; align?: "left" | "right" }[];
  keyOf?: (r: any, i: number) => string;
  dense?: boolean;
}) {
  if (!rows.length) return null;
  const min = (c: typeof cols[number]) => `${c.min ?? (c.align === "right" ? 50 : 70)}px`;
  return (
    <div className="overflow-x-auto">
      <div className={dense ? "min-w-0" : "min-w-[480px]"}>
        <div className="grid font-mono text-[10px] tracking-wider text-ink-mute px-2 py-1.5 border-b border-line"
          style={{ gridTemplateColumns: cols.map((c) => `minmax(${min(c)},1fr)`).join(" ") }}>
          {cols.map((c) => <div key={c.key} className={c.align === "right" ? "text-right" : ""}>{c.label}</div>)}
        </div>
        {rows.map((r, i) => (
          <div key={keyOf?.(r, i) ?? i} className="grid font-mono text-[11.5px] text-ink px-2 py-2 border-b border-line/50 items-center hover:bg-elevated/40"
            style={{ gridTemplateColumns: cols.map((c) => `minmax(${min(c)},1fr)`).join(" ") }}>
            {cols.map((c) => (
              <div key={c.key} className={`pr-2 ${c.align === "right" ? "text-right" : "truncate"} ${c.cls ?? ""}`}>
                {c.render ? c.render(r[c.key], r, i) : (typeof r[c.key] === "object" ? JSON.stringify(r[c.key]) : String(r[c.key] ?? "—"))}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/* 历史交易 */
function TxHistoryView({ result, onCopy, copied }: { result: any; onCopy: (k: string, v: string) => void; copied: string }) {
  const unwrap = (x: any): any => {
    if (!x || typeof x !== "object") return null;
    if (Array.isArray(x)) return x;
    if ("data" in x) return unwrap(x.data);
    if (Object.keys(x).length === 1) return unwrap(x[Object.keys(x)[0]]);
    return null;
  };
  const arr = unwrap(result.json);
  if (!arr || !Array.isArray(arr) || arr.length === 0) return null;
  return (
    <KVTable
      rows={arr}
      cols={[
        { key: "txHash", label: "TX HASH", render: (v) => <code className="text-ink-dim">{v ? `${String(v).slice(0,6)}…${String(v).slice(-4)}` : "—"}</code> },
        { key: "type", label: "类型", render: (v) => <span className="pill pill-dim text-[9px]">{String(v ?? "—")}</span> },
        { key: "status", label: "状态", render: (v) => {
            const s = String(v ?? "").toLowerCase();
            const cls = s.includes("success") || s.includes("confirm") ? "text-green" : s.includes("fail") || s.includes("pend") ? "text-red" : "text-ink-dim";
            return <span className={`font-mono ${cls}`}>{String(v ?? "—")}</span>;
          } },
        { key: "amount", label: "金额", align: "right" },
        { key: "asset", label: "资产" },
        { key: "chain", label: "链" },
        { key: "time", label: "时间", render: (v) => <span className="text-ink-mute">{v ? new Date(Number(v) * 1000 || Date.parse(String(v)) || 0).toLocaleString() : "—"}</span> },
      ]}
      keyOf={(r, i) => r.txHash ?? `tx-${i}`}
    />
  );
}

/* 预测 PnL */
function PnlView({ result, onCopy, copied }: { result: any; onCopy: (k: string, v: string) => void; copied: string }) {
  const unwrap = (x: any): any => {
    if (!x || typeof x !== "object") return null;
    if (Array.isArray(x)) return x;
    if ("data" in x) return unwrap(x.data);
    return x;
  };
  const d = unwrap(result.json);
  if (!d) return <RawBox text={JSON.stringify(result.json, null, 2)} />;
  // 顶层 KV 视图（totalPnl / winRate / settled 等）
  if (!Array.isArray(d)) {
    const entries = Object.entries(d);
    return (
      <div className="space-y-2">
        {entries.map(([k, v]) => {
          const num = typeof v === "number" ? v : Number(v);
          const isMoney = /pnl|profit|loss|payout|usd|amount/i.test(k);
          const cls = isMoney && Number.isFinite(num) ? (num >= 0 ? "text-green" : "text-red") : "text-ink";
          const display = typeof v === "object" ? JSON.stringify(v) : String(v);
          return (
            <div key={k} className="rounded-md border border-line bg-elevated/30 px-3 py-2 flex items-center gap-2">
              <span className="font-mono text-[11px] text-gold w-32 shrink-0 break-all">{k}</span>
              <span className={`font-mono tabular text-[13px] ${cls} break-all flex-1`}>{display}</span>
              <button onClick={() => onCopy(`pnl:${k}`, display)} className="text-[10px] font-mono text-ink-dim hover:text-gold shrink-0">
                {copied === `pnl:${k}` ? "已复制 ✓" : "复制"}
              </button>
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <KVTable
      rows={d}
      cols={[
        { key: "market", label: "市场" },
        { key: "side", label: "方向" },
        { key: "shares", label: "份额", align: "right" },
        { key: "avgPrice", label: "均价", align: "right" },
        { key: "currentPrice", label: "现价", align: "right" },
        { key: "pnl", label: "PnL", align: "right", render: (v) => {
            const n = Number(v); if (!Number.isFinite(n)) return <span>{String(v)}</span>;
            return <span className={n >= 0 ? "text-green" : "text-red"}>{n >= 0 ? "+" : ""}{n.toFixed(2)}</span>;
          } },
      ]}
    />
  );
}

/* DeFi 协议排行 —— 数据 shape: {data:{total, list:[{defiProtocolId,protocolName,tvl,apy,apyDisplay,apyBps,investType:[],supportedChains:[],protocolLogo}]}} */
function DefiProtocolView({ result }: { result: any }) {
  const unwrap = (x: any): any => {
    if (!x || typeof x !== "object") return null;
    if (Array.isArray(x)) return x;
    if ("data" in x) return unwrap(x.data);
    if ("list" in x && Array.isArray((x as any).list)) return (x as any).list;
    if ("protocols" in x && Array.isArray((x as any).protocols)) return (x as any).protocols;
    return null;
  };
  const arr = unwrap(result.json);
  if (!arr || !Array.isArray(arr) || arr.length === 0) return <RawBox text={JSON.stringify(result.json, null, 2)} />;
  const numTvl = (v: any) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
  const fmtTvl = (n: number) => n >= 1e9 ? `$${(n/1e9).toFixed(2)}B` : n >= 1e6 ? `$${(n/1e6).toFixed(1)}M` : n >= 1e3 ? `$${(n/1e3).toFixed(1)}K` : `$${n.toFixed(0)}`;
  // 与 DefiOppView 一致的智能 APY 格式：<1000% 两位小数；≥1000% 紧凑 (1.5K% / 11.5K% / 123K%)
  const fmtApy = (r: any) => {
    let n: number | null = null;
    if (typeof r.apyBps === "number" && Number.isFinite(r.apyBps)) n = r.apyBps / 100;
    else if (typeof r.apy === "number" && Number.isFinite(r.apy)) n = r.apy;
    else if (typeof r.apyDisplay === "string" && r.apyDisplay) {
      const m = r.apyDisplay.match(/^([\d,]+(?:\.\d+)?)\s*%$/);
      if (m) {
        const raw = parseFloat(m[1].replace(/,/g, ""));
        if (Number.isFinite(raw)) n = raw;
        else return r.apyDisplay;
      } else return r.apyDisplay;
    } else return "—";
    if (n === null || !Number.isFinite(n)) return "—";
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M%`;
    if (abs >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K%`;
    return `${n.toFixed(2)}%`;
  };
  const apyNum = (r: any) => {
    if (typeof r.apyBps === "number" && Number.isFinite(r.apyBps)) return r.apyBps / 100;
    if (typeof r.apy === "number" && Number.isFinite(r.apy)) return r.apy;
    return 0;
  };
  const sorted = [...arr].sort((a, b) => numTvl(b.tvl) - numTvl(a.tvl));
  return (
    <KVTable
      rows={sorted.map((r: any, i: number) => ({ ...r, _rank: i + 1 }))}
      cols={[
        { key: "_rank", label: "#", align: "right", render: (_v, r) => <span className="text-ink-mute">{r._rank}</span> },
        { key: "protocolName", label: "协议", render: (v, r) => (
            <div className="flex items-center gap-1.5 min-w-0">
              {r.protocolLogo ? <img src={r.protocolLogo} alt="" className="w-4 h-4 rounded-sm shrink-0" onError={(e) => ((e.target as HTMLImageElement).style.display = "none")} /> : null}
              <span className="font-semibold text-ink truncate">{String(v ?? "—")}</span>
            </div>
          ) },
        { key: "tvl", label: "TVL", align: "right", render: (v) => <span className="tabular">{numTvl(v) > 0 ? fmtTvl(numTvl(v)) : "—"}</span>, cls: "text-ink-dim" },
        { key: "_apy", label: "APY", align: "right", render: (_v, r) => {
            const n = apyNum(r);
            const cls = n >= 50 ? "text-gold" : n >= 10 ? "text-green" : "text-ink-dim";
            return <span className={`tabular font-semibold ${cls}`}>{fmtApy(r)}</span>;
          } },
        { key: "investType", label: "类别", render: (v) => {
            const arr = Array.isArray(v) ? v : [v];
            return <div className="flex flex-wrap gap-1">{arr.filter(Boolean).map((t: string, i: number) => (
              <span key={i} className={`pill ${t === "Earn" ? "pill-green" : t === "LiquidityPool" ? "pill-gold" : "pill-dim"} text-[9px]`}>{t === "LiquidityPool" ? "LP" : t}</span>
            ))}</div>;
          } },
        { key: "supportedChains", label: "链", render: (v) => {
            const arr = Array.isArray(v) ? v : [];
            return <span className="text-ink-dim font-mono text-[10.5px]">{arr.join(", ") || "—"}</span>;
          } },
      ]}
    />
  );
}

/* DeFi 机会（带 investType 过滤芯片：Earn / LiquidityPool，自管理拉取）
 *   baw defi investment-list --investType Earn --json
 *   baw defi investment-list --investType LiquidityPool --json
 *   响应 shape: {data:{total, list:[{binanceChainId,protocolName,investmentName,investType,apyDisplay,apyBps,apy,tvl,apyType}]}}
 *   注：官方接口实际仅接受 Earn 与 LiquidityPool 两值；Loan 在 v1.9.0 不被支持（实测报 Invalid investType 100101）。
 */
const INVEST_TYPES: { key: "Earn" | "LiquidityPool"; label: string; cls: string }[] = [
  { key: "Earn",          label: "Earn",  cls: "pill-green" },
  { key: "LiquidityPool", label: "LP",    cls: "pill-gold" },
];

function DefiOppPanel() {
  const [t, setT] = useState<"Earn" | "LiquidityPool">("Earn");
  const [result, setResult] = useState<{ status: string; stdout?: string; stderr?: string; detail?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const fetchOne = async (it: "Earn" | "LiquidityPool") => {
    setBusy(true);
    try {
      const r: any = await api.walletRun(`baw defi investment-list --investType ${it} --json`).catch((e: any) => ({ status: "error", detail: String(e?.message ?? e) }));
      setResult(r);
    } finally { setBusy(false); }
  };
  useEffect(() => { fetchOne(t); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [t]);

  const parsed = result ? (
    result.status !== "ok"
      ? { error: true, text: result.detail || result.stderr || result.stdout || "命令失败" }
      : (() => {
          const raw = (result.stdout ?? "").trim();
          if (!raw) return { error: true, text: "（空输出）" };
          try { return { error: false, json: JSON.parse(raw), text: raw }; }
          catch { return { error: false, json: null, text: raw }; }
        })()
  ) : null;

  return (
    <div className="glass p-4 col-span-12 lg:col-span-5" style={{ borderRadius: 12 }}>
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <I.Star size={13} className="text-gold" />
        <span className="font-mono text-[12px] tracking-wider text-ink">DeFi 机会</span>
        <span className="pill pill-dim text-[10px]">Earn / LP / Loan</span>
        <code className="font-mono text-[10px] text-ink-mute truncate ml-1" title={`baw defi investment-list --investType ${t} --json`}>baw defi investment-list --investType {t} --json</code>
        <button onClick={() => fetchOne(t)} disabled={busy} className="ml-auto text-[11px] font-mono text-gold hover:underline disabled:opacity-50">
          <I.Refresh size={11} className={`inline ${busy ? "animate-spin" : ""}`} /> {busy ? "刷新中…" : "刷新"}
        </button>
      </div>
      {/* 过滤芯片 */}
      <div className="flex items-center gap-1.5 mb-3">
        {INVEST_TYPES.map((m) => (
          <button key={m.key} onClick={() => setT(m.key)}
            className={`px-3 py-1 rounded-md font-mono text-[11px] tracking-wider transition-colors border
              ${t === m.key ? `bg-elevated text-gold border-line` : `border-transparent text-ink-dim hover:text-ink`}`}>
            {m.label}
          </button>
        ))}
      </div>
      {!parsed ? (
        <div className="p-2 space-y-1.5">{Array.from({ length: 4 }).map((_, i) => <div key={i} className="shimmer h-6" />)}</div>
      ) : parsed.error ? (
        <div className="rounded-md border border-red/30 bg-red/5 p-3 font-mono text-[11px] text-red break-all">{parsed.text}</div>
      ) : (
        <DefiOppView result={parsed} investType={t} />
      )}
    </div>
  );
}

function DefiOppView({ result, investType }: { result: any; investType: "Earn" | "LiquidityPool" }) {
  const unwrap = (x: any): any => {
    if (!x || typeof x !== "object") return null;
    if (Array.isArray(x)) return x;
    if ("data" in x) return unwrap(x.data);
    if ("list" in x && Array.isArray((x as any).list)) return (x as any).list;
    if ("investments" in x && Array.isArray((x as any).investments)) return (x as any).investments;
    return null;
  };
  const arr = unwrap(result.json);
  if (!arr || !Array.isArray(arr) || arr.length === 0) return <RawBox text={JSON.stringify(result.json, null, 2)} />;
  const numTvl = (v: any) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
  const fmtTvl = (n: number) => n >= 1e6 ? `$${(n/1e6).toFixed(1)}M` : n >= 1e3 ? `$${(n/1e3).toFixed(1)}K` : `$${n.toFixed(0)}`;
  // 智能 APY 格式：<1000% 保留两位小数；≥1000% 自动转紧凑 (1.2K / 11.5K / 123K) 避免窄列截断
  const fmtApy = (r: any) => {
    let n: number | null = null;
    if (typeof r.apyBps === "number" && Number.isFinite(r.apyBps)) n = r.apyBps / 100;
    else if (typeof r.apy === "number" && Number.isFinite(r.apy)) n = r.apy;
    else if (typeof r.apyDisplay === "string" && r.apyDisplay) {
      // 官方格式如 "11,504.64%"，仅在 n 拿不到时用；但若 ≥1000 我们 重新紧凑化避免窄列溢出
      const m = r.apyDisplay.match(/^([\d,]+(?:\.\d+)?)\s*%$/);
      if (m) {
        const raw = parseFloat(m[1].replace(/,/g, ""));
        if (Number.isFinite(raw)) n = raw;
        else return r.apyDisplay;
      } else return r.apyDisplay;
    } else return "—";
    if (n === null || !Number.isFinite(n)) return "—";
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M%`;
    if (abs >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K%`;
    return `${n.toFixed(2)}%`;
  };
  const apyNum = (r: any) => {
    if (typeof r.apyBps === "number" && Number.isFinite(r.apyBps)) return r.apyBps / 100;
    if (typeof r.apy === "number" && Number.isFinite(r.apy)) return r.apy;
    if (typeof r.apyDisplay === "string" && r.apyDisplay) {
      const m = r.apyDisplay.match(/^([\d,]+(?:\.\d+)?)\s*%$/);
      if (m) return parseFloat(m[1].replace(/,/g, ""));
    }
    return 0;
  };
  const sorted = [...arr].sort((a, b) => apyNum(b) - apyNum(a));
  return (
    <KVTable
      dense
      rows={sorted.map((r: any, i: number) => ({ ...r, _idx: i + 1 }))}
      cols={[
        { key: "_idx", label: "#", min: 30, align: "right", render: (_v, r) => <span className="text-ink-mute text-[10.5px] tabular">{r._idx}</span> },
        { key: "protocolName", label: "协议", min: 120, render: (v) => <span className="text-ink" title={String(v ?? "")}>{String(v ?? "—")}</span> },
        { key: "investmentName", label: "标的 / 池", min: 105, render: (v) => <span className="text-ink-dim" title={String(v ?? "")}>{String(v ?? "—")}</span> },
        { key: "investType", label: "类型", min: 56, render: (v) => {
            const t = String(v ?? "");
            const meta = INVEST_TYPES.find((m) => m.key === t);
            return <span className={`pill ${meta?.cls ?? "pill-dim"} text-[9px]`}>{meta ? meta.label : t}</span>;
          } },
        { key: "_apy", label: "APY/APR", min: 65, align: "right", render: (_v, r) => {
            const n = apyNum(r);
            const cls = n >= 50 ? "text-gold" : n >= 10 ? "text-green" : "text-ink-dim";
            return <span className={`tabular font-semibold whitespace-nowrap ${cls}`} title={`${n.toFixed(2)}%`}>{fmtApy(r)}</span>;
          } },
        { key: "tvl", label: "TVL", min: 70, align: "right", render: (v) => <span className="tabular text-ink-dim whitespace-nowrap">{numTvl(v) > 0 ? fmtTvl(numTvl(v)) : "—"}</span> },
      ]}
    />
  );
}

/* 我的 DeFi 持仓（含健康因子）—— 数据 shape: {data:{deFiTotalValue,deFiProtocolVOList:[{protocolName,investmentName,amount,value,healthFactor,apyDisplay,...}]}} */
function DefiPositionView({ result }: { result: any }) {
  const unwrap = (x: any): any => {
    if (!x || typeof x !== "object") return null;
    if (Array.isArray(x)) return x;
    if ("data" in x) return unwrap(x.data);
    if ("positions" in x && Array.isArray((x as any).positions)) return (x as any).positions;
    if ("deFiProtocolVOList" in x && Array.isArray((x as any).deFiProtocolVOList)) return (x as any).deFiProtocolVOList;
    return x;
  };
  const d = unwrap(result.json);
  // 顶层对象：{deFiTotalValue, deFiProtocolVOList:[...]} 或旧 {totalValue, positions}
  if (d && !Array.isArray(d) && typeof d === "object") {
    const positions = Array.isArray((d as any).deFiProtocolVOList) ? (d as any).deFiProtocolVOList
      : Array.isArray((d as any).positions) ? (d as any).positions
      : null;
    const totalVal = (d as any).deFiTotalValue ?? (d as any).totalValue ?? (d as any).totalUsdValue;
    const health = (d as any).healthFactor ?? (d as any).health;
    const hasSummary = totalVal !== undefined || health !== undefined;
    return (
      <div className="space-y-3">
        {hasSummary && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
            {totalVal !== undefined && (
              <div className="rounded-md border border-line bg-elevated/30 px-3 py-2">
                <div className="prefix">DeFi 总价值 (USD)</div>
                <div className="font-mono tabular text-[16px] font-bold mt-1 text-gold">${Number(totalVal).toLocaleString("en-US", { maximumFractionDigits: 2 })}</div>
              </div>
            )}
            {health !== undefined && (() => {
              const n = Number(health);
              const cls = Number.isFinite(n) ? (n >= 1.5 ? "text-green" : n >= 1.0 ? "text-gold" : "text-red") : "text-ink";
              return (
                <div className="rounded-md border border-line bg-elevated/30 px-3 py-2">
                  <div className="prefix">健康因子</div>
                  <div className={`font-mono tabular text-[16px] font-bold mt-1 ${cls}`}>{String(health)}</div>
                </div>
              );
            })()}
          </div>
        )}
        {positions && positions.length > 0 ? (
          <KVTable
            rows={positions}
            cols={[
              { key: "protocolName", label: "协议" },
              { key: "investmentName", label: "标的" },
              { key: "amount", label: "数量", align: "right" },
              { key: "value", label: "价值 USD", align: "right", cls: "text-ink-dim" },
              { key: "apyDisplay", label: "APY", align: "right" },
              { key: "pnl", label: "PnL", align: "right", render: (v) => {
                  const n = Number(v); if (!Number.isFinite(n)) return <span>—</span>;
                  return <span className={n >= 0 ? "text-green" : "text-red"}>{n >= 0 ? "+" : ""}{n.toFixed(2)}</span>;
                } },
            ]}
          />
        ) : (
          <div className="text-center py-4 font-mono text-[11.5px] text-ink-mute">暂无 DeFi 持仓（账户为空或接口未返回明细）</div>
        )}
      </div>
    );
  }
  if (Array.isArray(d) && d.length > 0) {
    return (
      <KVTable
        rows={d}
        cols={[
          { key: "protocolName", label: "协议" },
          { key: "investmentName", label: "标的" },
          { key: "amount", label: "数量", align: "right" },
          { key: "value", label: "价值", align: "right" },
        ]}
      />
    );
  }
  return <RawBox text={JSON.stringify(result.json, null, 2)} />;
}

/* Campaign / bStock 大赛面板 —— 严格按 references/campaign.md 的过期开关 */
function CampaignPanel() {
  const t = useT();
  const [state, setState] = useState<any>(null);
  const [err, setErr] = useState("");
  const refresh = async () => {
    try { setState(await api.walletCampaign()); setErr(""); }
    catch (e: any) { setErr(String(e?.message ?? e)); }
  };
  useEffect(() => { refresh(); }, []);
  if (err) return (
    <div className="glass p-4" style={{ borderRadius: 12 }}>
      <PanelHead icon={<I.Megaphone size={13} className="text-gold" />} title="Campaign / bStock 大赛" />
      <div className="rounded-md border border-red/30 bg-red/5 p-3 font-mono text-[11px] text-red">{err}</div>
    </div>
  );
  if (!state) return (
    <div className="glass p-4 space-y-2" style={{ borderRadius: 12 }}>
      <PanelHead icon={<I.Megaphone size={13} className="text-gold" />} title="Campaign / bStock 大赛" />
      <div className="shimmer h-8" /><div className="shimmer h-8" />
    </div>
  );
  const active = state.active === true;
  return (
    <div className="glass p-4" style={{ borderRadius: 12 }}>
      <div className="flex items-center gap-2 mb-3">
        <I.Megaphone size={13} className={active ? "text-gold" : "text-ink-mute"} />
        <span className="font-mono text-[12px] tracking-wider text-ink">Campaign / bStock 大赛</span>
        <span className={`pill ${active ? "pill-gold" : "pill-dim"} text-[10px]`}>
          {active ? "● 活动进行中" : "已结束"}
        </span>
        <span className="pill pill-dim text-[10px]">{state.name}</span>
        <a href={state.official_page} target="_blank" rel="noreferrer" className="ml-auto text-[11px] font-mono text-gold hover:underline">官方规则 ↗</a>
      </div>

      {/* 时间窗 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 mb-3">
        <div className="rounded-md border border-line bg-elevated/30 px-3 py-2">
          <div className="prefix">开始 (UTC)</div>
          <div className="font-mono tabular text-[12px] text-ink mt-1">{new Date(state.starts_at).toLocaleString()}</div>
        </div>
        <div className="rounded-md border border-line bg-elevated/30 px-3 py-2">
          <div className="prefix">截止 (UTC)</div>
          <div className="font-mono tabular text-[12px] text-ink mt-1">{new Date(state.ends_at).toLocaleString()}</div>
        </div>
        <div className="rounded-md border border-line bg-elevated/30 px-3 py-2">
          <div className="prefix">服务器当前 (UTC)</div>
          <div className="font-mono tabular text-[12px] text-ink mt-1">{new Date(state.server_now).toLocaleString()}</div>
        </div>
        <div className={`rounded-md border px-3 py-2 ${active ? "border-gold/40 bg-gold/5" : "border-line bg-elevated/30"}`}>
          <div className="prefix">距截止</div>
          <div className={`font-mono tabular text-[16px] font-bold mt-1 ${active ? "text-gold" : "text-ink-mute"}`}>
            {active ? `${state.days_remaining} 天` : "— 已结束 —"}
          </div>
        </div>
      </div>

      {/* 规则摘要（活动期内用于参考，活动期外仅留档） */}
      <div className="space-y-1.5">
        <div className="prefix">规则摘要（来源 references/campaign.md / 官方页面）</div>
        <ul className="font-mono text-[11.5px] text-ink-dim leading-relaxed list-disc pl-5 space-y-1">
          {state.rules_summary.map((r: string, i: number) => <li key={i}>{r}</li>)}
        </ul>
        <div className="font-mono text-[10.5px] text-ink-mute pt-2 flex items-center justify-between">
          <span>规则文档：<code className="text-ink-dim">{state.rule_doc}</code></span>
          <span>{state.operator}</span>
        </div>
      </div>
    </div>
  );
}

/* ============ 右侧侧栏（Agent 钱包模式） ============ */

/* 7 个只读 Wallet Skills（来自 src/skills_client.WALLET_SKILLS） */
const WALLET_SKILL_META: { key: string; title: string; desc: string; icon: string }[] = [
  { key: "query-token-info",                  title: "代币详情",       desc: "按合约地址查代币信息（名称/精度/官网/社交）", icon: "ℹ" },
  { key: "query-token-audit",                 title: "代币安全审计",   desc: "蜜罐/高税/可增发/黑名单等风险扫描",         icon: "🛡" },
  { key: "query-address-info",                title: "地址持仓洞察",   desc: "代币/NFT 持仓 + 近期活跃度",                icon: "👤" },
  { key: "crypto-market-rank",                title: "市场排行",       desc: "涨跌幅/市值/资金费率异常榜",                icon: "📊" },
  { key: "meme-rush",                         title: "Meme 发射台",    desc: "追踪新发射的 meme 币热度",                  icon: "🚀" },
  { key: "trading-signal",                    title: "聪明钱信号",     desc: "逐笔聪明钱流入/流出告警",                    icon: "⚡" },
  { key: "binance-tokenized-securities-info", title: "代币化美股 RWA", desc: "bStock / Ondo / xStocks 美股代币行情",  icon: "🇺🇸" },
];

/* 侧栏：实时行情 + 限额进度 + Skills 快捷入口 */
function WalletSidebar() {
  const t = useT();
  const [ticks, setTicks] = useState<{ [sym: string]: { price: number; change_pct: number } }>({});
  const [updatedAt, setUpdatedAt] = useState<number>(0);
  const refresh = useCallback(async () => {
    try {
      const d: any = await api.market();
      const out: any = {};
      for (const sym of ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]) {
        const r = (d.all || []).find((x: any) => x.symbol === sym);
        if (r) out[sym] = { price: r.price, change_pct: r.change_pct };
      }
      setTicks(out);
      setUpdatedAt(d.updated_at ?? Math.floor(Date.now() / 1000));
    } catch {}
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  const fmtPrice = (n: number) => n >= 1000 ? n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : n.toFixed(4);
  const updated = updatedAt ? new Date(updatedAt * 1000).toLocaleTimeString() : "—";

  return (
    <div className="space-y-4 xl:sticky xl:top-4">
      {/* 实时行情快讯 */}
      <div className="glass p-4" style={{ borderRadius: 12 }}>
        <div className="flex items-center gap-2 mb-3">
          <I.Market size={13} className="text-gold" />
          <span className="font-mono text-[12px] tracking-wider text-ink">实时行情</span>
          <span className="pill pill-green text-[10px]"><span className="dot dot-green live" /> {t("wallet.live")}</span>
          <span className="ml-auto font-mono text-[10.5px] text-ink-mute tabular">{updated}</span>
        </div>
        <div className="space-y-2">
          {[
            { sym: "BTCUSDT", name: "BTC", color: "#F7931A" },
            { sym: "ETHUSDT", name: "ETH", color: "#627EEA" },
            { sym: "SOLUSDT", name: "SOL", color: "#9945FF" },
            { sym: "BNBUSDT", name: "BNB", color: "#F0B90B" },
          ].map((m) => {
            const t = ticks[m.sym];
            const up = (t?.change_pct ?? 0) >= 0;
            return (
              <div key={m.sym} className="flex items-center gap-2 rounded-md border border-line bg-elevated/30 px-3 py-2">
                <div className="w-7 h-7 rounded-md flex items-center justify-center font-mono text-[10px] font-bold shrink-0"
                  style={{ background: `${m.color}22`, color: m.color, border: `1px solid ${m.color}55` }}>{m.name}</div>
                <div className="flex-1 min-w-0">
                  <div className="font-mono text-[12.5px] text-ink tabular truncate">{t ? `$${fmtPrice(t.price)}` : "—"}</div>
                </div>
                <div className={`font-mono text-[11.5px] tabular ${t ? (up ? "up" : "down") : "text-ink-mute"}`}>
                  {t ? `${up ? "+" : ""}${t.change_pct.toFixed(2)}%` : "—"}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 每日限额（Binance 设定 · 只读） */}
      <div className="glass p-4" style={{ borderRadius: 12 }}>
        <div className="flex items-center gap-2 mb-3">
          <I.Lock size={13} className="text-gold" />
          <span className="font-mono text-[12px] tracking-wider text-ink">每日限额（Binance 设定）</span>
        </div>
        <div className="space-y-2.5">
          {[
            { k: "swap", label: "市价兑换", cap: 50000,  color: "var(--gold)",     icon: "↔" },
            { k: "defi", label: "DeFi 操作", cap: 100000, color: "var(--green)",   icon: "◎" },
            { k: "x402", label: "x402 支付", cap: 20,     color: "var(--ink-dim)", icon: "$" },
          ].map((c) => {
            return (
              <div key={c.k}>
                <div className="flex items-center justify-between font-mono text-[11px] mb-1">
                  <span className="text-ink-dim flex items-center gap-1.5">
                    <span className="text-gold font-bold">{c.icon}</span>
                    {c.label}
                  </span>
                  <span className="text-ink tabular">${c.cap.toLocaleString("en-US")}</span>
                </div>
                <div className="h-1.5 rounded-full bg-elevated overflow-hidden">
                  <div className="h-full rounded-full transition-all" style={{ width: `${Math.min(100, (0 / c.cap) * 100)}%`, background: c.color }} />
                </div>
              </div>
            );
          })}
          <div className="font-mono text-[10px] text-ink-mute pt-1">实时使用量需 baw wallet left-quota 返回后接入</div>
        </div>
      </div>

      {/* Wallet Skills 快捷入口 */}
      <div className="glass p-4" style={{ borderRadius: 12 }}>
        <div className="flex items-center gap-2 mb-3">
          <I.Bolt size={13} className="text-gold" />
          <span className="font-mono text-[12px] tracking-wider text-ink">Wallet Skills（7 个只读）</span>
          <span className="pill pill-dim text-[10px]">无需钱包</span>
        </div>
        <div className="grid grid-cols-1 gap-1.5">
          {WALLET_SKILL_META.map((s) => (
            <a key={s.key} href="#skills"
              className="group flex items-center gap-2.5 rounded-md border border-line bg-elevated/30 hover:border-gold/40 px-2.5 py-2 transition-colors">
              <span className="w-6 h-6 rounded-md bg-elevated flex items-center justify-center font-mono text-[11px] font-bold text-gold shrink-0">{s.icon}</span>
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[11.5px] text-ink truncate group-hover:text-gold">{s.title}</div>
                <div className="font-mono text-[10px] text-ink-mute truncate">{s.desc}</div>
              </div>
              <I.Link size={11} className="text-ink-mute group-hover:text-gold shrink-0" />
            </a>
          ))}
        </div>
        <div className="font-mono text-[10px] text-ink-mute mt-2.5">点击跳转技能库运行；Agent 钱包连接后还可跑 binance-agentic-wallet（转账/兑换）</div>
      </div>
    </div>
  );
}

/* 链上钱包 Tab：7 个 Wallet Skills 卡片网格 */
function WalletSkillsGrid() {
  const t = useT();
  const [installed, setInstalled] = useState<Record<string, boolean>>({});
  useEffect(() => {
    (async () => {
      try {
        const all: any[] = await api.skills();
        const m: Record<string, boolean> = {};
        for (const s of all) m[s.key] = !!s.installed;
        setInstalled(m);
      } catch {}
    })();
  }, []);
  const onInstall = async (key: string) => {
    try {
      await api.skillsInstall(key);
      setInstalled((p) => ({ ...p, [key]: true }));
    } catch {}
  };
  return (
    <div className="glass p-4" style={{ borderRadius: 12 }}>
      <div className="flex items-center gap-2 mb-3">
        <I.Bolt size={13} className="text-gold" />
        <span className="font-mono text-[12px] tracking-wider text-ink">{t("wallet.skills")}</span>
        <span className="pill pill-dim text-[10px]">官方 7 个 · 只读</span>
        <a href="#skills" className="ml-auto text-[11px] font-mono text-gold hover:underline">技能库 ↗</a>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5">
        {WALLET_SKILL_META.map((s) => {
          const ok = installed[s.key];
          return (
            <div key={s.key} className="group rounded-lg border border-line bg-elevated/30 hover:border-gold/40 transition-colors p-3 flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <span className="w-7 h-7 rounded-md bg-elevated flex items-center justify-center font-mono text-[12px] font-bold text-gold shrink-0">{s.icon}</span>
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-[12px] text-ink truncate">{s.title}</div>
                  <code className="font-mono text-[9.5px] text-ink-mute truncate block">{s.key}</code>
                </div>
                <span className={`pill ${ok ? "pill-green" : "pill-dim"} text-[9.5px] shrink-0`}>{ok ? "已装" : "未装"}</span>
              </div>
              <div className="font-mono text-[10.5px] text-ink-dim leading-relaxed">{s.desc}</div>
              <div className="flex items-center gap-1.5 mt-0.5">
                {ok ? (
                  <a href="#skills" className="btn-ghost text-[10.5px] py-1 flex-1 justify-center"><I.Play size={10} /> 运行</a>
                ) : (
                  <button onClick={() => onInstall(s.key)} className="btn-ghost text-[10.5px] py-1 flex-1 justify-center"><I.Download size={10} /> 一键安装</button>
                )}
                <a href={`https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/${s.key}`}
                  target="_blank" rel="noreferrer" className="btn-ghost text-[10.5px] py-1" title="官方仓库">
                  <I.Link size={10} />
                </a>
              </div>
            </div>
          );
        })}
      </div>
      <div className="font-mono text-[10px] text-ink-mute mt-3">
        7 个 Skill 全部「只读」：无需私钥 / API Key / 签名；query-address-info 等按需填入链上地址即可使用。
      </div>
    </div>
  );
}
