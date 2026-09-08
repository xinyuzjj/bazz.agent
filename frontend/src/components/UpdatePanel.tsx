import React, { useEffect } from "react";
import { useT } from "../i18n/i18n";
import useUpdater, { UpdaterState } from "../hooks/useUpdater";

/* ============================================================
 * UpdatePanel —— 设置页「软件更新」区块（v1.2.13）
 *   应用内自动更新：检查 → 下载（进度条）→ 就绪 → 两步确认重启 →
 *   后端 DETACHED 的 PS 脚本完成「备份 WORKSPACE → 整目录替换 → 还原 → 重启」。
 *   任何一步失败都停在 err 态：给 [重试] + [打开下载页]（浏览器兜底，不把人卡死）。
 * ============================================================ */

export default function UpdatePanel() {
  const t = useT();
  const upd = useUpdater(t);
  const st = upd.st;

  useEffect(() => { upd.check({ silent: true }); /* 仅进入页面时自动检查一次 */ }, []);

  const fmtMB = (n: number) => (n > 0 ? (n / 1024 / 1024).toFixed(0) : "");
  const isGold = st.phase === "avail" || st.phase === "ready" || st.phase === "confirm";

  let body: React.ReactNode;
  switch (st.phase) {
    case "init":
    case "checking":
      body = (
        <div className="flex items-center gap-2 font-mono text-[11.5px] text-ink-dim py-2">
          <span className="inline-block w-3 h-3 rounded-full border-2 border-gold/50 border-t-transparent animate-spin" />
          {t("update.checking")}
        </div>
      );
      break;
    case "none":
      body = (
        <div className="rounded-md border border-green/30 bg-green/[0.05] px-3 py-2.5 font-mono text-[11.5px] text-green flex items-center gap-2">
          <span>✓</span> {t("update.latest", { v: st.latest })}
        </div>
      );
      break;
    case "avail":
      body = (
        <div className="rounded-md border border-gold/30 bg-gold/[0.05] p-3">
          <div className="font-mono text-[12.5px] text-gold flex items-center gap-2">
            <span>⬆</span>{t("update.available")}
            <span className="pill pill-gold text-[9.5px]">v{st.latest}</span>
          </div>
          <div className="font-mono text-[10.5px] text-ink-dim mt-1.5 leading-relaxed">
            {t("update.autoHint")}
            {st.size ? `（约 ${fmtMB(st.size)} MB）` : ""}
          </div>
          <div className="mt-2.5 flex items-center gap-2">
            <button onClick={upd.download} className="btn-gold flex-1 justify-center text-[12px] py-1.5">
              <span className="text-[12px]">↓</span> {t("update.download")}
              {st.size ? ` · ${t("update.about", { n: fmtMB(st.size) })}` : ""}
            </button>
            <button onClick={upd.openDownload} className="btn-ghost text-[11.5px]" title={t("update.openRelease")}>
              ↗ {t("update.openDownloadShort")}
            </button>
          </div>
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
      break;
    case "dl": {
      const pct = st.total > 0 ? Math.min(99, Math.round((st.done / st.total) * 100)) : 0;
      body = (
        <div className="rounded-md border border-gold/30 bg-gold/[0.05] p-3">
          <div className="flex items-center justify-between font-mono text-[11px]">
            <span className="text-ink flex items-center gap-2">
              <span className="inline-block w-3 h-3 rounded-full border-2 border-gold/50 border-t-transparent animate-spin" />
              ↓ {t("update.downloading")} v{st.latest}
            </span>
            <span className="tabular text-ink-dim">{pct}%</span>
          </div>
          <div className="mt-2 h-2 rounded-full bg-elevated overflow-hidden">
            <div className="h-full rounded-full bg-gold transition-all" style={{ width: `${pct || 4}%` }} />
          </div>
          <div className="font-mono text-[9.5px] text-ink-mute mt-1 tabular">
            {(st.done / 1024 / 1024).toFixed(0)} MB / {(st.total / 1024 / 1024).toFixed(0)} MB
          </div>
        </div>
      );
      break;
    }
    case "ready":
      body = (
        <div className="rounded-md border border-green/30 bg-green/[0.05] p-3">
          <div className="font-mono text-[12.5px] text-green flex items-center gap-2">
            ✓ {t("update.downloaded")} <span className="pill pill-gold text-[9.5px]">v{st.latest}</span>
          </div>
          <div className="font-mono text-[10.5px] text-ink-dim mt-1">{t("update.restartHint")}</div>
          <button onClick={upd.confirmApply} className="btn-gold mt-2.5 w-full justify-center text-[12px] py-1.5">
            🔄 {t("update.restart")}
          </button>
        </div>
      );
      break;
    case "confirm":
      body = (
        <div className="rounded-md border border-gold/50 bg-gold/[0.08] p-3">
          <div className="font-mono text-[12.5px] text-gold">⚠ {t("update.confirmTitle")}</div>
          <div className="font-mono text-[10.5px] text-ink-dim mt-1 leading-relaxed">{t("update.confirmBody")}</div>
          <div className="mt-2.5 flex items-center gap-2">
            <button onClick={upd.apply} className="btn-gold flex-1 justify-center text-[12px] py-1.5">
              🔄 {t("update.confirmGo")}
            </button>
            <button onClick={upd.cancelApply} className="btn-ghost text-[11.5px]">{t("update.cancel")}</button>
          </div>
        </div>
      );
      break;
    case "applying":
      body = (
        <div className="flex items-center gap-2 font-mono text-[11.5px] text-ink py-2">
          <span className="inline-block w-3 h-3 rounded-full border-2 border-gold/50 border-t-transparent animate-spin" />
          {t("update.restarting")}
        </div>
      );
      break;
    default: { // err
      const e = st as Extract<UpdaterState, { phase: "err" }>;
      body = (
        <div>
          <div className="rounded-md border border-red/30 bg-red/[0.05] px-3 py-2 font-mono text-[11px] text-red break-all">
            ⚠ {e.msg}
          </div>
          <div className="font-mono text-[10px] text-ink-dim mt-1.5">{t("update.fallbackHint")}</div>
          <div className="flex items-center gap-2 mt-2">
            <button onClick={() => upd.check({ silent: false })} className="btn-ghost text-[11.5px]">
              <I_Refresh /> {t("update.retry")}
            </button>
            <button onClick={upd.openDownload} className="btn-ghost text-[11.5px] text-gold">
              ↗ {t("update.openDownload")}
            </button>
          </div>
        </div>
      );
      break;
    }
  }

  const latestLabel = st.phase === "avail" || st.phase === "dl" || st.phase === "ready" || st.phase === "confirm" || st.phase === "applying"
    ? (st as any).latest || "—"
    : st.phase === "none" ? (st as any).latest : "—";

  return (
    <div className="glass p-4">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <span className="text-gold text-[13px]">⬆</span>
        <span className="font-mono text-[12px] text-ink tracking-wider">{t("update.title")}</span>
        <span className={`pill ${st.phase === "none" ? "pill-green" : isGold ? "pill-gold" : st.phase === "err" ? "pill-red" : "pill-dim"}`}>
          <span className={`dot ${st.phase === "none" ? "dot-green live" : isGold ? "dot-gold" : st.phase === "err" ? "dot-red" : "dot-dim"}`} />
          {st.phase === "none" ? t("update.upToDate")
            : st.phase === "err" ? t("update.failed")
            : isGold ? t("update.updateReadyPill")
            : st.phase === "dl" ? "↓"
            : st.phase === "applying" ? "🔄"
            : t("update.checking")}
        </span>
        <button onClick={upd.openDownload} className="ml-auto btn-ghost !px-2.5 !py-1 text-[11px]">
          ↗ {t("update.openDownloadShort")}
        </button>
        <button onClick={() => upd.check({ silent: false })} disabled={st.phase === "checking" || st.phase === "dl" || st.phase === "applying"}
          className="btn-ghost !px-2.5 !py-1 text-[11px] disabled:opacity-50">
          <I_Refresh spin={st.phase === "checking"} /> {t("update.checkNow")}
        </button>
      </div>
      <div className="border-t border-line/60 pt-2 mb-2">
        <Row label={t("update.installed")} value={`v${(st as any).cur || "—"}`} />
        <Row label={t("update.latestVer")} value={latestLabel} accent={isGold ? "text-gold" : "text-ink"} />
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
