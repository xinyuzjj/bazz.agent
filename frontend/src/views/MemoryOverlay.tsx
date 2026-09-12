import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";
import { useI18n } from "../i18n/i18n";
import { confirmDialog } from "../components/ConfirmDialog";

/* Hermes 跨会话长期记忆与偏好中枢 —— 真实数据驱动版
 *
 * 数据来源：后端 SQLite `memory` 表（key/value/updated_at），通过
 *   GET /api/memory          列出全部条目
 *   GET /api/memory/stats    分类、文件大小、最近更新、Top N
 *   POST /api/memory         新增 / 覆盖
 *   DELETE /api/memory/{key} 删除
 *   GET /api/memory/export   导出 JSON
 *
 * 存储约定：key 用点分前缀表达分类（pref.* = 偏好，alloc.* = 资产分配，
 * habit.* = 操作习惯，fact.* = 事实，bot.* = Agent 档案）。任意键都可手动
 * 添加或删除；聊天里 Agent 也会自动写入。
 */

type MemRow = { key: string; value: string; updated_at: number; kind?: string; source?: string; hits?: number };
type Stats = {
  ok: boolean;
  total: number;
  categories: { name: string; count: number }[];
  recent: { key: string; value: string; updated_at: number }[];
  last_updated_at: number;
  db_path: string;
  db_size_bytes: number;
  engine: string;
};

// v1.4.1：优先用后端 kind 字段（pref/fact/event）分组；旧数据回退 key 前缀
function classify(r: MemRow): string {
  if (r.kind) return r.kind;
  return (r.key.split(".", 1)[0] || "misc").trim() || "misc";
}

// 已知 key 前缀 → i18n key；未知返回原名（不吞用户自定义前缀）
const CAT_I18N: Record<string, string> = {
  pref: "mem.cat.pref",
  alloc: "mem.cat.alloc",
  habit: "mem.cat.habit",
  fact: "mem.cat.fact",
  bot: "mem.cat.bot",
  context: "mem.cat.context",
  user: "mem.cat.user",
  trading: "mem.cat.trading",
  risk: "mem.cat.risk",
};
function catLabel(t: (k: string) => string, name: string): string {
  const key = CAT_I18N[name];
  return key ? t(key) : name;
}

function fmtBytes(n: number): string {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(2)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function relTime(ts: number, isEn: boolean): string {
  const s = Math.floor((Date.now() - ts) / 1000);
  const suf = isEn ? " ago" : " 前";
  if (s < 60) return `${s}s${suf}`;
  if (s < 3600) return `${Math.floor(s / 60)}m${suf}`;
  if (s < 86400) return `${Math.floor(s / 3600)}h${suf}`;
  return `${Math.floor(s / 86400)}d${suf}`;
}

function fmtTs(ts: number | undefined, locale: "zh" | "en"): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(locale === "en" ? "en-US" : "zh-CN", { hour12: false });
}

export function MemoryOverlay({ open, full, onClose }: { open: boolean; full?: boolean; onClose?: () => void }) {
  if (!open) return null;

  if (full) {
    return (
      <div className="p-5">
        <div className="memory-page glass overflow-hidden">
          <MemoryBody onClose={onClose} />
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/65 backdrop-blur-md flex items-center justify-center p-6" onClick={onClose}>
      <div className="glass-bright w-full max-w-6xl max-h-[88vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <MemoryBody onClose={onClose} />
      </div>
    </div>
  );
}

function MemoryBody({ onClose }: { onClose?: () => void }) {
  const { t, locale } = useI18n();
  const isEn = locale === "en";
  const [items, setItems] = useState<MemRow[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [k, setK] = useState("");
  const [v, setV] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [a, b] = await Promise.allSettled([api.memory(), api.memoryStats()]);
      const arr: MemRow[] = a.status === "fulfilled" ? (Array.isArray(a.value) ? a.value : (a.value?.items ?? [])) : [];
      setItems(arr);
      if (b.status === "fulfilled") setStats(b.value as Stats);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    if (!k.trim()) return;
    setBusy(true);
    try {
      await api.addMemory(k.trim(), v.trim());
      setK(""); setV(""); setAdding(false);
      await load();
    } finally { setBusy(false); }
  };
  const del = async (key: string) => {
    if (!(await confirmDialog(t("mem.delConfirm", { key }), { danger: true }))) return;
    await api.deleteMemory(key);
    await load();
  };
  const resetAll = async () => {
    if (!items.length) return;
    if (!(await confirmDialog(t("mem.clearConfirm", { n: items.length }), { danger: true }))) return;
    setBusy(true);
    try {
      // 顺序删除所有 key（并行请求可能撞锁）
      for (const r of items) {
        await api.deleteMemory(r.key).catch(() => {});
      }
      await load();
    } finally { setBusy(false); }
  };
  const exportJson = async () => {
    const txt = await api.exportMemory();
    const blob = new Blob([txt], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `bazz-memory-${new Date().toISOString().slice(0,10)}.json`;
    a.click(); URL.revokeObjectURL(url);
  };
  // v1.4.4：Markdown 记忆报告（后端按 kind 分组排版）
  const exportMd = async () => {
    const txt = await api.exportMemoryMd();
    const blob = new Blob([txt], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `bazz-memory-report-${new Date().toISOString().slice(0,10)}.md`;
    a.click(); URL.revokeObjectURL(url);
  };

  // 分组
  const grouped = useMemo(() => {
    const m: Record<string, MemRow[]> = {};
    for (const r of items) (m[classify(r)] ??= []).push(r);
    for (const k of Object.keys(m)) m[k].sort((a, b) => b.updated_at - a.updated_at);
    return m;
  }, [items]);

  const filtered = useMemo(() => {
    if (!q.trim()) return items;
    const ql = q.toLowerCase();
    return items.filter((r) => r.key.toLowerCase().includes(ql) || (r.value || "").toLowerCase().includes(ql));
  }, [items, q]);

  // 分类下的「特殊」面板
  const prefs = grouped["pref"] || [];
  const allocs = grouped["alloc"] || [];
  const habits = grouped["habit"] || [];
  const total = items.length;

  return (
    <>
      {/* Header */}
      <div className="px-5 py-4 border-b border-line flex items-center gap-3 flex-wrap">
        <I.Memory className="text-gold" size={22} />
        <div className="leading-tight">
          <div className="font-mono font-bold text-ink text-[16px]">{t("mem.heroTitle")}</div>
          <div className="font-mono text-[10px] text-ink-dim mt-0.5">{t("mem.heroSub")}</div>
        </div>
        <span className="ml-2 pill pill-gold">{t("memory.title")}</span>
        <div className="ml-auto flex items-center gap-2 font-mono text-[11px]">
          <span className={`pill ${total > 0 ? "pill-green" : "pill-dim"}`}>
            <span className={`dot ${total > 0 ? "dot-green live" : "dot-dim"}`} />
            {t("mem.indexed", { n: total })}
          </span>
          {stats?.db_size_bytes ? (
            <span className="pill pill-dim" title={stats?.db_path || ""}>{fmtBytes(stats.db_size_bytes)}</span>
          ) : null}
          {onClose && (
            <button onClick={onClose} className="rounded-md border border-line bg-elevated px-3 py-1.5 hover:border-red hover:text-red text-[12px]">
              {t("mem.back")}
            </button>
          )}
        </div>
      </div>

      {/* Toolbar */}
      <div className="px-5 py-3 border-b border-line flex items-center gap-3">
        <div className="relative flex-1">
          <I.Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-mute" />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder={t("mem.searchPh")} className="field" />
        </div>
        <button onClick={() => setAdding(!adding)} className="btn-gold"><I.Plus size={12} /> {t("mem.addBtn")}</button>
        <button onClick={load} disabled={loading} className="btn-ghost text-[12px]">
          <I.Refresh size={11} className={loading ? "animate-spin" : ""} /> {t("mem.refresh")}
        </button>
      </div>

      {adding && (
        <div className="px-5 py-3 border-b border-line flex items-center gap-2 bg-gold/5 flex-wrap">
          <input value={k} onChange={(e) => setK(e.target.value)}
            placeholder={t("mem.keyPh")} className="field flex-1 min-w-[200px]" />
          <input value={v} onChange={(e) => setV(e.target.value)}
            placeholder={t("mem.valuePh")} className="field flex-1 min-w-[200px]" />
          <button onClick={add} disabled={busy} className="btn-gold"><I.Check size={12} /> {t("mem.save")}</button>
        </div>
      )}

      <div className="grid grid-cols-12 gap-4 p-5">
        {/* LEFT — 真实画像 */}
        <div className="col-span-12 lg:col-span-5 space-y-4">
          <SectionCard title={t("mem.cat.pref")} sub={t("mem.prefSub")} icon={<I.Shield size={14} className="text-gold" />}>
            {prefs.length === 0 ? (
              <Empty hint={t("mem.prefEmptyHint")} />
            ) : (
              <div className="space-y-1.5">
                {prefs.map((r) => (
                  <KVRow key={r.key} k={r.key} v={r.value} ts={r.updated_at} kind={r.kind} hits={r.hits} onDel={() => del(r.key)} />
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard title={t("mem.cat.alloc")} sub={t("mem.allocSub")} icon={<I.Market size={14} className="text-gold" />}>
            {allocs.length === 0 ? (
              <Empty hint={t("mem.allocEmptyHint")} />
            ) : (
              <div className="space-y-1.5">
                {allocs.map((r) => (
                  <AllocRow key={r.key} k={r.key} v={r.value} ts={r.updated_at} onDel={() => del(r.key)} />
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard title={t("mem.cat.habit")} sub={t("mem.habitSub")} icon={<I.Bolt size={14} className="text-gold" />}>
            {habits.length === 0 ? (
              <Empty hint={t("mem.habitEmptyHint")} />
            ) : (
              <div className="space-y-1.5">
                {habits.map((r) => (
                  <KVRow key={r.key} k={r.key} v={r.value} ts={r.updated_at} kind={r.kind} hits={r.hits} onDel={() => del(r.key)} />
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard title={t("mem.engineTitle")} sub={t("mem.engineSub")} icon={<I.Cpu size={14} className="text-gold" />}>
            <div className="space-y-1.5 font-mono text-[11px]">
              <div className="flex items-center gap-2">
                <span className="prefix">{t("mem.engine")}</span>
                <span className="text-ink">{stats?.engine || t("mem.engineVal")}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="prefix">{t("mem.file")}</span>
                <span className="text-ink-dim break-all" title={stats?.db_path}>{stats?.db_path || "—"}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="prefix">{t("mem.size")}</span>
                <span className="text-ink">{fmtBytes(stats?.db_size_bytes || 0)}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="prefix">{t("mem.updated")}</span>
                <span className="text-ink">{stats?.last_updated_at ? relTime(stats.last_updated_at, isEn) + ` · ${fmtTs(stats.last_updated_at, locale)}` : "—"}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="prefix">{t("mem.total")}</span>
                <span className="text-ink">{t("mem.entries", { n: total })}</span>
              </div>
              <div className="flex items-center gap-2 flex-wrap pt-1">
                {stats?.categories?.length ? stats.categories.map((c) => (
                  <span key={c.name} className="pill pill-dim text-[10px]" title={catLabel(t, c.name)}>
                    {c.name} · {c.count}
                  </span>
                )) : <span className="font-mono text-[10.5px] text-ink-mute">{t("mem.noCat")}</span>}
              </div>
            </div>
          </SectionCard>
        </div>

        {/* RIGHT — 全部条目按分类 */}
        <div className="col-span-12 lg:col-span-7">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2"><I.Pin className="text-gold" size={14} /><span className="font-mono text-[12px] text-ink">{t("mem.factTitle")}</span></div>
            <span className="pill pill-dim">{q ? t("mem.matchN", { a: filtered.length, b: total }) : t("mem.totalAnchors", { n: total })}</span>
          </div>
          {total === 0 && !loading ? (
            <div className="rounded-md border border-dashed border-line bg-elevated/30 p-8 text-center">
              <I.Memory size={28} className="text-ink-mute mx-auto mb-2" />
              <div className="font-mono text-[13px] text-ink">{t("mem.emptyTitle")}</div>
              <div className="mt-1.5 font-mono text-[11px] text-ink-mute leading-relaxed max-w-[480px] mx-auto">
                {t("mem.emptyBody")}
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              {Object.keys(grouped).sort((a, b) => grouped[b].length - grouped[a].length).map((cat) => {
                const rows = grouped[cat].filter((r) =>
                  !q || r.key.toLowerCase().includes(q.toLowerCase()) || (r.value || "").toLowerCase().includes(q.toLowerCase())
                );
                if (!rows.length) return null;
                return (
                  <div key={cat}>
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="pill pill-gold text-[10px]">{cat}</span>
                      <span className="prefix">{catLabel(t, cat)}</span>
                      <span className="pill pill-dim text-[10px] ml-auto">{rows.length}</span>
                    </div>
                    <div className="space-y-1.5">
                      {rows.map((r) => (
                        <KVRow key={r.key} k={r.key} v={r.value} ts={r.updated_at} kind={r.kind} hits={r.hits} onDel={() => del(r.key)} />
                      ))}
                    </div>
                  </div>
                );
              })}
              {q && filtered.length === 0 && (
                <div className="text-center py-6 font-mono text-[12px] text-ink-mute">{t("mem.noMatch", { q })}</div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="px-5 py-3 border-t border-line flex items-center justify-between font-mono text-[11px] flex-wrap gap-2">
        <div className="flex items-center gap-3 text-ink-mute">
          <span className={`pill ${total > 0 ? "pill-green" : "pill-dim"}`}>
            <span className={`dot ${total > 0 ? "dot-green" : "dot-dim"}`} /> {total > 0 ? t("mem.localSync") : t("mem.localEmpty")}
          </span>
          {stats?.last_updated_at ? <span>{t("mem.lastUpd", { t: relTime(stats.last_updated_at, isEn) })}</span> : null}
          {stats?.db_path ? <span className="text-ink-mute" title={stats.db_path}>{stats.db_path}</span> : null}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={exportMd} disabled={busy || total === 0} className="btn-ghost">
            <I.Memory size={11} /> {t("mem.exportMdBtn")}
          </button>
          <button onClick={exportJson} disabled={busy || total === 0} className="btn-ghost">
            <I.Download size={11} /> {t("mem.exportBtn")}
          </button>
          <button onClick={resetAll} disabled={busy || total === 0} className="btn-ghost text-red/90 hover:text-red">
            <I.Trash size={11} /> {t("mem.resetBtn")}
          </button>
        </div>
      </div>
    </>
  );
}

function SectionCard({ title, sub, icon, children }: any) {
  return (
    <div className="glass p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">{icon}<span className="font-mono text-[12px] text-ink">{title}</span></div>
        <span className="prefix">{sub}</span>
      </div>
      {children}
    </div>
  );
}

function KVRow({ k, v, ts, kind, hits, onDel }: { k: string; v: string; ts: number; kind?: string; hits?: number; onDel: () => void }) {
  const { t, locale } = useI18n();
  const isEn = locale === "en";
  const kindKey = kind ? CAT_I18N[kind] : undefined;
  return (
    <div className="rounded-md border border-line bg-card/40 p-2.5">
      <div className="flex items-center gap-2">
        {kindKey && <span className="pill pill-dim text-[9px] shrink-0">{t(kindKey)}</span>}
        <span className="font-mono text-[11.5px] text-ink-dim truncate" title={k}>{k}</span>
        {!!hits && hits > 0 && <span className="font-mono text-[9px] text-ink-mute shrink-0" title={t("mem.hitsTitle")}>⚡{hits}</span>}
        <span className="ml-auto font-mono text-[9.5px] text-ink-mute shrink-0" title={fmtTs(ts, locale)}>{relTime(ts, isEn)}</span>
        <button onClick={onDel} className="rounded p-1 hover:bg-elevated shrink-0" title={t("mem.delTitle")}><I.Trash size={11} className="text-red" /></button>
      </div>
      <div className="mt-1 text-[12.5px] text-ink break-all whitespace-pre-wrap">{v || <span className="text-ink-mute">{t("mem.emptyVal")}</span>}</div>
    </div>
  );
}

function AllocRow({ k, v, ts, onDel }: { k: string; v: string; ts: number; onDel: () => void }) {
  const { locale } = useI18n();
  const isEn = locale === "en";
  // alloc.btc = 40 → bar of 40%, 资产名 = btc
  const label = k.split(".").slice(1).join(".") || k;
  const pct = Number((v || "").replace(/[^0-9.\-]/g, ""));
  const ok = Number.isFinite(pct) && pct >= 0 && pct <= 100;
  return (
    <div className="rounded-md border border-line bg-card/40 p-2.5">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11.5px] text-ink truncate" title={k}>{label}</span>
        <span className="ml-auto font-mono text-[10.5px] text-gold tabular shrink-0">{ok ? `${pct}%` : (v || "—")}</span>
        <button onClick={onDel} className="rounded p-1 hover:bg-elevated shrink-0"><I.Trash size={11} className="text-red" /></button>
      </div>
      {ok && (
        <div className="mt-1.5 h-1.5 rounded-full bg-line overflow-hidden">
          <div className="h-full bg-gold" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
        </div>
      )}
      <div className="mt-1 font-mono text-[9.5px] text-ink-mute" title={fmtTs(ts, locale)}>{relTime(ts, isEn)}</div>
    </div>
  );
}

function Empty({ hint }: { hint: string }) {
  const t = useI18n().t;
  return (
    <div className="rounded-md border border-dashed border-line bg-elevated/20 px-3 py-3">
      <div className="font-mono text-[11px] text-ink-mute leading-relaxed">{t("mem.emptyRec")}</div>
      <div className="mt-1 font-mono text-[10px] text-ink-mute/80 leading-relaxed">{hint}</div>
    </div>
  );
}
