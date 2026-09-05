import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";

/* 币安广场 —— Agent 发文台账
 *
 * 广场 OpenAPI 官方只开放发帖（/content/add），不开放读取 → 页面不拉任何广场数据，
 * 展示的是本机每次真实发布后落下的本地台账（data/square_posts.json）：
 *   - Agent 在聊天里用 square-post 技能发布成功/失败 → via=agent（自动记账）
 *   - Web3Skills 页手动「运行」square-post → via=manual
 * 帖子真实链接为 https://www.binance.com/square/post/{id}，可跳转原帖。
 * 数据来自 GET /api/square/posts。
 */

type Post = {
  id: string; ts: number; kind: string; title: string; text: string;
  tags: string[]; media?: string; post_id?: string; share_url?: string;
  status: "posted" | "failed"; error?: string; via?: "agent" | "manual";
};
type Stats = { total: number; posted: number; failed: number; today: number; week: number; limit_per_day: number };
type KeyInfo = { present: boolean; masked: string; source?: string };

const KIND_META: Record<string, { label: string; glyph: string; tip: string }> = {
  text: { label: "短文", glyph: "✎", tip: "纯文本快讯" },
  article: { label: "文章", glyph: "▤", tip: "带标题的长文" },
  image: { label: "图文", glyph: "◫", tip: "图片 + 说明" },
  video: { label: "视频", glyph: "▶", tip: "视频帖" },
};

function fmtTs(ts: number | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function rel(ts: number): string {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return `${s}s 前`;
  if (s < 3600) return `${Math.floor(s / 60)}m 前`;
  if (s < 86400) return `${Math.floor(s / 3600)}h 前`;
  return `${Math.floor(s / 86400)}d 前`;
}

export function SquarePostView() {
  const [data, setData] = useState<{ posts: Post[]; stats: Stats; key: KeyInfo } | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState<"all" | "posted" | "failed">("all");

  // KEY_VAULT —— OpenAPI Key 填入
  const [squareKey, setSquareKey] = useState("");
  const [showSquareKey, setShowSquareKey] = useState(false);
  const [squareBusy, setSquareBusy] = useState(false);
  const [squareErr, setSquareErr] = useState("");
  const [squareOk, setSquareOk] = useState("");

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const r: any = await api.squarePosts();
      if (r?.ok === false) { setErr(r?.error || "读取台账失败"); return; }
      setErr("");
      setData({ posts: r?.posts ?? [], stats: r?.stats ?? {}, key: r?.key ?? { present: false, masked: "" } });
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => { load(); const t = setInterval(() => load(true), 15000); return () => clearInterval(t); }, [load]);

  const connectSquare = async () => {
    if (!squareKey.trim()) { setSquareErr("OpenAPI Key 不能为空。"); return; }
    setSquareBusy(true); setSquareErr(""); setSquareOk("");
    try {
      const r: any = await api.squareConnect(squareKey.trim());
      if (r?.ok === false) { setSquareErr(r?.error || "保存失败"); return; }
      const backupNote = r?.key?.backed_up_to
        ? ` · 旧 Key 已备份到 ${r.key.backed_up_to}`
        : "";
      setSquareOk((r?.note ? `已保存 · ${r.note}` : "已保存到 ~/.config/binance-square/openapi-key") + backupNote);
      setSquareKey("");
      setShowSquareKey(false);
      await load(true);
    } catch (e: any) {
      setSquareErr(e?.message || String(e));
    } finally {
      setSquareBusy(false);
    }
  };

  const disconnectSquare = async () => {
    setSquareBusy(true); setSquareErr(""); setSquareOk("");
    try {
      const r: any = await api.squareDisconnect();
      if (r?.ok === false) { setSquareErr(r?.error || "清理失败"); return; }
      const backupNote = r?.key?.backed_up_to
        ? ` · 旧 Key 已备份到 ${r.key.backed_up_to}（可手动恢复）`
        : "";
      setSquareOk((r?.note || "已清理本地密钥。") + backupNote);
      await load(true);
    } catch (e: any) {
      setSquareErr(e?.message || String(e));
    } finally {
      setSquareBusy(false);
    }
  };

  const stats = useMemo<Stats>(() => data?.stats ?? { total: 0, posted: 0, failed: 0, today: 0, week: 0, limit_per_day: 100 }, [data]);
  const key = data?.key ?? { present: false, masked: "" };
  const shown = useMemo(() => data?.posts.filter((p) => filter === "all" || p.status === filter) ?? [], [data, filter]);
  const posts = data?.posts ?? [];

  return (
    <div className="p-5 space-y-4">
      {/* Header */}
      <div className="glass p-4 flex items-center gap-3 flex-wrap" style={{ borderRadius: 12 }}>
        <I.Megaphone className="text-gold" size={22} />
        <div className="leading-tight">
          <div className="font-mono text-[15px] font-bold text-ink tracking-wide">广场 · Agent 发文台账</div>
          <div className="font-mono text-[10px] text-ink-dim tracking-[0.12em] mt-0.5">SQUARE · AGENT PUBLISH LEDGER · 本机发布即记账</div>
        </div>
        {key.present ? (
          <span className="pill pill-green"><span className="dot dot-green live" /> OpenAPI KEY {key.masked}</span>
        ) : (
          <span className="pill pill-red"><I.Key size={11} /> 未配置 OpenAPI Key</span>
        )}
        <span className={`pill ${err ? "pill-red" : "pill-dim"} ml-auto`}>
          {err ? <span className="text-red">{err}</span> : posts.length > 0 ? "每 15 秒自动刷新" : "台账为空"}
        </span>
        <button onClick={() => load(false)} disabled={loading} className="btn-ghost">
          <I.Refresh size={12} className={loading ? "animate-spin" : ""} /> 刷新
        </button>
      </div>

      {/* KEY_VAULT —— Square OpenAPI Key 填入 / 状态 / 断开 */}
      <div className="glass p-3.5 space-y-2.5">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="pill pill-gold">SQUARE_OPENAPI_VAULT</span>
          <span className="font-mono text-[10px] text-ink-dim">[X-Square-OpenAPI-Key · ~/.config/binance-square/openapi-key · 0600]</span>
          {key.present ? (
            <>
              <span className="pill pill-green"><span className="dot dot-green live" /> 已连接 Square OpenAPI</span>
              <span className="pill pill-dim font-mono">KEY {key.masked}</span>
              <span className="font-mono text-[10px] text-ink-mute">来源 · {key.source}</span>
              <div className="ml-auto flex items-center gap-2 font-mono text-[10px]">
                <button onClick={disconnectSquare} disabled={squareBusy} className="btn-ghost text-[11px] py-1 text-ink-mute hover:text-red">
                  {squareBusy ? <I.Refresh size={10} className="animate-spin" /> : <I.X size={10} />} 断开（删除本地密钥）
                </button>
              </div>
            </>
          ) : (
            <>
              <span className="pill pill-red">未配置 OpenAPI Key</span>
              <span className="pill pill-dim">填入后将自动启用真实发文通道（Agent 可调用 square-post 技能）</span>
            </>
          )}
        </div>

        {!key.present && (
          <div className="rounded-md border border-gold/30 bg-gold/[0.04] p-3 space-y-2">
            <div className="font-mono text-[11px] text-ink-dim flex items-center gap-2">
              <I.Key size={12} className="text-gold" /> 填入 Square OpenAPI Key —— 保存到 <code className="text-gold">~/.config/binance-square/openapi-key</code>（0600 权限，与官方 save-key.mjs 一致），Agent 发布广场内容后会自动落账到这里。
            </div>
            <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-2">
              <div className="relative">
                <input type={showSquareKey ? "text" : "password"} value={squareKey}
                  onChange={(e) => setSquareKey(e.target.value)}
                  placeholder="Square OpenAPI Key（来自 binance.com → 创作者中心 → OpenAPI）"
                  className="field font-mono text-[12px] pr-14" autoComplete="off" spellCheck={false} />
                <button type="button" onClick={() => setShowSquareKey((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] font-mono text-ink-mute hover:text-gold">
                  {showSquareKey ? "隐藏" : "显示"}
                </button>
              </div>
              <button onClick={connectSquare} disabled={squareBusy || !squareKey.trim()} className="btn-gold">
                {squareBusy ? <I.Refresh size={12} className="animate-spin" /> : <I.Key size={12} />} 保存并启用
              </button>
            </div>
            {squareErr && (
              <div className="rounded-md border border-red/30 bg-red/5 px-3 py-2 font-mono text-[11px] text-red leading-relaxed">
                {squareErr}
              </div>
            )}
            {squareOk && (
              <div className="rounded-md border border-green/40 bg-green/5 px-3 py-2 font-mono text-[11px] text-green leading-relaxed">{squareOk}</div>
            )}
            <div className="font-mono text-[10px] text-ink-mute leading-relaxed">
              ⚠ 密钥仅保存本机（OpenAPI 官方要求 <code className="text-gold">0600</code> 文件权限，详见 <code className="text-gold">save-key.mjs</code>）。如要撤销，填完保存后到顶部「断开」按钮删除即可（环境变量 <code className="text-gold">BINANCE_SQUARE_OPENAPI_KEY</code> 优先级高于本地文件）。
            </div>
          </div>
        )}

        {key.present && (squareErr || squareOk) && (
          <div className={squareErr ? "rounded-md border border-red/30 bg-red/5 px-3 py-2 font-mono text-[11px] text-red" : "rounded-md border border-green/40 bg-green/5 px-3 py-2 font-mono text-[11px] text-green"}>
            {squareErr || squareOk}
          </div>
        )}
      </div>

      {/* KPI —— 只读台账统计 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="累计记录" value={String(stats.total)} sub="本机发布流水" gold />
        <Stat label="今日已发" value={`${stats.today} / ${stats.limit_per_day}`} sub="OpenAPI 每日上限" />
        <Stat label="发布成功" value={String(stats.posted)} sub="已在广场可见" green />
        <Stat label="发布失败" value={String(stats.failed)} sub="可在下方查看原因" red={stats.failed > 0} />
      </div>

      {/* 说明卡 —— 数据口径 */}
      <div className="rounded-md border border-line bg-elevated/20 px-4 py-3 flex items-start gap-3" style={{ borderRadius: 10 }}>
        <I.Shield size={14} className="text-gold shrink-0 mt-0.5" />
        <div className="font-mono text-[11px] text-ink-dim leading-relaxed">
          广场 OpenAPI 官方<b className="text-ink">只发不读</b>，因此本页不拉取广场数据，只展示<b className="text-ink">本机经 square-post 真实发布后的记录</b>：
          在聊天里让 Agent 把内容发布到币安广场（成功或失败都会自动落账），发成功的帖子可通过「原帖」跳到 <span className="text-gold">binance.com/square/post/{'{id}'}</span> 查看。
          手动测试可到「技能」页对 square-post 点运行。
        </div>
      </div>

      {/* 过滤 */}
      <div className="glass p-2 flex items-center gap-1.5 flex-wrap" style={{ borderRadius: 12 }}>
        {([["all", `全部 ${stats.total}`], ["posted", `已发布 ${stats.posted}`], ["failed", `失败 ${stats.failed}`]] as const).map(([k, label]) => (
          <button key={k} onClick={() => setFilter(k)}
            className={`px-3.5 py-1.5 rounded-md font-mono text-[12px] transition-colors
              ${filter === k ? "bg-gold text-canvas" : "text-ink-dim hover:bg-card/60 border border-transparent"}`}>
            {label}
          </button>
        ))}
        <span className="prefix ml-auto">本机记录 · 非广场实时数据</span>
      </div>

      {/* Feed */}
      {err ? (
        <div className="glass px-5 py-8 text-center" style={{ borderRadius: 12 }}>
          <div className="font-mono text-[13px] text-red">台账读取失败：{err}</div>
          <div className="mt-1 font-mono text-[11px] text-ink-mute">请确认后端 desktop_app 已启动，并稍后重试。</div>
        </div>
      ) : loading && !data ? (
        <div className="glass px-5 py-10 text-center" style={{ borderRadius: 12 }}>
          <span className="inline-block w-4 h-4 border-2 border-gold/30 border-t-gold rounded-full animate-spin" />
          <div className="mt-2 font-mono text-[12px] text-ink-mute">读取发文台账…</div>
        </div>
      ) : shown.length === 0 ? (
        <div className="glass px-5 py-10 text-center space-y-2" style={{ borderRadius: 12 }}>
          <I.Megaphone className="text-ink-mute mx-auto" size={26} />
          <div className="font-mono text-[13px] text-ink">还没有{filter === "all" ? "" : (filter === "posted" ? "已发布" : "失败")}记录</div>
          <div className="max-w-[520px] mx-auto font-mono text-[11px] text-ink-mute leading-relaxed">
            {key.present
              ? <>在聊天中对 Agent 说「把这条发到币安广场」即可真实发布并自动记账，稍后会自动出现在这里。</>
              : <>尚未配置 Square OpenAPI Key：先设置环境变量 <span className="text-gold">BINANCE_SQUARE_OPENAPI_KEY</span> 或用技能页保存 key，Agent 发布后才会记账。</>}
          </div>
        </div>
      ) : (
        <div className="space-y-2.5">
          {shown.map((p, i) => <PostCard key={p.id || `${p.ts}-${i}`} p={p} />)}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, sub, gold, green, red }: { label: string; value: string; sub: string; gold?: boolean; green?: boolean; red?: boolean }) {
  const color = gold ? "text-gold" : green ? "text-green" : red ? "text-red" : "text-ink";
  return (
    <div className="glass px-4 py-3" style={{ borderRadius: 12 }}>
      <div className="prefix">{label}</div>
      <div className={`mt-1 font-mono text-[19px] font-bold tabular ${color}`}>{value}</div>
      <div className="mt-0.5 font-mono text-[10px] text-ink-mute">{sub}</div>
    </div>
  );
}

function PostCard({ p }: { p: Post }) {
  const meta = KIND_META[p.kind] || { label: p.kind || "帖", glyph: "•", tip: "" };
  const ok = p.status === "posted";
  const share = p.share_url || (p.post_id ? `https://www.binance.com/square/post/${p.post_id}` : "");
  return (
    <div className="glass p-3.5 space-y-2" style={{ borderRadius: 12 }}>
      {/* head */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="pill pill-dim text-[10px] font-mono" title={meta.tip}>
          <span className="text-gold mr-1">{meta.glyph}</span>{meta.label}
        </span>
        {ok ? (
          <span className="pill pill-green text-[10px]"><span className="dot dot-green live" /> 已发布</span>
        ) : (
          <span className="pill pill-red text-[10px]"><I.X size={9} /> 失败</span>
        )}
        {p.via && (
          <span className="pill pill-dim text-[10px]">{p.via === "agent" ? "Agent 自动" : "手动运行"}</span>
        )}
        <span className="ml-auto font-mono text-[10px] text-ink-mute" title={fmtTs(p.ts)}>{rel(p.ts)} · {fmtTs(p.ts)}</span>
        {ok && share && (
          <a href={share} target="_blank" rel="noreferrer"
             className="btn-ghost text-[11px] py-1 !text-gold" title="在币安广场打开原帖">
            <I.Link size={11} /> 原帖 ↗
          </a>
        )}
      </div>

      {/* body */}
      {p.title && <div className="font-mono text-[13px] font-bold text-ink">{p.title}</div>}
      {p.text && (
        <div className="font-mono text-[12px] text-ink leading-relaxed whitespace-pre-line break-words line-clamp-4">
          {p.text}
        </div>
      )}
      {!ok && p.error && (
        <div className="rounded-md border border-red/25 bg-red/5 px-2.5 py-1.5 font-mono text-[10.5px] text-red/90 break-all leading-relaxed">
          {p.error}
        </div>
      )}

      {/* foot */}
      {(p.tags && p.tags.length > 0) && (
        <div className="flex flex-wrap gap-1.5">
          {p.tags.map((t, i) => <span key={i} className="pill pill-dim text-[10px]">#{t}</span>)}
        </div>
      )}
      {ok && !p.post_id && !p.share_url && (
        <div className="font-mono text-[10px] text-ink-mute">发布成功，但未返回帖子 ID（可去广场创作者后台确认）。</div>
      )}
    </div>
  );
}
