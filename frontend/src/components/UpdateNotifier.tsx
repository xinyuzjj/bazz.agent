import React, { useEffect, useRef } from "react";
import { useT } from "../i18n/i18n";
import useUpdater from "../hooks/useUpdater";

/* ============================================================
 * UpdateNotifier —— 右下角更新提醒卡（v1.2.13 降频版）
 *   规则（对应反馈「弹窗不要一直弹」）：
 *     · 只在本会话启动挂载时自动检查【一次】；不再有 5 分钟巡检，
 *       也不再监听窗口焦点/可见性变化 —— 不会反复弹出。
 *     · 「稍后」＝本会话对该版本不再提醒（点完即消失，重启软件后才再查）。
 *     · 手动检查请用设置页「软件更新」区块的「检查更新」按钮（结果在面板内展示）。
 *   卡片内可直接走完整自动更新：下载（进度）→ 就绪 → 两步确认 → 重启安装。
 * ============================================================ */

export default function UpdateNotifier() {
  const t = useT();
  const upd = useUpdater(t);
  const st = upd.st;
  const hideFor = useRef<string>("");

  // 仅挂载时自动检查一次（silent：失败走 8s 小 toast，不常驻打扰）
  useEffect(() => { upd.check({ silent: true }); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  // 「已是最新」绿 toast 短暂展示后收起
  useEffect(() => {
    if (st.phase !== "none") return;
    const tm = setTimeout(upd.reset, 2600);
    return () => clearTimeout(tm);
  }, [st.phase, upd]);

  // 启动检查静默失败：8s 小红条自动收起
  useEffect(() => {
    if (st.phase !== "err" || !(st as any).auto) return;
    const tm = setTimeout(upd.reset, 8000);
    return () => clearTimeout(tm);
  }, [st.phase, upd]);

  // 「稍后」：本会话不再提醒该版本
  const later = () => {
    const latest = (st as any).latest;
    if (latest) hideFor.current = latest;
    upd.reset();
  };

  // —— 收起态 ——
  if (st.phase === "init") return null;

  const fmtMB = (n: number) => (n > 0 ? (n / 1024 / 1024).toFixed(0) : "");

  // —— 小 toast：检查中 / 已是最新 / 静默失败 ——
  if (st.phase === "checking") {
    return (
      <div className="fixed bottom-4 right-4 z-[99] glass px-3 py-2 rounded-lg font-mono text-[11px] text-ink-dim flex items-center gap-2"
        style={{ boxShadow: "0 6px 24px rgba(0,0,0,.25)" }}>
        <span className="inline-block w-3 h-3 rounded-full border-2 border-gold/50 border-t-transparent animate-spin" />
        {t("update.checking")}
      </div>
    );
  }
  if (st.phase === "none") {
    return (
      <div className="fixed bottom-4 right-4 z-[99] glass px-3 py-2 rounded-lg font-mono text-[11px] text-green flex items-center gap-2"
        style={{ boxShadow: "0 6px 24px rgba(0,0,0,.25)" }}>
        ✓ {t("update.latest", { v: (st as any).latest })}
      </div>
    );
  }
  if (st.phase === "err" && (st as any).auto) {
    return (
      <div className="fixed bottom-4 right-4 z-[99] glass px-3 py-2 rounded-lg font-mono text-[11px] text-red/90 flex items-center gap-2 max-w-[340px]"
        style={{ boxShadow: "0 6px 24px rgba(0,0,0,.25)" }}>
        <span>⚠</span>
        <span className="truncate">{st.msg}</span>
      </div>
    );
  }

  // —— 主卡片：avail / dl / ready / confirm / applying / 常驻 err ——
  const pct = st.phase === "dl" && st.total > 0 ? Math.min(99, Math.round((st.done / st.total) * 100)) : 0;
  const info = st as any;

  return (
    <div className="fixed bottom-4 right-4 z-[99] glass p-3.5 rounded-xl w-[340px]"
      style={{ boxShadow: "0 10px 32px rgba(0,0,0,.28)" }}>
      {st.phase === "avail" && (
        <>
          <div className="flex items-center gap-2">
            <span className="text-gold text-[13px]">⬆</span>
            <b className="font-mono text-[12px] text-ink tracking-wide">{t("update.available")}</b>
            <span className="pill pill-gold text-[9.5px]">v{info.latest}</span>
            <button onClick={later}
              className="ml-auto text-[10px] font-mono text-ink-mute hover:text-ink">{t("update.later")}</button>
          </div>
          <div className="font-mono text-[10.5px] text-ink-dim mt-1">
            {t("update.fromTo", { a: info.cur, b: info.latest })}
            {info.size ? ` · ${t("update.about", { n: fmtMB(info.size) })}` : ""}
          </div>
          <div className="mt-2.5 flex items-center gap-2">
            <button onClick={upd.download} className="btn-gold flex-1 justify-center text-[11.5px] py-1.5">
              <span className="text-[12px]">↓</span> {t("update.download")}
            </button>
            <button onClick={later} className="btn-ghost text-[11.5px]">{t("update.later")}</button>
          </div>
        </>
      )}
      {st.phase === "dl" && (
        <>
          <div className="flex items-center gap-2">
            <b className="font-mono text-[12px] text-ink tracking-wide">↓ {t("update.downloading")} v{info.latest}</b>
            <span className="ml-auto font-mono text-[10px] text-ink-dim tabular">{pct}%</span>
          </div>
          <div className="mt-2 h-1.5 rounded-full bg-elevated overflow-hidden">
            <div className="h-full rounded-full bg-gold transition-all" style={{ width: `${pct || 4}%` }} />
          </div>
          <div className="font-mono text-[9.5px] text-ink-mute mt-1 tabular">
            {(info.done / 1024 / 1024).toFixed(0)} MB / {(info.total / 1024 / 1024).toFixed(0)} MB
          </div>
        </>
      )}
      {st.phase === "ready" && (
        <>
          <div className="flex items-center gap-2">
            <span className="text-green text-[13px]">✓</span>
            <b className="font-mono text-[12px] text-ink tracking-wide">{t("update.downloaded")}</b>
            <span className="pill pill-gold text-[9.5px]">v{info.latest}</span>
          </div>
          <div className="font-mono text-[10.5px] text-ink-dim mt-1">{t("update.restartHint")}</div>
          <button onClick={upd.confirmApply} className="btn-gold mt-2.5 w-full justify-center text-[11.5px] py-1.5">
            🔄 {t("update.restart")}
          </button>
        </>
      )}
      {st.phase === "confirm" && (
        <>
          <div className="font-mono text-[12px] text-gold">⚠ {t("update.confirmTitle")}</div>
          <div className="font-mono text-[10px] text-ink-dim mt-1 leading-relaxed">{t("update.confirmBody")}</div>
          <div className="mt-2.5 flex items-center gap-2">
            <button onClick={upd.apply} className="btn-gold flex-1 justify-center text-[11.5px] py-1.5">
              🔄 {t("update.confirmGo")}
            </button>
            <button onClick={upd.cancelApply} className="btn-ghost text-[11.5px]">{t("update.cancel")}</button>
          </div>
        </>
      )}
      {st.phase === "applying" && (
        <div className="flex items-center gap-2 font-mono text-[11.5px] text-ink">
          <span className="inline-block w-3 h-3 rounded-full border-2 border-gold/50 border-t-transparent animate-spin" />
          {t("update.restarting")}
        </div>
      )}
      {st.phase === "err" && !(st as any).auto && (
        <>
          <div className="flex items-center gap-2">
            <span className="text-red text-[13px]">⚠</span>
            <b className="font-mono text-[12px] text-ink">{t("update.failed")}</b>
          </div>
          <div className="font-mono text-[10.5px] text-red/90 mt-1 break-all">{st.msg}</div>
          <div className="font-mono text-[10px] text-ink-dim mt-1.5">{t("update.fallbackHint")}</div>
          <div className="mt-2 flex gap-2">
            <button onClick={() => upd.check({ silent: false })} className="btn-ghost flex-1 justify-center text-[11px]">{t("update.retry")}</button>
            <button onClick={upd.openDownload} className="btn-ghost flex-1 justify-center text-[11px] text-gold">↗ {t("update.openDownloadShort")}</button>
            <button onClick={later} className="btn-ghost flex-1 justify-center text-[11px]">{t("update.later")}</button>
          </div>
        </>
      )}
    </div>
  );
}
