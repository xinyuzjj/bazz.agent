// 行情表格行组件（v1.4.0 拆分自 MarketsView）
// 每行 React.memo + 内部 useLiveTick 订阅自身实时价：WS tick 只让价格变化的行重渲，
// 父组件整表不再随每秒时间戳 tick 重渲。
import React, { memo, useEffect, useRef, useState } from "react";
import { I } from "./icons";
import { useT } from "../i18n/i18n";
import { useLiveTick } from "../lib/live";

/* ---------------- 共享类型（与后端 /api/market* 对齐） ---------------- */

export type Ticker = { symbol: string; price: number; change_pct: number; volume: number; quote_volume: number; high: number; low: number };
export type MinTicker = { symbol: string; price: number; change_pct: number };
export type Signal = {
  symbol: string; price: number; change_pct: number; volume: number;
  funding_rate?: number; direction: string; emoji?: string; reason?: string; score?: number;
};
export type FutureRow = { symbol: string; price: number; change_pct: number; quote_volume: number; high: number; low: number; funding_rate: number };
export type EquityRow = FutureRow & { name?: string; leverage?: string };
export type RadarRow = {
  symbol: string; price: number; quote_volume: number; vol_ratio: number;
  tag: string; side: "LONG" | "WATCH_SHORT" | "WATCH"; note: string; score: number;
  change7d_pct?: number; change30d_pct?: number; drawdown_pct?: number;
  change3d_pct?: number; position_pct?: number; floor_rising?: boolean;
  // v1.5.0 雷达 v2：语义层阶段 + 确认层因子
  stage?: string; stage_label?: string;
  change1h_pct?: number; change24_pct?: number; rvol15?: number; amp24?: number;
  cooldown?: boolean; listed_days?: number | null;
  factors?: {
    flow?: number; jump?: number; speed5m?: number; rvol15?: number; amp24?: number;
    funding?: number; funding_peak?: number | null;
    oi_chg24?: number; oi_pulse15?: number;
    top_ratio?: number | null; global_ratio?: number | null; taker_ratio?: number | null;
    liq_5m?: number; liq_side?: string;
    btc_beta?: number | null; btc_residual?: number | null;
  };
  reasons?: string[];
  // v1.6.3 控盘代理（妖币识别）：命中即说明这是庄家剧本盘 —— 做多侧已被后端降级为 WATCH
  manip?: string[]; manip_note?: string;
  // v1.6.9（#6）：确认层只覆盖前 24 个候选 —— 未覆盖的行 oi_last=None → oi_usd=0，
  // 于是「换手畸高」判据分母为 0、永不成立。带上 fut_qv 才能在 UI 上把
  // 「没测到」与「真没有」分开（oi_usd==0 且 fut_qv>0 ⇒ 控盘未测）。
  oi_usd?: number; fut_qv?: number; spot_qv?: number; no_spot?: boolean;
  // v1.6.9（#5）：已越过 _TRIG_LATE_CHG24 启动窗口 —— 后端只在展示层改写
  // tag/stage_label/side，stage 状态机键不动，前端据此单独渲染 pill。
  late?: boolean;
  // v1.6.9（D1）：爆仓流是否真的接上了。false ⇒ 爆仓类因子一律「未测」，不得当 0。
  liq_available?: boolean | null;
};
// v1.6.9（#2）：阈值由后端 payload 单一来源下发，前端不再硬编码。
// 病根：后端 v1.5.66 把 _TAKER_BUY_DOMINANT 从 1.85 修到 1.30，前端摘要行仍写 1.5 →
// taker ∈ [1.30, 1.50) 的币「买盘主导」信号在 UI 上完全不可见（后端 reasons 有、前端 bits 没有，
// 而 `bits.length ? bits.join() : note` 的短路又让 note 也不显示）→「分数为什么高」解释不通。
export type RadarThresholds = Record<string, number | undefined>;
// 兜底值：仅在 payload 缺 thresholds（旧后端 / 预览 mock）时使用，取值与后端常量保持一致
export const FALLBACK_TAKER_BUY_DOMINANT = 1.30;

// v1.6.9（档一 §16）：阈值命中率监控快照（后端 scanner.threshold_hitrate()）。
// `dead_rules` 非空 = 样本已够（≥ min_rows 行）但某阈值一次都没命中 ——
// 这正是 `_TAKER_BUY_DOMINANT = 1.85` 当年的形态（自上线起命中 0 个却在四处被使用）。
// 这类规则不报错、不让测试变红，只能靠命中率暴露，所以必须在界面上说出来。
export type RadarThresholdHits = {
  scans?: number; rows?: number; since?: number; min_rows?: number;
  dead_rules?: string[];
  rules?: Record<string, { desc?: string; hits?: number; rows?: number; rate?: number; dead?: boolean }>;
};

// v1.6.9（档二 C6）：本地时序库体检快照（后端 scanner.ts_store_view() / state.ts_stats()）。
// 存在的意义与 §16 同源：**让「到底存没存下来」可见**。
// 只写不看的落盘等于没有落盘 —— 出问题时（表建了但一直写 0 行）没有任何地方会说。
// `last_written` 是最近一轮扫描真正写入的行数；`rows` 是库里累计行数。
export type RadarTsStore = {
  rows?: number; symbols?: number; oldest?: number; newest?: number;
  keep_days?: number; last_written?: number; last_ts?: number;
};

// v1.7.1（C3 延迟治理）：雷达数据的**新鲜度**（后端 `_with_age()` 下发）。
// 为什么不让前端自己拿 `Date.now() - updated_at*1000` 算：
//   ① 本地时钟与服务端有偏差时直接算错，而错的方向不固定；
//   ② 前端不知道**本轮生效的** TTL（可被 env `BAZZ_RADAR2_TTL` 覆盖），
//      于是无法判断「这个数字是不是已经过期」，只能一直显示得像新鲜的；
//   ③ 三处消费点各算各的，迟早不一致。
// `age_sec` = 读的时刻 − 本轮扫描时刻；`stale` = `age_sec` 是否超过本轮 TTL。
export type RadarFresh = { age_sec?: number; stale?: boolean; ttl?: number };
export type OrderMode = "spot-long" | "futures-long" | "futures-short";

// v1.5.0 语义层阶段 → pill 样式（吸筹/点火=绿，垂直拉升=金，派发顶/崩跌=红，沉寂/异动=灰）
// v1.6.2 新增 EXTENDED（已拉升·追高区）：越过 12% 追高线，启动窗口已过 → 中性偏警示
export const STAGE_META: Record<string, { cls: string }> = {
  ACCUMULATION: { cls: "pill-green" },
  IGNITION:     { cls: "pill-green" },
  SHORT_AMBUSH: { cls: "pill-red" },
  VERTICAL:     { cls: "pill-gold" },
  EXTENDED:     { cls: "pill-gold" },
  DISTRIBUTION: { cls: "pill-red" },
  CRASH:        { cls: "pill-red" },
  DORMANT:      { cls: "pill-dim" },
  ACTIVE:       { cls: "pill-dim" },
};

export const SIDE_META: Record<RadarRow["side"], { label: string; cls: string }> = {
  LONG: { label: "markets.sideLong", cls: "pill-green" },
  WATCH_SHORT: { label: "markets.sideShort", cls: "pill-red" },
  WATCH: { label: "markets.sideWatch", cls: "pill-dim" },
};
export const RADAR_COLS = {
  ignition: { tpl: "2fr 0.9fr 0.9fr 0.9fr 1fr 0.9fr 1fr 2.9fr", head: ["markets.col.symbol", "markets.h.price", "3D", "30D", "markets.col.pos90", "markets.col.volratio", "markets.col.verdict", "markets.col.action"] },
  takeoff:  { tpl: "1.9fr 0.9fr 0.9fr 0.9fr 0.9fr 1fr 0.8fr 1fr 2.9fr", head: ["markets.col.symbol", "markets.h.price", "7D", "30D", "markets.col.fromhigh", "markets.h.quotevol", "markets.col.surge", "markets.col.verdict", "markets.col.action"] },
} as const;

// v1.5.2 妖币追踪：启动前发现 → 后续暴涨/暴跌结局验证（v1.5.7 增加方向 + 当前涨跌幅）
export type TrackRow = {
  id: string; symbol: string; stage: string; direction?: "LONG" | "SHORT";
  found_price: number; found_score: number; reasons?: string[];
  status: "pending" | "closed";
  outcome: "" | "moon" | "dump" | "expired";
  max_gain_pct: number; max_drop_pct: number;
  peak_price: number; trough_price: number;
  last_price: number; outcome_price: number;
  review?: string; holding?: number;
  // v1.6.5（OPT-06）：疑似假启动（跟踪≥24h 却始终没给过 5% 浮盈）+ 已跟踪小时数
  fake_start?: boolean; age_h?: number;
  found_at: number; closed_at: number | null; updated_at: number;
};
// v1.6.5（OPT-07）：按 stage 分组胜率
export type StageStat = { pending: number; moon: number; dump: number; expired: number; closed: number; win_rate: number };
export type TrackStats = {
  total?: number; pending?: number; moon?: number; dump?: number; expired?: number;
  by_stage?: Record<string, StageStat>; stages?: [string, StageStat][];
};
export type TracksData = {
  pending: TrackRow[]; history: TrackRow[]; stats?: TrackStats; ts?: number; error?: string;
  // v1.6.9（#12/#13）：pending/history 的**全量**条数 vs 实际下发的展示条数。
  // 顶部计数基于全量，历史表只列 HISTORY_SHOWN 条 —— 两个数必须都能拿到才能注明口径。
  pending_total?: number; history_total?: number; history_shown?: number; history_shown_limit?: number;
};
export const OUTCOME_META: Record<string, { label: string; cls: string }> = {
  moon:    { label: "markets.outcomeMoon", cls: "pill-green" },
  dump:    { label: "markets.outcomeDump", cls: "pill-red" },
  expired: { label: "markets.outcomeExpired", cls: "pill-dim" },
};
export const TRACK_COLS = {
  pending: { tpl: "1.9fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.9fr", head: ["markets.col.symbol", "markets.trackFoundPrice", "markets.trackNowPrice", "markets.trackChg", "markets.trackMaxGain", "markets.trackMaxDrop", "markets.trackPnl", "markets.trackFoundAt"] },
  history: { tpl: "1.9fr 0.8fr 0.8fr 0.9fr 0.8fr 0.8fr 0.8fr 0.9fr", head: ["markets.col.symbol", "markets.trackFoundPrice", "markets.trackOutcomePrice", "markets.trackDate", "markets.trackMaxGain", "markets.trackMaxDrop", "markets.trackPnl", "markets.trackDuration"] },
} as const;
// 仓位模拟（与后端 radar_tracker 一致）：100U 本金 × 10x 合约，爆仓封底 -100U
export const trackPnl = (direction: string | undefined, found: number, px: number) => {
  if (!found || !px) return null;
  const chg = (px / found - 1) * 100;
  const roi = (direction === "SHORT" ? -chg : chg) * 10;
  return Math.max(-100, roi);
};
// 关单日期（历史战绩列）：MM-DD HH:mm（后端时间戳为秒）
export const fmtCloseDate = (ts: number | null) => {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};
// 相对时间：60s→"xm"、1h→"x.xh"、更长→"x.xd"（列头文案区分"发现于/持续"）
export const fmtAgo = (ts: number, now: number = Date.now() / 1000) => {
  const s = Math.max(0, now - ts);
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
};

/* ---------------- 格式化 ---------------- */

export const fmtPrice = (n: number) => {
  if (!n && n !== 0) return "—";
  if (n >= 1000) return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 1) return n.toFixed(4);
  return n.toPrecision(4);
};
export const fmtVol = (v: number) => {
  if (!v && v !== 0) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
};
export const fmtRate = (r?: number) => r === undefined ? "—" : `${r > 0 ? "+" : ""}${(r * 100).toFixed(4)}%`;
// v1.6.9（档二 C6）：**计数**用的紧凑格式。刻意不复用 `fmtVol` —— 后者带 `$` 前缀（金额口径），
// 拿它显示「时序库行数」会把行数印成美元，正是本项目反复出现的「口径被静默换掉」那类毛病。
export const fmtCount = (v: number) => {
  const n = Number(v) || 0;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
};
// v1.7.1（C3）：陈旧时长（秒）→ 人读的紧凑串。0/负 → "0s"（刚算完，不是「无数据」）。
// 只做单位换算，不在这里判「算不算陈旧」—— 那是后端 `stale` 的职责（它才知道本轮 TTL）。
export const fmtAge = (sec?: number) => {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), r = s % 60;
  return r ? `${m}m${r}s` : `${m}m`;
};
export const baseName = (s: string) => s.replace(/USDT$/, "");

/* ---------------- v1.5.3 可视化：价格闪烁 / 24h 区间条 / 侧栏 bar 行 ---------------- */

// 价格变化闪烁：对比上次值，变化时返回一次性 flash-up / flash-down class
export function useFlash(value: number): string {
  const prev = useRef(value);
  const [cls, setCls] = useState("");
  useEffect(() => {
    if (value > prev.current) setCls("flash-up");
    else if (value < prev.current) setCls("flash-down");
    prev.current = value;
    const t = setTimeout(() => setCls(""), 650);
    return () => clearTimeout(t);
  }, [value]);
  return cls;
}

// 24h 区间位置条：当前价在 [low, high] 中的位置（CoinMarketCap 风格渐变条 + 位置点）
export const PosBar = memo(function PosBar({ low, high, price }: { low: number; high: number; price: number }) {
  const t = useT();
  if (!(low > 0 && high > low)) return null;
  const pos = Math.min(1, Math.max(0, (price - low) / (high - low)));
  return (
    <div className="posbar" title={t("markets.posbarTitle")}>
      <div className="posbar-fill" style={{ width: "100%" }} />
      <div className="posbar-dot" style={{ left: `${pos * 100}%` }} />
    </div>
  );
});

// 侧栏：24h 成交额热度行（底部横向 bar，宽度 = 相对最大成交额）
export const VolHeatRow = memo(function VolHeatRow({ v, rank, maxVol, onTrade, onAnalyze }: {
  v: { symbol: string; price: number; change_pct: number; quote_volume: number };
  rank: number; maxVol: number;
  onTrade?: (symbol: string) => void;
  onAnalyze?: (symbol: string) => void;
}) {
  const t = useT();
  const up = v.change_pct >= 0;
  const w = maxVol > 0 ? Math.max(2, (v.quote_volume / maxVol) * 100) : 0;
  return (
    <button onClick={() => onTrade?.(v.symbol)}
      className="w-full rounded-lg border border-line bg-card/40 px-2.5 py-1.5 hover:border-gold/40 transition-colors group">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] text-ink-mute w-4">#{rank + 1}</span>
        <span className="font-mono text-[13px] text-ink flex-1 text-left truncate">{baseName(v.symbol)}</span>
        <span className={`font-mono tabular text-[13px] ${up ? "up" : "down"}`}>{up ? "+" : ""}{v.change_pct.toFixed(1)}%</span>
        <span className="font-mono tabular text-[12.5px] text-ink-dim">{fmtVol(v.quote_volume)}</span>
        <span onClick={(e) => { e.stopPropagation(); onAnalyze?.(v.symbol); }}
          className="btn-ghost px-1.5 py-0.5 text-[11px] opacity-0 group-hover:opacity-100"><I.Search size={9} /> {t("markets.analyze")}</span>
      </div>
      <div className="hbar mt-1.5"><div style={{ width: `${w}%`, background: up ? "var(--green)" : "var(--red)" }} /></div>
    </button>
  );
});

// 侧栏：多空比行（大户多空比双段比例条，1:1 = 中线）
export const LsLine = memo(function LsLine({ r, onTrade }: {
  r: { symbol: string; top_ratio: number | null; global_ratio: number | null; divergence: boolean };
  onTrade?: (symbol: string) => void;
}) {
  const t = useT();
  const ratio = r.top_ratio ?? 0;
  const longPct = ratio > 0 ? Math.min(0.95, Math.max(0.05, ratio / (1 + ratio))) : 0.5;
  // 背离方向跟随大户（smart money）：大户比>1 = 主力净多 → 偏多；<1 = 主力净空 → 偏空
  const divergeLong = (r.top_ratio ?? 1) >= 1;
  return (
    <button onClick={() => onTrade?.(r.symbol)}
      className="w-full rounded-md px-1.5 py-1 hover:bg-elevated/40 transition-colors">
      <div className="flex items-center gap-2 font-mono text-[12px]">
        <span className="text-ink flex-1 text-left truncate">{baseName(r.symbol)}</span>
        {r.divergence && (
          <span className={`pill text-[10px] ${divergeLong ? "pill-green" : "pill-red"}`}
            title={t("markets.lsDivergeTip")}>
            {t(divergeLong ? "markets.lsDivergeLong" : "markets.lsDivergeShort")}
          </span>
        )}
        <span className="text-ink-dim tabular" title={t("markets.lsTop")}>
          {t("markets.lsTopShort")} {r.top_ratio != null ? r.top_ratio.toFixed(2) : "—"}
        </span>
        <span className="text-ink-mute tabular" title={t("markets.lsGlobal")}>
          {t("markets.lsGlobalShort")} {r.global_ratio != null ? r.global_ratio.toFixed(2) : "—"}
        </span>
      </div>
      <div className="flex items-center gap-1.5 mt-1">
        <span className="font-mono text-[10px] text-green">{t("markets.long")}</span>
        <div className="hbar flex-1 flex">
          <div style={{ width: `${longPct * 100}%`, background: "var(--green)" }} />
          <div style={{ width: `${(1 - longPct) * 100}%`, background: "var(--red)" }} />
        </div>
        <span className="font-mono text-[10px] text-red">{t("markets.short")}</span>
      </div>
    </button>
  );
});

/* ---------------- 更新时间角标（自带 1s tick，隔离重渲范围） ---------------- */

export const UpdatedAgo = memo(function UpdatedAgo({ updatedAt }: { updatedAt?: number }) {
  const t = useT();
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const iv = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(iv); }, []);
  if (!updatedAt) return <span>—</span>;
  const secs = Math.max(0, Math.floor((now / 1000) - updatedAt));
  if (secs < 1) return <span>{t("markets.updatedNow")}</span>;
  if (secs < 60) return <span>{t("markets.updatedAgo", { n: secs })}</span>;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return <span>{t("markets.updatedMinAgo", { n: mins })}</span>;
  return <span>{new Date(updatedAt * 1000).toLocaleTimeString()}</span>;
});

/* ---------------- 现货全市场行 ---------------- */

export const SpotRow = memo(function SpotRow({ r, onDetail, onAnalyze }: {
  r: Ticker;
  onDetail?: (symbol: string, base?: any) => void;
  onAnalyze?: (symbol: string) => void;
}) {
  const t = useT();
  const tick = useLiveTick("spot", r.symbol);
  const price = tick ? tick.p : r.price;
  const chg = tick ? tick.c : r.change_pct;
  const qv = tick && tick.q ? tick.q : r.quote_volume;
  const low = tick && tick.l ? tick.l : r.low;
  const high = tick && tick.h ? tick.h : r.high;
  const flash = useFlash(price);
  const up = chg >= 0;
  return (
    <div key={r.symbol} onClick={() => onDetail?.(r.symbol, r)} title={t("markets.detail.open")}
      className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
      style={{ gridTemplateColumns: "1.7fr 1fr 1.1fr 1.2fr 1.4fr" }}>
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono font-semibold text-ink group-hover:text-gold transition-colors">{baseName(r.symbol)}</span>
        <span className="font-mono text-[11.5px] text-ink-mute">/USDT</span>
        <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(r.symbol); }}
          className="btn-ghost px-1.5 py-0.5 text-[11px] opacity-0 group-hover:opacity-100" title={t("markets.analyze")}><I.Search size={9} /> {t("markets.analyze")}</button>
      </div>
      <div className={`font-mono tabular text-ink text-[13.5px] font-medium px-1 -mx-1 ${flash}`}>{fmtPrice(price)}</div>
      <div className={`font-mono tabular text-[13.5px] font-medium ${up ? "up" : "down"}`}>
        {up ? "+" : ""}{chg.toFixed(2)}%
      </div>
      <div className="font-mono tabular text-ink-dim text-[13px]">{fmtVol(qv)}</div>
      <div className="pr-1"><PosBar low={low} high={high} price={price} /></div>
    </div>
  );
});

/* ---------------- 合约（永续）行 ---------------- */

export const FutRow = memo(function FutRow({ r, oi, onDetail, onAnalyze }: {
  r: FutureRow;
  oi?: { oi: number | null; notional: number | null };
  onDetail?: (symbol: string, base?: any) => void;
  onAnalyze?: (symbol: string) => void;
}) {
  const t = useT();
  const tick = useLiveTick("futures", r.symbol);
  const price = tick ? tick.p : r.price;
  const chg = tick ? tick.c : r.change_pct;
  const qv = tick && tick.q ? tick.q : r.quote_volume;
  const low = tick && tick.l ? tick.l : r.low;
  const high = tick && tick.h ? tick.h : r.high;
  const flash = useFlash(price);
  const up = chg >= 0;
  return (
    <div key={r.symbol} onClick={() => onDetail?.(r.symbol, r)} title={t("markets.detail.open")}
      className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
      style={{ gridTemplateColumns: "1.5fr 1fr 1fr 1.1fr 0.9fr 1.1fr 1.2fr" }}>
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(r.symbol)}</span>
        <span className="font-mono text-[11.5px] text-ink-mute">/USDT</span>
        <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(r.symbol); }}
          className="btn-ghost px-1.5 py-0.5 text-[11px] opacity-0 group-hover:opacity-100" title={t("markets.analyze")}><I.Search size={9} /> {t("markets.analyze")}</button>
      </div>
      <div className={`font-mono tabular text-ink text-[13.5px] font-medium px-1 -mx-1 ${flash}`}>{fmtPrice(price)}</div>
      <div className={`font-mono tabular text-[13.5px] font-medium ${up ? "up" : "down"}`}>{up ? "+" : ""}{chg.toFixed(2)}%</div>
      <div className="font-mono tabular text-ink-dim text-[13px]">{fmtVol(qv)}</div>
      <div className={`font-mono tabular text-[13px] ${Math.abs(r.funding_rate) >= 0.001 ? "text-gold font-semibold" : r.funding_rate >= 0 ? "text-green" : "text-red"}`}>{fmtRate(r.funding_rate)}</div>
      <div className={`font-mono tabular text-[13px] ${oi?.notional != null ? "text-ink-dim" : "text-ink-mute"}`} title={oi?.oi != null ? `OI ${oi.oi.toFixed(0)} ${baseName(r.symbol)}` : undefined}>
        {oi?.notional != null ? `$${fmtVol(oi.notional)}` : "—"}
      </div>
      <div className="pr-1"><PosBar low={low} high={high} price={price} /></div>
    </div>
  );
});

/* ---------------- 股票化代币合约卡片 ---------------- */

export const EquityCard = memo(function EquityCard({ e, onDetail }: {
  e: EquityRow;
  onDetail?: (symbol: string, base?: any) => void;
}) {
  const t = useT();
  const tick = useLiveTick("futures", e.symbol);
  const price = tick ? tick.p : e.price;
  const chg = tick ? tick.c : e.change_pct;
  const qv = tick && tick.q ? tick.q : e.quote_volume;
  const cfg = e.leverage ? { label: `≤${e.leverage}x`, cls: "pill-gold" } : { label: "永续", cls: "pill-dim" };
  const up = chg >= 0;
  return (
    <button onClick={() => onDetail?.(e.symbol)} title={t("markets.detail.open")}
      className="group flex flex-col gap-1.5 rounded-xl border border-line bg-card/40 p-3 text-left hover:border-gold/50 hover:shadow-sm transition-all">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="font-mono font-semibold text-[15px] text-ink group-hover:text-gold">{baseName(e.symbol)}</span>
            <span className="pill pill-dim text-[10.5px]">/USDT</span>
            <span className={`pill ${cfg.cls} text-[10.5px]`}>{cfg.label}</span>
          </div>
          <div className="font-mono text-[11.5px] text-ink-mute truncate mt-0.5" title={e.name}>{e.name ?? "—"}</div>
        </div>
        <span className={`font-mono tabular text-[14px] font-semibold ${up ? "up" : "down"}`}>
          {up ? "+" : ""}{chg.toFixed(2)}%
        </span>
      </div>
      <div className="flex items-center justify-between font-mono tabular text-[13px]">
        <span className="text-ink">{fmtPrice(price)}</span>
        <span className={`text-[12.5px] ${up ? "up" : "down"}`}>24h</span>
      </div>
      <div className="flex items-center justify-between font-mono text-[12px] text-ink-dim">
        <span>{fmtVol(qv)}</span>
        <span className={Math.abs(e.funding_rate) >= 0.001 ? "text-gold" : "text-ink-mute"}>
          {t("markets.funding")} {fmtRate(e.funding_rate)}
        </span>
      </div>
    </button>
  );
});

/* ---------------- 妖币雷达行 ---------------- */

export const RadarLine = memo(function RadarLine({ m, mode, th, onTrade, onOrder, onAnalyze, onDetail }: {
  m: RadarRow;
  mode: "ignition" | "takeoff";
  // v1.6.9（#2）：后端下发的阈值（缺失时退回 FALLBACK_TAKER_BUY_DOMINANT）
  th?: RadarThresholds;
  onTrade?: (symbol: string) => void;
  onOrder?: (symbol: string, mode: OrderMode) => void;
  onAnalyze?: (symbol: string) => void;
  onDetail?: (symbol: string, base?: any) => void;
}) {
  const t = useT();
  const side = SIDE_META[m.side];
  const isIgn = mode === "ignition";
  // v1.5.0 确认因子摘要（悬浮 title / 次行展示）
  const f = m.factors ?? {};
  const bits: string[] = [];
  if (f.flow != null) bits.push(`flow ${f.flow}`);
  if (f.jump != null && f.jump > 0) bits.push(`J${f.jump.toFixed(1)}`);
  if (f.speed5m != null && Math.abs(f.speed5m) >= 0.3) bits.push(`5m ${f.speed5m > 0 ? "+" : ""}${f.speed5m.toFixed(1)}%`);
  if (f.rvol15 != null && f.rvol15 >= 1.2) bits.push(`RVOL ${f.rvol15.toFixed(1)}x`);
  if (f.funding != null && Math.abs(f.funding) >= 0.0015) bits.push(`费率${f.funding > 0 ? "+" : ""}${(f.funding * 100).toFixed(3)}%`);
  if (f.oi_chg24 != null && Math.abs(f.oi_chg24) >= 5) bits.push(`OI ${f.oi_chg24 > 0 ? "+" : ""}${f.oi_chg24.toFixed(0)}%`);
  if (f.top_ratio != null) bits.push(`大户 ${f.top_ratio.toFixed(2)}`);
  // v1.6.9（#2）：阈值改读后端下发值（原为硬编码 1.5，比后端 1.30 严，挡掉了 1.30~1.50 的信号）
  const tkMin = th?.taker_buy_dominant ?? FALLBACK_TAKER_BUY_DOMINANT;
  if (f.taker_ratio != null && f.taker_ratio >= tkMin) bits.push(`taker ${f.taker_ratio.toFixed(2)}`);
  if (f.liq_5m != null && f.liq_5m >= 3e5) bits.push(`爆 $${(f.liq_5m / 1e6).toFixed(1)}M${f.liq_side === "long" ? "多" : f.liq_side === "short" ? "空" : ""}`);
  const stageCls = m.stage ? STAGE_META[m.stage]?.cls ?? "pill-dim" : "pill-dim";
  // v1.6.9（D1）：爆仓流没接上时，摘要行必须显式说明 —— 否则用户看到的「没有爆仓 bit」
  // 会被理解成「真的没爆仓」，而实际是「这个数据源整体不可用」。
  const bitsText = bits.length ? bits.join(" · ") : m.note;
  const rowTip = m.liq_available === false ? `${bitsText} ｜ ${t("markets.liqUnavailableTip")}` : bitsText;
  // v1.6.9（#6）：控盘指纹为空 + oi_usd=0 但合约有成交额 ⇒ 这一行压根没进确认层
  const manipUnmeasured = !m.manip?.length && (m.oi_usd ?? 0) === 0 && (m.fut_qv ?? 0) > 0;
  return (
    <div onClick={() => onDetail?.(m.symbol, m)} title={t("markets.detail.open")}
      className={`grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 transition-colors cursor-pointer group
        ${m.side === "LONG" ? "bg-green/5 hover:bg-green/10" : m.side === "WATCH_SHORT" ? "bg-red/5 hover:bg-red/10" : "hover:bg-elevated/40"}`}
      style={{ gridTemplateColumns: RADAR_COLS[mode].tpl }}>
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(m.symbol)}</span>
          <span className="font-mono text-[10.5px] text-ink-mute">/USDT</span>
          {/* v1.6.9（#5）：late 时 stage 仍是 ACCUMULATION/IGNITION（状态机键不改），
              若沿用 STAGE_META[stage].cls 会出现「绿标 + 追高语义」的自相矛盾。
              这里按 late 单独渲染金标，文案走 i18n（后端 stage_label 是中文字面量，
              直接用它会让英文界面串中文）。 */}
          {m.late ? (
            <span className="pill pill-gold text-[10.5px]" title={t("markets.stageStartedTip")}>
              {t("markets.stageStarted")}
            </span>
          ) : m.stage_label ? (
            <span className={`pill ${stageCls} text-[10.5px]`} title={m.tag}>{m.stage_label}</span>
          ) : (
            <span className={`pill ${m.side === "LONG" ? "pill-green" : m.side === "WATCH_SHORT" ? "pill-red" : "pill-dim"} text-[10.5px]`} title={m.tag}>{m.tag}</span>
          )}
          <span className="pill pill-dim text-[10.5px]" title={t("markets.scoreTitle")}>{t("markets.scorePrefix")}{m.score}</span>
          {m.cooldown && <span className="pill pill-dim text-[10.5px]" title={t("markets.cooldownTitle")}>{t("markets.cooldown")}</span>}
          {!!m.manip?.length && (
            <span className="pill pill-red text-[10.5px]" title={m.manip_note || m.manip.join(" / ")}>
              控盘 {m.manip.join("/")}
            </span>
          )}
          {/* v1.6.9（#6）：控盘指纹为空 ≠ 干净。确认层只覆盖前 24 个候选，
              第 24 名之后 oi_usd=0 → 「换手畸高」分母为 0 永不成立 → 系统性漏报。
              这里显式标「控盘未测」，让用户知道该点「分析」拿准确结论。 */}
          {manipUnmeasured && (
            <span className="pill pill-dim text-[10.5px]" title={t("markets.manipUnmeasuredTip")}>
              {t("markets.manipUnmeasured")}
            </span>
          )}
          {/* v1.6.9（D1）：爆仓流未接入 —— 面板级横幅之外，行级也给一个可悬浮的锚点 */}
          {m.liq_available === false && (
            <span className="pill pill-dim text-[10.5px]" title={t("markets.liqUnavailableTip")}>
              {t("markets.liqUnavailable")}
            </span>
          )}
          {isIgn && m.floor_rising && <span className="pill pill-dim text-[10.5px]" title={t("markets.floorRisingTitle")}>{t("markets.floorRising")}</span>}
        </div>
        <div className="font-mono text-[11px] text-ink-mute truncate mt-0.5" title={rowTip}>
          {bits.length ? bits.join(" · ") : m.note}
        </div>
      </div>
      <div className="font-mono tabular text-ink text-[13px]">{fmtPrice(m.price)}</div>

      {isIgn ? (
        <>
          <div className={`font-mono tabular text-[13px] ${(m.change3d_pct ?? 0) >= 0 ? "up" : "down"}`}>{m.change3d_pct! >= 0 ? "+" : ""}{m.change3d_pct!.toFixed(1)}%</div>
          <div className={`font-mono tabular text-[13px] ${(m.change30d_pct ?? 0) >= 0 ? "up" : "down"}`}>{m.change30d_pct! >= 0 ? "+" : ""}{m.change30d_pct!.toFixed(0)}%</div>
          <div className="font-mono tabular text-ink-dim text-[13px]">
            {t("markets.positionPrefix")}{m.position_pct?.toFixed(0)}%{m.position_pct! <= 30 ? t("markets.posLow") : m.position_pct! <= 45 ? t("markets.posMidLow") : t("markets.posMid")}
          </div>
        </>
      ) : (
        <>
          <div className={`font-mono tabular text-[13px] ${(m.change7d_pct ?? 0) >= 0 ? "up" : "down"}`}>{m.change7d_pct! >= 0 ? "+" : ""}{m.change7d_pct!.toFixed(1)}%</div>
          <div className={`font-mono tabular text-[13px] ${(m.change30d_pct ?? 0) >= 0 ? "up" : "down"}`}>{m.change30d_pct! >= 0 ? "+" : ""}{m.change30d_pct!.toFixed(0)}%</div>
          <div className={`font-mono tabular text-[13px] ${(m.drawdown_pct ?? 0) < 0 ? "down" : "text-ink-dim"}`}>{m.drawdown_pct!.toFixed(1)}%</div>
          <div className="font-mono tabular text-ink-dim text-[13px]">{fmtVol(m.quote_volume)}</div>
        </>
      )}

      <div className="font-mono tabular text-[13px] text-ink">{m.vol_ratio >= 1 ? "+" : ""}{m.vol_ratio.toFixed(1)}x</div>
      <div><span className={`pill ${side.cls} text-[11.5px]`}>{t(side.label)}</span></div>
      <div className="flex flex-wrap items-center gap-1.5">
        <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(m.symbol); }}
          className="btn-ghost text-[12px] py-1" title={t("markets.analyze")}><I.Search size={10} /> {t("markets.analyze")}</button>
        {m.side === "LONG" && (
          <>
            <button onClick={(e) => { e.stopPropagation(); onOrder?.(m.symbol, "spot-long"); }}
              className="btn-ghost text-[12px] py-1 border-green/40 text-green hover:border-green"><I.Check size={10} /> {t("markets.spotBuy")}</button>
            <button onClick={(e) => { e.stopPropagation(); onOrder?.(m.symbol, "futures-long"); }}
              className="btn-ghost text-[12px] py-1"><I.Bolt size={10} /> {t("markets.futuresLong")}</button>
          </>
        )}
        {m.side === "WATCH_SHORT" && (
          <button onClick={(e) => { e.stopPropagation(); onOrder?.(m.symbol, "futures-short"); }}
            className="btn-ghost text-[12px] py-1 border-red/40 text-red hover:border-red"><I.Bolt size={10} /> {t("markets.futuresShort")}</button>
        )}
        {m.side === "WATCH" && (
          <button onClick={(e) => { e.stopPropagation(); onTrade?.(m.symbol); }}
            className="btn-ghost text-[12px] py-1"><I.Search size={10} /> {t("markets.view")}</button>
        )}
      </div>
    </div>
  );
});

/* ---------------- 妖币追踪行（v1.5.2：启动前发现 → 结局验证） ---------------- */

export const TrackLine = memo(function TrackLine({ r, variant, onDetail }: {
  r: TrackRow;
  variant: "pending" | "history";
  onDetail?: (symbol: string, base?: any) => void;
}) {
  const t = useT();
  const isP = variant === "pending";
  const stageCls = STAGE_META[r.stage]?.cls ?? "pill-dim";
  const oc = OUTCOME_META[r.outcome] ?? null;
  const gain = r.max_gain_pct ?? 0;
  const drop = r.max_drop_pct ?? 0;
  const isShort = r.direction === "SHORT";
  // v1.5.8：失败复盘展开（dump 行）
  const [revOpen, setRevOpen] = useState(false);
  // 当前涨跌幅：pending=现价 vs 发现价；history=结局价 vs 发现价
  const refPx = r.found_price || 0;
  const curPx = isP ? (r.last_price || 0) : (r.outcome_price || r.last_price || 0);
  const curChg = refPx > 0 && curPx > 0 ? (curPx / refPx - 1) * 100 : null;
  return (
    <div onClick={() => onDetail?.(r.symbol, r)} title={t("markets.detail.open")}
      className={`grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 transition-colors cursor-pointer group
        ${isP
          ? (isShort ? "bg-red/5 hover:bg-red/10" : "bg-green/5 hover:bg-green/10")
          : r.outcome === "moon" ? "bg-green/5 hover:bg-green/10"
            : r.outcome === "dump" ? "bg-red/5 hover:bg-red/10" : "hover:bg-elevated/40"}`}
      style={{ gridTemplateColumns: TRACK_COLS[variant].tpl }}>
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(r.symbol)}</span>
          <span className="font-mono text-[10.5px] text-ink-mute">/USDT</span>
          {/* 方向：做多/做空（做多跌太多会判 dump=失败；做空跌了反而是兑现） */}
          <span className={`pill ${isShort ? "pill-red" : "pill-green"} text-[10.5px]`}>
            {isShort ? t("markets.short") : t("markets.long")}
          </span>
          <span className={`pill ${stageCls} text-[10.5px]`}>{t(`markets.stage.${r.stage}`)}</span>
          {isP && !!r.holding && (
            <span className="pill pill-gold text-[10.5px]" title={t("markets.holdingTip")}>{t("markets.holding")}</span>
          )}
          {/* v1.6.5（OPT-06）疑似假启动：只提示、不自动平仓（VTHO 反例：早期回撤 7.9% → +31.3%） */}
          {isP && !!r.fake_start && (
            <span className="pill pill-red text-[10.5px]"
              title={t("markets.fakeStartTip", { h: String(r.age_h ?? 0) })}>{t("markets.fakeStart")}</span>
          )}
          {!isP && oc && <span className={`pill ${oc.cls} text-[10.5px]`}>{t(oc.label)}</span>}
          {/* 失败复盘：点击展开（阻止冒泡，不触发详情浮层） */}
          {!isP && r.outcome === "dump" && !!r.review && (
            <button onClick={(e) => { e.stopPropagation(); setRevOpen((v) => !v); }}
              className={`pill text-[10.5px] ${revOpen ? "pill-red" : "border border-red/50 text-red hover:bg-red/10"}`}
              title={t("markets.reviewTip")}>
              {t("markets.reviewPill")}
            </button>
          )}
        </div>
        {(r.reasons?.length ?? 0) > 0 && (
          <div className="font-mono text-[11px] text-ink-mute truncate mt-0.5">{r.reasons!.slice(0, 3).join(" · ")}</div>
        )}
      </div>
      <div className="font-mono tabular text-ink text-[13px]">{fmtPrice(r.found_price)}</div>
      <div className="font-mono tabular text-ink text-[13px]">{curPx ? fmtPrice(curPx) : "—"}</div>
      {isP ? (
        <div className={`font-mono tabular text-[13px] font-medium ${curChg == null ? "text-ink-mute" : curChg >= 0 ? "up" : "down"}`}>
          {curChg == null ? "—" : `${curChg >= 0 ? "+" : ""}${curChg.toFixed(1)}%`}
        </div>
      ) : (
        <div className="font-mono tabular text-ink-dim text-[12.5px]" title={new Date((r.closed_at || r.updated_at) * 1000).toLocaleString()}>
          {fmtCloseDate(r.closed_at || r.updated_at)}
        </div>
      )}
      <div className={`font-mono tabular text-[13px] ${gain > 0 ? "up" : "text-ink-mute"}`}>+{gain.toFixed(1)}%</div>
      <div className={`font-mono tabular text-[13px] ${drop > 0 ? "down" : "text-ink-mute"}`}>-{drop.toFixed(1)}%</div>
      {/* 仓位模拟：100U 本金 × 10x 合约（爆仓封底 -100U） */}
      {(() => {
        const roi = trackPnl(r.direction, refPx, curPx);
        return (
          <div className={`font-mono tabular text-[13px] font-medium ${roi == null ? "text-ink-mute" : roi >= 0 ? "up" : "down"}`}
            title={t("markets.trackPnlTip")}>
            {roi == null ? "—" : `${roi >= 0 ? "+$" : "-$"}${Math.abs(roi).toFixed(0)}`}
          </div>
        );
      })()}
      <div className="font-mono text-ink-dim text-[12.5px]">
        {isP ? fmtAgo(r.found_at) : fmtAgo(r.closed_at || r.updated_at, r.found_at)}
      </div>
      {revOpen && !!r.review && (
        <div onClick={(e) => e.stopPropagation()} style={{ gridColumn: "1 / -1" }}
          className="whitespace-pre-wrap rounded-md border border-red/30 bg-red/5 px-3 py-2 mt-1 font-mono text-[11.5px] leading-relaxed text-ink-dim">
          <div className="text-red font-semibold mb-1">{t("markets.reviewPill")}</div>
          {r.review}
        </div>
      )}
    </div>
  );
});

/* ---------------- 智能异动信号行 ---------------- */

export const SignalRow = memo(function SignalRow({ r, onDetail, onAnalyze }: {
  r: Signal;
  onDetail?: (symbol: string, base?: any) => void;
  onAnalyze?: (symbol: string) => void;
}) {
  const t = useT();
  const up = r.change_pct >= 0;
  return (
    <div onClick={() => onDetail?.(r.symbol, r)} title={t("markets.detail.open")}
      className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
      style={{ gridTemplateColumns: "200px 100px 95px 105px 100px 90px minmax(150px,1fr) 70px", minWidth: 950, gap: 12 }}>
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(r.symbol)}</span>
        <span className="font-mono text-[11.5px] text-ink-mute">/USDT</span>
        <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(r.symbol); }}
          className="btn-ghost px-1.5 py-0.5 text-[11px]" title={t("markets.analyze")}><I.Search size={9} /> {t("markets.analyze")}</button>
      </div>
      <div className="font-mono tabular text-ink text-[13.5px]">{fmtPrice(r.price)}</div>
      <div className={`font-mono tabular text-[13.5px] ${up ? "up" : "down"}`}>
        {up ? "+" : ""}{r.change_pct.toFixed(2)}%
      </div>
      <div className="font-mono tabular text-ink-dim text-[13px]">{fmtVol(r.volume)}</div>
      <div className={`font-mono tabular text-[13px] ${(r.funding_rate ?? 0) >= 0 ? "text-green" : "text-red"}`}>{fmtRate(r.funding_rate)}</div>
      <div><span className={`pill ${up ? "pill-green" : "pill-red"}`}>{r.direction ?? (up ? t("markets.long") : t("markets.short"))}</span></div>
      <div className="font-mono text-[12.5px] text-ink-dim truncate" title={r.reason}>{r.reason ?? "—"}</div>
      <div className="font-mono tabular text-gold text-[13.5px]">{(r.score ?? 0).toFixed(1)}</div>
    </div>
  );
});
