import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { I } from "./icons";
import { useI18n } from "../i18n/i18n";
import { pushToast } from "./Toasts";

/** v1.5.41 订阅登录：四家订阅直连提供方卡片（Hermes 风格）。
 *  copilot/nous = 设备码流；codex/anthropic-oauth = 浏览器 PKCE + 本地回环。 */

type SubStatus = { connected: boolean; account: string; auth_kind: string; expires_at: number };

const SUBS: { id: string; name: string; desc: { zh: string; en: string }; mode: "device" | "browser"; color: string }[] = [
  { id: "copilot", name: "GitHub Copilot", desc: { zh: "GitHub 账号设备码登录 · GPT-5.5 / Codex / Claude", en: "GitHub device-code login · GPT-5.5 / Codex / Claude" }, mode: "device", color: "text-[#8ab4f8]" },
  { id: "codex", name: "ChatGPT / Codex", desc: { zh: "ChatGPT Plus/Pro 浏览器登录 · GPT-5.5 / GPT-5.3-Codex", en: "ChatGPT Plus/Pro browser login · GPT-5.5 / GPT-5.3-Codex" }, mode: "browser", color: "text-[#9be8b0]" },
  { id: "anthropic-oauth", name: "Claude (Pro / Max)", desc: { zh: "Claude 订阅浏览器登录 · Sonnet / Opus / Haiku", en: "Claude subscription browser login · Sonnet / Opus / Haiku" }, mode: "browser", color: "text-[#f0b073]" },
  { id: "nous", name: "Nous Portal", desc: { zh: "Nous 订阅 · 300+ 模型统一网关（支持 sk- Key 兜底）", en: "Nous subscription · 300+ models (sk- key fallback)" }, mode: "device", color: "text-[#d9a5f5]" },
];

export function SubscriptionPanel({ onUse }: { onUse?: (providerId: string) => void }) {
  const { t, locale } = useI18n();
  const isEn = locale === "en";
  const [status, setStatus] = useState<Record<string, SubStatus>>({});
  const [busy, setBusy] = useState<string>("");
  const [flow, setFlow] = useState<any>(null); // {provider, mode, session, userCode, verificationUri, authUrl, detail}
  const pollRef = useRef<number | null>(null);
  const [nousKey, setNousKey] = useState("");
  const [nousKeyOpen, setNousKeyOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r: any = await api.llmAuthStatus();
      setStatus(r?.providers ?? {});
    } catch { /* 静默：状态卡不弹错 */ }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  // 轮询登录进度
  const stopPoll = useCallback(() => {
    if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
  }, []);
  useEffect(() => () => stopPoll(), [stopPoll]);

  const beginPoll = useCallback((provider: string, session: string, intervalMs: number) => {
    stopPoll();
    pollRef.current = window.setInterval(async () => {
      try {
        const r: any = await api.llmAuthPoll(provider, session);
        if (r?.ok && r?.status === "done") {
          stopPoll();
          setFlow(null);
          pushToast(t("sub.connectedTitle"), `${SUBS.find((s) => s.id === provider)?.name ?? provider} · ${r.account ?? ""}`, "ok");
          refresh();
        } else if (r?.ok === false) {
          stopPoll();
          setFlow(null);
          pushToast(t("sub.failTitle"), String(r?.detail ?? "").slice(0, 200), "bad");
        }
        // pending → 继续轮
      } catch (e: any) {
        stopPoll(); setFlow(null);
        pushToast(t("common.opFailed"), String(e?.message ?? e).slice(0, 160), "bad");
      }
    }, Math.max(1500, intervalMs));
  }, [refresh, stopPoll, t]);

  const connect = async (sub: typeof SUBS[number]) => {
    if (busy) return;
    setBusy(sub.id);
    try {
      const r: any = await api.llmAuthStart(sub.id);
      if (!r?.ok) { pushToast(t("sub.failTitle"), String(r?.detail ?? ""), "bad"); return; }
      if (r.mode === "device") {
        setFlow({ provider: sub.id, mode: "device", session: r.session,
                  userCode: r.user_code, verificationUri: r.verification_uri, detail: r.detail });
        beginPoll(sub.id, r.session, (r.interval ?? 3) * 1000);
      } else {
        // 浏览器 PKCE：先起本地回调轮询，再开系统默认浏览器（Electron 下裸弹窗会开应用内窗口）
        beginPoll(sub.id, r.session, 2000);
        api.openExternal(r.auth_url);
        setFlow({ provider: sub.id, mode: "browser", session: r.session, authUrl: r.auth_url, detail: r.detail });
      }
    } catch (e: any) {
      pushToast(t("common.opFailed"), String(e?.message ?? e).slice(0, 160), "bad");
    } finally { setBusy(""); }
  };

  const disconnect = async (id: string) => {
    try { await api.llmAuthLogout(id); refresh(); } catch (e: any) {
      pushToast(t("common.opFailed"), String(e?.message ?? e).slice(0, 160), "bad");
    }
  };

  const saveNousKey = async () => {
    if (!nousKey.trim()) return;
    try {
      const r: any = await api.llmAuthApiKey("nous", nousKey.trim());
      if (r?.ok) { setNousKey(""); setNousKeyOpen(false); refresh(); pushToast(t("sub.connectedTitle"), "Nous Portal (API Key)", "ok"); }
      else pushToast(t("sub.failTitle"), String(r?.detail ?? ""), "bad");
    } catch (e: any) { pushToast(t("common.opFailed"), String(e?.message ?? e).slice(0, 160), "bad"); }
  };

  const anyConnected = Object.values(status).some((s) => s?.connected);

  return (
    <div className="glass p-4">
      <div className="flex items-center gap-2 mb-1">
        <I.Lock size={14} className="text-gold" />
        <span className="font-mono text-[12px] text-ink tracking-wider">{t("sub.title")}</span>
        <span className={`pill ${anyConnected ? "pill-green" : "pill-dim"}`}>
          <span className={`dot ${anyConnected ? "dot-green live" : ""}`} />
          {isEn ? "Subscription OAuth" : "订阅直连 · 免 API Key"}
        </span>
      </div>
      <div className="font-mono text-[10px] text-ink-mute mb-2.5">{t("sub.sub")}</div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
        {SUBS.map((s) => {
          const st = status[s.id];
          const connected = !!st?.connected;
          return (
            <div key={s.id} className={`flex items-center gap-2.5 rounded-md border px-3 py-2.5 ${connected ? "border-green/40 bg-green/[0.05]" : "border-line bg-card/40"}`}>
              <span className={`dot ${connected ? "dot-green live" : "dot-red"}`} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className={`font-mono text-[12px] font-bold ${s.color}`}>{s.name}</span>
                  {connected && (
                    <span className="pill pill-green !py-0 !px-1.5 text-[9px]">✓ {t("sub.connected")}</span>
                  )}
                </div>
                <div className="font-mono text-[9.5px] text-ink-dim truncate" title={isEn ? s.desc.en : s.desc.zh}>
                  {st?.auth_kind === "api_key" ? (isEn ? "Manual API Key" : "手动 API Key") : (isEn ? s.desc.en : s.desc.zh)}
                  {connected && st?.account && <span className="text-ink-mute"> · {st.account}</span>}
                </div>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {connected ? (
                  <button onClick={() => disconnect(s.id)} className="btn-ghost !px-2 !py-1 text-[10.5px]">{t("sub.disconnect")}</button>
                ) : (
                  <button onClick={() => connect(s)} disabled={busy === s.id} className="btn-ghost !px-2 !py-1 text-[10.5px] !text-gold">
                    {busy === s.id ? "…" : t("sub.connect")}
                  </button>
                )}
                {s.id === "nous" && !connected && (
                  <button onClick={() => setNousKeyOpen((v) => !v)} className="btn-ghost !px-2 !py-1 text-[10.5px]" title={isEn ? "Use sk- API key" : "改用 sk- API Key"}>
                    <I.Key size={10} />
                  </button>
                )}
                {onUse && (
                  <button onClick={() => onUse(s.id)} className="btn-ghost !px-2 !py-1 text-[10.5px]" title={isEn ? "Use as provider" : "设为当前提供方"}>
                    <I.Cpu size={10} />
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {nousKeyOpen && (
        <div className="mt-2 flex items-center gap-2">
          <input type="password" value={nousKey} onChange={(e) => setNousKey(e.target.value)}
            placeholder="sk-..." className="field !py-1.5 flex-1" />
          <button onClick={saveNousKey} className="btn-gold !py-1.5 !px-3 text-[11px]">{t("settings.saveCfg")}</button>
        </div>
      )}

      {/* 设备码 / 等待回连 浮层 */}
      {flow && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 backdrop-blur-sm"
             onClick={(e) => { if (e.target === e.currentTarget) { stopPoll(); setFlow(null); } }}>
          <div className="glass p-5 max-w-md w-[92%] rounded-xl border border-line shadow-2xl">
            <div className="flex items-center gap-2 mb-3">
              <I.Lock size={16} className="text-gold" />
              <span className="font-mono text-[13px] font-bold text-ink">
                {SUBS.find((x) => x.id === flow.provider)?.name} · {t("sub.loginTitle")}
              </span>
            </div>
            {flow.mode === "device" ? (
              <>
                <div className="font-mono text-[11px] text-ink-dim mb-2">{t("sub.deviceStep1")}</div>
                <div className="flex items-center gap-3 mb-3">
                  <span className="font-mono text-[22px] font-bold tracking-[0.2em] text-gold select-all">{flow.userCode}</span>
                  <button onClick={() => { try { navigator.clipboard.writeText(flow.userCode); } catch { /* ignore */ } }}
                          className="btn-ghost !px-2 !py-1 text-[10.5px]">{t("sub.copy")}</button>
                </div>
                <div className="font-mono text-[11px] text-ink-dim mb-1">{t("sub.deviceStep2")}</div>
                <button onClick={() => api.openExternal(flow.verificationUri)}
                        className="btn-gold w-full !py-2 mt-1">{t("sub.openVerify")}</button>
              </>
            ) : (
              <>
                <div className="font-mono text-[11px] text-ink-dim mb-2">{t("sub.browserWait")}</div>
                <button onClick={() => api.openExternal(flow.authUrl)}
                        className="btn-gold w-full !py-2">{t("sub.reopenBrowser")}</button>
              </>
            )}
            <div className="mt-3 flex items-center justify-center gap-2 font-mono text-[10.5px] text-ink-mute">
              <span className="dot dot-green live" /> {t("sub.waiting")}
            </div>
            <button onClick={() => { stopPoll(); setFlow(null); }}
                    className="btn-ghost w-full !py-1.5 mt-3 text-[11px]">{t("common.cancel")}</button>
          </div>
        </div>
      )}
    </div>
  );
}
