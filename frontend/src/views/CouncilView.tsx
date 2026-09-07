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
          <div className="font-mono text-[18px] font-bold text-ink">{t("council.jointTitle", { symbol: symbol.replace("USDT", "") })}</div>
          <div className="font-mono text-[11px] text-ink-dim mt-1">{t("council.subtitle")}</div>
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
        <button onClick={run} disabled={running} className="btn-gold"><I.Council size={12} /> {running ? t("council.deliberating") : t("council.submit")} <span className="ml-1 text-[10px]">2-STEP SIGN</span></button>
        {onDispatch && (
          <button onClick={() => onDispatch(symbol)} className="btn-ghost"><I.Cex size={12} /> {t("council.dispatchCex")}</button>
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
          {personas(symbol, t).map((p, i) => (
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
          <span className="prefix">{t("council.matrixTitle")}</span>
          <span className="pill pill-gold">STATUS: LOCKED_FOR_EXECUTION</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Mat label={t("council.matDirection")} value="↗ LONG" sub={t("council.matDirSub")} detail="Consensus: Unanimous Bullish" />
          <Mat label={t("council.matHorizon")} value={`${tf} ~ 24H`} sub="Short-term Swing Protocol" />
          <Mat label={t("council.matPosition")} value="15% / 3x LEV" sub={t("council.matPositionSub")} />
          <Mat label={t("council.matExecution")} value={t("council.matExecVal")} sub="Split Slice Duration: 120s" />
          <Mat label={t("council.matBreaker")} value="-2.0% / $87,400" sub="HARD SL (NO TRAILING DELAY)" warn />
        </div>
        <div className="mt-3 rounded-md border border-line bg-elevated/40 p-3 flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <I.Bolt className="text-gold" size={16} />
            <div>
              <div className="text-[13px] text-ink">{t("council.sorTitle")}</div>
              <div className="font-mono text-[10px] text-ink-mute mt-0.5">{t("council.sorDesc")}</div>
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
              <span className="font-mono text-[12px] text-ink">{t("council.equityTitle")}</span>
              <span className="tabs ml-2">
                <button>{t("council.tab30d")}</button>
                <button>{t("council.tab90d")}</button>
                <button>{t("council.tabAll")}</button>
              </span>
            </div>
          </div>
          <div className="grid grid-cols-4 gap-3 font-mono text-[11px]">
            <Metric label={t("council.mWinRate")} value="78.4%" delta="+8.2% vs BP" up />
            <Metric label={t("council.mExpectancy")} value="+1.85 R" delta="Risk/Reward: 1:2.8" up />
            <Metric label={t("council.mAcc")} value="0.14" delta="Optimal Calibration" up />
            <Metric label={t("council.mHighOpp")} value="+38.64%" delta="Max DD: -4.1%" up />
          </div>
          <EquityCurve />
          <div className="mt-3">
            <div className="prefix mb-1.5">30-DAY SIGNAL ATTRIBUTION BY MODEL</div>
            <div className="grid grid-cols-4 gap-2 font-mono text-[11px]">
              {[
                { m: "DeepSeek-R1", a: "+18.4% Alpha" },
                { m: "Claude 3.5",  a: "+9.8% Alpha" },
                { m: "Hermes-L2",   a: "+11.2% Alpha" },
                { m: "GPT-4o",      a: t("council.streakStable") },
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
            <div className="flex items-center gap-2"><I.Star className="text-gold" size={14} /><span className="font-mono text-[12px] text-ink">{t("council.archiveTitle")}</span></div>
            <span className="pill pill-gold">{t("council.recents")}</span>
          </div>
          {archive(t).map((r, i) => (
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
            <button className="text-gold hover:underline">{t("council.viewFull")}</button>
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

function personas(_sym: string, t: any) {
  return [
    { code: "AGENT_01", model: "DeepSeek-R1", title: t("council.p1Title"), subtitle: t("council.p1Sub"), body: t("council.p1Body"), conf: 94.2, weight: 0.30, synapse: "ACTIVE" },
    { code: "AGENT_02", model: "Claude-3.5", title: t("council.p2Title"), subtitle: t("council.p2Sub"), body: t("council.p2Body"), conf: 88.0, weight: 0.25, synapse: "ACTIVE" },
    { code: "GUARDRAIL", model: "GPT-4o", title: t("council.p3Title"), subtitle: t("council.p3Sub"), body: t("council.p3Body"), conf: 72.5, weight: 0.25, synapse: "ENFORCED" },
    { code: "AGENT_04", model: "Hermes-L2", title: t("council.p4Title"), subtitle: t("council.p4Sub"), body: t("council.p4Body"), conf: 91.0, weight: 0.25, synapse: "ACTIVE" },
  ];
}

function archive(t: any) {
  return [
    { date: "20250227-04", bias: "WIN", symbol: "ETH/USDT", summary: t("council.arch1"), delta: "+1,260" },
    { date: "20250226-08", bias: "WIN", symbol: "SOL/USDT", summary: t("council.arch2"), delta: "+790" },
    { date: "20250225-02", bias: "VETOED", symbol: "BNB/USDT", summary: t("council.arch3"), delta: "0" },
    { date: "20250224-11", bias: "LOSS", symbol: "AVAX/USDT", summary: t("council.arch4"), delta: "-215" },
    { date: "20250223-01", bias: "WIN", symbol: "BTC/USDT", summary: t("council.arch5"), delta: "+2,480" },
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
