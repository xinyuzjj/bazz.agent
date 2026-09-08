import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n/i18n";

/* ============================================================
 * UpdatePanel —— 设置页「软件更新」区块
 *   进入即自动检查；展示 当前/最新版本 → 有新版本时下载（进度）
 *   → 重启并更新（拿 Electron 主进程 pid → 后端生成脱离更新脚本
 *   → 关窗触发整目录替换重启）
 * ============================================================ */

type St =
  | { s: "init"; cur: string }
  | { s: "checking"; cur: string }
  | { s: "none"; cur: string; latest: string }
  | { s: "avail"; cur: string; info: any }
  | { s: "dl"; cur: string; done: number; total: number; zip: string; info: any }
  | { s: "ready"; cur: string; zip: string; info: any }
  | { s: "applying"; cur: string }
  | { s: "err"; cur: string; msg: string };

export default function UpdatePanel() {
  const t = useT();
  const [st, setSt] = useState<St>({ s: "init", cur: "" });
  const busyRef = useRef(false);

  const doCheck = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      const r = await api.updateCheck();
      const cur = r?.current ?? "";
      if (!r?.ok) { setSt({ s: "err", cur, msg: r?.error || t("update.failed") }); return; }
      if (r.available) setSt({ s: "avail", cur, info: r });
      else setSt({ s: "none", cur, latest: r.latest ?? "" });
    } catch (e: any) {
      setSt((p) => ({ s: "err", cur: p.cur, msg: String(e?.message ?? e) }));
    } finally { busyRef.current = false; }
  }, [t]);

  useEffect(() => { doCheck(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  // 下载中轮询进度
  useEffect(() => {
    if (st.s !== "dl") return;
    const poll = setInterval(async () => {
      try {
        const s = await api.updateStatus();
        if (s?.error) { setSt((p) => ({ s: "err", cur: p.cur, msg: s.error })); return; }
        if (s?.ready) {
          setSt((p) => (p.s === "dl"
            ? { s: "ready", cur: p.cur, zip: s.path || p.zip, info: p.info ?? null }
            : p));
          return;
        }
        if (s?.active) {
          setSt((p) => (p.s === "dl"
            ? { ...p, done: s.done || 0, total: s.total || 0, zip: s.path || p.zip }
            : p));
        }
      } catch { /* 忽略单次轮询失败 */ }
    }, 700);
    return () => clearInterval(poll);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.s]);

  const startDownload = useCallback(async () => {
    setSt((p) => (p.s === "avail"
      ? { s: "dl", cur: p.cur, done: 0, total: 0, zip: "", info: p.info }
      : p));
    const url = st.s === "avail" ? st.info?.asset?.url : "";
    if (!url) return;
    try {
      const r = await api.updateDownload(url);
      if (r?.started === false && r?.error) setSt((p) => ({ s: "err", cur: p.cur, msg: r.error }));
    } catch (e: any) { setSt((p) => ({ s: "err", cur: p.cur, msg: String(e?.message ?? e) })); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.s]);

  const apply = useCallback(async (zip: string) => {
    setSt((p) => ({ s: "applying", cur: p.cur }));
    try {
      let pid = 0;
      try { pid = Number(await (window as any).bazzWindow?.getPid?.()) || 0; } catch { pid = 0; }
      if (!pid) { setSt((p) => ({ s: "err", cur: p.cur, msg: t("update.devOnly") })); return; }
      const r = await api.updateApply(zip, pid);
      if (!r?.ok) { setSt((p) => ({ s: "err", cur: p.cur, msg: r?.error || t("update.failed") })); return; }
      setTimeout(() => { try { (window as any).bazzWindow?.close?.(); } catch {} }, 1200);
    } catch (e: any) {
      setSt((p) => ({ s: "err", cur: p.cur, msg: String(e?.message ?? e) }));
    }
  }, [t]);

  const row = (label: string, value: string, cls = "text-ink") => (
    <div className="flex items-center justify-between py-1">
      <span className="prefix">{label}</span>
      <span className={`font-mono text-[12px] ${cls}`}>{value}</span>
    </div>
  );

  let body: React.ReactNode;
  if (st.s === "checking" || st.s === "init") {
    body = (
      <div className="flex items-center gap-2 font-mono text-[11.5px] text-ink-dim py-2">
        <span className="inline-block w-3 h-3 rounded-full border-2 border-gold/50 border-t-transparent animate-spin" />
        {t("update.checking")}
      </div>
    );
  } else if (st.s === "none") {
    body = (
      <div className="rounded-md border border-green/30 bg-green/[0.05] px-3 py-2.5 font-mono text-[11.5px] text-green flex items-center gap-2">
        <span>✓</span> {t("update.latest", { v: st.latest })}
      </div>
    );
  } else if (st.s === "avail") {
    const a = st.info?.asset;
    body = (
      <div className="rounded-md border border-gold/30 bg-gold/[0.05] p-3">
        <div className="font-mono text-[12.5px] text-gold flex items-center gap-2">
          <span>⬆</span>{t("update.available")}
          <span className="pill pill-gold text-[9.5px]">{st.info.latest}</span>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <button onClick={startDownload} className="btn-gold flex-1 justify-center text-[12px] py-1.5">
            <span className="text-[12px]">↓</span> {t("update.download")}
            {a?.size ? ` · ${t("update.about", { n: (a.size / 1024 / 1024).toFixed(0) })}` : ""}
          </button>
          {st.info.html_url && (
            <a href={st.info.html_url} target="_blank" rel="noreferrer"
              className="btn-ghost text-[11.5px]" title={t("update.openRelease")}>
              ↗
            </a>
          )}
        </div>
        {st.info.notes && (
          <details className="mt-2.5">
            <summary className="cursor-pointer list-none font-mono text-[10.5px] text-ink-dim hover:text-ink select-none">
              ▸ {t("update.releaseNotes")}
            </summary>
            <pre className="mt-1.5 max-h-40 overflow-auto text-[10.5px] text-ink-dim whitespace-pre-wrap leading-relaxed"
              style={{ fontFamily: "inherit" }}>{String(st.info.notes)}</pre>
          </details>
        )}
      </div>
    );
  } else if (st.s === "dl") {
    const pct = st.total > 0 ? Math.min(99, Math.round((st.done / st.total) * 100)) : 0;
    body = (
      <div className="px-1 py-1.5">
        <div className="flex items-center justify-between font-mono text-[11px] mb-1.5">
          <span className="text-ink flex items-center gap-2">
            <span className="inline-block w-3 h-3 rounded-full border-2 border-gold/50 border-t-transparent animate-spin" />
            ↓ {t("update.downloading")}
          </span>
          <span className="tabular text-ink-dim">{pct}%</span>
        </div>
        <div className="h-2 rounded-full bg-elevated overflow-hidden">
          <div className="h-full rounded-full bg-gold transition-all" style={{ width: `${pct || 4}%` }} />
        </div>
        <div className="font-mono text-[9.5px] text-ink-mute mt-1 tabular">
          {(st.done / 1024 / 1024).toFixed(0)} MB / {(st.total / 1024 / 1024).toFixed(0)} MB
        </div>
      </div>
    );
  } else if (st.s === "ready") {
    body = (
      <div className="rounded-md border border-green/30 bg-green/[0.05] p-3">
        <div className="font-mono text-[12.5px] text-green flex items-center gap-2">✓ {t("update.downloaded")}</div>
        <div className="font-mono text-[10.5px] text-ink-dim mt-1">{t("update.restartHint")}</div>
        <button onClick={() => apply(st.zip)} className="btn-gold mt-2.5 w-full justify-center text-[12px] py-1.5">
          🔄 {t("update.restart")}
        </button>
      </div>
    );
  } else if (st.s === "applying") {
    body = (
      <div className="flex items-center gap-2 font-mono text-[11.5px] text-ink py-2">
        <span className="inline-block w-3 h-3 rounded-full border-2 border-gold/50 border-t-transparent animate-spin" />
        {t("update.restarting")}
      </div>
    );
  } else {
    body = (
      <div>
        <div className="rounded-md border border-red/30 bg-red/[0.05] px-3 py-2 font-mono text-[11px] text-red break-all">
          ⚠ {st.msg}
        </div>
        <button onClick={doCheck} className="btn-ghost mt-2 text-[11.5px]">
          <I_Refresh /> {t("update.retry")}
        </button>
      </div>
    );
  }

  const hasInfo = st.s === "avail" || st.s === "dl" || st.s === "ready";
  const latestLabel = hasInfo ? String((st as any).info?.latest ?? "") : st.s === "none" ? st.latest : "—";
  return (
    <div className="glass p-4">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <span className="text-gold text-[13px]">⬆</span>
        <span className="font-mono text-[12px] text-ink tracking-wider">{t("update.title")}</span>
        <span className={`pill ${st.s === "none" ? "pill-green" : st.s === "avail" || st.s === "ready" ? "pill-gold" : st.s === "err" ? "pill-red" : "pill-dim"}`}>
          <span className={`dot ${st.s === "none" ? "dot-green live" : st.s === "avail" || st.s === "ready" ? "dot-gold" : st.s === "err" ? "dot-red" : "dot-dim"}`} />
          {st.s === "none" ? t("update.upToDate") : st.s === "avail" || st.s === "ready" ? t("update.updateReadyPill") : st.s === "err" ? t("update.failed") : st.s === "dl" ? "↓" : t("update.checking")}
        </span>
        <button onClick={doCheck} disabled={st.s === "checking" || st.s === "dl" || st.s === "applying"}
          className="ml-auto btn-ghost !px-2.5 !py-1 text-[11px] disabled:opacity-50">
          <I_Refresh spin={st.s === "checking"} /> {t("update.checkNow")}
        </button>
      </div>
      <div className="border-t border-line/60 pt-2 mb-2">
        {row(t("update.installed"), `v${st.cur || "—"}`)}
        {row(t("update.latestVer"), latestLabel, hasInfo ? "text-gold" : "text-ink")}
      </div>
      {body}
    </div>
  );
}

function I_Refresh({ spin }: { spin?: boolean }) {
  return <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
    className={`inline-block mr-1 ${spin ? "animate-spin" : ""}`}>
    <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}
