import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";
import { confirmDialog } from "../components/ConfirmDialog";
import { useT } from "../i18n/i18n";

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

function rel(ts: number): { n: number; u: string } {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return { n: s, u: "s" };
  if (s < 3600) return { n: Math.floor(s / 60), u: "m" };
  if (s < 86400) return { n: Math.floor(s / 3600), u: "h" };
  return { n: Math.floor(s / 86400), u: "d" };
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
      if (r?.ok === false) { setErr(r?.error || t("square.readLedgerFail")); return; }
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
    if (!squareKey.trim()) { setSquareErr(t("square.keyEmpty")); return; }
    setSquareBusy(true); setSquareErr(""); setSquareOk("");
    try {
      const r: any = await api.squareConnect(squareKey.trim());
      if (r?.ok === false) { setSquareErr(r?.error || t("square.saveFail")); return; }
      const backupNote = r?.key?.backed_up_to
        ? ` · ${t("square.keyBackedUp", { path: r.key.backed_up_to })}`
        : "";
      setSquareOk((r?.note ? `${t("square.saved")} · ${r.note}` : t("square.savedToPath")) + backupNote);
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
      if (r?.ok === false) { setSquareErr(r?.error || t("square.clearFail")); return; }
      const backupNote = r?.key?.backed_up_to
        ? ` · ${t("square.keyBackedUpRecover", { path: r.key.backed_up_to })}`
        : "";
      setSquareOk((r?.note || t("square.clearedLocal")) + backupNote);
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
  const t = useT();

  // v1.5.35：删除单条失败文章 / 清空所有失败 —— 仅本地台账，不会调用币安 API
  const delPost = useCallback(async (id: string) => {
    if (!id) return;
    if (!await confirmDialog(t("square.delPostTip"), { title: t("square.delPost"), danger: true })) return;
    try {
      const r: any = await api.squarePostsDelete([id]);
      if (r?.ok === false) { setErr(r?.error || t("square.delFail")); return; }
      load(true);
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  }, [t, load]);

  const clearAllFailed = useCallback(async () => {
    const ids = (data?.posts ?? []).filter(p => p.status === "failed" && p.id).map(p => p.id);
    if (ids.length === 0) return;
    if (!await confirmDialog(t("square.clearFailedTip", { n: ids.length }),
      { title: t("square.clearFailed"), danger: true })) return;
    try {
      const r: any = await api.squarePostsDelete(ids);
      if (r?.ok === false) { setErr(r?.error || t("square.delFail")); return; }
      load(true);
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  }, [t, data, load]);

  return (
    <div className="p-5 space-y-4">
      {/* Header */}
      <div className="page-heading flex items-center gap-3 flex-wrap">
        <I.Megaphone className="text-gold" size={22} />
        <div className="leading-tight">
          <div className="font-mono text-[15px] font-bold text-ink tracking-wide">{t("square.title")}</div>
          <div className="font-mono text-[10px] text-ink-dim tracking-[0.12em] mt-0.5">{t("square.subtitle")}</div>
        </div>
        {key.present ? (
          <span className="pill pill-green"><span className="dot dot-green live" /> OpenAPI KEY {key.masked}</span>
        ) : (
          <span className="pill pill-red"><I.Key size={11} /> {t("square.noKey")}</span>
        )}
        <span className={`pill ${err ? "pill-red" : "pill-dim"} ml-auto`}>
          {err ? <span className="text-red">{err}</span> : posts.length > 0 ? t("square.autoRefresh") : t("square.ledgerEmpty")}
        </span>
        <button onClick={() => load(false)} disabled={loading} className="btn-ghost">
          <I.Refresh size={12} className={loading ? "animate-spin" : ""} /> {t("square.refresh")}
        </button>
      </div>

      {/* KEY_VAULT —— Square OpenAPI Key 填入 / 状态 / 断开 */}
      <div className="glass p-3.5 space-y-2.5">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="pill pill-gold">SQUARE_OPENAPI_VAULT</span>
          <span className="font-mono text-[10px] text-ink-dim">[X-Square-OpenAPI-Key · ~/.config/binance-square/openapi-key · 0600]</span>
          {key.present ? (
            <>
              <span className="pill pill-green"><span className="dot dot-green live" /> {t("square.connected")}</span>
              <span className="pill pill-dim font-mono">KEY {key.masked}</span>
              <span className="font-mono text-[10px] text-ink-mute">{t("square.source", { source: key.source ?? "" })}</span>
              <div className="ml-auto flex items-center gap-2 font-mono text-[10px]">
                <button onClick={disconnectSquare} disabled={squareBusy} className="btn-ghost text-[11px] py-1 text-ink-mute hover:text-red">
                  {squareBusy ? <I.Refresh size={10} className="animate-spin" /> : <I.X size={10} />} {t("square.disconnect")}
                </button>
              </div>
            </>
          ) : (
            <>
              <span className="pill pill-red">{t("square.noKey")}</span>
              <span className="pill pill-dim">{t("square.enableHint")}</span>
            </>
          )}
        </div>

        {!key.present && (
          <div className="rounded-md border border-gold/30 bg-gold/[0.04] p-3 space-y-2">
            <div className="font-mono text-[11px] text-ink-dim flex items-center gap-2">
              <I.Key size={12} className="text-gold" /> {t("square.fillKey")}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-2">
              <div className="relative">
                <input type={showSquareKey ? "text" : "password"} value={squareKey}
                  onChange={(e) => setSquareKey(e.target.value)}
                  placeholder={t("square.keyPlaceholder")}
                  className="field font-mono text-[12px] pr-14" autoComplete="off" spellCheck={false} />
                <button type="button" onClick={() => setShowSquareKey((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] font-mono text-ink-mute hover:text-gold">
                  {showSquareKey ? t("square.hide") : t("square.show")}
                </button>
              </div>
              <button onClick={connectSquare} disabled={squareBusy || !squareKey.trim()} className="btn-gold">
                {squareBusy ? <I.Refresh size={12} className="animate-spin" /> : <I.Key size={12} />} {t("square.saveEnable")}
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
              {t("square.keyWarn")}
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
        <Stat label={t("square.statTotal")} value={String(stats.total)} sub={t("square.subTotal")} gold />
        <Stat label={t("square.statToday")} value={`${stats.today} / ${stats.limit_per_day}`} sub={t("square.subToday")} />
        <Stat label={t("square.statPosted")} value={String(stats.posted)} sub={t("square.subPosted")} green />
        <Stat label={t("square.statFailed")} value={String(stats.failed)} sub={t("square.subFailed")} red={stats.failed > 0} />
      </div>

      {/* 说明卡 —— 数据口径 */}
      <div className="rounded-md border border-line bg-elevated/20 px-4 py-3 flex items-start gap-3" style={{ borderRadius: 10 }}>
        <I.Shield size={14} className="text-gold shrink-0 mt-0.5" />
        <div className="font-mono text-[11px] text-ink-dim leading-relaxed">
          {t("square.dataNote")}
        </div>
      </div>

      {/* 过滤 */}
      <div className="glass p-2 flex items-center gap-1.5 flex-wrap" style={{ borderRadius: 12 }}>
        {([["all", t("square.filterAll", { n: stats.total })], ["posted", t("square.filterPosted", { n: stats.posted })], ["failed", t("square.filterFailed", { n: stats.failed })]] as const).map(([k, label]) => (
          <button key={k} onClick={() => setFilter(k)}
            className={`px-3.5 py-1.5 rounded-md font-mono text-[12px] transition-colors
              ${filter === k ? "bg-gold text-canvas" : "text-ink-dim hover:bg-card/60 border border-transparent"}`}>
            {label}
          </button>
        ))}
        {filter === "failed" && stats.failed > 0 ? (
          <button onClick={clearAllFailed}
            className="ml-auto btn-ghost text-[11px] py-1 text-red/80 hover:text-red"
            title={t("square.clearFailedTip", { n: stats.failed })}>
            <I.Trash size={11} /> {t("square.clearFailed")}
          </button>
        ) : (
          <span className="prefix ml-auto">{t("square.localNote")}</span>
        )}
      </div>

      {/* Feed */}
      {err ? (
        <div className="glass px-5 py-8 text-center" style={{ borderRadius: 12 }}>
          <div className="font-mono text-[13px] text-red">{t("square.readFail", { err })}</div>
          <div className="mt-1 font-mono text-[11px] text-ink-mute">{t("square.retryHint")}</div>
        </div>
      ) : loading && !data ? (
        <div className="glass px-5 py-10 text-center" style={{ borderRadius: 12 }}>
          <span className="inline-block w-4 h-4 border-2 border-gold/30 border-t-gold rounded-full animate-spin" />
          <div className="mt-2 font-mono text-[12px] text-ink-mute">{t("square.loadingLedger")}</div>
        </div>
      ) : shown.length === 0 ? (
        <div className="glass px-5 py-10 text-center space-y-2" style={{ borderRadius: 12 }}>
          <I.Megaphone className="text-ink-mute mx-auto" size={26} />
          <div className="font-mono text-[13px] text-ink">{t("square.noRecords", { kind: filter === "all" ? "" : (filter === "posted" ? t("square.posted") : t("square.failed")) })}</div>
          <div className="max-w-[520px] mx-auto font-mono text-[11px] text-ink-mute leading-relaxed">
            {key.present
              ? <>{t("square.emptyHintKey")}</>
              : <>{t("square.emptyHintNoKey")}</>}
          </div>
        </div>
      ) : (
        <div className="space-y-2.5">
          {shown.map((p, i) => <PostCard key={p.id || `${p.ts}-${i}`} p={p} onDelete={delPost} />)}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, sub, gold, green, red }: { label: string; value: string; sub: string; gold?: boolean; green?: boolean; red?: boolean }) {
  const color = gold ? "text-gold" : green ? "text-green" : red ? "text-red" : "text-ink";
  return (
    <div className="square-stat glass">
      <div className="prefix">{label}</div>
      <div className={`mt-1 font-mono text-[19px] font-bold tabular ${color}`}>{value}</div>
      <div className="mt-0.5 font-mono text-[10px] text-ink-mute">{sub}</div>
    </div>
  );
}

function PostCard({ p, onDelete }: { p: Post; onDelete?: (id: string) => void }) {
  const t = useT();
  const meta = KIND_META[p.kind] || { label: p.kind || "帖", glyph: "•", tip: "" };
  const ok = p.status === "posted";
  const share = p.share_url || (p.post_id ? `https://www.binance.com/square/post/${p.post_id}` : "");
  const relTime = rel(p.ts);
  return (
    <div className="square-post glass space-y-2">
      {/* head */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="pill pill-dim text-[10px] font-mono" title={meta.tip}>
          <span className="text-gold mr-1">{meta.glyph}</span>{meta.label}
        </span>
        {ok ? (
          <span className="pill pill-green text-[10px]"><span className="dot dot-green live" /> {t("square.posted")}</span>
        ) : (
          <span className="pill pill-red text-[10px]"><I.X size={9} /> {t("square.failed")}</span>
        )}
        {p.via && (
          <span className="pill pill-dim text-[10px]">{p.via === "agent" ? t("square.viaAgent") : t("square.viaManual")}</span>
        )}
        <span className="ml-auto font-mono text-[10px] text-ink-mute" title={fmtTs(p.ts)}>{t("square.ago", { n: relTime.n, u: relTime.u })} · {fmtTs(p.ts)}</span>
        {ok && share && (
          <a href={share} target="_blank" rel="noreferrer"
             className="btn-ghost text-[11px] py-1 !text-gold" title={t("square.openOriginal")}>
            <I.Link size={11} /> {t("square.originalPost")}
          </a>
        )}
        {!ok && onDelete && p.id && (
          <button onClick={() => onDelete(p.id)}
            className="btn-ghost text-[11px] py-1 text-red/80 hover:text-red" title={t("square.delPost")}>
            <I.Trash size={11} /> {t("square.delPost")}
          </button>
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
        <div className="font-mono text-[10px] text-ink-mute">{t("square.noPostId")}</div>
      )}
    </div>
  );
}
