import React, { useEffect, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";
import { CronPanel, McpPanel, ChannelPanel } from "./AdminPanels";
import { useT } from "../i18n/i18n";

const LLM_PROVIDERS = [
  { id: "openai", label: "OpenAI", base: "https://api.openai.com/v1", presets: ["gpt-4o", "gpt-4o-mini", "o1-preview"] },
  { id: "deepseek", label: "DeepSeek", base: "https://api.deepseek.com/v1", presets: ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"] },
  { id: "moonshot", label: "Moonshot (Kimi)", base: "https://api.moonshot.cn/v1", presets: ["moonshot-v1-8k", "moonshot-v1-32k"] },
  { id: "ollama", label: "Ollama (Local)", base: "http://127.0.0.1:11434/v1", presets: ["llama3.1", "qwen2.5"] },
  { id: "custom", label: "自定义 / OpenAI 兼容", base: "", presets: [] },
];

export function SettingsView({ settings, onSaved, onNav }: { settings: any; onSaved: (s: any) => void; onNav?: (n: string) => void }) {
  const t = useT();
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
      const r: any = await fetch("/api/gateways").then((x) => x.json());
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
            <div className="font-mono font-bold text-ink text-[16px]">系统统一设置与全局安全数据中</div>
            <div className="font-mono text-[11px] text-ink-dim mt-0.5">本地硬件均存即硬件级黑端加密，所有私钥、API 密钥与 LLM 凭据均通过 AES-256-GCM 密文加密保存。无任何明文通信及云端同步风险。</div>
          </div>
          <span className="ml-auto pill pill-gold"><I.Lock size={10} /> 100% ARMED · HARDENED</span>
          <div className="rounded-md border border-line bg-elevated/40 px-3 py-1.5 text-right">
            <div className="font-mono text-[10px] text-ink-dim">AES-256 GCM / CHACHA20-POLY1305</div>
            <div className="font-mono text-[10px] text-ink-mute">密文无需重置 · 物理访问保密型加密</div>
          </div>
        </div>
      </div>

      {/* 大模型配置（真实 · 多模型 + fallback 链） */}
      <div className="glass p-4">
        <div className="flex items-center gap-2 mb-3">
          <I.Cpu size={14} className="text-gold" />
          <span className="font-mono text-[12px] text-ink tracking-wider">大模型 (LLM) 配置</span>
          <span className={`pill ${llm.configured ?? !!llm.api_key ? "pill-green" : "pill-red"}`}>
            <span className={`dot ${llm.configured ?? !!llm.api_key ? "dot-green live" : "dot-red"}`} />
            {llm.configured ?? !!llm.api_key ? "已配置" : "未配置"}
          </span>
          <span className="prefix ml-auto">多模型 fallback · 密钥直连</span>
        </div>
        {testRes && (
          <div className={`mb-3 rounded-md border px-3 py-2 font-mono text-[11px] break-all whitespace-pre-wrap ${testRes.ok === false ? "border-red/40 bg-red/5 text-red" : "border-green/40 bg-green/5 text-green"}`}>
            <div className="flex items-center gap-2 mb-1 font-bold">
              {testRes.ok ? "✅ " : "❌ "}
              {testRes.ok ? "连接 OK" : "连接失败"}
              {testRes.model_tried && <span className="text-ink-dim font-normal">· 测试模型 {testRes.model_tried}</span>}
            </div>
            {testRes.ok && testRes.model_hint && testRes.model_hint !== testRes.model_tried && (
              <div className="mt-1 flex items-center gap-1.5 text-gold">
                <I.Alert size={11} />「{testRes.model_tried}」不在该提供方目录里（返回 {testRes.count ?? "?"} 个），已建议首项：
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
              {LLM_PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select></div>
          <div className="col-span-1"><label className="prefix block mb-1">{t("settings.baseUrl")}</label>
            <input value={llm.base_url ?? ""} onChange={(e) => setLlm({ ...llm, base_url: e.target.value })} placeholder="https://.../v1" className="field" /></div>
          <div className="col-span-1"><label className="prefix block mb-1">{t("settings.apiKey")}</label>
            <input type="password" value={llm.api_key ?? ""} onChange={(e) => setLlm({ ...llm, api_key: e.target.value })}
              placeholder={llm.configured ? "••••••••（已保存，留空不修改）" : "sk-..."} className="field" /></div>
          <div><label className="prefix block mb-1">主模型</label>
            <input list="llm-model-list" value={llm.model ?? ""} onChange={(e) => setLlm({ ...llm, model: e.target.value })} placeholder="模型名" className="field" />
            <datalist id="llm-model-list">{modelList.map((m) => <option key={m} value={m} />)}</datalist></div>
        </div>
        <div className="mt-3">
          <div className="flex items-center justify-between mb-1.5">
            <label className="prefix">备用模型 Fallback（主模型 429/5xx/超时时自动切换）</label>
            <button onClick={pullModels} className="text-[11px] font-mono text-gold hover:underline flex items-center gap-1"><I.Refresh size={10} /> 拉取模型</button>
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
            {modelList.length === 0 && <span className="font-mono text-[10.5px] text-ink-mute">点「拉取模型」或手动填主模型后点需要备用的模型</span>}
          </div>
        </div>
        {/* aux 任务细分模型槽 + key_env 注入 */}
        <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2">
          <div><label className="prefix block mb-1">KeyEnv 环境变量名（api_key 留空时从该环境变量注入）</label>
            <input value={llm.key_env ?? ""} onChange={(e) => setLlm({ ...llm, key_env: e.target.value })}
              placeholder="如 LLM_API_KEY / DEEPSEEK_API_KEY，留空=直填 Key" className="field" /></div>
          <div><label className="prefix block mb-1">Aux · 深度思考槽 (reasoning)</label>
            <input list="llm-model-list" value={llm.aux?.reasoning ?? ""} onChange={(e) => setLlm({ ...llm, aux: { reasoning: e.target.value, vision: llm.aux?.vision ?? "", summarize: llm.aux?.summarize ?? "" } })}
              placeholder="留空=跟随主模型链" className="field" /></div>
          <div><label className="prefix block mb-1">Aux · 视觉槽 (vision)</label>
            <input list="llm-model-list" value={llm.aux?.vision ?? ""} onChange={(e) => setLlm({ ...llm, aux: { reasoning: llm.aux?.reasoning ?? "", vision: e.target.value, summarize: llm.aux?.summarize ?? "" } })}
              placeholder="多模态模型，留空=跟随主模型链" className="field" /></div>
          <div><label className="prefix block mb-1">Aux · 摘要/压缩槽 (summarize)</label>
            <input list="llm-model-list" value={llm.aux?.summarize ?? ""} onChange={(e) => setLlm({ ...llm, aux: { reasoning: llm.aux?.reasoning ?? "", vision: llm.aux?.vision ?? "", summarize: e.target.value } })}
              placeholder="长文摘要/上下文压缩，留空=跟随主模型链" className="field" /></div>
        </div>
        <div className="mt-4 flex items-center gap-2">
          <button onClick={test} disabled={testBusy} className="btn-ghost"><I.Zap size={12} /> {testBusy ? "测试中…" : "测试连接"}</button>
          <button onClick={save} className="btn-gold"><I.Check size={12} /> 保存配置</button>
          <span className="prefix ml-auto">key 仅存本机，不跨设备同步</span>
        </div>
      </div>

      {/* 网关 (Gateway) 连接状态 —— Binance Agent OS 各通道 */}
      <div className="glass p-4">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-gold text-[13px]">🛰️</span>
          <span className="font-mono text-[12px] text-ink tracking-wider">网关 (Gateway) 连接状态</span>
          <span className={`pill ${gwOk ? "pill-green" : "pill-red"}`}>
            <span className={`dot ${gwOk ? "dot-green live" : "dot-red"}`} />
            {gwOk ? "ALL SYSTEMS NOMINAL" : `${gwTotal - gwOnline} DOWN`}
          </span>
          <button onClick={loadGateways} disabled={gwBusy} className="ml-auto btn-ghost !px-2 !py-1 text-[11px]">
            <I.Download size={10} className="inline mr-1" />{gwBusy ? "刷新中…" : "刷新"}
          </button>
        </div>
        <div className="font-mono text-[10px] text-ink-mute mb-2.5">MCP Agentic · x402 · Agentic Wallet · Skills Hub —— 向 Agent 暴露真实网关工具</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
          {(gwList.length ? gwList : []) .map((g: any) => (
            <div key={g.name} className={`flex items-center gap-2 rounded-md border px-2.5 py-2 ${g.connected ? "border-line bg-card/40" : "border-red/30 bg-red/[0.04]"}`}>
              <span className={`dot ${g.connected ? "dot-green live" : "dot-red"}`} />
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[11.5px] text-ink truncate">{g.name}</div>
                {g.detail && <div className="font-mono text-[9.5px] text-ink-mute truncate" title={g.detail}>{g.detail}</div>}
              </div>
              <span className={`pill ${g.connected ? "pill-green" : "pill-dim"}`}>{g.connected ? "在线" : "离线"}</span>
            </div>
          ))}
          {gwList.length === 0 && <div className="col-span-full font-mono text-[10.5px] text-ink-mute">未取到网关状态（后端未响应？）</div>}
        </div>
        {mcpToolCount > 0 && <div className="mt-2 font-mono text-[10px] text-ink-dim">MCP 运行时可用工具：{mcpToolCount} 个（授权后自动发现）</div>}
      </div>

      {/* Agent 思考行为 */}
      <div className="glass p-4">
        <div className="flex items-center gap-2 mb-3">
          <I.Cpu size={14} className="text-gold" />
          <span className="font-mono text-[12px] text-ink tracking-wider">Agent 思考行为</span>
          <span className={`pill ${deepThinking ? "pill-green" : "pill-dim"}`}>
            <span className={`dot ${deepThinking ? "dot-green live" : "dot-dim"}`} />
            {deepThinking ? "深度思考 ON" : "已关闭"}
          </span>
          <span className="prefix ml-auto">像 WorkBuddy 那样先想清楚再答</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <label className={`rounded-md border p-3 cursor-pointer transition-colors ${deepThinking ? "border-gold/60 bg-gold/[0.06]" : "border-line bg-elevated/40"}`}>
            <div className="flex items-center gap-2 mb-1.5">
              <input type="checkbox" checked={deepThinking} onChange={async (e) => {
                const on = e.target.checked; setDeepThinking(on);
                try { await api.setDeepThinking(on); } catch {}
              }} className="accent-gold" />
              <span className="font-mono text-[12px] text-ink">深度思考（Deep Thinking）</span>
            </div>
            <div className="font-mono text-[10.5px] text-ink-dim leading-relaxed">
              开启后：每轮请求注入 THINKING_PROTOCOL，让模型在正文前先写
              <code className="px-1 mx-1 rounded bg-elevated/80">&lt;thinking&gt;</code>
              块；前端「深度思考」折叠块会展示该过程并把它从正文剥离开。
              原生推理模型（deepseek-v4-pro / deepseek-v4-flash 思考模式 / claude-opus-5-thinking / o1 等）天然输出 reasoning_content，无需协议即可生效。
            </div>
          </label>
          <div className="rounded-md border border-line bg-elevated/40 p-3">
            <div className="font-mono text-[11px] text-ink-mute mb-1">思考协议六步</div>
            <ol className="font-mono text-[10.5px] text-ink-dim leading-relaxed space-y-0.5 list-decimal list-inside">
              <li>拆解：目标 / 约束 / 未知</li>
              <li>列至少 2 个假设及成立条件</li>
              <li>用工具数据支持/推翻</li>
              <li>推理：因果链 + 不确定性</li>
              <li>自检：反方质疑 / 边界</li>
              <li>收敛：结论 + 依据 + 风险</li>
            </ol>
          </div>
          <div className="rounded-md border border-line bg-elevated/40 p-3">
            <div className="font-mono text-[11px] text-ink-mute mb-1">开关效果</div>
            <div className="font-mono text-[10.5px] text-ink-dim leading-relaxed">
              <b className="text-ink">开</b>：token 预算自动扩到 ≥4000 以容纳思考 + 正文；多花一点预算但每轮都是真思考。<br/>
              <b className="text-ink">关</b>：跳过协议注入，仅在原生推理模型上仍可看到 reasoning；省 token 但浅思考。
            </div>
          </div>
        </div>
      </div>

      {/* 4-TIER ENCLAVE 区块已删除 */}

      {/* System metrics 行已删除 */}

      {/* Skills footer 已删除（安装/运行统一在左侧「技能库」） */}

      {/* Agent 4 通道操作面板 + Cron + MCP */}
      <ChannelPanel onNav={onNav} />
      <CronPanel />
      <McpPanel />

      {/* Hermes 插件 SDK：plugins/<id>/plugin.json + main.py 命令 */}
      <div className="glass p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2"><I.Plug size={14} className="text-gold" /><span className="font-mono text-[12px] text-ink">插件 (Plugins)</span>
            <span className="prefix">// Hermes 插件 SDK · 命令可被 LLM 直接调用</span>
          </div>
          <button onClick={loadPlugins} className="btn-ghost"><I.Refresh size={12} /> 刷新</button>
        </div>
        {plMsg && (
          <div className={`mb-3 rounded-md border px-3 py-2 font-mono text-[11px] whitespace-pre-wrap break-all ${plMsg.ok === false ? "border-red/40 bg-red/5 text-red" : "border-green/40 bg-green/5 text-green"}`}>
            {plMsg.detail}
          </div>
        )}
        {plugins.length === 0 && (
          <div className="rounded-md border border-dashed border-line px-3 py-4 font-mono text-[11px] text-ink-mute">
            暂无插件。在项目根建 <span className="text-gold">plugins/&lt;id&gt;/plugin.json</span> + main.py（每个 command 一个同名函数）即可自动注册为 LLM 工具。
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
                {plBusy === `${p.id}.` && <span className="font-mono text-[10px] text-gold">运行中…</span>}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function TierCard({ tier, name, status, body, rows, footer }: any) {
  const rws: string[][] = rows ?? [];
  const fts: string[] = footer ?? [];
  return (
    <div className="rounded-md border border-line bg-card/40 p-3">
      <div className="flex items-center justify-between">
        <span className="prefix">TIER {tier} // {name}</span>
        <span className="pill pill-gold">{status}</span>
      </div>
      <div className="mt-2 text-[13px] text-ink">{body}</div>
      <div className="mt-2 space-y-1">
        {rws.map(([a, b]: string[], i: number) => (
          <div key={i} className="flex items-center justify-between font-mono text-[11px]">
            <span className="text-ink-dim truncate pr-2">{a}</span>
            <span className={`pill ${b === "STANDBY" ? "pill-dim" : b === "[x]" || b === "" ? "pill-red" : "pill-green"} text-[9px]`}>{b}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 pt-2 border-t border-line space-y-0.5">
        {fts.map((f: string, i: number) => (
          <div key={i} className="font-mono text-[10px] text-ink-mute">{f}</div>
        ))}
      </div>
    </div>
  );
}

function Metric({ label, value, up }: { label: string; value: string; up?: boolean }) {
  return (
    <div className="glass p-3 flex items-center justify-between">
      <div className="flex items-center gap-2"><I.Bolt size={14} className={up ? "text-green" : "text-gold"} /><span className="prefix">{label}</span></div>
      <span className={`font-mono tabular text-[15px] font-bold ${up ? "up" : "text-ink"}`}>{value}</span>
    </div>
  );
}
