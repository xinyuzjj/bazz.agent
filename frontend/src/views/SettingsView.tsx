import React, { useEffect, useState } from "react";
import { api, authHeaders } from "../api";
import { I } from "../components/icons";
import { CronPanel, McpPanel, ChannelPanel } from "./AdminPanels";
import { useI18n } from "../i18n/i18n";
import UpdatePanel from "../components/UpdatePanel";

const LLM_PROVIDERS = [
  // presets 与后端 src/llm.py 的 PROVIDERS 完全对齐（按 2026-09 各厂商官方 API 现役目录核实）。
  // 首项为切换厂商时的默认模型；改模型前可用「拉取模型」读取真实 /models 目录。
  { id: "openai", label: "OpenAI", labelEn: "OpenAI", base: "https://api.openai.com/v1", presets: ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.3-codex", "o3"] },
  { id: "anthropic", label: "Anthropic (Claude)", labelEn: "Anthropic (Claude)", base: "https://api.anthropic.com/v1", presets: ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"] },
  { id: "google", label: "Google (Gemini)", labelEn: "Google (Gemini)", base: "https://generativelanguage.googleapis.com/v1beta/openai", presets: ["gemini-3.1-pro", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3-flash", "gemini-3.1-flash-lite"] },
  { id: "xai", label: "xAI (Grok)", labelEn: "xAI (Grok)", base: "https://api.x.ai/v1", presets: ["grok-4.6", "grok-4.5", "grok-4.3"] },
  // DeepSeek 官方 API 现用 v4 系列：deepseek-v4-pro (1M, GA) / deepseek-v4-flash (1M, public beta) / deepseek-v4-flash-vision-exp。
  // 旧别名 deepseek-chat / deepseek-reasoner 已于 2026-07-24 下线，遇历史配置由后端 _LEGACY_MODEL_MAP 自动归一到 v4。
  { id: "deepseek", label: "DeepSeek", labelEn: "DeepSeek", base: "https://api.deepseek.com/v1", presets: ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"] },
  // moonshot-v1 全系与 kimi-k2.5 已于 2026-08-31 下线，现役为 kimi-k3 家族。
  { id: "moonshot", label: "Moonshot (Kimi)", labelEn: "Moonshot (Kimi)", base: "https://api.moonshot.cn/v1", presets: ["kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6"] },
  // 阿里云百炼：qwen-max/plus/turbo 为自动指向最新版的长期别名。
  { id: "qwen", label: "阿里云百炼 (Qwen)", labelEn: "Alibaba Bailian (Qwen)", base: "https://dashscope.aliyuncs.com/compatible-mode/v1", presets: ["qwen-max", "qwen-plus", "qwen-turbo"] },
  // 智谱 BigModel：GLM-5.3 旗舰（glm-4-plus/air/flash 已退役）。
  { id: "zhipu", label: "智谱 (GLM)", labelEn: "Zhipu (GLM)", base: "https://open.bigmodel.cn/api/paas/v4", presets: ["glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-5", "glm-4.7"] },
  { id: "mistral", label: "Mistral", labelEn: "Mistral", base: "https://api.mistral.ai/v1", presets: ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest", "codestral-latest"] },
  // 硅基流动：模型用「组织/模型」全名，精确前缀以「拉取模型」返回为准。
  { id: "siliconflow", label: "硅基流动 SiliconFlow", labelEn: "SiliconFlow", base: "https://api.siliconflow.cn/v1", presets: ["deepseek-ai/DeepSeek-V4-Pro", "deepseek-ai/DeepSeek-V4-Flash", "Qwen/Qwen3.6-27B", "Qwen/Qwen3.6-35B-A3B", "Pro/moonshotai/Kimi-K2.6", "Pro/zai-org/GLM-5.2"] },
  // OpenRouter：聚合网关，模型名「厂商/模型」，完整目录请用「拉取模型」。
  { id: "openrouter", label: "OpenRouter", labelEn: "OpenRouter", base: "https://openrouter.ai/api/v1", presets: ["openai/gpt-5.6-sol", "anthropic/claude-sonnet-5", "google/gemini-3.1-pro", "x-ai/grok-4.6"] },
  { id: "ollama", label: "Ollama (Local)", labelEn: "Ollama (Local)", base: "http://127.0.0.1:11434/v1", presets: ["qwen3", "llama3.3", "deepseek-r1", "gemma3"] },
  { id: "custom", label: "自定义 / OpenAI 兼容", labelEn: "Custom / OpenAI-compatible", base: "", presets: [] },
];

export function SettingsView({ settings, onSaved, onNav }: { settings: any; onSaved: (s: any) => void; onNav?: (n: string) => void }) {
  const { t, locale } = useI18n();
  const isEn = locale === "en";
  const [llm, setLlm] = useState<any>(() => ({ backup_models: [], aux: {}, ...(settings?.llm ?? {}) }));
  const [testRes, setTestRes] = useState<any>(null);
  const [testBusy, setTestBusy] = useState(false);
  const [modelList, setModelList] = useState<string[]>([]);
  const [plugins, setPlugins] = useState<any[]>([]);
  const [plBusy, setPlBusy] = useState<string>("");
  const [plMsg, setPlMsg] = useState<any>(null);
  const [gwList, setGwList] = useState<any[]>([]);
  const [gwBusy, setGwBusy] = useState(false);
  const [mcpToolCount, setMcpToolCount] = useState(0);
  const [deepThinking, setDeepThinking] = useState(true);
  const gwOnline = gwList.filter((g: any) => g.connected).length;
  const gwTotal = gwList.length;
  const gwOk = gwTotal > 0 && gwOnline === gwTotal;
  const loadGateways = async () => {
    setGwBusy(true);
    try {
      const r: any = await fetch("/api/gateways", { headers: authHeaders() }).then((x) => x.json());
      setGwList(Array.isArray(r?.gateways) ? r.gateways : []);
      setMcpToolCount(r?.mcp_tools ?? 0);
    } catch {}
    setGwBusy(false);
  };
  useEffect(() => { loadGateways(); }, []);

  useEffect(() => { if (settings?.llm) setLlm((p: any) => ({ backup_models: p.backup_models ?? [], ...settings.llm })); }, [settings]);

  const loadPlugins = async () => {
    try { const d: any = await api.plugins(); setPlugins(Array.isArray(d) ? d : []); } catch {}
  };
  useEffect(() => { loadPlugins(); }, []);
  useEffect(() => { (async () => { try { setDeepThinking(await api.getDeepThinking()); } catch {} })(); }, []);
  const runPlugin = async (p: any, cmd: string) => {
    if (plBusy) return;
    setPlBusy(`${p.id}.${cmd}`);
    try {
      const r: any = await api.pluginCommand(p.id, cmd, {});
      setPlMsg({ ok: r?.ok !== false, detail: (r?.text || r?.error || JSON.stringify(r)).slice(0, 600) });
    } catch (e: any) { setPlMsg({ ok: false, detail: e?.message ?? String(e) }); }
    finally { setPlBusy(""); }
  };
  const togglePlugin = async (p: any) => {
    try { await api.togglePlugin(p.id, !p.enabled); loadPlugins(); }
    catch (e: any) { setPlMsg({ ok: false, detail: e?.message ?? String(e) }); }
  };

  const save = async () => {
    const merged = { ...(settings || {}), llm };
    await api.saveSettings(merged);
    // 保存接口只回 {ok:true}，含不了 configured 标志 → 必须回拉一次让顶栏 LLM ARMED/OFFLINE 立刻翻转
    const fresh = await api.settings().catch(() => null);
    if (fresh) onSaved(fresh);
  };

  const test = async () => {
    setTestBusy(true);
    try {
      const r = await api.llmTest(llm);
      setTestRes(r);
    } catch (e: any) { setTestRes({ ok: false, detail: e?.message ?? String(e) }); }
    finally { setTestBusy(false); }
  };

  const pullModels = async () => {
    try {
      const r: any = await api.llmModels(llm.provider ?? "custom", llm.base_url ?? "", llm.api_key ?? "");
      setModelList(r.models ?? []);
    } catch (e: any) { setModelList([]); }
  };

  const prov = LLM_PROVIDERS.find((p) => p.id === llm.provider);

  return (
    <div className="p-5 space-y-4">
      {/* Header */}
      <div className="glass p-4">
        <div className="flex items-center gap-3">
          <I.Shield className="text-gold" size={22} />
          <div>
            <div className="font-mono font-bold text-ink text-[16px]">{t("settings.title")}</div>
            <div className="font-mono text-[11px] text-ink-dim mt-0.5">{t("settings.encHint")}</div>
          </div>
          <span className="ml-auto pill pill-gold"><I.Lock size={10} /> 100% ARMED · HARDENED</span>
          <div className="rounded-md border border-line bg-elevated/40 px-3 py-1.5 text-right">
            <div className="font-mono text-[10px] text-ink-dim">AES-256 GCM / CHACHA20-POLY1305</div>
            <div className="font-mono text-[10px] text-ink-mute">{t("settings.encSub")}</div>
          </div>
        </div>
      </div>

      {/* 软件更新 —— 置顶展示：打开设置第一眼可见（v1.2.10 起从页底迁到此处） */}
      <UpdatePanel />

      {/* 大模型配置（真实 · 多模型 + fallback 链） */}
      <div className="glass p-4">
        <div className="flex items-center gap-2 mb-3">
          <I.Cpu size={14} className="text-gold" />
          <span className="font-mono text-[12px] text-ink tracking-wider">{t("settings.llmTitle")}</span>
          <span className={`pill ${llm.configured ?? !!llm.api_key ? "pill-green" : "pill-red"}`}>
            <span className={`dot ${llm.configured ?? !!llm.api_key ? "dot-green live" : "dot-red"}`} />
            {llm.configured ?? !!llm.api_key ? t("settings.configured") : t("settings.unconfigured")}
          </span>
          <span className="prefix ml-auto">{t("settings.llmPrefix")}</span>
        </div>
        {testRes && (
          <div className={`mb-3 rounded-md border px-3 py-2 font-mono text-[11px] break-all whitespace-pre-wrap ${testRes.ok === false ? "border-red/40 bg-red/5 text-red" : "border-green/40 bg-green/5 text-green"}`}>
            <div className="flex items-center gap-2 mb-1 font-bold">
              {testRes.ok ? "✅ " : "❌ "}
              {testRes.ok ? t("settings.connOK") : t("settings.connFail")}
              {testRes.model_tried && <span className="text-ink-dim font-normal">{t("settings.testedModel", { m: testRes.model_tried })}</span>}
            </div>
            {testRes.ok && testRes.model_hint && testRes.model_hint !== testRes.model_tried && (
              <div className="mt-1 flex items-center gap-1.5 text-gold">
                <I.Alert size={11} />{t("settings.modelNotInCat", { m: testRes.model_tried, n: testRes.count ?? "?" })}
                <button onClick={() => setLlm({ ...llm, model: testRes.model_hint })}
                  className="underline hover:text-ink">{testRes.model_hint}</button>
              </div>
            )}
            <div>{testRes.detail ?? testRes.error ?? JSON.stringify(testRes)}</div>
          </div>
        )}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <div><label className="prefix block mb-1">{t("settings.provider")}</label>
            <select value={llm.provider ?? "custom"} onChange={(e) => {
              const p = LLM_PROVIDERS.find((x) => x.id === e.target.value);
              setLlm({ ...llm, provider: e.target.value, base_url: p?.base ?? llm.base_url, model: (p?.presets?.[0]) ?? llm.model });
            }} className="field">
              {LLM_PROVIDERS.map((p) => <option key={p.id} value={p.id}>{isEn ? (p.labelEn ?? p.label) : p.label}</option>)}
            </select></div>
          <div className="col-span-1"><label className="prefix block mb-1">{t("settings.baseUrl")}</label>
            <input value={llm.base_url ?? ""} onChange={(e) => setLlm({ ...llm, base_url: e.target.value })} placeholder="https://.../v1" className="field" /></div>
          <div className="col-span-1"><label className="prefix block mb-1">{t("settings.apiKey")}</label>
            <input type="password" value={llm.api_key ?? ""} onChange={(e) => setLlm({ ...llm, api_key: e.target.value })}
              placeholder={llm.configured ? t("settings.keySavedPh") : "sk-..."} className="field" /></div>
          <div><label className="prefix block mb-1">{t("settings.mainModel")}</label>
            <input list="llm-model-list" value={llm.model ?? ""} onChange={(e) => setLlm({ ...llm, model: e.target.value })} placeholder={t("settings.modelPh")} className="field" />
            <datalist id="llm-model-list">{modelList.map((m) => <option key={m} value={m} />)}</datalist></div>
        </div>
        <div className="mt-3">
          <div className="flex items-center justify-between mb-1.5">
            <label className="prefix">{t("settings.backupFallback")}</label>
            <button onClick={pullModels} className="text-[11px] font-mono text-gold hover:underline flex items-center gap-1"><I.Refresh size={10} /> {t("settings.pullModels")}</button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(modelList.length ? modelList : (prov?.presets ?? [])).map((m) => {
              const on = (llm.backup_models ?? []).includes(m);
              const isMain = m === llm.model;
              return (
                <button key={m} onClick={() => setLlm({
                  ...llm,
                  backup_models: on ? (llm.backup_models ?? []).filter((x: string) => x !== m) : [...(llm.backup_models ?? []), m],
                })}
                  className={`rounded-md border px-2.5 py-1 font-mono text-[11px] transition-colors ${isMain ? "border-gold text-gold" : on ? "bg-gold/10 border-gold/50 text-gold" : "border-line text-ink-dim hover:text-ink"}`}>
                  {m}{isMain ? " ★" : ""}
                </button>
              );
            })}
            {modelList.length === 0 && <span className="font-mono text-[10.5px] text-ink-mute">{t("settings.backupHint")}</span>}
          </div>
        </div>
        {/* aux 任务细分模型槽 + key_env 注入 */}
        <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2">
          <div><label className="prefix block mb-1">{t("settings.keyEnv")}</label>
            <input value={llm.key_env ?? ""} onChange={(e) => setLlm({ ...llm, key_env: e.target.value })}
              placeholder={t("settings.keyEnvPh")} className="field" /></div>
          <div><label className="prefix block mb-1">{t("settings.auxReasoning")}</label>
            <input list="llm-model-list" value={llm.aux?.reasoning ?? ""} onChange={(e) => setLlm({ ...llm, aux: { reasoning: e.target.value, vision: llm.aux?.vision ?? "", summarize: llm.aux?.summarize ?? "" } })}
              placeholder={t("settings.auxFollowPh")} className="field" /></div>
          <div><label className="prefix block mb-1">{t("settings.auxVision")}</label>
            <input list="llm-model-list" value={llm.aux?.vision ?? ""} onChange={(e) => setLlm({ ...llm, aux: { reasoning: llm.aux?.reasoning ?? "", vision: e.target.value, summarize: llm.aux?.summarize ?? "" } })}
              placeholder={t("settings.auxVisionPh")} className="field" /></div>
          <div><label className="prefix block mb-1">{t("settings.auxSummarize")}</label>
            <input list="llm-model-list" value={llm.aux?.summarize ?? ""} onChange={(e) => setLlm({ ...llm, aux: { reasoning: llm.aux?.reasoning ?? "", vision: llm.aux?.vision ?? "", summarize: e.target.value } })}
              placeholder={t("settings.auxSummarizePh")} className="field" /></div>
        </div>
        <div className="mt-4 flex items-center gap-2">
          <button onClick={test} disabled={testBusy} className="btn-ghost"><I.Zap size={12} /> {testBusy ? t("settings.testing") : t("settings.testConn")}</button>
          <button onClick={save} className="btn-gold"><I.Check size={12} /> {t("settings.saveCfg")}</button>
          <span className="prefix ml-auto">{t("settings.keyLocal")}</span>
        </div>
      </div>

      {/* 网关 (Gateway) 连接状态 —— Binance Agent OS 各通道 */}
      <div className="glass p-4">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-gold text-[13px]">🛰️</span>
          <span className="font-mono text-[12px] text-ink tracking-wider">{t("gw.title")}</span>
          <span className={`pill ${gwOk ? "pill-green" : "pill-red"}`}>
            <span className={`dot ${gwOk ? "dot-green live" : "dot-red"}`} />
            {gwOk ? "ALL SYSTEMS NOMINAL" : `${gwTotal - gwOnline} DOWN`}
          </span>
          <button onClick={loadGateways} disabled={gwBusy} className="ml-auto btn-ghost !px-2 !py-1 text-[11px]">
            <I.Download size={10} className="inline mr-1" />{gwBusy ? t("settings.refreshing") : t("settings.refresh")}
          </button>
        </div>
        <div className="font-mono text-[10px] text-ink-mute mb-2.5">{t("gw.sub")}</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
          {(gwList.length ? gwList : []) .map((g: any) => (
            <div key={g.name} className={`flex items-center gap-2 rounded-md border px-2.5 py-2 ${g.connected ? "border-line bg-card/40" : "border-red/30 bg-red/[0.04]"}`}>
              <span className={`dot ${g.connected ? "dot-green live" : "dot-red"}`} />
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[11.5px] text-ink truncate">{g.name}</div>
                {g.detail && <div className="font-mono text-[9.5px] text-ink-mute truncate" title={g.detail}>{g.detail}</div>}
              </div>
              <span className={`pill ${g.connected ? "pill-green" : "pill-dim"}`}>{g.connected ? t("gw.online") : t("gw.offline")}</span>
            </div>
          ))}
          {gwList.length === 0 && <div className="col-span-full font-mono text-[10.5px] text-ink-mute">{t("gw.none")}</div>}
        </div>
        {mcpToolCount > 0 && <div className="mt-2 font-mono text-[10px] text-ink-dim">{t("gw.mcpTools", { n: mcpToolCount })}</div>}
      </div>

      {/* Agent 思考行为 */}
      <div className="glass p-4">
        <div className="flex items-center gap-2 mb-3">
          <I.Cpu size={14} className="text-gold" />
          <span className="font-mono text-[12px] text-ink tracking-wider">{t("settings.deepTitle")}</span>
          <span className={`pill ${deepThinking ? "pill-green" : "pill-dim"}`}>
            <span className={`dot ${deepThinking ? "dot-green live" : "dot-dim"}`} />
            {deepThinking ? t("settings.deepOn") : t("settings.deepOff")}
          </span>
          <span className="prefix ml-auto">{t("settings.deepLike")}</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <label className={`rounded-md border p-3 cursor-pointer transition-colors ${deepThinking ? "border-gold/60 bg-gold/[0.06]" : "border-line bg-elevated/40"}`}>
            <div className="flex items-center gap-2 mb-1.5">
              <input type="checkbox" checked={deepThinking} onChange={async (e) => {
                const on = e.target.checked; setDeepThinking(on);
                try { await api.setDeepThinking(on); } catch {}
              }} className="accent-gold" />
              <span className="font-mono text-[12px] text-ink">{t("settings.deepToggleLabel")}</span>
            </div>
            <div className="font-mono text-[10.5px] text-ink-dim leading-relaxed">
              {t("settings.deepOnA")}
              <code className="px-1 mx-1 rounded bg-elevated/80">&lt;thinking&gt;</code>
              {t("settings.deepOnB")}
            </div>
          </label>
          <div className="rounded-md border border-line bg-elevated/40 p-3">
            <div className="font-mono text-[11px] text-ink-mute mb-1">{t("settings.thinkProtocol")}</div>
            <ol className="font-mono text-[10.5px] text-ink-dim leading-relaxed space-y-0.5 list-decimal list-inside">
              <li>{t("settings.step1")}</li>
              <li>{t("settings.step2")}</li>
              <li>{t("settings.step3")}</li>
              <li>{t("settings.step4")}</li>
              <li>{t("settings.step5")}</li>
              <li>{t("settings.step6")}</li>
            </ol>
          </div>
          <div className="rounded-md border border-line bg-elevated/40 p-3">
            <div className="font-mono text-[11px] text-ink-mute mb-1">{t("settings.toggleEffect")}</div>
            <div className="font-mono text-[10.5px] text-ink-dim leading-relaxed">
              <b className="text-ink">{t("settings.toggleOnColon")}</b>{t("settings.toggleOnDesc")}<br/>
              <b className="text-ink">{t("settings.toggleOffColon")}</b>{t("settings.toggleOffDesc")}
            </div>
          </div>
        </div>
      </div>

      {/* 4-TIER ENCLAVE 区块已删除 */}

      {/* System metrics 行已删除 */}

      {/* Skills footer 已删除（安装/运行统一在左侧「技能库」） */}

      {/* Agent 4 通道操作面板 + Cron + MCP（软件更新已上移至页首） */}
      <ChannelPanel onNav={onNav} />
      <CronPanel />
      <McpPanel />

      {/* Hermes 插件 SDK：plugins/<id>/plugin.json + main.py 命令 */}
      <div className="glass p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2"><I.Plug size={14} className="text-gold" /><span className="font-mono text-[12px] text-ink">{t("plugins.title")}</span>
            <span className="prefix">{t("plugins.sub")}</span>
          </div>
          <button onClick={loadPlugins} className="btn-ghost"><I.Refresh size={12} /> {t("settings.refresh")}</button>
        </div>
        {plMsg && (
          <div className={`mb-3 rounded-md border px-3 py-2 font-mono text-[11px] whitespace-pre-wrap break-all ${plMsg.ok === false ? "border-red/40 bg-red/5 text-red" : "border-green/40 bg-green/5 text-green"}`}>
            {plMsg.detail}
          </div>
        )}
        {plugins.length === 0 && (
          <div className="rounded-md border border-dashed border-line px-3 py-4 font-mono text-[11px] text-ink-mute">
            {t("plugins.emptyLead")} <span className="text-gold">plugins/&lt;id&gt;/plugin.json</span> + main.py{t("plugins.emptyTail")}
          </div>
        )}
        <div className="space-y-3">
          {plugins.map((p) => (
            <div key={p.id} className="rounded-md border border-line bg-card/40 p-3">
              <div className="flex items-center gap-2">
                <I.Plug size={13} className="text-gold" />
                <span className="font-mono text-[12.5px] font-semibold text-ink">{p.name}</span>
                <span className="pill pill-dim font-mono">v{p.version}</span>
                <span className="prefix">{p.id} · {p.author}</span>
                <button onClick={() => togglePlugin(p)} className="ml-auto flex items-center gap-1.5 text-[10px] font-mono text-ink-mute hover:text-ink">
                  LLM TOOLS <span className={`tgl ${p.enabled ? "on" : ""}`} />
                </button>
              </div>
              <div className="text-[11px] text-ink-dim mt-1">{p.description}</div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(p.commands ?? []).map(([cmd, desc]: [string, string]) => (
                  <button key={cmd} onClick={() => runPlugin(p, cmd)} disabled={!!plBusy}
                    className="rounded-md border border-line bg-elevated/40 px-2.5 py-1 font-mono text-[11px] text-ink-dim hover:border-gold/40 hover:text-gold transition-colors flex items-center gap-1.5">
                    <I.Play size={10} /> {p.id}.{cmd}
                    <span className="text-ink-mute/70 font-normal">· {desc}</span>
                  </button>
                ))}
                {plBusy === `${p.id}.` && <span className="font-mono text-[10px] text-gold">{t("plugins.running")}</span>}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
