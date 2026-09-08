import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n/i18n";

/* ============================================================
 * UpdatePanel —— 设置页「软件更新」区块（v1.2.11 全新设计）
 *   旧版：自动下载 zip + 整目录替换 + 强制关窗重启（多次在中国代理环境失败）
 *   新版：只检查版本 + 用系统默认浏览器打开 GitHub 下载页
 *     · 已是最新 → 绿条「已是最新 vX.Y.Z」
 *     · 有新版本 → 金条「发现新版本 vX.Y.Z」+ 「打开下载页」大按钮
 *     · 检查失败 → 红条 + 原因 + 重试 + 「打开下载页」兜底
 *   任何状态下都展示「打开下载页」按钮，去不去更新用户说了算
 * ============================================================ */

type St =
  | { s: "init"; cur: string }
  | { s: "checking"; cur: string }
  | { s: "none"; cur: string; latest: string; html_url: string }
  | { s: "avail"; cur: string; latest: string; html_url: string; published_at: string; notes: string }
  | { s: "err"; cur: string; msg: string; html_url: string };

const FALLBACK_URL = "https://github.com/xinyuzjj/bazz.agent/releases/latest";

export default function UpdatePanel() {
  const t = useT();
  const [st, setSt] = useState<St>({ s: "init", cur: "" });
  const busyRef = useRef(false);

  const doCheck = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      const r: any = await api.updateCheck();
      const cur = r?.current ?? "";
      const html = r?.html_url || FALLBACK_URL;
      if (!r?.ok) { setSt({ s: "err", cur, msg: r?.error || t("update.failed"), html_url: html }); return; }
      if (r.available) {
        setSt({ s: "avail", cur, latest: r.latest ?? "", html_url: html, published_at: r.published_at ?? "", notes: r.notes ?? "" });
      } else {
        setSt({ s: "none", cur, latest: r.latest ?? cur, html_url: html });
      }
    } catch (e: any) {
      setSt((p) => ({ s: "err", cur: p.cur, msg: String(e?.message ?? e), html_url: FALLBACK_URL }));
    } finally { busyRef.current = false; }
  }, [t]);

  useEffect(() => { doCheck(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const openDownloadPage = useCallback(() => {
    const url = (st as any).html_url || FALLBACK_URL;
    api.openExternal(url);
  }, [st]);

  // 三态主体
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
    body = (
      <div className="rounded-md border border-gold/30 bg-gold/[0.05] p-3">
        <div className="font-mono text-[12.5px] text-gold flex items-center gap-2">
          <span>⬆</span>{t("update.available")}
          <span className="pill pill-gold text-[9.5px]">v{st.latest}</span>
        </div>
        <div className="font-mono text-[10.5px] text-ink-dim mt-1.5 leading-relaxed">
          {t("update.openHint")}
        </div>
        <button onClick={openDownloadPage} className="btn-gold mt-2.5 w-full justify-center text-[12.5px] py-2">
          ↗ {t("update.openDownload")}
        </button>
        {st.notes && (
          <details className="mt-2.5">
            <summary className="cursor-pointer list-none font-mono text-[10.5px] text-ink-dim hover:text-ink select-none">
              ▸ {t("update.releaseNotes")}
            </summary>
            <pre className="mt-1.5 max-h-40 overflow-auto text-[10.5px] text-ink-dim whitespace-pre-wrap leading-relaxed"
              style={{ fontFamily: "inherit" }}>{st.notes}</pre>
          </details>
        )}
      </div>
    );
  } else {
    body = (
      <div>
        <div className="rounded-md border border-red/30 bg-red/[0.05] px-3 py-2 font-mono text-[11px] text-red break-all">
          ⚠ {st.msg}
        </div>
        <div className="flex items-center gap-2 mt-2">
          <button onClick={doCheck} className="btn-ghost text-[11.5px]">
            <I_Refresh /> {t("update.retry")}
          </button>
          <button onClick={openDownloadPage} className="btn-ghost text-[11.5px]">
            ↗ {t("update.openDownload")}
          </button>
        </div>
      </div>
    );
  }

  const hasInfo = st.s === "avail";
  const latestLabel = hasInfo ? (st as any).latest : st.s === "none" ? (st as any).latest : "—";
  return (
    <div className="glass p-4">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <span className="text-gold text-[13px]">⬆</span>
        <span className="font-mono text-[12px] text-ink tracking-wider">{t("update.title")}</span>
        <span className={`pill ${st.s === "none" ? "pill-green" : st.s === "avail" ? "pill-gold" : st.s === "err" ? "pill-red" : "pill-dim"}`}>
          <span className={`dot ${st.s === "none" ? "dot-green live" : st.s === "avail" ? "dot-gold" : st.s === "err" ? "dot-red" : "dot-dim"}`} />
          {st.s === "none" ? t("update.upToDate") : st.s === "avail" ? t("update.updateReadyPill") : st.s === "err" ? t("update.failed") : t("update.checking")}
        </span>
        <button onClick={openDownloadPage} className="ml-auto btn-ghost !px-2.5 !py-1 text-[11px]">
          ↗ {t("update.openDownloadShort")}
        </button>
        <button onClick={doCheck} disabled={st.s === "checking"}
          className="btn-ghost !px-2.5 !py-1 text-[11px] disabled:opacity-50">
          <I_Refresh spin={st.s === "checking"} /> {t("update.checkNow")}
        </button>
      </div>
      <div className="border-t border-line/60 pt-2 mb-2">
        <Row label={t("update.installed")} value={`v${st.cur || "—"}`} />
        <Row label={t("update.latestVer")} value={latestLabel} accent={hasInfo ? "text-gold" : "text-ink"} />
      </div>
      {body}
    </div>
  );
}

function Row({ label, value, accent = "text-ink" }: { label: string; value: string; accent?: string }) {
  return (
    <div className="flex items-center justify-between py-1">
      <span className="prefix">{label}</span>
      <span className={`font-mono text-[12px] ${accent}`}>{value}</span>
    </div>
  );
}

function I_Refresh({ spin }: { spin?: boolean }) {
  return <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
    className={`inline-block mr-1 ${spin ? "animate-spin" : ""}`}>
    <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}
