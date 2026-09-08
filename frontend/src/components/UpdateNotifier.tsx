import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n/i18n";

/* ============================================================
 * UpdateNotifier —— v1.2.11 极简版
 *   旧版：自动下载/整目录替换/强制关窗重启（多次在中国代理环境失败，已废弃）
 *   新版：右下角轻量卡片仅在「检测到新版本」时浮出，单一动作——
 *         点「打开下载页」由主进程用系统默认浏览器打开 GitHub release URL。
 *   「稍后」仅本会话隐藏当前版本号；检查失败短暂浮 8s 自动收起，避免打扰。
 * ============================================================ */

const POLL_MS = 5 * 60 * 1000;          // 5 分钟巡检
const FALLBACK_URL = "https://github.com/xinyuzjj/bazz.agent/releases/latest";

type State =
  | { s: "idle" }
  | { s: "checking" }
  | { s: "none"; current: string }                       // 已是最新（短暂浮 + 自动收）
  | { s: "avail"; current: string; latest: string; html_url: string; notes: string }
  | { s: "err"; msg: string; auto: boolean };            // auto=true 短暂浮，false 卡片常驻

export default function UpdateNotifier() {
  const t = useT();
  const [st, setSt] = useState<State>({ s: "idle" });
  const hideFor = useRef<string>("");     // 本会话已「稍后」过的版本号
  const timer = useRef<any>(null);

  const doCheck = useCallback(async (opts?: { silent?: boolean }) => {
    if (st.s === "checking") return;
    setSt({ s: "checking" });
    try {
      const r: any = await api.updateCheck();
      if (!r?.ok) {
        setSt({ s: "err", msg: r?.error || t("update.checkFail"), auto: !opts?.silent });
        return;
      }
      if (r.available && r.latest !== hideFor.current) {
        setSt({
          s: "avail",
          current: r.current ?? "",
          latest: r.latest ?? "",
          html_url: r.html_url || FALLBACK_URL,
          notes: String(r.notes ?? "").slice(0, 800),
        });
      } else if (!r.available) {
        setSt({ s: "none", current: r.current ?? "" });
        setTimeout(() => setSt((p) => (p.s === "none" ? { s: "idle" } : p)), 2600);
      } else {
        setSt({ s: "idle" });
      }
    } catch (e: any) {
      setSt({ s: "err", msg: String(e?.message ?? e), auto: !opts?.silent });
    }
  }, [st.s, t]);

  useEffect(() => {
    doCheck({ silent: true });
    timer.current = setInterval(() => doCheck({ silent: true }), POLL_MS);
    return () => { if (timer.current) clearInterval(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 窗口重新聚焦 → 立即复查
  useEffect(() => {
    const onFocus = () => { if (document.visibilityState === "visible") doCheck({ silent: true }); };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doCheck]);

  // 短暂失败条 8s 自动收
  useEffect(() => {
    if (st.s !== "err" || !st.auto) return;
    const tm = setTimeout(() => setSt((p) => (p.s === "err" ? { s: "idle" } : p)), 8000);
    return () => clearTimeout(tm);
  }, [st.s]);

  const openPage = useCallback(() => {
    const url = st.s === "avail" ? st.html_url : FALLBACK_URL;
    api.openExternal(url);
  }, [st]);

  if (st.s === "idle") return null;

  // 短暂小 toast：检查中 / 已是最新
  if (st.s === "checking") {
    return (
      <div className="fixed bottom-4 right-4 z-[99] glass px-3 py-2 rounded-lg font-mono text-[11px] text-ink-dim flex items-center gap-2"
        style={{ boxShadow: "0 6px 24px rgba(0,0,0,.25)" }}>
        <span className="inline-block w-3 h-3 rounded-full border-2 border-gold/50 border-t-transparent animate-spin" />
        {t("update.checking")}
      </div>
    );
  }
  if (st.s === "none") {
    return (
      <div className="fixed bottom-4 right-4 z-[99] glass px-3 py-2 rounded-lg font-mono text-[11px] text-green flex items-center gap-2"
        style={{ boxShadow: "0 6px 24px rgba(0,0,0,.25)" }}>
        ✓ {t("update.latest", { v: st.current })}
      </div>
    );
  }

  // 主卡片：avail / err
  return (
    <div className="fixed bottom-4 right-4 z-[99] glass p-3.5 rounded-xl w-[340px]"
      style={{ boxShadow: "0 10px 32px rgba(0,0,0,.28)" }}>
      {st.s === "avail" ? (
        <>
          <div className="flex items-center gap-2">
            <span className="text-gold text-[13px]">⬆</span>
            <b className="font-mono text-[12px] text-ink tracking-wide">{t("update.available")}</b>
            <span className="pill pill-gold text-[9.5px]">v{st.latest}</span>
            <button onClick={() => { hideFor.current = st.latest; setSt({ s: "idle" }); }}
              className="ml-auto text-[10px] font-mono text-ink-mute hover:text-ink">{t("update.later")}</button>
          </div>
          <div className="font-mono text-[10.5px] text-ink-dim mt-1">
            {t("update.fromTo", { a: st.current, b: st.latest })}
          </div>
          {st.notes && (
            <pre className="mt-2 max-h-24 overflow-auto text-[10px] text-ink-dim whitespace-pre-wrap font-mono leading-relaxed border-t border-line/50 pt-1.5"
              style={{ fontFamily: "inherit" }}>{st.notes}</pre>
          )}
          <button onClick={openPage} className="btn-gold mt-2.5 w-full justify-center text-[11.5px] py-1.5">
            ↗ {t("update.openDownload")}
          </button>
        </>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <span className="text-red text-[13px]">⚠</span>
            <b className="font-mono text-[12px] text-ink">{t("update.failed")}</b>
          </div>
          <div className="font-mono text-[10.5px] text-red/90 mt-1 break-all">{st.msg}</div>
          <div className="mt-2 flex gap-2">
            <button onClick={() => setSt({ s: "idle" })} className="btn-ghost flex-1 justify-center text-[11px]">{t("update.later")}</button>
            <button onClick={openPage} className="btn-ghost flex-1 justify-center text-[11px] text-gold">↗ {t("update.openDownload")}</button>
          </div>
        </>
      )}
    </div>
  );
}
