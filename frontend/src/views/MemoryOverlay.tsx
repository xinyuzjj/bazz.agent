import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";
import { useT } from "../i18n/i18n";

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

type MemRow = { key: string; value: string; updated_at: number };
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

function classify(key: string): string {
  return (key.split(".", 1)[0] || "misc").trim() || "misc";
}

function prettyCategory(name: string): string {
  return ({
    pref: "风险与偏好",
    alloc: "资产分配",
    habit: "操作习惯",
    fact: "事实锚",
    bot: "Agent 档案",
    context: "上下文",
    user: "用户档案",
    trading: "交易风格",
    risk: "风控规则",
  } as Record<string, string>)[name] || name;
}

function fmtBytes(n: number): string {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(2)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function relTime(ts: number): string {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return `${s}s 前`;
  if (s < 3600) return `${Math.floor(s / 60)}m 前`;
  if (s < 86400) return `${Math.floor(s / 3600)}h 前`;
  return `${Math.floor(s / 86400)}d 前`;
}

function fmtTs(ts: number | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

export function MemoryOverlay({ open, full, onClose }: { open: boolean; full?: boolean; onClose?: () => void }) {
  if (!open) return null;

  if (full) {
    return (
      <div className="p-5">
        <div className="glass-bright overflow-hidden" style={{ borderRadius: 12 }}>
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
  const t = useT();
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
    if (!window.confirm(`删除记忆「${key}」？`)) return;
    await api.deleteMemory(key);
    await load();
  };
  const resetAll = async () => {
    if (!items.length) return;
    if (!window.confirm(`将清空全部 ${items.length} 条记忆（操作前请先导出备份），确定？`)) return;
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

  // 分组
  const grouped = useMemo(() => {
    const m: Record<string, MemRow[]> = {};
    for (const r of items) (m[classify(r.key)] ??= []).push(r);
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
  const facts = grouped["fact"] || [];
  const total = items.length;

  return (
    <>
      {/* Header */}
      <div className="px-5 py-4 border-b border-line flex items-center gap-3 flex-wrap">
        <I.Memory className="text-gold" size={22} />
        <div className="leading-tight">
          <div className="font-mono font-bold text-ink text-[16px]">Hermes 跨会话长期记忆与偏好中枢</div>
          <div className="font-mono text-[10px] text-ink-dim mt-0.5">Persistent Semantic Memory Core · 自动沉淀交易习惯、风险约束与多会话事实上下文</div>
        </div>
        <span className="ml-2 pill pill-gold">{t("memory.title")}</span>
        <div className="ml-auto flex items-center gap-2 font-mono text-[11px]">
          <span className={`pill ${total > 0 ? "pill-green" : "pill-dim"}`}>
            <span className={`dot ${total > 0 ? "dot-green live" : "dot-dim"}`} />
            已索引 {total} 片段
          </span>
          {stats?.db_size_bytes ? (
            <span className="pill pill-dim" title={stats?.db_path || ""}>{fmtBytes(stats.db_size_bytes)}</span>
          ) : null}
          {onClose && (
            <button onClick={onClose} className="rounded-md border border-line bg-elevated px-3 py-1.5 hover:border-red hover:text-red text-[12px]">
              ← 返回面板
            </button>
          )}
        </div>
      </div>

      {/* Toolbar */}
      <div className="px-5 py-3 border-b border-line flex items-center gap-3">
        <div className="relative flex-1">
          <I.Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-mute" />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="在长期记忆中检索偏好、风险戒律或历史决策（key/value 模糊匹配）..." className="field" />
        </div>
        <button onClick={() => setAdding(!adding)} className="btn-gold"><I.Plus size={12} /> 添加新偏好规则</button>
        <button onClick={load} disabled={loading} className="btn-ghost text-[12px]">
          <I.Refresh size={11} className={loading ? "animate-spin" : ""} /> 刷新
        </button>
      </div>

      {adding && (
        <div className="px-5 py-3 border-b border-line flex items-center gap-2 bg-gold/5 flex-wrap">
          <input value={k} onChange={(e) => setK(e.target.value)}
            placeholder="key (例: pref.risk.max_leverage)" className="field flex-1 min-w-[200px]" />
          <input value={v} onChange={(e) => setV(e.target.value)}
            placeholder="value (偏好内容)" className="field flex-1 min-w-[200px]" />
          <button onClick={add} disabled={busy} className="btn-gold"><I.Check size={12} /> 保存</button>
        </div>
      )}

      <div className="grid grid-cols-12 gap-4 p-5">
        {/* LEFT — 真实画像 */}
        <div className="col-span-12 lg:col-span-5 space-y-4">
          <SectionCard title="风险与偏好" sub="PREFERENCE · 来自 pref.* 键值" icon={<I.Shield size={14} className="text-gold" />}>
            {prefs.length === 0 ? (
              <Empty hint='聊天里说"记住：杠杆不超过 10x"会自动沉淀到 pref.risk.* 键。' />
            ) : (
              <div className="space-y-1.5">
                {prefs.map((r) => (
                  <KVRow k={r.key} v={r.value} ts={r.updated_at} onDel={() => del(r.key)} />
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard title="资产分配" sub="TARGET_ALLOCATION · 来自 alloc.* 键值" icon={<I.Market size={14} className="text-gold" />}>
            {allocs.length === 0 ? (
              <Empty hint='手动添加 alloc.btc=40 alloc.bnb=35 alloc.sol=15 alloc.alpha=10 等键来建立配置。' />
            ) : (
              <div className="space-y-1.5">
                {allocs.map((r) => (
                  <AllocRow k={r.key} v={r.value} ts={r.updated_at} onDel={() => del(r.key)} />
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard title="操作习惯" sub="ACTION_HABITS · 来自 habit.* 键值" icon={<I.Bolt size={14} className="text-gold" />}>
            {habits.length === 0 ? (
              <Empty hint='如 habit.entry_pattern="15m/4h EMA20 回落企稳再入场"。聊天里说明习惯即可沉淀。' />
            ) : (
              <div className="space-y-1.5">
                {habits.map((r) => (
                  <KVRow k={r.key} v={r.value} ts={r.updated_at} onDel={() => del(r.key)} />
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard title="记忆引擎" sub="Engine · 真实存储后端" icon={<I.Cpu size={14} className="text-gold" />}>
            <div className="space-y-1.5 font-mono text-[11px]">
              <div className="flex items-center gap-2">
                <span className="prefix">引擎</span>
                <span className="text-ink">{stats?.engine || "SQLite · 本地存档"}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="prefix">文件</span>
                <span className="text-ink-dim break-all" title={stats?.db_path}>{stats?.db_path || "—"}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="prefix">大小</span>
                <span className="text-ink">{fmtBytes(stats?.db_size_bytes || 0)}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="prefix">最近更新</span>
                <span className="text-ink">{stats?.last_updated_at ? relTime(stats.last_updated_at) + ` · ${fmtTs(stats.last_updated_at)}` : "—"}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="prefix">总条目</span>
                <span className="text-ink">{total} 条</span>
              </div>
              <div className="flex items-center gap-2 flex-wrap pt-1">
                {stats?.categories?.length ? stats.categories.map((c) => (
                  <span key={c.name} className="pill pill-dim text-[10px]" title={prettyCategory(c.name)}>
                    {c.name} · {c.count}
                  </span>
                )) : <span className="font-mono text-[10.5px] text-ink-mute">暂无分类</span>}
              </div>
            </div>
          </SectionCard>
        </div>

        {/* RIGHT — 全部条目按分类 */}
        <div className="col-span-12 lg:col-span-7">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2"><I.Pin className="text-gold" size={14} /><span className="font-mono text-[12px] text-ink">跨会话事实库</span></div>
            <span className="pill pill-dim">{q ? `匹配 ${filtered.length}/${total}` : `共 ${total} 条核心锚点`}</span>
          </div>
          {total === 0 && !loading ? (
            <div className="rounded-md border border-dashed border-line bg-elevated/30 p-8 text-center">
              <I.Memory size={28} className="text-ink-mute mx-auto mb-2" />
              <div className="font-mono text-[13px] text-ink">长期记忆为空</div>
              <div className="mt-1.5 font-mono text-[11px] text-ink-mute leading-relaxed max-w-[480px] mx-auto">
                在聊天中对 Agent 说「记住：XXX」，或在本页右上「添加新偏好规则」手动写入。Agent 会自动按 key 前缀（pref.* / alloc.* / habit.* / fact.*）分类沉淀。
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
                      <span className="prefix">{prettyCategory(cat)}</span>
                      <span className="pill pill-dim text-[10px] ml-auto">{rows.length} 条</span>
                    </div>
                    <div className="space-y-1.5">
                      {rows.map((r) => (
                        <KVRow key={r.key} k={r.key} v={r.value} ts={r.updated_at} onDel={() => del(r.key)} />
                      ))}
                    </div>
                  </div>
                );
              })}
              {q && filtered.length === 0 && (
                <div className="text-center py-6 font-mono text-[12px] text-ink-mute">没有匹配「{q}」的条目</div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="px-5 py-3 border-t border-line flex items-center justify-between font-mono text-[11px] flex-wrap gap-2">
        <div className="flex items-center gap-3 text-ink-mute">
          <span className={`pill ${total > 0 ? "pill-green" : "pill-dim"}`}>
            <span className={`dot ${total > 0 ? "dot-green" : "dot-dim"}`} /> 本地存档 {total > 0 ? "已同步" : "空"}
          </span>
          {stats?.last_updated_at ? <span>上次更新 {relTime(stats.last_updated_at)}</span> : null}
          {stats?.db_path ? <span className="text-ink-mute" title={stats.db_path}>{stats.db_path}</span> : null}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={exportJson} disabled={busy || total === 0} className="btn-ghost">
            <I.Download size={11} /> 导出记忆知识库 (JSON)
          </button>
          <button onClick={resetAll} disabled={busy || total === 0} className="btn-ghost text-red/90 hover:text-red">
            <I.Trash size={11} /> 重置非核心记忆
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

function KVRow({ k, v, ts, onDel }: { k: string; v: string; ts: number; onDel: () => void }) {
  return (
    <div className="rounded-md border border-line bg-card/40 p-2.5">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11.5px] text-ink-dim truncate" title={k}>{k}</span>
        <span className="ml-auto font-mono text-[9.5px] text-ink-mute shrink-0" title={fmtTs(ts)}>{relTime(ts)}</span>
        <button onClick={onDel} className="rounded p-1 hover:bg-elevated shrink-0" title="删除"><I.Trash size={11} className="text-red" /></button>
      </div>
      <div className="mt-1 text-[12.5px] text-ink break-all whitespace-pre-wrap">{v || <span className="text-ink-mute">（空值）</span>}</div>
    </div>
  );
}

function AllocRow({ k, v, ts, onDel }: { k: string; v: string; ts: number; onDel: () => void }) {
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
      <div className="mt-1 font-mono text-[9.5px] text-ink-mute" title={fmtTs(ts)}>{relTime(ts)}</div>
    </div>
  );
}

function Empty({ hint }: { hint: string }) {
  return (
    <div className="rounded-md border border-dashed border-line bg-elevated/20 px-3 py-3">
      <div className="font-mono text-[11px] text-ink-mute leading-relaxed">暂无记录</div>
      <div className="mt-1 font-mono text-[10px] text-ink-mute/80 leading-relaxed">{hint}</div>
    </div>
  );
}