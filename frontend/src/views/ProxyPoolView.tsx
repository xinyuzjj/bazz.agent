import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n/i18n";
import { confirmDialog } from "../components/ConfirmDialog";

// 代理池配置（v1.3.7）—— 仿 Ant Browser：导入代理 / 订阅 / 测速 / 一键启用；
// hysteria2 等内核协议由可下载的 mihomo 内核转发，http/socks 节点直连。
export function ProxyPoolView({ embedded = false }: { embedded?: boolean }) {
  const t = useT();
  const [pool, setPool] = useState<any>({ active_id: "", entries: [], kernel: { installed: false, running: false, version: "", download: {} } });
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [mode, setMode] = useState<"url" | "text">("url");
  const [subUrl, setSubUrl] = useState("");
  const [pasteText, setPasteText] = useState("");
  const dlTimer = useRef<any>(null);

  const load = useCallback(async () => {
    try { setPool(await api.proxies()); } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    load();
    const tm = setInterval(load, 15000);
    return () => clearInterval(tm);
  }, [load]);

  // 内核下载中 → 轮询下载进度
  useEffect(() => {
    const dl = pool?.kernel?.download;
    if (dl?.active && !dlTimer.current) {
      dlTimer.current = setInterval(async () => {
        try {
          const st = await api.kernelStatus();
          setPool((p: any) => ({ ...p, kernel: st }));
          if (!st?.download?.active) { clearInterval(dlTimer.current); dlTimer.current = null; load(); }
        } catch { /* ignore */ }
      }, 1500);
    }
    return () => { if (dlTimer.current && !dl?.active) { clearInterval(dlTimer.current); dlTimer.current = null; } };
  }, [pool?.kernel?.download?.active]); // eslint-disable-line

  const flash = (kind: "ok" | "err", text: string) => {
    setMsg({ kind, text });
    setTimeout(() => setMsg(null), 4000);
  };

  const kernel = pool.kernel || {};
  const dl = kernel.download || {};

  const doImport = async () => {
    setBusy("import");
    try {
      const body: any = mode === "url" ? { url: subUrl.trim() } : { text: pasteText };
      const r: any = await api.proxyImport(body);
      if (r?.ok) {
        flash("ok", mode === "url"
          ? t("proxy.importedSub", { n: r.added ?? 0, u: r.updated ?? 0 })
          : t("proxy.importedText", { n: r.added ?? 0, u: r.updated ?? 0 }));
        setSubUrl(""); setPasteText(""); setShowImport(false);
        await load();
      } else flash("err", r?.error || t("proxy.importFail"));
    } catch (e: any) { flash("err", e?.message || String(e)); }
    finally { setBusy(""); }
  };

  const refreshSubs = async () => {
    setBusy("refresh");
    try { await api.proxyRefresh(); await load(); flash("ok", t("proxy.refreshed")); }
    catch (e: any) { flash("err", e?.message || String(e)); }
    finally { setBusy(""); }
  };

  const testAll = async () => {
    setBusy("testall");
    try { await api.proxyTest(); await load(); flash("ok", t("proxy.testDone")); }
    catch (e: any) { flash("err", e?.message || String(e)); }
    finally { setBusy(""); }
  };

  const testOne = async (id: string) => {
    setBusy("t-" + id);
    try { await api.proxyTest(id); await load(); }
    catch (e: any) { flash("err", e?.message || String(e)); }
    finally { setBusy(""); }
  };

  const useNode = async (id: string) => {
    setBusy("u-" + id);
    try {
      const r: any = await api.proxyActive(id);
      if (r?.ok) { await load(); flash("ok", id ? t("proxy.switched") : t("proxy.directOn")); }
      else flash("err", r?.error || t("proxy.switchFail"));
    } catch (e: any) { flash("err", e?.message || String(e)); }
    finally { setBusy(""); }
  };

  const del = async (id: string) => {
    if (!(await confirmDialog(t("proxy.delConfirm"), { danger: true }))) return;
    setBusy("d-" + id);
    try { await api.proxyDelete(id); await load(); }
    catch (e: any) { flash("err", e?.message || String(e)); }
    finally { setBusy(""); }
  };

  const kernelBtn = async (fn: () => Promise<any>, key: string, okMsg?: string) => {
    setBusy(key);
    try {
      const r: any = await fn();
      if (r && r.ok === false) { flash("err", r.error || t("proxy.kernelFail")); }
      else if (okMsg) flash("ok", okMsg);
      await load();
    } catch (e: any) { flash("err", e?.message || String(e)); }
    finally { setBusy(""); }
  };

  // ---- 样式 ----
  const th = "px-3 py-2.5 text-left text-[11px] font-mono uppercase tracking-wider text-ink-dim whitespace-nowrap";
  const td = "px-3 py-2.5 text-[13px] text-ink whitespace-nowrap max-w-[260px] truncate";
  const btn = "px-3 h-8 rounded-md text-[12px] font-medium border transition-colors whitespace-nowrap";
  const btnGhost = `${btn} border-line text-ink-dim hover:text-ink hover:bg-elevated`;
  const btnGold = `${btn} border-gold/60 bg-gold/15 text-gold hover:bg-gold/25`;

  const statusCell = (e: any) => {
    if (e.status === "ok") return <span className="text-emerald-400 font-mono text-[12px]">● {e.latency_ms ?? "-"}ms</span>;
    if (e.status === "dead") return <span className="text-red-400 font-mono text-[12px]" title={e.error}>● {t("proxy.dead")}</span>;
    return <span className="text-ink-dim font-mono text-[12px]">○ {t("proxy.untested")}</span>;
  };

  const protoBadge = (e: any) => (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[11px] font-mono ${e.direct ? "bg-sky-500/15 text-sky-300" : "bg-violet-500/15 text-violet-300"}`}>
      {e.proto}
    </span>
  );

  return (
    <div className={embedded ? "" : "max-w-[1200px] mx-auto p-5"}>
      {/* 标题 + 操作 */}
      <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
        {!embedded && <h1 className="text-[18px] font-bold text-ink">{t("proxy.title")}</h1>}
        <div className="flex items-center gap-2 flex-wrap">
          {/* 内核状态与控制 */}
          <span className="text-[12px] font-mono text-ink-dim">
            {t("proxy.kernel")}:
            <span className={kernel.running ? "text-emerald-400" : kernel.installed ? "text-gold" : "text-ink-dim"}>
              {kernel.running ? ` ${t("proxy.kRunning")}${kernel.version ? " " + kernel.version : ""} :${kernel.mixed_port}`
                : kernel.installed ? ` ${t("proxy.kStopped")}${kernel.version ? " " + kernel.version : ""}`
                : ` ${t("proxy.kNone")}`}
            </span>
          </span>
          {!kernel.installed && (
            <button className={btnGold} disabled={!!busy || dl.active}
              onClick={() => kernelBtn(() => api.kernelDownload(), "k-dl")}>
              {dl.active ? t("proxy.downloading") : t("proxy.downloadKernel")}
            </button>
          )}
          {kernel.installed && !kernel.running && (
            <button className={btnGhost} disabled={!!busy} onClick={() => kernelBtn(() => api.kernelStart(), "k-start", t("proxy.kStarted"))}>
              {t("proxy.startKernel")}
            </button>
          )}
          {kernel.running && (
            <button className={btnGhost} disabled={!!busy} onClick={() => kernelBtn(() => api.kernelStop(), "k-stop")}>
              {t("proxy.stopKernel")}
            </button>
          )}
          <button className={btnGhost} disabled={!!busy} onClick={refreshSubs}>{t("proxy.refreshSub")}</button>
          <button className={btnGhost} disabled={!!busy} onClick={testAll}>{busy === "testall" ? t("proxy.testing") : t("proxy.testAll")}</button>
          <button className={btnGold} onClick={() => { setMode("url"); setShowImport(true); }}>{t("proxy.import")}</button>
        </div>
      </div>

      {/* 下载进度 */}
      {dl.active && (
        <div className="mb-3 rounded-lg border border-line bg-elevated p-3">
          <div className="flex justify-between text-[12px] font-mono text-ink-dim mb-1.5">
            <span>{t("proxy.downloadingKernel")}</span>
            <span>{dl.total ? `${((dl.done / dl.total) * 100).toFixed(0)}%` : ""}</span>
          </div>
          <div className="h-1.5 rounded bg-black/40 overflow-hidden">
            <div className="h-full bg-gold transition-all" style={{ width: dl.total ? `${(dl.done / dl.total) * 100}%` : "15%" }} />
          </div>
          {dl.error && <div className="text-[11px] text-amber-400 mt-1.5">{dl.error}</div>}
        </div>
      )}
      {dl.ready && !dl.active && (
        <div className="mb-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-[12px] text-emerald-300">
          {t("proxy.kernelReady")}
        </div>
      )}

      {msg && (
        <div className={`mb-3 rounded-lg border px-3 py-2 text-[12px] ${msg.kind === "ok" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" : "border-red-500/30 bg-red-500/10 text-red-300"}`}>
          {msg.text}
        </div>
      )}

      {/* 节点表 */}
      <div className="rounded-xl border border-line bg-elevated/40 overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-line bg-black/20">
              <th className={th}>{t("proxy.colName")}</th>
              <th className={th}>{t("proxy.colGroup")}</th>
              <th className={th}>{t("proxy.colSource")}</th>
              <th className={th}>{t("proxy.colType")}</th>
              <th className={th}>{t("proxy.colServer")}</th>
              <th className={th}>{t("proxy.colLatency")}</th>
              <th className={th}>{t("proxy.colStatus")}</th>
              <th className={`${th} text-right`}>{t("proxy.colActions")}</th>
            </tr>
          </thead>
          <tbody>
            {/* 直连行 */}
            <tr className={`border-b border-line/60 ${!pool.active_id ? "bg-gold/10" : ""}`}>
              <td className={td}>
                <span className="text-ink font-medium">{t("proxy.directName")}</span>
              </td>
              <td className={td}>-</td><td className={td}>-</td>
              <td className={td}><span className="px-1.5 py-0.5 rounded text-[11px] font-mono bg-black/30 text-ink-dim">direct</span></td>
              <td className={td}>-</td>
              <td className={td}><span className="font-mono text-[12px] text-ink-dim">{t("proxy.na")}</span></td>
              <td className={td}><span className="font-mono text-[12px] text-ink-dim">{t("proxy.na")}</span></td>
              <td className={`${td} text-right`}>
                <button className={!pool.active_id ? btnGold : btnGhost} disabled={!!busy} onClick={() => useNode("")}>
                  {!pool.active_id ? t("proxy.inUse") : t("proxy.use")}
                </button>
              </td>
            </tr>
            {pool.entries?.map((e: any) => {
              const active = pool.active_id === e.id;
              const canUse = e.direct || kernel.installed;
              return (
                <tr key={e.id} className={`border-b border-line/40 hover:bg-white/[0.02] ${active ? "bg-gold/10" : ""}`}>
                  <td className={td}>
                    <div className="font-medium text-ink truncate" title={e.note || e.name}>{e.name}</div>
                    {!e.direct && !kernel.installed && <div className="text-[10px] text-amber-400/80 mt-0.5">{t("proxy.needKernel")}</div>}
                    {e.error && e.status === "dead" && <div className="text-[10px] text-red-400/70 mt-0.5 truncate" title={e.error}>{e.error}</div>}
                  </td>
                  <td className={td}>{e.group}</td>
                  <td className={td}><span className="text-ink-dim text-[12px]">{e.source}</span></td>
                  <td className={td}>{protoBadge(e)}</td>
                  <td className={td}><span className="font-mono text-[12px] text-ink-dim">{e.server || "-"}{e.port ? `:${e.port}` : ""}</span></td>
                  <td className={td}>{statusCell(e)}</td>
                  <td className={td}>{statusCell(e)}</td>
                  <td className={`${td} text-right`}>
                    <div className="inline-flex gap-1.5">
                      <button className={btnGhost} disabled={!!busy} onClick={() => testOne(e.id)}>
                        {busy === "t-" + e.id ? t("proxy.testing") : t("proxy.test")}
                      </button>
                      <button className={active ? btnGold : btnGhost} disabled={!!busy || !canUse}
                        title={canUse ? "" : t("proxy.needKernel")}
                        onClick={() => useNode(e.id)}>
                        {active ? t("proxy.inUse") : t("proxy.use")}
                      </button>
                      <button className={`${btnGhost} hover:text-red-400 hover:border-red-500/40`} disabled={!!busy} onClick={() => del(e.id)}>
                        {t("proxy.delete")}
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
            {(!pool.entries || pool.entries.length === 0) && (
              <tr><td colSpan={8} className="px-3 py-10 text-center text-[13px] text-ink-dim">{t("proxy.empty")}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-ink-dim">
        {t("proxy.hint")}
      </p>

      {/* 导入弹窗 */}
      {showImport && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={() => setShowImport(false)}>
          <div className="w-full max-w-[560px] rounded-xl border border-line bg-canvas p-5" onClick={(ev) => ev.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-[15px] font-bold text-ink">{t("proxy.importTitle")}</h2>
              <button className="text-ink-dim hover:text-ink" onClick={() => setShowImport(false)}>✕</button>
            </div>
            <div className="flex gap-2 mb-3">
              <button className={mode === "url" ? btnGold : btnGhost} onClick={() => setMode("url")}>{t("proxy.modeSub")}</button>
              <button className={mode === "text" ? btnGold : btnGhost} onClick={() => setMode("text")}>{t("proxy.modeText")}</button>
            </div>
            {mode === "url" ? (
              <>
                <input className="w-full h-9 px-3 rounded-md bg-black/30 border border-line text-[13px] text-ink outline-none focus:border-gold/60"
                  placeholder="https://your-subscription-link…"
                  value={subUrl} onChange={(e) => setSubUrl(e.target.value)} />
                <p className="mt-2 text-[11px] text-ink-dim">{t("proxy.subHint")}</p>
              </>
            ) : (
              <>
                <textarea className="w-full h-40 p-3 rounded-md bg-black/30 border border-line text-[12px] font-mono text-ink outline-none focus:border-gold/60 resize-none"
                  placeholder={"http://user:pass@host:port\nsocks5://host:port\nhost:port"}
                  value={pasteText} onChange={(e) => setPasteText(e.target.value)} />
                <p className="mt-2 text-[11px] text-ink-dim">{t("proxy.textHint")}</p>
              </>
            )}
            <div className="flex justify-end gap-2 mt-4">
              <button className={btnGhost} onClick={() => setShowImport(false)}>{t("proxy.cancel")}</button>
              <button className={btnGold} disabled={busy === "import" || (mode === "url" ? !subUrl.trim() : !pasteText.trim())}
                onClick={doImport}>
                {busy === "import" ? t("proxy.importing") : t("proxy.importDo")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
