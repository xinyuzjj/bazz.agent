import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

/* ============================================================
 * useUpdater —— 自动更新共享状态机（v1.2.13）
 *   UpdateNotifier（启动浮卡）与 UpdatePanel（设置页区块）共用同一套
 *   后端链路：check → download（轮询进度）→ ready → confirm（两步确认）→
 *   apply（DETACHED PS 脚本整目录替换重启，应用自动退出）。
 *   失败一律进 err 态，界面提供 [重试] + [打开下载页]（浏览器兜底）。
 * ============================================================ */

export type UpdaterState =
  | { phase: "init" }
  | { phase: "checking"; cur: string }
  | { phase: "none"; cur: string; latest: string }
  | { phase: "avail"; cur: string; latest: string; html_url: string; assetUrl: string; size: number; notes: string }
  | { phase: "dl"; cur: string; latest: string; html_url: string; notes: string; done: number; total: number; zip: string }
  | { phase: "ready"; cur: string; latest: string; html_url: string; zip: string }
  | { phase: "confirm"; cur: string; latest: string; html_url: string; zip: string }
  | { phase: "applying"; cur: string; latest: string }
  | { phase: "err"; cur: string; msg: string; html_url: string; auto?: boolean };

const FALLBACK_URL = "https://github.com/xinyuzjj/bazz.agent/releases/latest";
const POLL_MS = 700;
const CLOSE_DELAY_MS = 1500; // 让后端先把 apply 响应吐给前端，再关窗触发 Electron 退出

export default function useUpdater(t: (key: string, params?: Record<string, string | number>) => string) {
  const [st, setSt] = useState<UpdaterState>({ phase: "init" });
  const stRef = useRef(st);
  stRef.current = st;
  const curRef = useRef("");
  const busy = useRef(false);

  const mkErr = useCallback((msg: string, html_url = FALLBACK_URL, auto = false): UpdaterState =>
    ({ phase: "err", cur: curRef.current, msg, html_url, auto }), []);

  const check = useCallback(async (opts?: { silent?: boolean }) => {
    if (busy.current) return;
    const cur = stRef.current;
    if (cur.phase === "dl" || cur.phase === "applying") return;
    busy.current = true;
    setSt((p) => ({ phase: "checking", cur: curRef.current } as any));
    try {
      const r: any = await api.updateCheck();
      curRef.current = r?.current ?? curRef.current;
      const html = r?.html_url || FALLBACK_URL;
      if (!r?.ok) {
        setSt(mkErr(r?.error || t("update.checkFail"), html, !opts?.silent));
        return;
      }
      if (r.available) {
        const asset = r.asset || {};
        setSt({
          phase: "avail", cur: curRef.current, latest: String(r.latest ?? ""),
          html_url: html, assetUrl: asset.url || "", size: asset.size || 0,
          notes: String(r.notes ?? "").slice(0, 1200),
        });
      } else {
        setSt({ phase: "none", cur: curRef.current, latest: String(r.latest ?? curRef.current) });
      }
    } catch (e: any) {
      setSt(mkErr(String(e?.message ?? e), FALLBACK_URL, !opts?.silent));
    } finally { busy.current = false; }
  }, [mkErr, t]);

  const download = useCallback(async () => {
    const cur = stRef.current;
    if (cur.phase !== "avail" || !cur.assetUrl) return;
    setSt({
      phase: "dl", cur: curRef.current, latest: cur.latest, html_url: cur.html_url,
      notes: cur.notes, done: 0, total: 0, zip: "",
    });
    try {
      const r: any = await api.updateDownload(cur.assetUrl);
      if (r?.started === false && r?.error) setSt(mkErr(String(r.error), cur.html_url));
    } catch (e: any) {
      setSt(mkErr(String(e?.message ?? e), cur.html_url));
    }
  }, [mkErr]);

  // 下载中轮询进度
  useEffect(() => {
    if (st.phase !== "dl") return;
    let alive = true;
    const tick = async () => {
      try {
        const s: any = await api.updateStatus();
        if (!alive) return;
        if (s?.error) {
          setSt(mkErr(String(s.error), (stRef.current as any).html_url || FALLBACK_URL));
          return;
        }
        if (s?.ready) {
          setSt((p) => p.phase === "dl" ? {
            phase: "ready", cur: curRef.current, latest: (p as any).latest || "",
            html_url: (p as any).html_url || FALLBACK_URL, zip: s.path || (p as any).zip || "",
          } : p);
          return;
        }
        if (s?.active) {
          setSt((p) => p.phase === "dl" ? { ...p, done: s.done || 0, total: s.total || 0, zip: s.path || (p as any).zip || "" } : p);
        }
      } catch { /* 单次轮询失败忽略，下轮再试 */ }
    };
    const tm = setInterval(tick, POLL_MS);
    return () => { alive = false; clearInterval(tm); };
  }, [st.phase, mkErr]);

  // 两步确认：ready → confirm（防手滑直接重启）
  const confirmApply = useCallback(() => {
    const cur = stRef.current;
    if (cur.phase !== "ready") return;
    setSt({ phase: "confirm", cur: curRef.current, latest: cur.latest, html_url: cur.html_url, zip: cur.zip });
  }, []);

  const cancelApply = useCallback(() => {
    const cur = stRef.current;
    if (cur.phase !== "confirm") return;
    setSt({ phase: "ready", cur: curRef.current, latest: cur.latest, html_url: cur.html_url, zip: cur.zip });
  }, []);

  const apply = useCallback(async () => {
    const cur = stRef.current;
    if (cur.phase !== "confirm") return;
    setSt({ phase: "applying", cur: curRef.current, latest: cur.latest });
    try {
      let pid = 0;
      try { pid = Number(await (window as any).bazzWindow?.getPid?.()) || 0; } catch { pid = 0; }
      if (!pid) {
        setSt(mkErr(t("update.devOnly"), cur.html_url));
        return;
      }
      const r: any = await api.updateApply(cur.zip, pid);
      if (!r?.ok) { setSt(mkErr(r?.error || t("update.failed"), cur.html_url)); return; }
      // 响应已返回 → 稍候让后端把结果写盘，再关窗：Electron 退出后由脱离的 PS 脚本接管替换
      setTimeout(() => {
        try { (window as any).bazzWindow?.close?.(); } catch { /* 非桌面态忽略 */ }
      }, CLOSE_DELAY_MS);
    } catch (e: any) {
      setSt(mkErr(String(e?.message ?? e), (stRef.current as any).html_url || FALLBACK_URL));
    }
  }, [mkErr, t]);

  const openDownload = useCallback(() => {
    const html = (stRef.current as any).html_url || FALLBACK_URL;
    api.openExternal(html);
  }, []);

  const reset = useCallback(() => setSt({ phase: "init" }), []);

  return { st, check, download, confirmApply, cancelApply, apply, openDownload, reset };
}
