import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n/i18n";

/* ============================================================
 * UpdateNotifier —— 自动更新悬浮提醒卡
 *   挂载即检查（+ 每 10 分钟自动复查）；发现新版本时右下角浮出卡片：
 *     下载（显示进度）→ 重启并更新（拿 Electron 主进程 pid → 后端生成 PS
 *     脚本脱离启动 → 关窗触发整目录替换重启）
 *   「稍后」仅本次会话隐藏；非桌面（浏览器/dev）仅提示、禁用应用。
 * ============================================================ */

type State =
  | { s: "idle" }                     // 未检查 / 已隐藏
  | { s: "checking" }                 // 正在查
  | { s: "none"; current: string }    // 已是最新（短暂 toast 后回 idle）
  | { s: "avail"; info: any }         // 有新版，未下载
  | { s: "dl"; info: any; done: number; total: number; zip: string }
  | { s: "ready"; info: any; zip: string }   // 下载完成，可重启
  | { s: "applying" }                 // 已触发更新，等待退出
  | { s: "err"; msg: string; auto?: boolean };   // auto=自动检查失败（短暂浮条后自动回 idle）

export default function UpdateNotifier() {
  const t = useT();
  const [st, setSt] = useState<State>({ s: "idle" });
  const hideFor = useRef<string>("");            // 本会话忽略的版本号
  const timer = useRef<any>(null);

  const doCheck = useCallback(async (opts?: { silent?: boolean }) => {
    if (st.s === "dl" || st.s === "applying" || st.s === "checking") return;
    setSt({ s: "checking" });
    try {
      const r = await api.updateCheck();
      if (!r?.ok) {
        // 检查失败（网络/服务器）——不再完全静默：浮一个小红条说明原因，
        // 8 秒后自动收起；用户可点重试。手动场景则常驻卡片。
        setSt({ s: "err", msg: r?.error || t("update.checkFail"), auto: !opts?.silent });
        return;
      }
      if (r.available && r.latest !== hideFor.current) {
        setSt({ s: "avail", info: r });
      } else if (!r.available) {
        setSt({ s: "none", current: r.current ?? "" });
        setTimeout(() => setSt((p) => (p.s === "none" ? { s: "idle" } : p)), 2600);
      } else {
        setSt({ s: "idle" });
      }
    } catch (e: any) {
      setSt({ s: "err", msg: String(e?.message ?? e), auto: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.s]);

  // 首次挂载 + 定时复查（10 分钟 → 5 分钟，更及时感知新版本）
  useEffect(() => {
    doCheck();
    timer.current = setInterval(doCheck, 5 * 60 * 1000);
    return () => { if (timer.current) clearInterval(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 窗口重新聚焦 → 立即复查（从别的窗口切回来就能发现刚发布的新版本）
  useEffect(() => {
    const onFocus = () => { if (document.visibilityState === "visible") doCheck(); };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doCheck]);

  // 自动失败的红条：8 秒后自动回 idle，避免常驻打扰
  useEffect(() => {
    if (st.s !== "err" || !st.auto) return;
    const tm = setTimeout(() => setSt((p) => (p.s === "err" ? { s: "idle" } : p)), 8000);
    return () => clearTimeout(tm);
  }, [st.s]);

  // 下载中轮询进度
  useEffect(() => {
    if (st.s !== "dl") return;
    const poll = setInterval(async () => {
      try {
        const s = await api.updateStatus();
        if (s?.error) { setSt({ s: "err", msg: s.error }); return; }
        if (s?.ready) { setSt({ s: "ready", info: st.info, zip: s.path || st.zip }); return; }
        if (s?.active) {
          setSt({ s: "dl", info: st.info, done: s.done || 0, total: s.total || 0, zip: s.path || "" });
        }
      } catch { /* 忽略单次轮询失败 */ }
    }, 700);
    return () => clearInterval(poll);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.s]);

  const startDownload = useCallback(async (url: string) => {
    setSt((p) => (p.s === "avail" ? { ...p, s: "dl", done: 0, total: 0, zip: "" } : p));
    try {
      const r = await api.updateDownload(url);
      if (r?.started === false && r?.error) setSt({ s: "err", msg: r.error });
    } catch (e: any) { setSt({ s: "err", msg: String(e?.message ?? e) }); }
  }, []);

  const apply = useCallback(async (zip: string) => {
    setSt({ s: "applying" });
    try {
      let pid = 0;
      try { pid = Number(await (window as any).bazzWindow?.getPid?.()) || 0; } catch { pid = 0; }
      if (!pid) { setSt({ s: "err", msg: t("update.devOnly") }); return; }
      const r = await api.updateApply(zip, pid);
      if (!r?.ok) { setSt({ s: "err", msg: r?.error || t("update.err") }); return; }
      // 给后端一点时间写脚本 → 关窗 → Electron 退出，PS 脚本随后完成替换重启
      setTimeout(() => { try { (window as any).bazzWindow?.close?.(); } catch {} }, 1200);
    } catch (e: any) {
      setSt({ s: "err", msg: String(e?.message ?? e) });
    }
  }, [t]);

  // —— 非展示态（idle / checking / none 短暂显示 / 无版本差异）——
  if (st.s === "idle") return null;

  const fmtMB = (n: number) => n > 0 ? `${(n / 1024 / 1024).toFixed(0)} MB` : "";

  // 小 toast（none / checking）
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

  // 主卡片（avail / dl / ready / applying / err）
  const info = (st as any).info as any;
  const card: React.ReactNode[] = [];
  const pct = st.s === "dl" && st.total > 0 ? Math.min(99, Math.round((st.done / st.total) * 100)) : 0;

  if (st.s === "avail") {
    const sizeMB = info?.asset?.size ? (info.asset.size / 1024 / 1024).toFixed(0) : "";
    card.push(
      <>
        <div className="flex items-center gap-2">
          <span className="text-gold text-[13px]">⬆</span>
          <b className="font-mono text-[12px] text-ink tracking-wide">{t("update.available")}</b>
          <span className="pill pill-gold text-[9.5px]">{info.latest}</span>
          <button onClick={() => { hideFor.current = info.latest; setSt({ s: "idle" }); }}
            className="ml-auto text-[10px] font-mono text-ink-mute hover:text-ink">{t("update.later")}</button>
        </div>
        <div className="font-mono text-[10.5px] text-ink-dim mt-1">
          {t("update.fromTo", { a: info.current, b: info.latest })}
          {sizeMB ? ` · ${t("update.about", { n: sizeMB })}` : ""}
        </div>
        {info.notes && (
          <pre className="mt-2 max-h-24 overflow-auto text-[10px] text-ink-dim whitespace-pre-wrap font-mono leading-relaxed border-t border-line/50 pt-1.5"
            style={{ fontFamily: "inherit" }}>{String(info.notes).slice(0, 800)}</pre>
        )}
        <div className="mt-2.5 flex items-center gap-2">
          <button onClick={() => startDownload(info?.asset?.url)} className="btn-gold flex-1 justify-center text-[11.5px] py-1.5">
            <span className="text-[12px]">↓</span> {t("update.download")}
          </button>
          <button onClick={() => { hideFor.current = info.latest; setSt({ s: "idle" }); }}
            className="btn-ghost text-[11.5px]">{t("update.later")}</button>
        </div>
      </>
    );
  } else if (st.s === "dl") {
    card.push(
      <>
        <div className="flex items-center gap-2">
          <b className="font-mono text-[12px] text-ink tracking-wide">↓ {t("update.downloading")}</b>
          <span className="ml-auto font-mono text-[10px] text-ink-dim tabular">{pct}%</span>
        </div>
        <div className="mt-2 h-1.5 rounded-full bg-elevated overflow-hidden">
          <div className="h-full rounded-full bg-gold transition-all" style={{ width: `${pct || 4}%` }} />
        </div>
        <div className="font-mono text-[9.5px] text-ink-mute mt-1">
          {t("update.fromTo", { a: info?.current, b: info?.latest })} · {fmtMB(st.done)} / {fmtMB(st.total)}
        </div>
      </>
    );
  } else if (st.s === "ready") {
    card.push(
      <>
        <div className="flex items-center gap-2">
          <span className="text-green text-[13px]">✓</span>
          <b className="font-mono text-[12px] text-ink tracking-wide">{t("update.downloaded")}</b>
          <span className="pill pill-gold text-[9.5px]">{info?.latest}</span>
        </div>
        <div className="font-mono text-[10.5px] text-ink-dim mt-1">{t("update.restartHint")}</div>
        <button onClick={() => apply(st.zip)} className="btn-gold mt-2.5 w-full justify-center text-[11.5px] py-1.5">
          🔄 {t("update.restart")}
        </button>
      </>
    );
  } else if (st.s === "applying") {
    card.push(
      <div className="flex items-center gap-2 font-mono text-[11.5px] text-ink">
        <span className="inline-block w-3 h-3 rounded-full border-2 border-gold/50 border-t-transparent animate-spin" />
        {t("update.restarting")}
      </div>
    );
  } else if (st.s === "err") {
    const msg = (st as any).msg || t("update.err");
    card.push(
      <>
        <div className="flex items-center gap-2">
          <span className="text-red text-[13px]">⚠</span>
          <b className="font-mono text-[12px] text-ink">{t("update.failed")}</b>
        </div>
        <div className="font-mono text-[10.5px] text-red/90 mt-1 break-all">{msg}</div>
        <div className="mt-2 flex gap-2">
          <button onClick={() => setSt({ s: "idle" })} className="btn-ghost flex-1 justify-center text-[11px]">{t("update.later")}</button>
          <button onClick={() => doCheck()} className="btn-ghost flex-1 justify-center text-[11px] text-gold">{t("update.retry")}</button>
        </div>
      </>
    );
  }

  return (
    <div className="fixed bottom-4 right-4 z-[99] glass p-3.5 rounded-xl w-[340px]"
      style={{ boxShadow: "0 10px 32px rgba(0,0,0,.28)" }}>
      {card}
    </div>
  );
}
