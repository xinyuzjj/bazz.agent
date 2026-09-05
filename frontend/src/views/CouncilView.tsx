import React, { useState } from "react";
import { I } from "../components/icons";
import { useT } from "../i18n/i18n";

export function CouncilView({ onDispatch }: { onDispatch?: (symbol: string) => void }) {
  const t = useT();
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [tf, setTf] = useState("4H");
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [active, setActive] = useState(0);

  const run = () => {
    setRunning(true); setProgress(0); setActive(0);
    const t = setInterval(() => {
      setProgress((p) => {
        const np = p + 8;
        if (np >= 33 && np < 66) setActive(1);
        if (np >= 66 && np < 100) setActive(2);
        if (np >= 100) { clearInterval(t); setRunning(false); setActive(3); return 100; }
        return np;
      });
    }, 280);
  };

  return (
    <div className="p-5 space-y-4">
      {/* Header */}
      <div className="glass p-4 flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="dot dot-green live" />
          <span className="font-mono text-[10px] text-ink-dim tracking-[0.12em]">SWARM COUNCIL C3</span>
        </div>
        <span className="font-mono text-[10px] text-ink-dim ml-2">SESSION: #20250220-09</span>
        <span className="pill pill-gold"><I.Shield size={10} /> SUPERMAJORITY_RATIFIED 3/4</span>
        <div className="ml-2">
          <div className="font-mono text-[18px] font-bold text-ink">{symbol.replace("USDT", "")} / USDT 突破与多空博弈联合研判</div>
          <div className="font-mono text-[11px] text-ink-dim mt-1">4 人设，并行同种问题精解读多空双方仓定，基于链上筹码扫掘、奥姆订单簿深度流与冷摆指标合成连贯可操作策略。</div>
        </div>
        <div className="ml-auto">
          <div className="prefix">{t("council.statutory")}</div>
          <div className="flex items-center gap-2 mt-1">
            <span className="font-mono text-[28px] font-bold text-green">LONG</span>
            <span className="font-mono text-[10px] text-ink-dim">3x</span>
          </div>
          <div className="font-mono text-[10px] text-green mt-0.5">CONFIDENCE: 88.5%</div>
          <div className="font-mono text-[10px] text-ink-mute">MPC Enclave Verified</div>
        </div>
        <button onClick={run} disabled={running} className="btn-gold"><I.Council size={12} /> {running ? "审议中…" : "提交审议执行"} <span className="ml-1 text-[10px]">2-STEP SIGN</span></button>
        {onDispatch && (
          <button onClick={() => onDispatch(symbol)} className="btn-ghost"><I.Cex size={12} /> 裁决 → 派发到 CEX</button>
        )}
      </div>

      {/* Meta row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Meta label="CONSENSUS DIVERGENCE" value="0.114" sub="(LOW)" hint="convergence tight" />
        <Meta label="EXECUTION WINDOW" value="< 140 SECONDS" sub="(TIGHT)" hint="enter now" />
        <Meta label="AUTO DEFENSE SL" value="$87,400" sub="(-2.0%)" warn />
        <Meta label="ORDER LOSS SCORE" value="0.14" sub="(EXCELLENT)" hint="excellent fill expected" />
      </div>

      {/* Deliberation cards */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <span className="dot dot-gold live" />
            <span className="font-mono text-[12px] text-ink tracking-wider">AGENT DELIBERATION CARDS</span>
            <span className="pill pill-dim ml-2">4 PERSPECTIVES FEDERATED</span>
          </div>
          <span className="prefix">LATENCY: 42ms // DECENTRALIZED SYNC</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          {personas(symbol).map((p, i) => (
            <div key={i} className={`glass p-3.5 transition-all ${active === i || active >= 3 ? "" : running ? "opacity-60" : ""}`}>
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] text-ink-dim tracking-wider">{p.code}</span>
                <span className="font-mono text-[10px] text-ink-mute ml-auto">{p.model}</span>
              </div>
              <div className="mt-1.5 font-mono font-semibold text-ink text-[14px]">{p.title}</div>
              <div className="font-mono text-[10px] text-ink-mute mt-0.5">{p.subtitle}</div>
              <div className="mt-2 prefix">RATIONALE / REASONING</div>
              <div className="text-[12px] text-ink-dim mt-1.5 leading-relaxed">{p.body}</div>
              <div className="mt-3">
                <div className="flex items-center justify-between font-mono text-[10px]">
                  <span className="text-ink-dim">{t("council.modelConf")}</span>
                  <span className="text-gold">{p.conf.toFixed(1)}%</span>
                </div>
                <div className="mt-1 h-1.5 rounded-full bg-line overflow-hidden">
                  <div className="h-full bg-gold" style={{ width: `${p.conf}%` }} />
                </div>
                <div className="mt-1.5 flex justify-between font-mono text-[10px] text-ink-mute">
                  <span>WEIGHT: {p.weight.toFixed(2)}</span>
                  <span>SYNAPSE: {p.synapse}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Adjudication Matrix */}
      <div className="glass p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="prefix">结构化裁决参数矩阵 (Adjudication Matrix)</span>
          <span className="pill pill-gold">STATUS: LOCKED_FOR_EXECUTION</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Mat label="做多方向 (DIRECTION)" value="↗ LONG" sub="做多" detail="Consensus: Unanimous Bullish" />
          <Mat label="时间周期 (HORIZON)" value={`${tf} ~ 24H`} sub="Short-term Swing Protocol" />
          <Mat label="仓位配比与杠杆" value="15% / 3x LEV" sub="0.500 USDT 按仓 | ISOLATED" />
          <Mat label="条件性执行约束" value="TWAP $88,350 ↘ 50股  $89,100 ↗ 50股" sub="Split Slice Duration: 120s" />
          <Mat label="熔断风控阈值" value="-2.0% / $87,400" sub="HARD SL (NO TRAILING DELAY)" warn />
        </div>
        <div className="mt-3 rounded-md border border-line bg-elevated/40 p-3 flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <I.Bolt className="text-gold" size={16} />
            <div>
              <div className="text-[13px] text-ink">双阶段 TWAP 算 法路由器 (Smart Order Routing)</div>
              <div className="font-mono text-[10px] text-ink-mute mt-0.5">挂单将联通 V2 Binance VIP2 FIX API 异步推送，推动分散滑点至深度仓位对冲点。</div>
            </div>
          </div>
          <div className="flex items-center gap-3 font-mono text-[11px]">
            <span>ESTIMATED SLIPPAGE <span className="text-green ml-1">&lt; 0.012%</span></span>
            <span>MAX DRAWDOWN ALLOCANCE <span className="text-red ml-1">-170 USDT</span></span>
          </div>
        </div>
      </div>

      {/* Bottom panels: equity curve + history */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="glass p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[12px] text-ink">30 天议会 Alpha 净值曲线与胜率归因</span>
              <span className="tabs ml-2"><button>30D</button><button>90D</button><button>ALL</button></span>
            </div>
          </div>
          <div className="grid grid-cols-4 gap-3 font-mono text-[11px]">
            <Metric label="历史胜率 (WIN PROBACY)" value="78.4%" delta="+8.2% vs BP" up />
            <Metric label="期望盈利 (EXPECTANCY)" value="+1.85 R" delta="Risk/Reward: 1:2.8" up />
            <Metric label="胜率 · 准确率分" value="0.14" delta="Optimal Calibration" up />
            <Metric label="高机会类策略" value="+38.64%" delta="Max DD: -4.1%" up />
          </div>
          <EquityCurve />
          <div className="mt-3">
            <div className="prefix mb-1.5">30-DAY SIGNAL ATTRIBUTION BY MODEL</div>
            <div className="grid grid-cols-4 gap-2 font-mono text-[11px]">
              {[
                { m: "DeepSeek-R1", a: "+18.4% Alpha" },
                { m: "Claude 3.5",  a: "+9.8% Alpha" },
                { m: "Hermes-L2",   a: "+11.2% Alpha" },
                { m: "GPT-4o",      a: "8 次连胜稳固" },
              ].map((x, i) => (
                <div key={i} className="rounded-md border border-line bg-elevated/40 px-2 py-1.5 text-center">
                  <div className="text-ink-dim">{x.m}</div>
                  <div className="text-gold mt-0.5">{x.a}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="glass p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2"><I.Star className="text-gold" size={14} /><span className="font-mono text-[12px] text-ink">历史裁决归档与胜负检视</span></div>
            <span className="pill pill-gold">RECENTS</span>
          </div>
          {archive().map((r, i) => (
            <div key={i} className={`grid grid-cols-3 gap-2 py-2 border-b border-line/60 last:border-0 items-center`}>
              <div>
                <div className="flex items-center gap-2">
                  <span className={`pill ${r.bias === "WIN" ? "pill-green" : "pill-red"}`}>{r.bias === "WIN" ? "WIN" : "LOSS"}</span>
                  <span className="font-mono font-semibold text-ink">{r.symbol}</span>
                </div>
                <div className="font-mono text-[10px] text-ink-mute mt-1">{r.date}</div>
              </div>
              <div className="font-mono text-[11px] text-ink-dim">{r.summary}</div>
              <div className={`text-right font-mono tabular ${r.bias === "WIN" ? "up" : "down"}`}>{r.delta} USDT</div>
            </div>
          ))}
          <div className="mt-3 flex items-center justify-between font-mono text-[10px] text-ink-dim">
            <span>AUDIT LOG HASH: e4b2..97e9</span>
            <button className="text-gold hover:underline">查看完整上级明标记 →</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Meta({ label, value, sub, hint, warn }: { label: string; value: string; sub: string; hint?: string; warn?: boolean }) {
  return (
    <div className="glass p-3">
      <div className="prefix">{label}</div>
      <div className={`mt-1 font-mono tabular text-[16px] font-bold ${warn ? "text-red" : "text-ink"}`}>{value}</div>
      <div className="font-mono text-[10px] text-ink-mute">{sub}</div>
      {hint && <div className="font-mono text-[10px] text-ink-dim mt-1">{hint}</div>}
    </div>
  );
}

function Mat({ label, value, sub, detail, warn }: { label: string; value: string; sub: string; detail?: string; warn?: boolean }) {
  return (
    <div className="rounded-md border border-line bg-card/40 p-3">
      <div className="prefix">{label}</div>
      <div className={`mt-1 font-mono tabular text-[14px] font-bold ${warn ? "text-red" : "text-gold"}`}>{value}</div>
      <div className="font-mono text-[10px] text-ink-mute mt-1">{sub}</div>
      {detail && <div className="font-mono text-[10px] text-ink-dim mt-0.5">{detail}</div>}
    </div>
  );
}

function Metric({ label, value, delta, up }: { label: string; value: string; delta: string; up?: boolean }) {
  return (
    <div className="rounded-md border border-line bg-card/40 p-2.5">
      <div className="prefix">{label}</div>
      <div className={`mt-1 font-mono tabular text-[16px] font-bold ${up ? "up" : "text-ink"}`}>{value}</div>
      <div className="font-mono text-[10px] text-ink-mute">{delta}</div>
    </div>
  );
}

function personas(_sym: string) {
  return [
    { code: "AGENT_01", model: "DeepSeek-R1", title: "趋势明式", subtitle: "技术专家 · DeepSeek-R1", body: "4H EMA20 形态末端确认，K 线收盘在增长斜率上轨，RSI 增强，成交量保持于多头洗透位。成功宽放增幅 240%，伴随强烈买盘信号。", conf: 94.2, weight: 0.30, synapse: "ACTIVE" },
    { code: "AGENT_02", model: "Claude-3.5", title: "宏观与巨鯨追踪者", subtitle: "深度思维链 · Sonnet", body: "全网中心化交易所出现 3,200 BTC 净流出，未观测到续跌延展，工与军期巨鲸地址转出多空，实数据出货增长近 14 日极低水平。", conf: 88.0, weight: 0.25, synapse: "ACTIVE" },
    { code: "GUARDRAIL", model: "GPT-4o", title: "法务风险审核", subtitle: "法务专家 · GPT-4o", body: "盯本应未在余繁项会索索提货 (+0.028%)，智能线熔赔资金流动性为盈，抑制触发护仓护金。仓位收益提款 3%，严格熔幅止损于 1.8% 以内。", conf: 72.5, weight: 0.25, synapse: "ENFORCED" },
    { code: "AGENT_04", model: "Hermes-L2", title: "微观结构与深度", subtitle: "量化执行 · Cere L2", body: "历史调度 ±0.5% 范围内的重提交加仓位 2.4 单，在 $88,100-$88,350 区域给出 480 BTC 冲击鱼费护严委托带栏。", conf: 91.0, weight: 0.25, synapse: "ACTIVE" },
  ];
}

function archive() {
  return [
    { date: "20250227-04", bias: "WIN", symbol: "ETH/USDT", summary: "LONG 5X · 调度 4/4 · $2,789 Yesterday", delta: "+1,260" },
    { date: "20250226-08", bias: "WIN", symbol: "SOL/USDT", summary: "SHORT 3X · 调度 4/4 · 叠口道量 24h", delta: "+790" },
    { date: "20250225-02", bias: "VETOED", symbol: "BNB/USDT", summary: "LONG · 2/4 员 · 风险雷·主事（衡性存在）", delta: "0" },
    { date: "20250224-11", bias: "LOSS", symbol: "AVAX/USDT", summary: "LONG · 3/4 员 · 调望 -1.8% · 机上偏差锚", delta: "-215" },
    { date: "20250223-01", bias: "WIN", symbol: "BTC/USDT", summary: "LONG 3X · 调度 4/4 · 烟雾量 50 BTC", delta: "+2,480" },
  ];
}

function EquityCurve() {
  const pts = Array.from({ length: 30 }, (_, i) => 1000 + i * 13 + Math.sin(i * 0.6) * 18 + (i > 20 ? i * 4 : 0));
  const w = 600, h = 130;
  const max = Math.max(...pts), min = Math.min(...pts);
  const sx = (i: number) => (i / (pts.length - 1)) * w;
  const sy = (v: number) => h - ((v - min) / (max - min || 1)) * (h - 12) - 6;
  const d = pts.map((v, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)} ${sy(v).toFixed(1)}`).join(" ");
  const area = `${d} L${w} ${h} L0 ${h} Z`;
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} className="mt-3">
      <defs>
        <linearGradient id="eg" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#0ECB81" stopOpacity="0.4" />
          <stop offset="100%" stopColor="#0ECB81" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#eg)" />
      <path d={d} fill="none" stroke="#0ECB81" strokeWidth="1.6" />
      <text x="6" y={h - 6} fill="#5E6673" fontSize="10" fontFamily="JetBrains Mono">DAY -30</text>
      <text x={w - 70} y={h - 6} fill="#5E6673" fontSize="10" fontFamily="JetBrains Mono">TODAY (SESSION #09)</text>
    </svg>
  );
}