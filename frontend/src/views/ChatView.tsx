import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, streamChat, authHeaders } from "../api";
import { I } from "../components/icons";
import { AgentSigninCard, ChainWalletPanel, Spin } from "../components/WalletBits";
import { AgentAvatar, Blobatar } from "../components/Blobatar";
import { useT } from "../i18n/i18n";
import { pushToast } from "../components/Toasts";

type ChatMsg = {
  id: string; role: "user" | "assistant"; text: string;
  reasoning?: string;
  model?: string;     // 该条回复实际命中的模型（snapshot/model 落库回读）
  tools?: { name: string; args?: any; ok?: boolean; detail?: string }[];
  approval?: { id: string; title: string; label: string; action: string; signal?: any };
  persona?: string;   // 群聊里该条回复来自哪个 Agent
  pending?: boolean; note?: string; ts: number;
  images?: string[];  // 用户附图 dataURL（仅本地会话气泡展示）
};

type LeftTab = "sessions" | "bots" | "files" | "terminal";

type AgentCfg = { model: string; tone: string; skills: string[]; tools: string[]; prompt?: string };
// Bot 工具子集候选（Hermes bots：每档案可声明可用的工具白名单；留空 = 全部）
const AGENT_TOOLS = [
  "scan_market", "market_quote", "check_risk", "propose_trade",
  "explain_x402", "list_skills", "run_skill", "onchain_ops",
  "memory_write", "fetch_url", "read_file", "write_file",
  "run_command", "gateway_status", "mcp_call", "get_help",
];
type Agent = { id: string; name: string; title: string; description: string; avatar: string; color: string; config?: AgentCfg };

const AVATARS = ["🤖", "🧭", "🦊", "💰", "🛡️", "🧠", "⚡", "🐙", "📈", "🧿", "👾", "🔥"];
const COLORS = ["#F0B90B", "#0ECB81", "#F6465D", "#8B5CF6", "#3B82F6", "#EC4899", "#5E6673", "#F59E0B"];

export function ChatView({
  convId, conversations, setConversations, setConvId, onTrade, onNav, pendingMsg, onPendingConsumed,
}: {
  convId: string | null; conversations: any[]; setConversations: (c: any[]) => void; setConvId: (id: string | null) => void;
  onTrade?: (symbol: string) => void; onNav?: (nav: string) => void;
  pendingMsg?: string; onPendingConsumed?: () => void;
}) {
  const t = useT();
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [autoExec, setAutoExec] = useState(true);  // 默认开：首页 = 全能模式（沙箱工具自动放行）
  // @ 提及自动补全（Hermes：输入 @ 弹出 Agent 选项）
  const inputRef = useRef<HTMLInputElement>(null);
  const [mention, setMention] = useState<{ at: number; q: string } | null>(null); // @ 起始位置 + 已输入查询
  const [mHi, setMHi] = useState(0); // 高亮索引
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null); // Stop 中断当前生成
  const approvalRef = useRef<any>(null); // 待确认动作（确认后随下一条消息发回后端执行）
  // 流式文本/思考累积缓冲：text delta 同帧多发会被 React 批处理吞掉，先累积到 ref，requestAnimationFrame 内 setMessages commit，每帧最多 1 次
  const textBufRef = useRef<Map<string, string>>(new Map());
  const reasonBufRef = useRef<Map<string, string>>(new Map());
  const textRafRef = useRef<Map<string, number>>(new Map());
  const [editSel, setEditSel] = useState<{ id: string; text: string } | null>(null); // 正在“编辑重发”的用户消息
  const [renamingId, setRenamingId] = useState<string | null>(null); // 正在改名中的会话 id
  const [renameDraft, setRenameDraft] = useState("");
  const [palOpen, setPalOpen] = useState(false);  // ⌘K 命令面板
  const [palQ, setPalQ] = useState("");
  const palRef = useRef<HTMLInputElement>(null);
  const [leftTab, setLeftTab] = useState<LeftTab>("sessions");
  const [log, setLog] = useState<{ ts: string; line: string }[]>([]);
  const [files, setFiles] = useState<any[]>([]);
  const [fileCwd, setFileCwd] = useState("");
  const [fileModal, setFileModal] = useState<{ name: string; path: string; size: number; is_text: boolean; content: string; too_large?: boolean; loading: boolean; error?: string } | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [convMenu, setConvMenu] = useState<string | null>(null); // 当前展开 ⋮ 菜单的会话 id
  // v1.4.3 会话搜索：防抖调 /api/conversations/search；convHits 非空 = 搜索态（列表显示命中结果）
  const [convQuery, setConvQuery] = useState("");
  const [convHits, setConvHits] = useState<any[] | null>(null);
  const [wlItems, setWlItems] = useState<string[]>([]);
  const [wlOpen, setWlOpen] = useState(false);
  const [listening, setListening] = useState(false); // Web Speech 录音中
  const recRef = useRef<any>(null);
  // 附件：图片走 dataURL（粘贴/选择，发送给后端直读或 OCR）；其它文件保留 File 引用发送时取文本摘录
  const [attachments, setAttachments] = useState<{ name: string; size: number; file: File; kind: "image" | "file"; dataUrl?: string }[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [loadingHist, setLoadingHist] = useState(false);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [activeBot, setActiveBot] = useState<Agent | null>(null);
  const [editBot, setEditBot] = useState<Agent | null | "new">(null); // null=关, 'new'=新建
  const [form, setForm] = useState({ name: "", title: "", description: "", avatar: "🤖", color: "#F0B90B" });
  const [cfg, setCfg] = useState<AgentCfg>({ model: "", tone: "", skills: [], tools: [], prompt: "" });
  const [availSkills, setAvailSkills] = useState<any[]>([]);
  const [botMsg, setBotMsg] = useState("");
  // 会话级模型选择：默认取 provider_snapshot.model（防漂移）；下拉切换后仅本会话生效
  const [sessionModel, setSessionModel] = useState<string>("");
  const [defaultModel, setDefaultModel] = useState<string>("");
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  // 钱包快捷弹层（Agent 钱包扫码 / 链上 CEX）——懒加载，开弹层才拉状态
  const [walletOpen, setWalletOpen] = useState(false);
  const [walletBusy, setWalletBusy] = useState(false);
  const [walletAgent, setWalletAgent] = useState<any>(null);
  const [walletRuntime, setWalletRuntime] = useState<any>(null);
  const [walletCex, setWalletCex] = useState<any>(null);
  const walletAgentConn = !!walletAgent?.status?.connected;
  const walletAgentReady = !!(walletRuntime?.bundled || walletAgent?.cli?.installed);
  const walletCexCfg = !!walletCex?.configured;

  const loadWalletLite = useCallback(async () => {
    setWalletBusy(true);
    try {
      const [a, r, c] = await Promise.allSettled([
        api.wallet().catch(() => null),      // 后端 30s TTL 缓存，秒回
        api.walletRuntime().catch(() => null),
        api.web3Status().catch(() => null),
      ]);
      setWalletAgent(a.status === "fulfilled" ? a.value : null);
      setWalletRuntime(r.status === "fulfilled" ? r.value : null);
      setWalletCex(c.status === "fulfilled" ? c.value : null);
    } catch { /* 静默 */ } finally { setWalletBusy(false); }
  }, []);

  const toggleWallet = () => {
    const next = !walletOpen;
    setWalletOpen(next);
    if (next) loadWalletLite();
  };
  // 未读（Hermes activity watermark）：记录每个 Agent 上次"已读"时间戳
  const [seen, setSeen] = useState<Record<string, number>>(() => {
    try { return JSON.parse(localStorage.getItem("scout-bot-seen") || "{}") || {}; } catch { return {}; }
  });
  const [activity, setActivity] = useState<Record<string, number>>({}); // name -> last assistant ts(ms)
  const [grouping, setGrouping] = useState(false); // 群聊进行中
  // 群聊房间（Hermes room log）
  const [rooms, setRooms] = useState<any[]>([]);
  const [groupFormOpen, setGroupFormOpen] = useState(false);
  const [gName, setGName] = useState("");
  const [gPicked, setGPicked] = useState<string[]>([]);
  const [roomMsg, setRoomMsg] = useState("");
  const markSeen = (name: string) => {
    if (!name) return;
    setSeen((prev) => {
      if (prev[name] && prev[name] > Date.now() - 1000) return prev;
      const nx = { ...prev, [name]: Date.now() };
      localStorage.setItem("scout-bot-seen", JSON.stringify(nx));
      return nx;
    });
  };
  const scrollRef = useRef<HTMLDivElement>(null);
  const curConv = (conversations ?? []).find((c: any) => c.id === convId);
  const roomFallback = (rooms ?? []).find((r: any) => r.id === convId);
  const inRoom = (curConv && curConv.kind === "room") || (roomFallback && roomFallback.kind === "room");
  const roomInfo = (curConv && curConv.kind === "room" ? curConv : null) || (roomFallback && roomFallback.kind === "room" ? roomFallback : null) || null;

  // ============ @ 提及自动补全 ============
  // 候选池：房间内 = 房间成员（含 @everyone 全员）；DM = 全部 Agent
  const mentionPool: { name: string; avatar: string; color: string; title: string }[] = (() => {
    if (!mention) return [];
    let names: string[] = [];
    if (inRoom && roomInfo) {
      names = [...new Set(((roomInfo.members ?? []) as string[]).map((x) => x.replace(/^@/, "")))];
    } else {
      names = agents.map((a) => a.name);
    }
    const all: { name: string; avatar: string; color: string; title: string }[] = [];
    if (inRoom && roomInfo && names.length) all.push({ name: "everyone", avatar: "👥", color: "#F0B90B", title: t("chat.memberAll") });
    for (const n of names) {
      const ag = agents.find((a) => a.name === n);
      all.push({ name: n, avatar: ag?.avatar ?? "👤", color: ag?.color ?? "#8B8F98", title: ag?.title ?? "Agent" });
    }
    const q = (mention.q || "").toLowerCase();
    const list = q ? all.filter((x) => x.name.toLowerCase().includes(q)) : all;
    if (mHi >= list.length) setMHi(0); // 列表收缩时防越界
    return list.slice(0, 12);
  })();
  // 文本中最后一个 @ 处于"正在输入"状态（光标在末尾且 @ 后无空白/无第二个 @）→ 弹出
  const scanMention = (v: string) => {
    const m = /(?:^|[\s,，。！？、；：;:!?/（(])@([^\s@,，。！？、；：;:!?/）)]*)$/.exec(v);
    if (m && (m[1].length > 0 || v.endsWith("@"))) {
      const at = v.length - m[0].length + m[0].lastIndexOf("@");
      const nq = m[1] ?? "";
      setMention((prev) => (prev && prev.at === at && prev.q === nq ? prev : { at, q: nq }));
      setMHi(0);
    } else {
      setMention(null);
    }
  };
  const pickMention = (c: { name: string }) => {
    if (!mention) return;
    setInput((prev) => {
      const head = prev.slice(0, mention.at);
      const rest = prev.slice(mention.at).replace(/^@[^\s@]*/, "");
      return head + "@" + c.name + " " + rest;
    });
    setMention(null);
    requestAnimationFrame(() => inputRef.current?.focus());
  };
  const onMentionKey = (e: React.KeyboardEvent) => {
    if (!mention) return false;
    const n = mentionPool.length;
    if (e.key === "ArrowDown") { e.preventDefault(); if (n) setMHi((h) => (h + 1) % n); return true; }
    if (e.key === "ArrowUp") { e.preventDefault(); if (n) setMHi((h) => (h - 1 + n) % n); return true; }
    if (e.key === "Enter" || e.key === "Tab") {
      if (n && mentionPool[mHi % n]) { e.preventDefault(); pickMention(mentionPool[mHi % n]); return true; }
      if (e.key === "Enter") { setMention(null); return false; } // 无匹配 → 正常发送
    }
    if (e.key === "Escape") { e.preventDefault(); setMention(null); return true; }
    return false;
  };

  // ============ 会话 ============
  const refreshList = () => {
    api.conversations(true).then((d: any) => setConversations(Array.isArray(d) ? d : d?.items ?? [])).catch(() => {});
    api.rooms().then((d: any) => setRooms(Array.isArray(d?.rooms) ? d.rooms : [])).catch(() => {});
  };
  // 会话搜索（v1.4.3）：300ms 防抖；清空即回到普通列表
  useEffect(() => {
    const q = convQuery.trim();
    if (!q) { setConvHits(null); return; }
    const id = setTimeout(() => {
      api.searchConversations(q).then((d: any) => setConvHits(Array.isArray(d) ? d : [])).catch(() => setConvHits(null));
    }, 300);
    return () => clearTimeout(id);
  }, [convQuery]);
  // 房间（群聊）加载/开关
  useEffect(() => { if (leftTab === "bots") { api.rooms().then((d: any) => setRooms(Array.isArray(d?.rooms) ? d.rooms : [])).catch(() => {}); } }, [leftTab]);
  const openRoomConv = (c: any) => {
    if (streaming || grouping) return;
    setActiveBot(null); // 进房间时退出单人 persona
    setConvId(c.id);
    loadHistory(c.id);
  };
  const createRoom = async () => {
    if (!gName.trim()) { setRoomMsg(t("chat.roomNeedName")); return; }
    if (gPicked.length < 2) { setRoomMsg(t("chat.roomNeed2")); return; }
    try {
      const r: any = await api.createRoom({ name: gName.trim(), members: gPicked });
      setGroupFormOpen(false); setRoomMsg("");
      setConvId(r.id);
      refreshList();
      loadHistory(r.id);
    } catch (e: any) { setRoomMsg(t("chat.roomCreateFail", { err: e?.message ?? e })); }
  };
  const togglePick = (name: string) =>
    setGPicked((prev) => prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]);
  const delRoom = async (rid: string) => {
    if (!window.confirm(t("chat.confirmDelRoom"))) return;
    try { await api.deleteRoom(rid); } catch {}
    refreshList();
    if (convId === rid) { setConvId(null); setMessages([]); }
  };
  const roomMemberAdd = async (rid: string, name: string) => {
    try { const r: any = await api.roomMembers(rid, "add", name); if (r?.members) setConvId(r.id); } catch {}
    refreshList();
  };
  const loadHistory = async (cid: string) => {
    setLoadingHist(true);
    pushLog(`LOAD > ${t("chat.logLoad", { id: cid.slice(0, 8) })}`);
    try {
      const r: any = await api.getConversation(cid);
      const msgs: ChatMsg[] = (r?.messages ?? []).map((m: any, i: number) => ({
        id: `h-${cid}-${i}`, role: m.role === "user" ? "user" : "assistant",
        text: m.content ?? "", tools: Array.isArray(m.tools) ? m.tools : [],
        persona: m.persona || "", reasoning: m.reasoning || "", model: m.model || "", ts: Date.now() + i,
      }));
      setMessages(msgs);
    } catch (e: any) { pushLog(`LOAD_ERR > ${e?.message ?? e}`); setMessages([]); }
    finally { setLoadingHist(false); }
  };
  const selectConv = async (cid: string) => {
    if (streaming || grouping) return;
    setConvId(cid);
    const conv = (conversations ?? []).find((c: any) => c.id === cid);
    if (conv?.persona) markSeen(conv.persona);
    await loadHistory(cid);
  };
  // 活动轮询 → 未读徽标（Hermes 水位线）
  useEffect(() => {
    const tick = async () => {
      try { const d: any = await api.botActivity(); setActivity(Object.fromEntries((d?.activity ?? []).map((a: any) => [a.persona, Math.round(a.last_ts * 1000)]))); } catch {}
    };
    tick();
    const t = setInterval(tick, 8000);
    return () => clearInterval(t);
  }, []);
  const unreadOf = (name: string) => {
    const last = activity[name];
    if (!last) return 0;
    if (activeBot?.name === name) return 0; // 正在与它聊 = 已读
    return last > (seen[name] || 0) ? 1 : 0;
  };
  const newConv = async () => {
    if (streaming) return;
    // 处于某个 bot 的专属界面：直接清空 = 新开一段该 bot 的一对一会话
    // （首条消息落库时后端自动带 persona=该 bot，见 /api/chat/stream）
    if (activeBot) { markSeen(activeBot.name); setConvId(null); setMessages([]); return; }
    try {
      const r: any = await api.newConversation();
      setMessages([]); pushLog(`NEW > ${r?.id?.slice(0, 8)}`);
      setConvId(r.id ?? null); refreshList();
    } catch (e: any) { pushToast(t("common.opFailed"), String(e?.message ?? e).slice(0, 120), "bad"); }
  };
  const delConv = async (cid: string) => {
    if (streaming) return;
    if (!window.confirm(t("chat.confirmDelSession"))) return;
    try { await api.deleteConversation(cid); } catch (e: any) { pushToast(t("common.opFailed"), String(e?.message ?? e).slice(0, 120), "bad"); }
    setConvMenu(null);
    refreshList();
    if (convId === cid) { setConvId(null); setMessages([]); }
  };
  const archiveConv = async (cid: string, archived: boolean) => {
    try { await api.archiveConversation(cid, archived); } catch (e: any) { pushToast(t("common.opFailed"), String(e?.message ?? e).slice(0, 120), "bad"); }
    setConvMenu(null);
    refreshList();
    if (archived && convId === cid) { setConvId(null); setMessages([]); }
  };
  // 审批白名单：查看 / 移除（命中白名单的命令下次自动执行不再弹审批）
  const loadWl = async () => {
    try { const d: any = await api.approvalWhitelist(); setWlItems(Array.isArray(d?.items) ? d.items : []); }
    catch { setWlItems([]); }
  };
  const removeWl = async (rule: string) => {
    try { await api.approvalWhitelistRemove(rule); await loadWl(); } catch {}
  };

  // ============ Bots（Hermes 式 Agent 档案）============
  const loadAgents = async () => {
    try { const d: any = await api.bots(); setAgents(Array.isArray(d?.agents) ? d.agents : []); }
    catch (e: any) { setBotMsg(t("chat.loadBotsFail", { err: e?.message ?? e })); }
  };
  useEffect(() => { loadAgents(); }, []);
  useEffect(() => { if (leftTab === "bots") loadAgents(); /* eslint-disable-line */ }, [leftTab]);
  // ⌘K / Ctrl+K 命令面板
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalOpen((v) => !v);
        setPalQ("");
        setTimeout(() => palRef.current?.focus(), 20);
      } else if (e.key === "Escape") {
        if (fileModal) { closeFileModal(); return; }
        if (convMenu) { setConvMenu(null); return; }
        if (wlOpen) { setWlOpen(false); return; }
        setPalOpen(false);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [fileModal, convMenu, wlOpen]); // 依赖这些 UI 态，Esc 才能读到最新值

  const loadSkillsAvail = async () => {
    try { const d: any = await api.skills(); setAvailSkills((Array.isArray(d) ? d : []).filter((s: any) => s.installed && (s.group === "binance-web3" || s.group === "binance"))); } catch {}
  };
  useEffect(() => { loadSkillsAvail(); }, []);

  // 加载当前 LLM 模型清单：合并「当前 provider 主模型 + 备用 + aux 槽位」并去重作为下拉选项
  const loadAvailableModels = async () => {
    try {
      const cfg: any = await api.getSettings();
      const llm = cfg?.llm || {};
      const provider = llm.provider || "";
      const snap = (conversations ?? []).find((c: any) => c.id === convId)?.provider_snapshot || {};
      const def = (snap.model || llm.model || "") as string;
      const list = await api.llmModels(provider || "", llm.base_url || "", llm.api_key || "");
      const arr: string[] = Array.isArray(list?.models) ? list.models : [];
      // 合并：默认模型 + 备用 + aux slots（如果与默认不同）
      const candidates: string[] = [];
      const add = (m: any) => { const s = String(m || "").trim(); if (s && !candidates.includes(s)) candidates.push(s); };
      add(def); add(llm.model); (llm.backup_models || []).forEach(add);
      const aux = llm.aux || {}; Object.values(aux).forEach(add);
      // 目录里有的优先，再补不重合的
      const merged: string[] = [];
      arr.forEach((m) => add(m));
      candidates.forEach((m) => { if (!merged.includes(m)) merged.push(m); });
      setAvailableModels(merged.length ? merged : (arr.length ? arr : [def || "gpt-5.4-mini"]));
      setDefaultModel(def);
      // 若该会话快照里有模型，优先作为默认；否则用全局默认
      setSessionModel(snap.model || llm.model || def);
    } catch {
      setAvailableModels(["gpt-5.4-mini", "deepseek-v4-flash"]);
    }
  };
  useEffect(() => { loadAvailableModels(); /* eslint-disable-line */ }, [convId, conversations?.length]);
  // 全能模式：默认开，启动时按用户上次设置加载
  useEffect(() => { api.getAutoExec().then((v) => setAutoExec(v !== false)); }, []);

  // 跨视图触发：行情按钮等 → 跳到对话时附带待发消息，到位后自动 send
  useEffect(() => {
    if (!pendingMsg) return;
    const t = pendingMsg;
    onPendingConsumed?.();
    // 给 ChatView 一次状态 tick 让 streaming/uploading 检测稳定
    setTimeout(() => { try { send(t); } catch {} }, 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingMsg]);

  const openAgentEditor = (b: Agent | "new") => {
    setEditBot(b);
    setForm(b === "new"
      ? { name: "", title: "", description: "", avatar: "blobatar", color: "#F0B90B" }
      : { name: b.name, title: b.title, description: b.description, avatar: b.avatar, color: b.color });
    const c = (b !== "new" && b.config) || { model: "", tone: "", skills: [], tools: [] };
    setCfg({ model: c.model ?? "", tone: c.tone ?? "", skills: Array.isArray(c.skills) ? c.skills : [],
             tools: Array.isArray(c.tools) ? c.tools : [], prompt: c.prompt ?? "" });
  };
  const saveAgent = async () => {
    if (!form.name.trim()) { setBotMsg(t("chat.botNeedName")); return; }
    const payload = { ...form, name: form.name.trim(), config: cfg };
    try {
      if (editBot === "new") await api.addBot(payload);
      else if (editBot) await api.updateBot(editBot.id, payload);
      setEditBot(null); setBotMsg("");
      await loadAgents();
    } catch (e: any) { setBotMsg(t("chat.botSaveFail", { err: e?.message ?? e })); }
  };
  const removeAgent = async (b: Agent) => {
    if (!window.confirm(t("chat.confirmDelAgent", { name: b.name }))) return;
    try { await api.deleteBot(b.id); if (activeBot?.id === b.id) setActiveBot(null); await loadAgents(); }
    catch (e: any) { setBotMsg(String(e?.message ?? e)); }
  };
  // 导出 Bot 包（bot.md）下载
  const exportAgent = async (b: Agent) => {
    try {
      const md = await api.exportBot(b.id);
      const blob = new Blob([md], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `bot-${b.id}.md`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 3000);
    } catch (e: any) { setBotMsg(t("chat.botExportFail", { err: e?.message ?? e })); }
  };
  // 导入 Bot 包（读取本地 .md 文本 → 写盘）
  const importAgent = async (file?: File) => {
    const pick = file ?? (await new Promise<File | null>((res) => {
      const inp = document.createElement("input");
      inp.type = "file"; inp.accept = ".md,.txt,text/markdown";
      inp.onchange = () => res(inp.files?.[0] ?? null);
      inp.click();
    }));
    if (!pick) return;
    const md = await pick.text();
    try {
      const ag: any = await api.importBot(md);
      setBotMsg(t("chat.botImported", { name: ag?.name ?? "" }));
      await loadAgents();
    } catch (e: any) { setBotMsg(`${t("chat.importFail")}: ${e?.message ?? e}`); }
  };
  // 点 Bot = 进入该 bot 的专属界面：会话列表只显示它自己的记录，并自动打开它最近的一段
  const talkWith = async (b: Agent) => {
    if (streaming || grouping) return;
    setActiveBot(b); markSeen(b.name); setLeftTab("sessions");
    const mine = (conversations ?? [])
      .filter((c: any) => c.persona === b.name && c.kind !== "room")
      .sort((x: any, y: any) => (y.updated_at ?? 0) - (x.updated_at ?? 0));
    const top = mine[0];
    if (top) { setConvId(top.id); await loadHistory(top.id); }
    else { setConvId(null); setMessages([]); } // 无专属记录 → 空白起始页，发送即开新段
  };
  // 退出 bot 专属界面（回到默认 master）：清掉聊天窗，避免把该 bot 的会话留在主视图
  const exitBot = () => {
    if (!activeBot) return;
    markSeen(activeBot.name);
    setConvId(null); setMessages([]); setActiveBot(null); setLeftTab("sessions");
  };

  // ============ 发送 / 流式 ============
  // ============ 群聊：@提及的 Agent 依次作答（Hermes Bot Chat） ============
  const groupSend = async (content: string, mentions: Agent[]) => {
    if (grouping || streaming) return;
    setInput("");
    setLeftTab("terminal");
    pushLog(`GROUP > ${mentions.map((m) => "@" + m.name).join(" ")} :: ${content.slice(0, 60)}`);

    const userMsg: ChatMsg = { id: crypto.randomUUID(), role: "user", text: content, ts: Date.now() };
    const botIds: Record<string, string> = {};
    const pre: ChatMsg[] = [userMsg];
    mentions.forEach((m) => { const id = crypto.randomUUID(); botIds[m.name] = id; pre.push({ id, role: "assistant", text: "", pending: true, persona: m.name, ts: Date.now() }); });
    setMessages((arr) => [...arr, ...pre]);
    setGrouping(true);
    try {
      const res = await fetch("/api/bots/reply", {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ conversation_id: convId, message: content, to: mentions.map((m) => m.name) }),
      });
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n"); buf = lines.pop() ?? "";
        for (const ln of lines) {
          if (!ln.trim()) continue;
          try {
            const ev = JSON.parse(ln);
            if (ev.type === "bot" && ev.name && botIds[ev.name]) {
              const targetId = botIds[ev.name];
              setMessages((arr) => arr.map((m) => m.id === targetId ? { ...m, text: (m.text || "") + (ev.text ?? ""), pending: false } : m));
            } else if (ev.type === "done" && ev.conversation_id && !convId) {
              setConvId(ev.conversation_id);
            }
          } catch {}
        }
      }
    } catch (e: any) {
      mentions.forEach((m) => {
        setMessages((arr) => arr.map((x) => x.id === botIds[m.name] ? { ...x, text: `⚠ ${e?.message ?? e}`, pending: false } : x));
      });
    } finally {
      setGrouping(false);
      refreshList();
    }
  };

  // ============ 输入框辅助：代码块 / 语音 / 附件 ============
  // 在光标位置插入一个空白代码块，并把光标落在块内第一行（Hermes 代码片段快捷插入）
  const insertCodeBlock = () => {
    const el = inputRef.current;
    if (!el) { setInput((p) => p + "\n```\n\n```\n"); return; }
    const start = el.selectionStart ?? input.length;
    const end = el.selectionEnd ?? input.length;
    const before = input.slice(0, start);
    const after = input.slice(end);
    // 紧贴前面追加换行避免粘到单词中间
    const padBefore = before.length && !before.endsWith("\n") ? "\n" : "";
    const padAfter = after.length && !after.startsWith("\n") ? "\n" : "";
    const block = `${padBefore}\`\`\`\n\n\`\`\`${padAfter}`;
    const cursor = before.length + padBefore.length + 4; // 光标落在 ```\n 之后
    setInput(before + block + after);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(cursor, cursor);
    });
  };

  // 语音输入：Web Speech API（Chrome/Edge 内置）→ 直接追加到输入框
  const toggleMic = () => {
    const W: any = window as any;
    const Rec = W.SpeechRecognition || W.webkitSpeechRecognition;
    if (!Rec) {
      pushLog(`MIC_ERR > ${t("chat.micUnsupported")}`);
      return;
    }
    if (listening) {
      try { recRef.current?.stop(); } catch {}
      setListening(false);
      return;
    }
    const rec = new Rec();
    rec.lang = "zh-CN"; rec.continuous = true; rec.interimResults = true;
    rec.onresult = (ev: any) => {
      let txt = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        txt += ev.results[i][0].transcript;
        if (ev.results[i].isFinal) txt += " ";
      }
      const t = txt.trim();
      if (!t) return;
      setInput((prev) => (prev ? prev + (prev.endsWith(" ") ? "" : " ") : "") + t);
    };
    rec.onend = () => setListening(false);
    rec.onerror = (ev: any) => { pushLog(`MIC_ERR > ${ev?.error ?? ""}`); setListening(false); };
    rec.start();
    recRef.current = rec;
    setListening(true);
    pushLog(`MIC > ${t("chat.micRec")}`);
  };

  // 附件：调起原生文件选择器；图片（image/*）压缩成 dataURL 显示预览+可被模型直读/OCR
  const pickFiles = () => { fileRef.current?.click(); };
  const handleFilePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const list = Array.from(e.target.files ?? []);
    if (!list.length) return;
    list.forEach(addFileAsAttachment);
    e.target.value = ""; // 允许重复选同一文件
  };
  // 图片降采样 → JPEG dataURL（限制最大边长，控制 base64 体积）
  const fileToDataUrl = (f: File): Promise<string> => new Promise((resolve, reject) => {
    const rd = new FileReader();
    rd.onerror = () => reject(new Error(t("chat.imgReadErr")));
    rd.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error(t("chat.imgDecodeErr")));
      img.onload = () => {
        const MAX = 1280;
        let { width: w, height: h } = img;
        if (Math.max(w, h) > MAX) {
          const s = MAX / Math.max(w, h);
          w = Math.round(w * s); h = Math.round(h * s);
        }
        const cv = document.createElement("canvas");
        cv.width = w; cv.height = h;
        const ctx = cv.getContext("2d");
        if (!ctx) { reject(new Error(t("chat.canvasErr"))); return; }
        ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, w, h); // 透明 PNG → 白底
        ctx.drawImage(img, 0, 0, w, h);
        resolve(cv.toDataURL("image/jpeg", 0.85));
      };
      img.src = rd.result as string;
    };
    rd.readAsDataURL(f);
  });
  const addFileAsAttachment = (f: File) => {
    if (f.type.startsWith("image/")) {
      fileToDataUrl(f).then((url) => {
        setAttachments((prev) => [...prev, { name: f.name, size: f.size, file: f, kind: "image", dataUrl: url }]);
      }).catch(() => {
        setAttachments((prev) => [...prev, { name: f.name, size: f.size, file: f, kind: "file" }]); // 降级为普通附件
      });
    } else {
      setAttachments((prev) => [...prev, { name: f.name, size: f.size, file: f, kind: "file" }]);
    }
  };
  // 粘贴图片：读剪贴板里 image/* 项直接入附件
  const onPasteImage = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const imgs: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.kind === "file" && it.type.startsWith("image/")) {
        const f = it.getAsFile();
        if (f) imgs.push(f);
      }
    }
    if (!imgs.length) return;
    e.preventDefault();
    imgs.forEach(addFileAsAttachment);
  };
  const removeAttachment = (i: number) =>
    setAttachments((prev) => prev.filter((_, idx) => idx !== i));

  // 房间内发消息：走 /api/chat/stream → 后端 room 引擎（轮次调度 + 各成员独立记录都在服务端）
  const roomSend = async (content: string) => {
    if (grouping || streaming) return;
    setMention(null);
    setInput("");
    setLeftTab("terminal");
    pushLog(`ROOM > ${content.slice(0, 60)}`);
    setMessages((arr) => [...arr, { id: crypto.randomUUID(), role: "user", text: content, ts: Date.now() }]);
    setGrouping(true);
    try {
      const res = await streamChat({ conversation_id: convId, message: content });
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n"); buf = lines.pop() ?? "";
        for (const ln of lines) {
          if (!ln.trim()) continue;
          try {
            const ev = JSON.parse(ln);
            if (ev.type === "room") pushLog(`ROOM ${ev.room} · ${t("chat.membersN", { n: ev.members.length })} · ${t("chat.roundsN", { n: ev.rounds })}`);
            else if (ev.type === "transfer" && ev.from && ev.to) {
              const payload = (ev.text ?? "").slice(0, 60) + ((ev.text ?? "").length > 60 ? "…" : "");
              setMessages((arr) => [...arr, { id: crypto.randomUUID(), role: "assistant", text: "", note: `⇄ @${ev.from} → @${ev.to} ${t("chat.transferTask")}${payload ? `：${payload}` : ""}`, ts: Date.now() }]);
              pushLog(`ROOM > ${t("chat.handoff", { from: ev.from, to: ev.to })}`);
            }
            else if (ev.type === "bot" && ev.name) {
              setMessages((arr) => [...arr, { id: crypto.randomUUID(), role: "assistant", text: ev.text ?? "", persona: ev.name, ts: Date.now() }]);
              pushLog(`ROOM > ${t("chat.spoke", { name: ev.name })}`);
            } else if (ev.type === "round" && ev.name && ev.status === "pass") {
              const reason = ev.reason ? ` · ${ev.reason}` : "";
              pushLog(`ROOM > ${t("chat.passRound", { name: ev.name })}${reason}`);
            } else if (ev.type === "warn") {
              pushLog(`ROOM_WARN > ${ev.detail ?? ""}`);
              setMessages((arr) => [...arr, { id: crypto.randomUUID(), role: "assistant", text: "⚠ " + (ev.detail ?? t("chat.roomNoResp")), ts: Date.now() }]);
            } else if (ev.type === "text" || ev.type === "delta") {
              setMessages((arr) => [...arr, { id: crypto.randomUUID(), role: "assistant", text: (ev.delta ?? ev.text ?? ""), ts: Date.now() }]);
            } else if (ev.type === "error") {
              setMessages((arr) => [...arr, { id: crypto.randomUUID(), role: "assistant", text: "⚠ " + (ev.detail ?? ""), ts: Date.now() }]);
            } else if (ev.type === "done") {
              pushLog(`DONE > room closed (${ev.spoke_total ?? 0} ${t("chat.turnUnit")})`);
            }
          } catch {}
        }
      }
    } catch (e: any) {
      pushLog(`ROOM_ERR > ${e?.message ?? e}`);
      setMessages((arr) => [...arr, { id: crypto.randomUUID(), role: "assistant", text: t("chat.roomFail", { err: e?.message ?? e }), ts: Date.now() }]);
    } finally {
      setGrouping(false);
      refreshList();
    }
  };

  const send = async (text?: string, opts?: { regenerate?: boolean; editText?: string; editId?: string | null }) => {
    const isRegen = !!opts?.regenerate || !!opts?.editText;
    let content = isRegen ? (opts?.editText ?? "") : (text ?? input).trim();
    if (streaming || uploading) return;
    if (!isRegen && !content && attachments.length === 0) return;
    const clearAttachments = () => setAttachments([]);
    const imgUrls: string[] = []; // 用户附图 dataURL → 直接随消息体送给后端（视觉直读/OCR）
    // 真上传附件：文本/二进制逐个 POST /api/upload 取摘录拼进消息；图片不入此流程
    if (!isRegen && attachments.length) {
      setUploading(true);
      try {
        for (const f of attachments) {
          if (f.kind === "image" && f.dataUrl) { imgUrls.push(f.dataUrl); continue; }
          try {
            const up: any = await api.uploadFile(f.file);
            if (up?.ok && up.is_text && up.excerpt) {
              content += (content ? "\n\n" : "") + `[${t("chat.attExcerpt", { name: up.name })}]\n${up.excerpt.slice(0, 4000)}`;
            } else if (up?.ok) {
              content += (content ? "\n\n" : "") + `[${t("chat.attBin", { name: up.name, kb: (up.size/1024).toFixed(1), path: up.path })}]`;
            } else {
              content += (content ? "\n\n" : "") + `[${t("chat.attFail", { name: f.name, err: up?.error ?? t("chat.unknownErr") })}]`;
            }
          } catch (e: any) {
            content += (content ? "\n\n" : "") + `[${t("chat.attFail", { name: f.name, err: e?.message ?? e })}]`;
          }
        }
      } finally { setUploading(false); }
    }
    // 纯图片无文字：给后端一个占位正文（否则 /chat/stream 400）；图片本身随 images 传递
    if (imgUrls.length && !content.trim()) content = t("chat.imgMsg");

    // 群聊房间：消息进房间 → 服务端逐成员轮次（Hermes room log）
    if (inRoom) { await roomSend(content); clearAttachments(); return; }
    if (grouping) return;
    setMention(null);
    setInput("");
    clearAttachments();
    setLeftTab("terminal");

    const persona = activeBot
      ? { name: activeBot.name, title: activeBot.title, description: activeBot.description, avatar: activeBot.avatar, color: activeBot.color, config: activeBot.config ?? {} }
      : undefined;
    const isEdit = !!opts?.editText;
    const editId = opts?.editId ?? editSel?.id ?? null;
    const freshAsstId = crypto.randomUUID();
    setMessages((m) => {
      if (isRegen) {
        // Regenerate / Edit：从被编辑的用户消息（或尾部 assistant 之前）切开，挂新的空 pending
        const upd = [...m];
        let cut = upd.length;
        if (isEdit && editId) {
          const ui = upd.findIndex((x) => x.id === editId);
          if (ui >= 0) {
            upd[ui] = { ...upd[ui], text: opts.editText! };
            cut = ui + 1; // 其后所有消息一并作废（fork 语义）
          }
        } else {
          while (cut > 0 && upd[cut - 1].role === "assistant") cut--;
        }
        upd.splice(cut, 0, { id: freshAsstId, role: "assistant", text: "", pending: true, ts: Date.now() });
        return upd;
      }
      const u = { id: crypto.randomUUID(), role: "user" as const, text: content, ts: Date.now(), ...(imgUrls.length ? { images: imgUrls } : {}) };
      return [...m, u, { id: freshAsstId, role: "assistant", text: "", pending: true, ts: Date.now() }];
    });
    setStreaming(true);
    const asstId = freshAsstId;
    pushLog(`${activeBot ? `@${activeBot.name}` : "USER"}${isRegen ? " [regen]" : ""} > ${content.slice(0, 80)}${content.length > 80 ? "…" : ""}`);

    const ac = new AbortController();
    abortRef.current = ac;
    const editIdx = isEdit && editId ? messages.findIndex((x) => x.id === editId) : undefined;
    try {
      const ap = approvalRef.current;
      approvalRef.current = null; // 一次性：仅随本次确认消息发回审批负载
      const res = await streamChat({
        conversation_id: convId, message: content, auto_exec: autoExec, persona,
        ...(imgUrls.length ? { images: imgUrls } : {}),
        ...(ap ? { approval: ap } : {}),
        ...(isRegen ? { regenerate: !isEdit, edit_text: isEdit ? content : "", edit_index: editIdx } : {}),
        ...(sessionModel && sessionModel !== defaultModel ? { llm_cfg: { model: sessionModel } } : {}),
      }, ac.signal);
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      // 逐字流式：每个 text/reasoning delta 后让出一帧，让 React 渲染 + 浏览器绘制真的有间隔（否则 React 批处理只渲染末态、视觉上"一次性出来"）
      const nextFrame = () => new Promise<void>((r) => requestAnimationFrame(() => r()));
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const ln of lines) {
          if (!ln.trim()) continue;
          try {
            const ev = JSON.parse(ln);
            applyEvent(ev, asstId);
            if (ev.type === "meta" && ev.conversation_id && !convId) setConvId(ev.conversation_id);
            // 仅对字符级流式事件让帧；tool/approval/done/error 立刻呈现
            if (ev.type === "text" || ev.type === "delta" || ev.type === "reasoning") {
              await nextFrame();
            }
          } catch {}
        }
      }
    } catch (e: any) {
      if (e?.name === "AbortError") {
        pushLog(`STOP > ${t("chat.stopDone")}`);
        setMessages((m) => m.map((x) => x.id === asstId ? { ...x, text: (x.text || "") + "\n\n⏹ " + t("chat.stopped"), pending: false } : x));
      } else {
        pushLog(`ERR > ${e?.message ?? e}`);
        setMessages((m) => m.map((x) => x.id === asstId ? { ...x, text: t("chat.streamFail", { err: e?.message ?? e }), pending: false } : x));
      }
    } finally { setStreaming(false); abortRef.current = null; refreshList(); /* 5s 后再刷一次：等自动标题落库 */ setTimeout(refreshList, 5000); }
  };
  // Stop：中断当前生成（前端断开流；后端断连后不再落库该条回复）
  const stopGen = () => { abortRef.current?.abort(); };

  function applyEvent(ev: any, asstId: string) {
    // 高频流式字段：累积到 ref，下一帧 commit 到 React state —— 避免 fetch 在同帧塞多 delta 时 React 批处理只渲染最后一帧
    if (ev.type === "text" || ev.type === "delta") {
      const cur = textBufRef.current.get(asstId) ?? "";
      textBufRef.current.set(asstId, cur + (ev.delta ?? ev.text ?? ""));
      if (!textRafRef.current.has(asstId)) {
        textRafRef.current.set(asstId, requestAnimationFrame(() => {
          textRafRef.current.delete(asstId);
          const t = textBufRef.current.get(asstId);
          if (t == null) return;
          setMessages((m) => m.map((x) => x.id === asstId ? { ...x, text: t } : x));
        }));
      }
      return;
    }
    if (ev.type === "reasoning") {
      const cur = reasonBufRef.current.get(asstId) ?? "";
      // 每条 reasoning 事件以 \n 分隔，前端「深度思考」块按行拆成步骤列表
      reasonBufRef.current.set(asstId, (cur ? cur + "\n" : "") + (ev.text ?? ""));
      const key = asstId + ":r";
      if (!textRafRef.current.has(key)) {
        textRafRef.current.set(key, requestAnimationFrame(() => {
          textRafRef.current.delete(key);
          const r = reasonBufRef.current.get(asstId);
          if (r == null) return;
          setMessages((m) => m.map((x) => x.id === asstId ? { ...x, reasoning: r, model: ev.model || x.model } : x));
          pushLog(`THINK > ${r.slice(-40).replace(/\n/g," ")}…`);
        }));
      }
      return;
    }
    setMessages((m) => {
      const idx = m.findIndex((x) => x.id === asstId);
      if (idx < 0) return m;
      const a = { ...m[idx] };
      // done 到达前若 ref 里还有未 commit 的累积文本，同步补到 message，再清空 ref（避免 RAF 后到的 setMessages 覆盖已设置字段）
      if (ev.type === "done") {
        const bufText = textBufRef.current.get(asstId);
        const bufReason = reasonBufRef.current.get(asstId);
        if (bufText != null) a.text = bufText;
        if (bufReason != null) a.reasoning = bufReason;
        textBufRef.current.delete(asstId);
        reasonBufRef.current.delete(asstId);
        textRafRef.current.delete(asstId);
        textRafRef.current.delete(asstId + ":r");
      }
      if (ev.type === "tool") {
        // 后端把工具对象塞在 ev.tool 下（含 icon/name/status/detail），兼容老字段
        const src = (ev.tool && typeof ev.tool === "object") ? ev.tool : ev;
        const tool = {
          icon: src.icon,
          name: src.name ?? src.tool ?? "tool",
          detail: src.detail ?? src.result,
          ok: (src.ok ?? (src.status === "success" || src.status === "ok")) === true,
          status: src.status,
        };
        a.tools = [...(a.tools ?? []), tool];
        pushLog(`TOOL > ${tool.name}${tool.ok ? " OK" : " …"}`);
      }
      else if (ev.type === "approval") { a.approval = ev.approval; pushLog(`APPROVAL_REQ > ${ev.approval?.title ?? ev.approval?.label ?? "—"}`); }
      else if (ev.type === "done") {
        a.pending = false;
        if (ev.model) a.model = ev.model;
        // 用 done.tools 反向补齐 ok（之前 tool 事件若 ok 缺失/未到，这里收尾）
        if (Array.isArray(ev.tools) && ev.tools.length) {
          const merged = (a.tools ?? []).map((t, i) => {
            const nt = ev.tools[i];
            if (!nt || typeof nt !== "object") return t;
            const ok = (nt.ok ?? (nt.status === "success" || nt.status === "ok")) === true;
            return { ...t, ...nt, ok };
          });
          a.tools = merged.length ? merged : ev.tools.map((t: any) => ({
            name: t.name ?? "tool", detail: t.detail, ok: (t.ok ?? t.status === "success") === true,
          }));
        }
        // 🔑 关键：审批卡由 done 事件附带 approval 字段，必须赋值给当前 bubble，否则「待确认」按钮永不出现
        if (ev.needs_approval && ev.approval && !a.approval) {
          a.approval = ev.approval;
          pushLog(`APPROVAL_REQ > ${ev.approval?.title ?? ev.approval?.label ?? "—"}`);
        }
        pushLog("DONE > asst closed");
      }
      else if (ev.type === "error") { a.text = (a.text ?? "") + "\n[error] " + (ev.detail ?? ""); a.pending = false; pushLog(`ERROR > ${ev.detail ?? ""}`); }
      const out = [...m]; out[idx] = a; return out;
    });
  }
  function pushLog(line: string) { const ts = new Date().toLocaleTimeString(); setLog((l) => [...l.slice(-120), { ts, line }]); }
  const approve = async (ap: any, opts?: { whitelist?: boolean }) => {
    if (!ap) return;
    const wl = !!opts?.whitelist;
    pushLog(`APPROVE${wl ? "+WL" : ""} > ${ap?.label ?? ap?.title ?? ap?.action}`);
    approvalRef.current = wl ? { ...ap, whitelist: true } : ap; // 确认的审批负载随下一条消息发回后端，由 dispatch 精确执行
    const tag = ap.label ?? ap.title ?? ap.action;
    const modeTxt = wl ? t("chat.trustExec") : t("chat.confirmExecBtn");
    const msg = t("chat.approveMsg", { mode: modeTxt, tag });
    setMessages((m) => [...m, { id: crypto.randomUUID(), role: "user", text: msg, ts: Date.now() }]);
    send(msg);
  };

  // Files tab
  const refreshFiles = (rel?: string) => {
    const p = rel !== undefined ? rel : fileCwd;
    api.workspaceFiles(p).then((d: any) => {
      setFiles(d?.items ?? []);
      setFileCwd(d?.path ?? "");
    }).catch(() => {});
  };
  useEffect(() => { if (leftTab === "files") refreshFiles(""); }, [leftTab]);
  const enterDir = (name: string) => {
    const next = fileCwd ? `${fileCwd}/${name}` : name;
    refreshFiles(next);
  };
  const goUp = () => {
    if (!fileCwd) return;
    const parts = fileCwd.split("/");
    parts.pop();
    refreshFiles(parts.join("/"));
  };
  const openFile = async (relName: string) => {
    const full = fileCwd ? `${fileCwd}/${relName}` : relName;
    setFileModal({ name: relName, path: full, size: 0, is_text: false, content: "", loading: true });
    try {
      const r: any = await api.workspaceRead(full);
      setFileModal({ name: relName, path: full, size: r?.size ?? 0, is_text: !!r?.is_text,
                     content: r?.content ?? "", too_large: !!r?.too_large, loading: false, error: r?.error });
    } catch (e: any) {
      setFileModal({ name: relName, path: full, size: 0, is_text: false, content: "", loading: false, error: String(e?.message ?? e) });
    }
  };
  const closeFileModal = () => setFileModal(null);
  // 删除文件/目录（文件浏览器每行右侧的删除按钮）：state.db 二次确认（存着全部会话与记忆）
  const deleteFile = async (name: string) => {
    const full = fileCwd ? `${fileCwd}/${name}` : name;
    if (!window.confirm(t(name === "state.db" ? "chat.fileDeleteConfirmDb" : "chat.fileDeleteConfirm", { name }))) return;
    try {
      await api.workspaceDelete(full);
      pushLog(`DEL > ${full}`);
      if (fileModal?.path === full) setFileModal(null);
      refreshFiles();
    } catch (e: any) {
      pushLog(`DEL_ERR > ${full}: ${String(e?.message ?? e).slice(0, 140)}`);
    }
  };
  // 已知文件/目录的用途说明（列表里显示为文件名下的小字，方便分辨哪些能删）
  const fileDescKey = (name: string) =>
    name === "state.db" ? "chat.desc.stateDb"
    : name === "attachments" ? "chat.desc.attachments"
    : name === "generated" ? "chat.desc.generated"
    : name === "logs" ? "chat.desc.logs"
    : name === "backups" ? "chat.desc.backups"
    : name === "square_posts.json" ? "chat.desc.squarePosts"
    : name === "proxies.json" ? "chat.desc.proxies"
    : name === "update-cache" ? "chat.desc.updateCache"
    : name === "workspace" ? "chat.desc.workspaceDir"
    : "";
  useEffect(() => { scrollRef.current?.scrollTo({ top: 999999, behavior: "smooth" }); }, [messages.length, messages[messages.length - 1]?.text]);

  const isEmpty = messages.length === 0;
  const fmtSize = (n?: number) => n === undefined || n === null ? "" : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : `${n}B`;
  const fmtWhen = (ts?: number) => {
    if (!ts) return "";
    const d = new Date(ts * 1000); const today = new Date(); today.setHours(0, 0, 0, 0);
    const that = new Date(d); that.setHours(0, 0, 0, 0);
    const day = Math.round((today.getTime() - that.getTime()) / 864e5);
    if (day <= 0) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (day === 1) return t("chat.yesterday"); if (day < 7) return t("chat.daysAgo", { day });
    return d.toLocaleDateString();
  };
  const fmtHM = (ts?: number) => (ts ? new Date(ts > 1e12 ? ts : ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");
  const copyText = async (textToCopy: string) => {
    if (!textToCopy) return;
    try { await navigator.clipboard.writeText(textToCopy); pushLog(`COPY > ${textToCopy.slice(0, 50).replace(/\n/g, " ")}…`); } catch { pushLog("COPY_ERR > clipboard unavailable"); }
  };
  // 判断某条消息是否是「当前展示列表的最后一条」（用于 Regenerate / 编辑重发的入口限定）
  const isLastTurn = (m: any) => {
    const last = messages[messages.length - 1];
    return last?.id === m.id;
  };
  // 发送按钮主入口：处于“编辑重发”模式时，把输入框内容作为新文本触发编辑
  const submit = async () => {
    if (editSel) {
      const t = input.trim();
      if (!t) return;
      setEditSel(null);
      await send(undefined, { editText: t, editId: editSel.id });
    } else {
      await send();
    }
  };
  // 会话列表作用域：默认 = master 主会话（persona 为空）；点进某 bot = 只显示该 bot 的专属记录
  // （dm 一对一 + kind='group' 群聊视角），互不混列。群聊房间(kind='room')共享日志仍在 Bots/群聊入口管理。
  const scopeName = activeBot?.name ?? null;
  const sessConvs = (conversations ?? []).filter((c: any) => {
    if (c.kind === "room") return false;
    if (scopeName) return c.persona === scopeName;
    return !c.persona || c.persona === "__group__";
  });
  // 搜索态（v1.4.3）：命中结果同样按当前作用域过滤；preview 已替换为命中片段
  const searching = convHits !== null;
  const convList = searching ? convHits!.filter((c: any) => {
    if (c.kind === "room") return false;
    if (scopeName) return c.persona === scopeName;
    return !c.persona || c.persona === "__group__";
  }) : sessConvs;

  return (
    <div className="grid grid-cols-12 gap-4 p-5 h-full">
      {/* Left rail */}
      <aside className="col-span-12 md:col-span-3 glass flex flex-col overflow-hidden" style={{ borderRadius: 12 }}>
        <div className="px-3 pt-3">
          <div className="flex items-center gap-1 px-1 py-1 rounded-md bg-elevated/50 border border-line">
            {(["sessions", "bots", "files", "terminal"] as LeftTab[]).map(tab => (
              <button key={tab} onClick={() => setLeftTab(tab)}
                className={`flex-1 px-2 py-1.5 rounded text-[11px] font-mono uppercase tracking-wider transition-colors ${leftTab === tab ? "bg-gold text-canvas" : "text-ink-dim hover:text-ink"}`}>
                {tab === "sessions" ? t("leftTab.sessions") : tab === "bots" ? t("leftTab.bots") : tab === "files" ? t("leftTab.files") : t("leftTab.terminal")}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-auto px-3 py-3 text-[13px]">
          {leftTab === "sessions" && (
            <div className="space-y-1.5">
              {scopeName ? (
                <div className="flex items-center gap-2 px-1 pb-1">
                  {(() => { const ab = agents.find((a) => a.name === scopeName); return ab ? <AgentAvatar avatar={ab.avatar} name={ab.name} color={ab.color} size={20} ring={false} /> : null; })()}
                  <span className="font-mono text-[11px] text-ink truncate">{t("sessions.scope", { name: scopeName })}</span>
                  <button onClick={exitBot} title={t("chat.exitToDefault")}
                    className="ml-auto shrink-0 rounded p-0.5 hover:bg-elevated text-ink-dim"><I.X size={11} /></button>
                </div>
              ) : (
                <div className="px-1 pb-1 font-mono text-[10px] tracking-[0.14em] flex items-center gap-1.5">
                  <span className="text-gold">{t("sessions.master")}</span>
                  <span className="text-ink-mute">·</span>
                  <span className="text-ink-dim">{t("sessions.allAround")}</span>
                  {autoExec && <span className="ml-auto inline-flex items-center gap-1 px-1 py-px rounded text-[9px] text-gold border border-gold/40 bg-gold/5">🌟 {t("chat.omni")}</span>}
                </div>
              )}
              <div className="relative mb-1.5">
                <I.Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute pointer-events-none" />
                <input value={convQuery} onChange={(e) => setConvQuery(e.target.value)}
                  placeholder={t("chat.searchConvs")}
                  className="w-full field !py-1.5 !pl-7 !pr-7 !text-[11px] font-mono" />
                {convQuery && (
                  <button onClick={() => setConvQuery("")}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-mute hover:text-ink">
                    <I.X size={11} />
                  </button>
                )}
              </div>
              <button onClick={newConv}
                className="w-full text-left rounded-md px-3 py-2 border border-line bg-card/40 hover:bg-elevated/60 flex items-center gap-2">
                <I.Plus size={12} className="text-gold" /><span className="font-mono text-[12px] text-ink">{scopeName ? t("sessions.newConvForAgent", { agent: scopeName }) : t("sessions.newConv")}</span>
              </button>
              {(searching
                ? convList
                : [...convList.filter((c: any) => !c.archived),
                   ...(showArchived ? convList.filter((c: any) => c.archived) : [])]
              ).map((c: any) => (
                <div key={c.id}
                  onContextMenu={(e) => { e.preventDefault(); if (c.kind !== "room") { setRenamingId(c.id); setRenameDraft(c.title || ""); } }}
                  onClick={() => selectConv(c.id)}
                  title={t("sessions.renameTip")}
                  className={`group relative w-full text-left rounded-md pl-3 pr-8 py-2 border cursor-pointer ${convId === c.id ? "border-gold bg-elevated" : "border-line bg-card/40 hover:bg-elevated/60"} ${c.archived ? "opacity-55" : ""}`}>
                  {renamingId === c.id ? (
                    <div onClick={(e) => e.stopPropagation()} className="flex items-center gap-1.5">
                      <input autoFocus value={renameDraft}
                        onChange={(e) => setRenameDraft(e.target.value)}
                        onKeyDown={async (e) => {
                          if (e.key === "Enter") {
                            const name = renameDraft.trim();
                            if (name) { try { await api.renameConversation(c.id, name); refreshList(); } catch (err: any) { pushToast(t("common.opFailed"), String(err?.message ?? err).slice(0, 120), "bad"); } }
                            setRenamingId(null);
                          } else if (e.key === "Escape") setRenamingId(null);
                        }}
                        onBlur={() => setRenamingId(null)}
                        className="flex-1 min-w-0 field !py-1 !px-2 text-[12px]" />
                    </div>
                  ) : (
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-[12px] text-ink truncate">
                        {c.kind === "group" && <span className="text-gold">◈ </span>}
                        {c.archived && <span className="text-ink-mute mr-1">{t("sessions.archived")}</span>}
                        {c.title || c.id.slice(0, 8)}
                      </span>
                      <span className="font-mono text-[9.5px] text-ink-mute shrink-0 ml-1">
                        {!!c.hits && <span className="text-gold mr-1.5">🔍{c.hits}</span>}
                        {c.kind === "group" ? t("sessions.groupView") : fmtWhen(c.updated_at)}
                      </span>
                    </div>
                  )}
                  <div className="font-mono text-[10px] text-ink-mute mt-0.5 truncate pr-4">{c.preview ?? t("sessions.empty")}</div>
                  {/* ⋮ 操作菜单触发器（避免遮住标题） */}
                  <button onClick={(e) => { e.stopPropagation(); setConvMenu(convMenu === c.id ? null : c.id); }}
                    className="absolute top-1.5 right-1.5 p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-elevated text-ink-dim transition-opacity"
                    title={t("sessions.menu")}>
                    <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
                      <circle cx="3" cy="8" r="1.4" /><circle cx="8" cy="8" r="1.4" /><circle cx="13" cy="8" r="1.4" />
                    </svg>
                  </button>
                  {convMenu === c.id && (
                    <div onClick={(e) => e.stopPropagation()}
                      className="absolute right-1.5 top-7 z-30 min-w-[120px] rounded-md border border-line bg-canvas/95 backdrop-blur shadow-xl py-1 text-[12px] font-mono">
                      <button onClick={() => archiveConv(c.id, !c.archived)}
                        className="w-full text-left px-3 py-1.5 hover:bg-elevated flex items-center gap-2 text-ink">
                        <I.Pin size={11} className="text-ink-mute" />
                        {c.archived ? t("chat.unarchive") : t("chat.archive")}
                      </button>
                      <button onClick={() => { setRenamingId(c.id); setRenameDraft(c.title || ""); setConvMenu(null); }}
                        className="w-full text-left px-3 py-1.5 hover:bg-elevated flex items-center gap-2 text-ink">
                        <I.Gear size={11} className="text-ink-mute" />{t("chat.rename")}
                      </button>
                      <div className="my-1 border-t border-line" />
                      <button onClick={() => delConv(c.id)}
                        className="w-full text-left px-3 py-1.5 hover:bg-red/15 flex items-center gap-2 text-red">
                        <I.Trash size={11} />{t("chat.delete")}
                      </button>
                    </div>
                  )}
                </div>
              ))}
              {(() => {
                const archCnt = sessConvs.filter((c: any) => c.archived).length;
                if (archCnt === 0) return null;
                return (
                  <button onClick={() => setShowArchived(v => !v)}
                    className="w-full text-left mt-2 px-2 py-1.5 rounded text-[10.5px] font-mono text-ink-mute hover:text-ink hover:bg-elevated/60 flex items-center gap-1.5">
                    <I.Lock size={10} />
                    {showArchived ? t("chat.hideArchived") : t("chat.viewArchived", { n: archCnt })}
                  </button>
                );
              })()}
              {loadingHist && <div className="shimmer h-8 mt-1" />}
              {searching && convList.length === 0 && <div className="text-[12px] text-ink-mute text-center py-6 font-mono">{t("chat.searchNoHit")}</div>}
              {!searching && sessConvs.length === 0 && <div className="text-[12px] text-ink-mute text-center py-6 font-mono">{scopeName ? t("chat.noConvsBot") : t("chat.noConvs")}</div>}
            </div>
          )}

          {leftTab === "bots" && (
            <div className="flex flex-col h-full">
              {/* Header row（Hermes 风格） */}
              <div className="flex items-center gap-2 px-1 mb-2">
                <span className="font-mono text-[11px] text-ink tracking-wide">{t("bots.title")}</span>
                <span className="font-mono text-[9.5px] text-ink-mute">one chat per agent · rooms</span>
                <button onClick={() => { setGName(""); setGPicked([]); setRoomMsg(""); setGroupFormOpen(true); }} title={t("bots.newGroup")}
                  className="rounded-md border border-line bg-card/60 hover:bg-gold hover:text-canvas transition-colors w-6 h-6 flex items-center justify-center">
                  <I.Users size={13} />
                </button>
                <button onClick={() => openAgentEditor("new")} title={t("bots.newAgent")}
                  className="ml-auto rounded-md border border-line bg-card/60 hover:bg-gold hover:text-canvas transition-colors w-6 h-6 flex items-center justify-center">
                  <I.Plus size={13} />
                </button>
              </div>
              {/* 群聊房间列表（Hermes: shared room log） */}
              {rooms.length > 0 && (
                <div className="mb-2">
                  <div className="px-1 mb-1 font-mono text-[9px] text-ink-mute tracking-[0.14em]">GROUP ROOMS · {rooms.length}</div>
                  <div className="space-y-1">
                    {rooms.map((r: any) => (
                      <div key={r.id} onClick={() => openRoomConv(r)}
                        className={`group rounded-md border px-2.5 py-1.5 cursor-pointer transition-colors ${convId === r.id ? "border-gold/60 bg-gold/5" : "border-line bg-card/40 hover:bg-elevated/60"}`}>
                        <div className="flex items-center gap-2">
                          <I.Users size={13} className="text-gold shrink-0" />
                          <div className="min-w-0 flex-1">
                            <div className="font-mono text-[11.5px] text-ink truncate">{r.title}</div>
                            <div className="font-mono text-[9px] text-ink-mute truncate">{t("chat.roomMeta", { n: r.members?.length ?? 0, when: fmtWhen(r.updated_at) })}</div>
                          </div>
                          <div className="flex shrink-0 -space-x-1.5">
                            {(r.members ?? []).slice(0, 4).map((mn: string) => {
                              const ag = (agents ?? []).find((a) => a.name === mn);
                              return ag ? <AgentAvatar key={mn} avatar={ag.avatar} name={ag.name} color={ag.color} size={18} /> : null;
                            })}
                          </div>
                          <button onClick={(e) => { e.stopPropagation(); delRoom(r.id); }}
                            className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-red/20 text-red transition-opacity"><I.Trash size={11} /></button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {botMsg && <div className="mb-2 rounded-md border border-gold/40 bg-gold/5 px-2 py-1 font-mono text-[10px] text-gold break-all">{botMsg}</div>}
              <div className="flex-1 overflow-auto space-y-1.5 pr-0.5">
                {agents.map((b) => {
                  const on = activeBot?.id === b.id;
                  const unread = unreadOf(b.name);
                  return (
                    <div key={b.id} onClick={() => talkWith(b)}
                      onContextMenu={(e) => { e.preventDefault(); openAgentEditor(b); }}
                      title={t("bots.editProfile")}
                      className={`group rounded-md border px-2.5 py-2 cursor-pointer transition-colors ${on ? "border-gold/60 bg-gold/5" : "border-line bg-card/40 hover:bg-elevated/60"}`}>
                      <div className="flex items-center gap-2.5">
                        <AgentAvatar avatar={b.avatar} name={b.name} color={b.color} size={34} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5">
                            <span className="font-mono text-[12.5px] text-ink truncate">{b.name}</span>
                            {on && <span className="dot dot-green live shrink-0" />}
                          </div>
                          <div className="font-mono text-[9.5px] text-ink-mute truncate">{b.title || b.id.slice(0, 8)}</div>
                          <div className="font-mono text-[9px] text-ink-mute/70 truncate">
                            {(conversations ?? []).some((c: any) => c.persona === b.name)
                              ? t("chat.agentMeta", { n: (conversations ?? []).filter((c: any) => c.persona === b.name).length, when: fmtWhen((conversations ?? []).filter((c: any) => c.persona === b.name).sort((x: any, y: any) => (y.updated_at ?? 0) - (x.updated_at ?? 0))[0]?.updated_at) })
                              : t("chat.noConvsYet")}
                          </div>
                        </div>
                        {unread > 0 && (
                          <span className="shrink-0 min-w-[16px] h-4 px-1 rounded-full bg-gold text-canvas font-mono text-[10px] font-bold flex items-center justify-center">{unread}</span>
                        )}
                        <div className="shrink-0 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button onClick={(e) => { e.stopPropagation(); exportAgent(b); }}
                            className="p-1 rounded hover:bg-elevated text-ink-dim" title={t("bots.exportPack")}><I.Download size={11} /></button>
                          <button onClick={(e) => { e.stopPropagation(); openAgentEditor(b); }}
                            className="p-1 rounded hover:bg-elevated text-ink-dim" title={t("bots.editProfile")}><I.Gear size={11} /></button>
                          <button onClick={(e) => { e.stopPropagation(); removeAgent(b); }}
                            className="p-1 rounded hover:bg-red/20 text-red" title={t("bots.delete")}><I.Trash size={11} /></button>
                        </div>
                      </div>
                    </div>
                  );
                })}
                {agents.length === 0 && <div className="text-center py-8 font-mono text-[11.5px] text-ink-mute">{t("chat.noAgents")}</div>}
              </div>
              <div className="mt-2 grid grid-cols-3 gap-1.5">
                <button onClick={() => openAgentEditor("new")}
                  className="rounded-md border border-dashed border-line py-2 font-mono text-[11px] text-ink-dim hover:border-gold/50 hover:text-gold transition-colors flex items-center justify-center gap-1">
                  <I.Plus size={11} /> Agent
                </button>
                <button onClick={() => importAgent()}
                  className="rounded-md border border-dashed border-line py-2 font-mono text-[11px] text-ink-dim hover:border-gold/50 hover:text-gold transition-colors flex items-center justify-center gap-1">
                  <I.Download size={11} /> {t("bots.import")}
                </button>
                <button onClick={() => { setGName(""); setGPicked([]); setRoomMsg(""); setGroupFormOpen(true); }}
                  className="rounded-md border border-dashed border-line py-2 font-mono text-[11px] text-ink-dim hover:border-gold/50 hover:text-gold transition-colors flex items-center justify-center gap-1">
                  <I.Users size={11} /> {t("chat.groupBtn")}
                </button>
              </div>
            </div>
          )}

          {leftTab === "files" && (
            <div className="space-y-1.5">
              {/* 面包屑 + 返回上一级 */}
              <div className="flex items-center gap-1.5 px-1 mb-1.5">
                <button onClick={() => refreshFiles("")}
                  className={`font-mono text-[11px] hover:underline ${fileCwd ? "text-ink-mute" : "text-gold"}`}>
                  {t("chat.projectRoot")}
                </button>
                {fileCwd.split("/").filter(Boolean).map((seg, i, arr) => (
                  <span key={i} className="flex items-center gap-1.5">
                    <span className="text-ink-mute">/</span>
                    <button
                      onClick={() => refreshFiles(arr.slice(0, i + 1).join("/"))}
                      className={`font-mono text-[11px] hover:underline ${i === arr.length - 1 ? "text-gold" : "text-ink-mute"}`}>
                      {seg}
                    </button>
                  </span>
                ))}
                {fileCwd && (
                  <button onClick={goUp} title={t("chat.upLevel")}
                    className="ml-auto rounded p-0.5 hover:bg-elevated text-ink-dim">
                    <I.Arrow size={11} />
                  </button>
                )}
              </div>
              {files.length === 0 ? (
                <div className="shimmer h-10" />
              ) : files.map((f, i) => {
                const descKey = fileDescKey(f.name);
                return (
                <div key={i}
                  onClick={() => f.type === "dir" ? enterDir(f.name) : openFile(f.name)}
                  className={`group w-full flex items-center gap-2 rounded-md border pl-3 pr-1.5 py-1.5 text-left cursor-pointer ${f.type === "dir" ? "border-line bg-card/40 hover:border-gold/40 hover:bg-elevated/60" : "border-line bg-card/30 hover:bg-elevated/60"}`}>
                  {f.type === "dir"
                    ? <I.Cex size={12} className="text-gold shrink-0" />
                    : <I.Download size={11} className="text-ink-dim shrink-0" />}
                  <span className="flex-1 min-w-0">
                    <span className="font-mono text-[12px] text-ink truncate block">{f.name}</span>
                    {!!descKey && (
                      <span className="font-mono text-[9.5px] text-ink-mute truncate block">{t(descKey)}</span>
                    )}
                  </span>
                  <span className="font-mono text-[10px] text-ink-mute shrink-0">
                    {f.type === "dir" ? "dir" : fmtSize(f.size)}
                  </span>
                  <button onClick={(ev) => { ev.stopPropagation(); deleteFile(f.name); }}
                    title={t("chat.fileDelete")}
                    className="shrink-0 rounded p-1 text-ink-mute hover:text-red hover:bg-red/15 transition-colors">
                    <I.Trash size={11} />
                  </button>
                </div>
                );
              })}
              <div className="text-[10px] text-ink-mute px-1 pt-1 font-mono">{t("chat.filesHint")}</div>
            </div>
          )}

          {leftTab === "terminal" && (
            <div className="font-mono text-[11px] leading-relaxed">
              {log.length === 0 && <div className="text-ink-mute text-center py-6">{t("chat.termIdle")}</div>}
              {log.map((l, i) => (
                <div key={i} className="flex gap-2 text-ink-dim">
                  <span className="text-ink-mute tabular">{l.ts}</span><span className="text-ink">{l.line}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="px-3 py-2 border-t border-line flex items-center justify-between font-mono text-[10px] text-ink-mute">
          <span>{leftTab.toUpperCase()} · {scopeName ? `@${scopeName}` : "master"} · {leftTab === "sessions" ? (sessConvs.filter((c:any)=>!c.archived).length) : leftTab === "bots" ? agents.length : leftTab === "files" ? `${files.length}${fileCwd ? ` @ ${fileCwd}` : ""}` : log.length}</span>
          {leftTab === "bots" && <button onClick={loadAgents} className="text-gold hover:underline">{t("chat.refresh")}</button>}
          {leftTab === "files" && (
            <button onClick={() => refreshFiles()} title={t("chat.refresh")} className="text-gold hover:underline flex items-center gap-1">
              <I.Refresh size={10} />{t("chat.refresh")}
            </button>
          )}
        </div>
      </aside>

      {/* Center: chat */}
      <main className="col-span-12 md:col-span-9 flex flex-col gap-3 min-h-0">
        {/* Active bot persona strip */}
        {activeBot && !inRoom && (
          <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg border border-gold/40 bg-gold/5">
            <AgentAvatar avatar={activeBot.avatar} name={activeBot.name} color={activeBot.color} size={28} />
            <div className="min-w-0">
              <div className="font-mono text-[12px] text-ink truncate">{t("chat.chatWith", { name: activeBot.name })} <span className="text-ink-mute">· {activeBot.title}</span></div>
              <div className="font-mono text-[10px] text-ink-dim truncate">{activeBot.description}</div>
            </div>
            <RoutinesInline bot={activeBot} />
            <button onClick={exitBot} title={t("chat.exitBotTip", { name: activeBot.name })}
              className="ml-auto shrink-0 rounded p-1 hover:bg-elevated text-ink-dim"><I.X size={12} /></button>
          </div>
        )}

        {/* 群聊房间条（Hermes room） */}
        {inRoom && roomInfo && (
          <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg border border-gold/40 bg-gold/5">
            <I.Users size={16} className="text-gold shrink-0" />
            <div className="min-w-0">
              <div className="font-mono text-[12px] text-ink truncate">{t("chat.roomBar", { name: roomInfo.title })}</div>
              <div className="flex items-center gap-1 mt-0.5">
                {(roomInfo.members ?? []).map((mn: string) => {
                  const ag = (agents ?? []).find((a) => a.name === mn);
                  if (!ag) return null;
                  return (
                    <button key={mn} title={t("chat.kickMember", { name: mn })} onClick={async () => {
                      if (!window.confirm(t("chat.confirmKick", { name: mn }))) return;
                      try { await api.roomMembers(roomInfo.id, "remove", mn); refreshList(); } catch (e: any) { setRoomMsg(String(e?.message ?? e)); }
                    }}
                      className="flex items-center gap-1 pr-1.5 py-0.5 rounded-full border border-line bg-card/60 hover:border-red/50 group/mem" style={{ borderColor: `${ag.color}44` }}>
                      <AgentAvatar avatar={ag.avatar} name={ag.name} color={ag.color} size={16} />
                      <span className="font-mono text-[9.5px]" style={{ color: ag.color }}>@{ag.name}</span>
                      <span className="opacity-0 group-hover/mem:opacity-100 text-red text-[9px]">✕</span>
                    </button>
                  );
                })}
                {(agents ?? []).filter((a) => !(roomInfo.members ?? []).includes(a.name)).length > 0 && (
                  <button title={t("chat.pullAgentTip")}
                    onClick={() => {
                      const nx = (agents ?? []).find((a) => !(roomInfo.members ?? []).includes(a.name));
                      if (nx) { api.roomMembers(roomInfo.id, "add", nx.name).then(() => refreshList()).catch((e: any) => setRoomMsg(String(e?.message ?? e))); }
                    }}
                    className="w-5 h-5 rounded-full border border-dashed border-line text-ink-mute hover:text-gold hover:border-gold/50 flex items-center justify-center"><I.Plus size={10} /></button>
                )}
              </div>
            </div>
            {roomMsg && <span className="font-mono text-[9px] text-red shrink-0">{roomMsg}</span>}
            <button onClick={() => { setConvId(null); setMessages([]); }} title={t("chat.leaveRoom")}
              className="ml-auto shrink-0 rounded p-1 hover:bg-elevated text-ink-dim"><I.X size={12} /></button>
          </div>
        )}

        {/* Messages / empty state */}
        <div ref={scrollRef} className="flex-1 glass overflow-auto" style={{ borderRadius: 12 }}>
          {isEmpty ? (
            <div className="h-full min-h-[60vh] flex flex-col items-center justify-center text-center px-6">
              <div className="flex items-center gap-4 select-none">
                {activeBot && !inRoom ? (
                  <><AgentAvatar avatar={activeBot.avatar} name={activeBot.name} color={activeBot.color} size={72} ring={false} />
                    <span className="font-mono font-extrabold tracking-tight text-ink leading-none" style={{ fontSize: "clamp(40px, 6vw, 84px)" }}>{activeBot.name}</span></>
                ) : inRoom && roomInfo ? (
                  <div className="flex items-center gap-4">
                    <div className="flex -space-x-3">
                      {(roomInfo.members ?? []).slice(0, 6).map((mn: string) => {
                        const ag = (agents ?? []).find((a) => a.name === mn);
                        return ag ? <AgentAvatar key={mn} avatar={ag.avatar} name={ag.name} color={ag.color} size={64} ring={false} /> : null;
                      })}
                    </div>
                    <span className="font-mono font-extrabold tracking-tight text-ink leading-none text-left" style={{ fontSize: "clamp(32px, 4.5vw, 64px)" }}>
                      {roomInfo.title}
                    </span>
                  </div>
                ) : (
                  <span className="font-mono font-extrabold tracking-tight text-ink leading-none" style={{ fontSize: "clamp(40px, 6vw, 84px)" }}>BAZZ<span className="text-gold">.</span>AGENT</span>
                )}
              </div>
              {activeBot && !inRoom ? (
                <p className="mt-5 max-w-2xl text-ink-dim text-[14px] leading-relaxed">
                  {activeBot.description || t("bots.startHint")} {t("bots.switchHint")}
                </p>
              ) : inRoom && roomInfo ? (
                <p className="mt-5 max-w-2xl text-ink-dim text-[14px] leading-relaxed">
                  {t("chat.roomEmptyHint", { name: roomInfo.title, n: (roomInfo.members ?? []).length })}
                </p>
              ) : (
                <p className="mt-6 max-w-2xl text-ink-dim text-[14px] leading-relaxed">
                  Type a task, question, or snippet. I remember the session, cite my sources, and stop to ask when I&rsquo;m unsure.
                </p>
              )}
              <div className="mt-6 flex flex-wrap items-center justify-center gap-2 font-mono text-[11px]">
                {!inRoom && [t("chat.qScanAnomaly"), t("chat.qArbPath"), t("chat.qBnbReview"), t("chat.qHedge"), t("chat.qRiskTest")].map(s => (
                  <button key={s} onClick={() => send(s)} className="pill pill-dim hover:border-gold/40 hover:text-gold transition-colors">⚡ {s}</button>
                ))}
              </div>
            </div>
          ) : (
            <div className="p-4 space-y-3">
              {messages.map((m) => {
                if ((m as any).note) {
                  return (
                    <div key={m.id} className="py-0.5 text-center msg-in">
                      <span className="inline-block rounded-full border border-gold/25 bg-gold/5 px-2.5 py-0.5 font-mono text-[9.5px] text-gold/90">{m.note}</span>
                    </div>
                  );
                }
                return (
                <div key={m.id} className={`group flex ${m.role === "user" ? "justify-end" : "justify-start"} msg-in`}>
                  <div className={`max-w-[88%] ${m.role === "user" ? "bg-elevated border border-line" : "bg-card/60 border border-line"} rounded-lg px-4 py-3`}>
                    {m.role === "assistant" && (() => {
                      const from = m.persona ? (agents ?? []).find((a) => a.name === m.persona) : null;
                      return (
                        <div className="flex items-center gap-2 mb-2">
                          {from
                            ? <><AgentAvatar avatar={from.avatar} name={from.name} color={from.color} size={20} ring={false} />
                              <span className="font-mono text-[11px]" style={{ color: from.color }}>@{from.name}</span></>
                            : <><span className="dot dot-gold live" /><span className="prefix">{activeBot ? `${activeBot.avatar} ${activeBot.name}` : "BAZZ AGENT"}</span></>}
                          <span className="ml-auto flex items-center gap-1.5 shrink-0 opacity-40 group-hover:opacity-100 transition-opacity">
                            {m.model && <span className="font-mono text-[9px] text-ink-mute max-w-[150px] truncate" title={t("chat.modelHit")}>{m.model}</span>}
                            <span className="font-mono text-[9px] text-ink-mute tabular">{fmtHM(m.ts)}</span>
                            {m.text && !m.pending && isLastTurn(m) && (
                              <button onClick={() => send(undefined, { regenerate: true })}
                                title={t("chat.regenerate")} className="p-0.5 rounded text-ink-mute hover:text-gold hover:bg-elevated transition-colors"><span className="font-mono text-[10px]">↻</span></button>
                            )}
                            {m.text && !m.pending && (
                              <button onClick={() => copyText(m.text)} title={t("chat.copyReply")}
                                className="p-0.5 rounded text-ink-mute hover:text-gold hover:bg-elevated transition-colors"><I.Copy size={11} /></button>
                            )}
                          </span>
                        </div>
                      );
                    })()}
                    {m.reasoning && (() => {
                      // 思考块按行拆成步骤，更像 WorkBuddy 的「深度思考」清单
                      const steps = m.reasoning.split("\n").map((s) => s.trim()).filter(Boolean);
                      const elapsed = m.ts ? Math.max(1, Math.round((Date.now() - m.ts) / 1000)) : 0;
                      return (
                        <details className="mb-2 rounded-md border border-gold/30 bg-gold/[0.04] overflow-hidden group/r">
                          <summary className="cursor-pointer list-none flex items-center gap-2 px-3 py-2 font-mono text-[10.5px] text-gold hover:bg-gold/[0.08] transition-colors">
                            <I.Cpu size={11} className={m.pending ? "text-gold status-live" : ""} />
                            <span className="font-semibold tracking-[0.08em]">{t("chat.thinkingTitle")}</span>
                            <span className="text-gold/70">· {t("chat.thinkingSteps", { n: steps.length })}</span>
                            <span className="text-gold/60">· {t("chat.thinkingChars", { n: m.reasoning.length })}</span>
                            {elapsed > 0 && <span className="text-gold/60">· {elapsed}s</span>}
                            {m.pending && <span className="text-gold/90 text-[9px] animate-pulse">thinking</span>}
                            {m.model && <span className="text-ink-dim/80">· {m.model}</span>}
                            <span className="ml-auto text-gold text-[10px] group-open/r:rotate-180 transition-transform">▾</span>
                          </summary>
                          <div className="px-3 py-2 border-t border-gold/20 space-y-1 max-h-72 overflow-auto">
                            {steps.map((s, si) => (
                              <div key={si} className="flex items-start gap-2 font-mono text-[11px] text-ink-dim">
                                <span className="text-gold/70 tabular shrink-0 mt-[1px]">{si + 1}.</span>
                                <span className="flex-1 break-words">{s}</span>
                              </div>
                            ))}
                            {m.pending && <div className="flow-bar mt-1.5" />}
                          </div>
                        </details>
                      );
                    })()}
                    {(m.tools ?? []).map((tl, i) => {
                      const running = tl.ok === undefined || tl.ok === null;
                      const failed = tl.ok === false;
                      return (
                        <div key={i}
                          className={`mb-2 rounded-md border bg-elevated/50 p-2.5 transition-colors ${running ? "border-gold/60 tool-running" : failed ? "border-red/40" : "border-line"}`}>
                          <div className="flex items-center gap-2">
                            <I.Cpu size={12} className={`text-gold ${running ? "tool-spin" : ""}`} />
                            <span className="font-mono text-[11px] text-gold tracking-wider">TOOL::{tl.name}</span>
                            <span className={`pill ${running ? "pill-gold" : failed ? "pill-red" : "pill-green"}`}>
                              {running ? t("chat.toolRunning") : failed ? t("chat.failed") : "OK"}
                            </span>
                            {running && <span className="ml-1 think-dots shrink-0"><span /><span /><span /></span>}
                          </div>
                          {tl.detail && <div className="mt-1.5 text-[12px] text-ink-dim font-mono">{tl.detail}</div>}
                        </div>
                      );
                    })}
                    <div className="text-[14px] text-ink leading-relaxed">
                      {m.pending && !m.text && !m.reasoning ? (
                        // 完全空 → 思考中：跳动点
                        <span className="inline-flex items-center gap-2 text-ink-mute">
                          <span className="font-mono text-[12px]">{t("chat.aiThinking")}</span>
                          <span className="think-dots"><span /><span /><span /></span>
                        </span>
                      ) : m.pending ? (
                        // 正在流式输出
                        <span><Md text={m.text || ""} /><span className="cursor-blink" aria-label="typing" /></span>
                      ) : (
                        <Md text={m.text || ""} />
                      )}
                    </div>
                    {m.role === "user" && !!m.images?.length && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {m.images.map((u, i) => (
                          <img key={i} src={u} alt={`img-${i}`} className="max-h-40 max-w-[220px] rounded-md border border-line object-contain bg-card/80" />
                        ))}
                      </div>
                    )}
                    {m.role === "user" && !m.pending && isLastTurn(m) && (
                      <div className="mt-1.5 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity justify-end">
                        <button onClick={() => { setInput(m.text ?? ""); setEditSel({ id: m.id, text: m.text ?? "" }); inputRef.current?.focus(); }}
                          title={t("chat.editResendTip")} className="p-1 rounded text-ink-mute hover:text-gold hover:bg-elevated transition-colors flex items-center gap-1">
                          <I.Gear size={10} /><span className="font-mono text-[9.5px]">{t("chat.editResend")}</span>
                        </button>
                      </div>
                    )}
                    {m.approval && (
                      <div className="mt-3 rounded-md border border-gold bg-gold/5 p-3">
                        <div className="flex items-center gap-2 mb-2">
                          <I.Shield size={14} className="text-gold shrink-0" />
                          <span className="font-mono text-[11px] text-gold truncate">NEEDS_APPROVAL · {m.approval.title ?? m.approval.label}</span>
                        </div>
                        {m.approval.action === "execute_order" && m.approval.signal && (() => {
                          const s = m.approval.signal;
                          const d = String(s.direction ?? "").toUpperCase();
                          const bear = d.includes("SHORT") || d.includes("BEAR") || String(s.direction ?? "").includes("空");
                          const dir = bear ? t("markets.short") : t("markets.long");
                          const route = s.route === "wallet" ? "wallet" : "exchange"; // 默认交易所；Agent 自动判断
                          const routeTxt = route === "wallet" ? t("chat.ordRouteWallet") : t("chat.ordRouteExchange");
                          const rows: Array<[string, string]> = [
                            [t("chat.ordRoute"), routeTxt],
                            [t("chat.ordSymbol"), `${s.symbol ?? "—"} · ${dir}`],
                            [t("chat.ordPrice"), s.price != null && s.price > 0 ? `$${s.price}` : "—"],
                            [t("chat.ordStopLoss"), s.stop_loss != null ? `$${s.stop_loss}` : "—"],
                            [t("chat.ordTakeProfit"), s.take_profit != null ? `$${s.take_profit}` : "—"],
                            [t("chat.ordQty"), s.quantity != null ? String(s.quantity) : "—"],
                            [t("chat.ordMaxLoss"), s.max_loss_usdt != null ? `$${s.max_loss_usdt}` : "—"],
                            ...(s.funding_rate != null ? [[t("chat.ordFunding"), `${(Number(s.funding_rate) * 100).toFixed(4)}%`] as [string, string]] : []),
                            ...(s.change_pct != null ? [[t("chat.ordChange"), `${Number(s.change_pct) > 0 ? "+" : ""}${Number(s.change_pct).toFixed(2)}%`] as [string, string]] : []),
                          ];
                          return (
                            <div className="mt-2 mb-2 rounded-md border border-gold/25 bg-gold/[0.05] divide-y divide-gold/10">
                              <div className="flex items-center justify-between px-2.5 py-1 font-mono text-[9.5px] tracking-wider text-gold/80">
                                <span>{t("chat.ordDetail")}</span>
                                <span className={`pill ${route === "wallet" ? "pill-gold" : "pill-green"}`}>
                                  {route === "wallet" ? "🛡️ " + t("chat.ordRouteWallet") : "🏦 " + t("chat.ordRouteExchange")}
                                </span>
                              </div>
                              {rows.map(([k, v]) => (
                                <div key={k} className="flex items-center justify-between px-2.5 py-1 font-mono text-[11px]">
                                  <span className="text-ink-dim">{k}</span>
                                  <span className="text-ink tabular">{v}</span>
                                </div>
                              ))}
                            </div>
                          );
                        })()}
                        <div className="flex flex-wrap gap-2">
                          <button onClick={() => approve(m.approval)} className="btn-gold"><I.Check size={12} /> {t("chat.confirmExecBtn")}</button>
                          {m.approval.action === "local_exec" && (
                            <button onClick={() => approve(m.approval, { whitelist: true })}
                              className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-mono border border-gold/60 bg-gold/15 text-gold hover:bg-gold/25 transition-colors">
                              <I.Pin size={12} /> {t("chat.trustExec")}
                            </button>
                          )}
                          <button onClick={() => { wlItems.length === 0 && loadWl(); setWlOpen(true); }}
                            className="ml-auto btn-ghost" title={t("chat.wlManageTip")}>
                            <I.Gear size={11} /> {t("chat.whitelist")} {wlItems.length > 0 ? `(${wlItems.length})` : ""}
                          </button>
                        </div>
                        {m.approval.action === "local_exec" && (
                          <div className="mt-1.5 font-mono text-[10px] text-ink-dim">{t("chat.trustExplained")}</div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Composer */}
        <div className="glass relative" style={{ borderRadius: 12 }}>
          {mention && mentionPool.length > 0 && (
            <div className="absolute bottom-full left-3 right-3 mb-1.5 z-40 overflow-hidden rounded-md border border-line/80 bg-card shadow-xl shadow-black/40" style={{ backdropFilter: "blur(10px)" }}>
              <div className="px-3 py-1.5 border-b border-line/50 font-mono text-[9.5px] text-ink-mute flex items-center justify-between">
                <span>@{mention.q || t("chat.mentionEllipsis")} {t("chat.mentionHint")}</span>
                <span className="text-gold">{mentionPool.length}</span>
              </div>
              <div className="max-h-56 overflow-auto py-1">
                {mentionPool.map((c, i) => (
                  <button key={c.name} onMouseDown={(e) => e.preventDefault()} onClick={() => pickMention(c)}
                    className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-left transition-colors ${i === mHi % mentionPool.length ? "bg-gold/10 border-l-2 border-gold" : "border-l-2 border-transparent hover:bg-elevated/60"}`}>
                    <AgentAvatar avatar={c.avatar} name={c.name} color={c.color} size={20} ring={false} />
                    <span className="min-w-0">
                      <span className="font-mono text-[12px] text-ink truncate block">@{c.name}</span>
                      <span className="font-mono text-[9px] text-ink-mute truncate block">{c.title}</span>
                    </span>
                    {i === mHi % mentionPool.length && <span className="ml-auto text-gold shrink-0 font-mono text-[10px]">↵</span>}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="flex items-center gap-1.5 px-2.5 pt-2 pb-1.5">
            <button onClick={pickFiles} className="btn-ghost" title={t("chat.attach")}><I.Download size={14} /></button>
            <input ref={fileRef} type="file" multiple hidden onChange={handleFilePick} />
            <input ref={inputRef} value={input}
              onChange={(e) => { const v = e.target.value; setInput(v); scanMention(v); }}
              onPaste={onPasteImage}
              onKeyDown={(e) => {
                if (onMentionKey(e)) return; // @ 弹层快捷键优先
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
              }}
              placeholder={inRoom && roomInfo
                ? t("chat.phRoom", { name: roomInfo.title })
                : activeBot
                  ? t("chat.phToBot", { name: activeBot.name })
                  : t("chat.phDefault")}
              className="flex-1 bg-transparent font-mono text-[14px] text-ink placeholder-ink-mute outline-none" />
            <button onClick={submit} disabled={streaming}
              className={`btn-gold ${streaming ? "opacity-50 cursor-not-allowed" : ""}`} title={editSel ? t("chat.saveResend") : t("chat.send")}>
              <I.Send size={12} />
            </button>
            {streaming && (
              <button onClick={stopGen} title={t("chat.stopGen")}
                className="shrink-0 w-8 h-8 rounded-md border border-red/40 text-red hover:bg-red/10 flex items-center justify-center transition-colors">
                <span className="w-2 h-2 bg-red rounded-[2px]" />
              </button>
            )}
          </div>
          {/* 模块行：会话状态 / 权限模式 / 模型下拉 / 语音 / 代码块 — 全跟 WorkBuddy 紧凑布局对齐 */}
          <div className="flex items-center gap-1.5 px-2.5 pb-2 border-t border-line/30 pt-1.5">
            <span className="ml-auto inline-flex items-center gap-1 font-mono text-[10px] text-ink-mute px-1.5 py-0.5 rounded-md border border-line/50 leading-none" title={activeBot ? t("chat.curBotTip", { name: activeBot.name }) : t("chat.defaultSessionTip")}>
              <span className="text-ink">↗</span>
              <span className="text-ink">{activeBot ? `@${activeBot.name}` : "master"}</span>
            </span>
            <span className="inline-flex items-center gap-1 font-mono text-[10px] px-1.5 py-0.5 rounded-md border border-line/50 leading-none"
              title={autoExec ? t("chat.modeOmniTitle") : t("chat.modeSafeTitle")}>
              <span className={autoExec ? "text-gold" : "text-ink-mute"}>⊙</span>
              <span className={autoExec ? "text-gold" : "text-ink-mute"}>{autoExec ? t("chat.modeOmni") : t("chat.modeSafe")}</span>
              <button onClick={() => setAutoExec((v) => { const n = !v; api.setAutoExec(n); return n; })} className={`tgl ml-1 ${autoExec ? "on" : ""}`} title={t("chat.toggleMode")} />
            </span>
            <span className="flex items-center gap-1 font-mono whitespace-nowrap" title={t("chat.modelPickTip")}>
              <select value={sessionModel}
                onChange={(e) => setSessionModel(e.target.value)}
                className="appearance-none bg-elevated/60 border border-line/60 rounded-md pl-1.5 pr-5 py-0.5 font-mono text-[10px] text-gold outline-none hover:border-gold/40 focus:border-gold transition-colors cursor-pointer leading-none max-w-[180px]"
                style={{ backgroundImage: "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8' viewBox='0 0 8 8'><path fill='%23d4af37' d='M1 2.5l3 3 3-3'/></svg></>\")", backgroundRepeat: "no-repeat", backgroundPosition: "right 4px center", backgroundSize: "7px 7px" }}>
                {(availableModels ?? []).map((m: string) => (
                  <option key={m} value={m} title={m}>{m}</option>
                ))}
              </select>
            </span>
            <button onClick={toggleMic} className={`btn-ghost ${listening ? "text-red" : ""}`} title={listening ? t("chat.stopMic") : t("chat.voiceInput")}><I.Mic size={14} /></button>
            <button onClick={insertCodeBlock} className="btn-ghost" title={t("chat.insertCode")}><span className="font-mono text-[12px]">{"</>"}</span></button>
            <button onClick={toggleWallet} className={`btn-ghost relative ${walletOpen ? "text-gold" : ""}`} title={t("chat.walletBtnTip")}>
              <I.Wallet size={14} />
              {(walletAgentConn || walletCexCfg) && (
                <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-green" style={{ boxShadow: "0 0 4px rgba(14,203,129,0.9)" }} />
              )}
            </button>
          </div>
          {editSel && (
            <div className="flex items-center gap-2 px-3 pb-2 font-mono text-[10px] text-gold">
              <I.Gear size={10} /> {t("chat.editingLast")}
              <button onClick={() => { setEditSel(null); setInput(""); }} className="ml-auto text-ink-mute hover:text-ink flex items-center gap-1"><I.X size={10} /> {t("chat.cancel")}</button>
            </div>
          )}
          {attachments.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 px-3 pb-2">
              {attachments.map((f, i) => (
                <span key={i} className="inline-flex items-center gap-1 rounded-md border border-line bg-elevated/40 px-1.5 py-0.5 font-mono text-[10.5px] text-ink-dim">
                  {f.kind === "image" && f.dataUrl
                    ? <><img src={f.dataUrl} alt={f.name} className="h-11 w-auto rounded-sm border border-line object-cover" title={f.name} />
                        <span className="text-ink-mute">{(f.size / 1024).toFixed(1)}KB</span></>
                    : <><I.Download size={10} /> {f.name}
                        <span className="text-ink-mute">· {(f.size / 1024).toFixed(1)}KB</span></>}
                  <button onClick={() => removeAttachment(i)} className="ml-0.5 text-ink-mute hover:text-red"><I.X size={10} /></button>
                </span>
              ))}
            </div>
          )}
          {/* 钱包快捷弹层：Agent 钱包（扫码）/ 链上钱包（API Key） */}
          {walletOpen && (
            <div className="absolute bottom-full right-2 mb-1.5 z-[70] w-[380px] max-w-[calc(100vw-20px)] overflow-hidden rounded-lg border border-line/80 bg-card shadow-2xl shadow-black/50"
              style={{ backdropFilter: "blur(12px)" }}>
              <div className="flex items-center gap-2 border-b border-line/60 px-3 py-2">
                <I.Wallet size={13} className="text-gold" />
                <span className="font-mono text-[12px] tracking-wider text-ink">{t("chat.walletTitle")}</span>
                <span className="pill pill-dim text-[9px]">{t("chat.walletQuick")}</span>
                {walletBusy && <span className="ml-1 inline-flex text-gold"><Spin /></span>}
                <button onClick={() => setWalletOpen(false)} className="ml-auto rounded p-0.5 hover:bg-elevated text-ink-dim" title={t("chat.closeTip")}><I.X size={12} /></button>
              </div>
              <div className="max-h-[66vh] overflow-auto p-3 space-y-4">
                {/* Agent 钱包（MPC 扫码） */}
                <div className="space-y-2.5">
                  <div className="flex items-center gap-2">
                    <I.Hex size={13} className="text-gold" />
                    <span className="font-mono text-[12px] text-ink">{t("chat.agentWallet")}</span>
                    {walletAgent ? (
                      <span className={`pill ${walletAgentConn ? "pill-green" : walletAgentReady ? "pill-red" : "pill-dim"}`}>
                        <span className={`dot ${walletAgentConn ? "dot-green live" : "dot-red"}`} />
                        {walletAgentConn ? t("chat.walletSignedIn") : walletAgentReady ? t("chat.walletNotLogin") : t("chat.walletCliMissing")}
                      </span>
                    ) : <span className="pill pill-dim">{t("chat.statusLoading")}</span>}
                    <button onClick={() => { setWalletOpen(false); onNav?.("wallet"); }} className="ml-auto btn-ghost text-[10.5px] py-1 text-gold">{t("chat.fullPage")} ↗</button>
                  </div>
                  {!walletAgent ? (
                    <div className="rounded-md border border-line bg-elevated/30 px-3 py-2 font-mono text-[10.5px] text-ink-mute">{t("chat.walletReading")}</div>
                  ) : !walletAgentReady ? (
                    <div className="rounded-md border border-red/30 bg-red/5 px-3 py-2 font-mono text-[10.5px] text-red leading-relaxed">
                      {t("chat.walletBawMissing")}
                    </div>
                  ) : walletAgentConn ? (
                    <div className="rounded-md border border-green/30 bg-green/5 px-3 py-2 font-mono text-[10.5px] text-green leading-relaxed">
                      {t("chat.walletMpcNote")}
                    </div>
                  ) : (
                    <>
                      <p className="text-[10.5px] text-ink-mute leading-relaxed">{t("chat.walletScanHintA")}<b className="text-gold">{t("chat.binanceApp")}</b>{t("chat.walletScanHintB")}</p>
                      <AgentSigninCard compact onDone={() => loadWalletLite()} runtimeBundled={!!walletRuntime?.bundled} />
                    </>
                  )}
                </div>

                <div className="border-t border-line/40" />

                {/* 链上钱包（CEX API Key + Secret） */}
                <div className="space-y-2.5">
                  <div className="flex items-center gap-2">
                    <I.Cex size={13} className="text-gold" />
                    <span className="font-mono text-[12px] text-ink">{t("chat.chainWallet")} <sup className="text-[8.5px] text-ink-mute">{t("chat.walletGwTag")}</sup></span>
                    {walletCex ? (
                      <span className={`pill ${walletCexCfg ? "pill-green" : "pill-dim"}`}>
                        <span className={`dot ${walletCexCfg ? "dot-green live" : "dot-red"}`} />
                        {walletCexCfg ? t("chat.connected") : t("chat.disconnected")}
                      </span>
                    ) : <span className="pill pill-dim">{t("chat.statusLoading")}</span>}
                    <button onClick={() => { setWalletOpen(false); onNav?.("wallet"); }} className="ml-auto btn-ghost text-[10.5px] py-1 text-gold">{t("chat.fullPage")} ↗</button>
                  </div>
                  <ChainWalletPanel compact onChanged={() => loadWalletLite()} />
                </div>

                <div className="border-t border-line/40" />
                <p className="text-[10px] text-ink-mute leading-relaxed px-0.5">
                  {t("chat.walletTip1")}
                  {t("chat.walletTip2")}
                </p>
              </div>
            </div>
          )}
        </div>
      </main>

      {/* ⌘K 命令面板：搜会话 / Bots / 快捷动作 */}
      {palOpen && (
        <div className="fixed inset-0 z-[60] flex items-start justify-center pt-[12vh]" style={{ background: "rgba(4,6,9,0.6)", backdropFilter: "blur(4px)" }}
          onClick={() => setPalOpen(false)}>
          <div className="glass-bright w-full max-w-lg overflow-hidden" style={{ borderRadius: 12 }} onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2 border-b border-line/60 px-3">
              <I.Search size={13} className="text-gold shrink-0" />
              <input ref={palRef} value={palQ} onChange={(e) => setPalQ(e.target.value)}
                placeholder={t("cmd.placeholder")}
                className="flex-1 bg-transparent py-3 font-mono text-[13px] text-ink placeholder-ink-mute outline-none" />
              <span className="font-mono text-[9px] text-ink-mute shrink-0 border border-line rounded px-1 py-0.5">ESC</span>
            </div>
            <div className="max-h-[50vh] overflow-auto py-1">
              {(() => {
                const q = palQ.trim().toLowerCase();
                const items: { icon: string; label: string; sub: string; run: () => void }[] = [];
                // 快捷动作（q 为空时置顶）
                items.push({ icon: "＋", label: t("chat.palNewConv"), sub: t("chat.palNewConvSub"), run: () => newConv() });
                items.push({ icon: "👤", label: t("chat.palNewBot"), sub: t("chat.palNewBotSub"), run: () => { setLeftTab("bots"); openAgentEditor("new"); } });
                items.push({ icon: "👥", label: t("chat.palNewGroup"), sub: t("chat.palNewGroupSub"), run: () => { setGName(""); setGPicked([]); setRoomMsg(""); setGroupFormOpen(true); } });
                items.push({ icon: "🛰️", label: t("chat.palGateway"), sub: t("chat.palGatewaySub"), run: () => { setLeftTab("sessions"); onNav?.("settings"); } });
                // Bots
                for (const b of agents ?? []) {
                  if (!q || b.name.toLowerCase().includes(q) || (b.title ?? "").toLowerCase().includes(q))
                    items.push({ icon: b.avatar, label: t("chat.palBotLabel") + b.name, sub: b.title || b.id, run: () => { setLeftTab("bots"); talkWith(b); } });
                }
                // 会话
                const scoped = (conversations ?? []).filter((c: any) => c.persona === (scopeName ?? "") && c.kind !== "room");
                for (const c of scoped.slice(0, 60)) {
                  if (!q || (c.title ?? "").toLowerCase().includes(q) || (c.preview ?? "").toLowerCase().includes(q))
                    items.push({ icon: "◈", label: t("chat.palConvLabel") + (c.title || c.id.slice(0, 8)), sub: c.preview ?? "", run: () => { setLeftTab("sessions"); selectConv(c.id); } });
                }
                const shown = items.slice(0, 30);
                if (!shown.length) return <div className="px-4 py-6 font-mono text-[11px] text-ink-mute text-center">{t("chat.palNoMatch")}</div>;
                return shown.map((it, i) => (
                  <button key={i} onClick={() => { setPalOpen(false); setPalQ(""); it.run(); }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-gold/10 transition-colors">
                    <span className="w-5 text-center text-[14px]">{it.icon}</span>
                    <span className="min-w-0 flex-1">
                      <span className="font-mono text-[12px] text-ink block truncate">{it.label}</span>
                      <span className="font-mono text-[9.5px] text-ink-mute block truncate">{it.sub}</span>
                    </span>
                    <span className="font-mono text-[9px] text-gold shrink-0">↵</span>
                  </button>
                ));
              })()}
            </div>
          </div>
        </div>
      )}

      {/* Agent 档案编辑弹窗（Hermes: New Agent / Edit Profile） */}
      {editBot !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6" style={{ background: "rgba(4,6,9,0.72)", backdropFilter: "blur(4px)" }}>
          <div className="glass-bright w-full max-w-2xl flex flex-col overflow-hidden" style={{ borderRadius: 12, maxHeight: "88vh" }}>
            {/* 头部（固定） */}
            <div className="flex items-center gap-2.5 px-5 py-3 border-b border-line">
              <AgentAvatar avatar={form.avatar} name={form.name || "New Agent"} color={form.color} size={36} />
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[13px] font-bold tracking-wide text-ink truncate">{editBot === "new" ? t("bots.newAgent") : `Edit · ${form.name || "Agent"}`}</div>
                <div className="font-mono text-[10px] text-ink-mute mt-0.5">{t("chat.profileHint")}</div>
              </div>
              <button onClick={() => setEditBot(null)} className="ml-auto text-ink-mute hover:text-ink"><I.X size={16} /></button>
            </div>
            {/* 表单主体（可滚动） */}
            <div className="flex-1 overflow-auto px-5 py-4 space-y-3">
              <div><label className="prefix block mb-1">Name *</label>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={t("chat.phAgentName")} className="field" /></div>
              <div><label className="prefix block mb-1">{t("bots.formTitle")}</label>
                <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder={t("chat.phAgentTitle")} className="field" /></div>
              <div><label className="prefix block mb-1">{t("bots.formDescription")}</label>
                <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={4}
                  placeholder={t("chat.phAgentDesc")} className="field resize-y min-h-[5.5rem]" /></div>
              <div>
                <label className="prefix block mb-1.5">{t("bots.formAvatar")}</label>
                <div className="flex items-center gap-1.5 mb-2">
                  <button onClick={() => setForm({ ...form, avatar: "blobatar" })}
                    className={`px-2.5 py-1 rounded-md font-mono text-[10.5px] border ${form.avatar.startsWith("blobatar") ? "bg-gold text-canvas border-gold" : "border-line text-ink-dim hover:text-ink"}`}>{t("chat.svgFace")}</button>
                  <button onClick={() => setForm({ ...form, avatar: "🤖" })}
                    className={`px-2.5 py-1 rounded-md font-mono text-[10.5px] border ${!form.avatar.startsWith("blobatar") ? "bg-gold text-canvas border-gold" : "border-line text-ink-dim hover:text-ink"}`}>{t("bots.formEmoji")}</button>
                </div>
                {form.avatar.startsWith("blobatar") ? (
                  <div className="flex items-center gap-3">
                    <div style={{ width: 56, height: 56 }}><Blobatar seed={form.avatar.split(":")[1] || form.name || "agent"} color={form.color} size={56} /></div>
                    <div className="text-[11px] text-ink-dim leading-relaxed">
                      {t("chat.blobHint1")}<br />{t("chat.blobHint2")}
                    </div>
                    <button onClick={() => setForm({ ...form, avatar: "blobatar:" + Math.random().toString(36).slice(2, 8) })}
                      className="ml-auto btn-ghost text-[11px] px-2 py-1">🎲 {t("chat.random")}</button>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {AVATARS.map((a) => (
                      <button key={a} onClick={() => setForm({ ...form, avatar: a })}
                        className={`w-8 h-8 rounded-full flex items-center justify-center text-[16px] border transition-all ${form.avatar === a ? "border-gold ring-1 ring-gold" : "border-line bg-elevated"}`}>{a}</button>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <label className="prefix block mb-1.5">{t("bots.formColor")}</label>
                <div className="flex flex-wrap gap-2">
                  {COLORS.map((c) => (
                    <button key={c} onClick={() => setForm({ ...form, color: c })}
                      className="w-7 h-7 rounded-full transition-transform" style={{ background: c, outline: form.color === c ? `2px solid #fff` : "none", outlineOffset: 2 }} />
                  ))}
                </div>
              </div>

              {/* Advanced：Hermes profile config（model / tone / skills） */}
              <details className="rounded-lg border border-line bg-elevated/20 overflow-hidden">
                <summary className="cursor-pointer list-none flex items-center gap-2 px-3 py-2 font-mono text-[11.5px] text-ink-dim hover:text-ink">
                  <I.Gear size={12} className="text-gold" /> Advanced profile config
                  <span className="ml-auto text-gold text-[10px]">▾</span>
                </summary>
                <div className="px-3 pb-3 pt-1 space-y-3 border-t border-line/50">
                  <div><label className="prefix block mb-1">{t("chat.cfgModel")}</label>
                    <input value={cfg.model} onChange={(e) => setCfg({ ...cfg, model: e.target.value })}
                      placeholder={t("chat.phCfgModel")} className="field" /></div>
                  <div><label className="prefix block mb-1">{t("chat.cfgTone")}</label>
                    <textarea value={cfg.tone} onChange={(e) => setCfg({ ...cfg, tone: e.target.value })} rows={3}
                      placeholder={t("chat.phCfgTone")} className="field resize-y min-h-[4.5rem]" /></div>
                  <div><label className="prefix block mb-1">{t("chat.cfgSystem")}</label>
                    <textarea value={cfg.prompt ?? ""} onChange={(e) => setCfg({ ...cfg, prompt: e.target.value })} rows={8}
                      placeholder={t("chat.phCfgSystem")} className="field font-mono text-[11px] resize-y min-h-[12rem]" />
                    <div className="font-mono text-[10px] text-ink-mute mt-1 text-right">{t("chat.promptLen", { n: (cfg.prompt ?? "").length })}</div></div>
                  <div>
                    <label className="prefix block mb-1">{t("chat.cfgTools")}</label>
                    <div className="flex flex-wrap gap-1 max-h-32 overflow-auto pr-1 mb-1.5">
                      {AGENT_TOOLS.map((t) => {
                        const on = cfg.tools.includes(t);
                        return (
                          <button key={t} onClick={() => setCfg({
                            ...cfg, tools: on ? cfg.tools.filter((x) => x !== t) : [...cfg.tools, t],
                          })}
                            className={`px-2 py-0.5 rounded font-mono text-[10px] border transition-colors ${on ? "border-gold/60 bg-gold/10 text-gold" : "border-line text-ink-mute hover:border-gold/40"}`}>
                            {t}
                          </button>
                        );
                      })}
                    </div>
                    <div className="flex items-center gap-2">
                      <button onClick={() => setCfg({ ...cfg, tools: [] })}
                        className="font-mono text-[9.5px] text-red/80 hover:text-red underline underline-offset-2">{t("chat.toolsClear")}</button>
                      <button onClick={() => setCfg({ ...cfg, tools: [...AGENT_TOOLS] })}
                        className="font-mono text-[9.5px] text-ink-mute hover:text-ink underline underline-offset-2">{t("chat.toolsAll")}</button>
                      <span className="font-mono text-[9.5px] text-ink-mute">{t("chat.toolsHint")}</span>
                    </div>
                  </div>
                  <div>
                    <label className="prefix block mb-1.5">{t("chat.cfgSkills")}</label>
                    <p className="font-mono text-[10.5px] text-ink-mute leading-relaxed">
                      {t("chat.skillsDesc1")} <span className="text-gold">run_skill</span> {t("chat.skillsDesc2")}
                      {t("chat.skillsDesc3", { n: availSkills.length })}
                    </p>
                  </div>
                </div>
              </details>
            </div>
            {/* 底部（固定） */}
            <div className="px-5 py-3 border-t border-line flex items-center justify-end gap-2">
              <span className="mr-auto font-mono text-[10px] text-ink-mute">{t("chat.scrollHint")}</span>
              <button onClick={() => setEditBot(null)} className="btn-ghost">{t("chat.cancel")}</button>
              <button onClick={saveAgent} className="btn-gold"><I.Check size={12} /> {t("chat.save")}</button>
            </div>
          </div>
        </div>
      )}

      {/* 文件查看（Files 面板点文件后的查看器） */}
      {fileModal && (
        <div onClick={closeFileModal}
          className="fixed inset-0 z-50 flex items-center justify-center p-6"
          style={{ background: "rgba(4,6,9,0.72)", backdropFilter: "blur(4px)" }}>
          <div onClick={(e) => e.stopPropagation()}
            className="glass-bright w-full max-w-3xl flex flex-col overflow-hidden"
            style={{ borderRadius: 12, maxHeight: "85vh" }}>
            <div className="flex items-center gap-2.5 px-5 py-3 border-b border-line">
              <I.Download size={16} className="text-gold shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[13px] font-bold text-ink truncate">{fileModal.name}</div>
                <div className="font-mono text-[10px] text-ink-mute truncate">
                  {fileModal.path || t("chat.fileRoot")} · {fmtSize(fileModal.size) || "—"}
                  {fileModal.is_text ? t("chat.fileText") : fileModal.size > 0 ? t("chat.fileBin") : ""}
                </div>
              </div>
              <button onClick={closeFileModal} className="ml-auto text-ink-mute hover:text-ink"><I.X size={16} /></button>
            </div>
            <div className="flex-1 overflow-auto p-4 bg-canvas/40">
              {fileModal.loading ? (
                <div className="shimmer h-32 rounded-md" />
              ) : fileModal.error ? (
                <div className="font-mono text-[12px] text-red">⚠ {fileModal.error}</div>
              ) : fileModal.too_large ? (
                <div className="font-mono text-[12px] text-ink-mute">{t("chat.fileTooLarge")}<span className="text-ink">{fileModal.path}</span></div>
              ) : !fileModal.is_text ? (
                <div className="font-mono text-[12px] text-ink-mute">{t("chat.fileBinPrev", { size: fmtSize(fileModal.size) })}</div>
              ) : (
                <pre className="font-mono text-[11.5px] leading-relaxed text-ink whitespace-pre-wrap break-all">
                  {fileModal.content || t("chat.fileEmpty")}
                </pre>
              )}
            </div>
            <div className="px-5 py-2.5 border-t border-line flex items-center justify-between">
              <span className="font-mono text-[10px] text-ink-mute">{t("chat.escHint")}</span>
              <div className="flex items-center gap-2">
                <button onClick={() => { if (fileModal) deleteFile(fileModal.name); }}
                  className="btn-ghost text-[11px] text-red/80 hover:text-red" disabled={fileModal.loading}>
                  <I.Trash size={11} /> {t("chat.fileDelete")}
                </button>
                <button onClick={() => copyText(fileModal.content)}
                  className="btn-ghost text-[11px]" disabled={!fileModal.is_text || fileModal.loading}>
                  <I.Copy size={11} /> {t("chat.copyAll")}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 审批白名单管理：查看 / 移除已信任的操作 */}
      {wlOpen && (
        <div onClick={() => setWlOpen(false)}
          className="fixed inset-0 z-50 flex items-center justify-center p-6"
          style={{ background: "rgba(4,6,9,0.72)", backdropFilter: "blur(4px)" }}>
          <div onClick={(e) => e.stopPropagation()}
            className="glass-bright w-full max-w-md flex flex-col overflow-hidden" style={{ borderRadius: 12, maxHeight: "70vh" }}>
            <div className="flex items-center gap-2.5 px-5 py-3 border-b border-line">
              <I.Pin size={16} className="text-gold" />
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[13px] font-bold text-ink">{t("chat.wlTitle")}</div>
                <div className="font-mono text-[10px] text-ink-mute">{t("chat.wlSub")}</div>
              </div>
              <button onClick={() => setWlOpen(false)} className="text-ink-mute hover:text-ink"><I.X size={16} /></button>
            </div>
            <div className="flex-1 overflow-auto p-4 space-y-1.5">
              {wlItems.length === 0 ? (
                <div className="font-mono text-[11px] text-ink-mute text-center py-8">
                  {t("chat.wlEmpty")}<br /><span className="text-[10px]">{t("chat.wlEmptyHint")}</span>
                </div>
              ) : wlItems.map((r) => (
                <div key={r} className="flex items-center gap-2 rounded-md border border-line bg-card/40 px-3 py-2 group/wl">
                  <I.Lock size={11} className="text-gold shrink-0" />
                  <span className="font-mono text-[11px] text-ink flex-1 break-all">{r}</span>
                  <button onClick={() => removeWl(r)} title={t("chat.wlRemove")}
                    className="shrink-0 rounded p-1 text-red/70 hover:text-red hover:bg-red/10 opacity-40 group-hover/wl:opacity-100 transition-opacity">
                    <I.Trash size={11} />
                  </button>
                </div>
              ))}
            </div>
            <div className="px-5 py-3 border-t border-line flex items-center justify-between">
              <span className="font-mono text-[10px] text-ink-mute">{t("chat.wlFmt")}</span>
              <button onClick={() => { loadWl(); }} className="btn-ghost text-[11px]"><I.Refresh size={11} /> {t("chat.refresh")}</button>
            </div>
          </div>
        </div>
      )}

      {/* 新建群聊房间（Hermes: New Group Chat —— 选择 bot 加入） */}
      {groupFormOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6" style={{ background: "rgba(4,6,9,0.72)", backdropFilter: "blur(4px)" }}>
          <div className="glass-bright w-full max-w-md p-5" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2.5 mb-4">
              <I.Users size={20} className="text-gold" />
              <div>
                <div className="font-mono text-[13px] font-bold tracking-wide text-ink">{t("bots.newGroupTitle")}</div>
                <div className="font-mono text-[10px] text-ink-mute mt-0.5">{t("chat.groupSub")}</div>
              </div>
              <button onClick={() => setGroupFormOpen(false)} className="ml-auto text-ink-mute hover:text-ink"><I.X size={16} /></button>
            </div>
            {roomMsg && <div className="mb-3 rounded-md border border-red/40 bg-red/5 px-3 py-2 font-mono text-[11px] text-red break-all">{roomMsg}</div>}
            <div><label className="prefix block mb-1">{t("chat.groupName")}</label>
              <input value={gName} onChange={(e) => setGName(e.target.value)} placeholder={t("chat.phGroupName")} className="field" /></div>
            <div className="mt-3">
              <label className="prefix block mb-1.5">{t("chat.membersSel", { n: gPicked.length })}</label>
              <div className="grid grid-cols-1 max-h-56 overflow-auto pr-1 gap-1">
                {agents.map((ag) => {
                  const on = gPicked.includes(ag.name);
                  return (
                    <button key={ag.id} onClick={() => togglePick(ag.name)}
                      className={`flex items-center gap-2 px-2 py-1.5 rounded-md border transition-colors text-left ${on ? "border-gold/60 bg-gold/5" : "border-line bg-card/40 hover:bg-elevated/60"}`}>
                      <AgentAvatar avatar={ag.avatar} name={ag.name} color={ag.color} size={22} />
                      <div className="min-w-0 flex-1">
                        <div className="font-mono text-[12px] text-ink truncate">{ag.name}</div>
                        <div className="font-mono text-[9.5px] text-ink-mute truncate">{ag.title}</div>
                      </div>
                      <span className={`tgl ${on ? "on" : ""} scale-75`} />
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="mt-5 flex items-center justify-end gap-2">
              <button onClick={() => setGroupFormOpen(false)} className="btn-ghost">{t("chat.cancel")}</button>
              <button onClick={createRoom} className="btn-gold"><I.Check size={12} /> {t("chat.createEnter")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* Routines —— 绑定到当前 Agent 的定时任务（Hermes Routines tile） */
function RoutinesInline({ bot }: { bot: Agent }) {
  const t = useT();
  const [jobs, setJobs] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [rName, setRName] = useState("");
  const [rCron, setRCron] = useState("0 9 * * *");

  const load = async () => {
    try {
      const d: any = await api.cron();
      const list = Array.isArray(d) ? d : (d?.items ?? []);
      setJobs(list.filter((j: any) => j?.persona === bot.name));
    } catch {}
  };
  useEffect(() => { if (open) load(); }, [open, bot.name]);

  const add = async () => {
    if (!rName.trim()) return;
    await api.addCron({ name: rName.trim(), schedule: rCron, task: "daily_scan_report", enabled: true, persona: bot.name });
    setRName(""); setAdding(false); load();
  };
  const toggle = async (j: any) => { await api.toggleCron(j.id, !j.enabled); load(); };
  const remove = async (j: any) => { await api.deleteCron(j.id); load(); };

  const fmtNext = (t?: number) => {
    if (!t) return "—";
    const ms = t > 1e12 ? t : t * 1000;
    return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <details open={open} onToggle={(e: any) => setOpen(e.currentTarget.open)} className="shrink-0 rounded-md border border-line/70 bg-card/50 overflow-hidden w-[215px]">
      <summary className="cursor-pointer list-none flex items-center gap-1.5 px-2.5 py-1.5 font-mono text-[10.5px] text-ink-dim hover:text-ink">
        <I.Refresh size={11} className="text-gold" /> Routines · @{bot.name}
        <span className="pill pill-dim ml-1 text-[9px]">{jobs.length}</span>
        <span className="ml-auto text-gold text-[9px]">▾</span>
      </summary>
      <div className="border-t border-line/50 px-2 py-1.5 space-y-1">
        {jobs.length === 0 && <div className="font-mono text-[9.5px] text-ink-mute px-1 py-1">{t("chat.noCron")}</div>}
        {jobs.map((j) => (
          <div key={j.id} className="flex items-center gap-1.5 py-1 px-1 rounded hover:bg-elevated/50">
            <div className="min-w-0 flex-1">
              <div className="font-mono text-[10px] text-ink truncate">{j.name}</div>
              <div className="font-mono text-[8.5px] text-ink-mute">{j.schedule} · next {fmtNext(j.next_run)}</div>
            </div>
            <button onClick={() => toggle(j)} className={`tgl ${j.enabled ? "on" : ""} scale-75`} title={t("chat.tglRun")} />
            <button onClick={() => remove(j)} className="text-red hover:bg-red/10 rounded p-0.5"><I.Trash size={10} /></button>
          </div>
        ))}
        {adding ? (
          <div className="space-y-1 pt-1">
            <input value={rName} onChange={(e) => setRName(e.target.value)} placeholder={t("chat.phCronName")} className="field text-[10px] py-1" />
            <input value={rCron} onChange={(e) => setRCron(e.target.value)} placeholder={t("chat.phCron")} className="field text-[10px] py-1" />
            <div className="flex gap-1">
              <button onClick={add} className="btn-gold flex-1 text-[10px] py-1"><I.Check size={10} /> {t("chat.save")}</button>
              <button onClick={() => setAdding(false)} className="btn-ghost text-[10px] py-1">{t("chat.cancel")}</button>
            </div>
          </div>
        ) : (
          <button onClick={() => setAdding(true)} className="w-full rounded border border-dashed border-line py-1 font-mono text-[9.5px] text-ink-dim hover:text-gold hover:border-gold/40 transition-colors flex items-center justify-center gap-1">
            <I.Plus size={9} /> {t("chat.addRoutine")}
          </button>
        )}
      </div>
    </details>
  );
}

/* ===== 输出渲染（Hermes 风格消息流）=====
 * Md：段落/标题/列表/引用/表格 + 行内加粗、行内代码、链接
 * 代码块 → CodeCard：语言徽标 + 复制 + 超长自动折叠
 */

function isTableSep(s?: string): boolean {
  if (!s || !/^\s*\|/.test(s)) return false;
  const cells = s.split("|").map((x) => x.trim());
  return cells.length >= 2 && cells.every((c) => c === "" || /^:?-{2,}:?$/.test(c));
}

function inlineMd(t: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  // 顺序很重要：先匹配跨内容的加粗/代码/斜体/删除线/链接，再降级。
  // 支持 **bold**、__bold__、`code`、*italic*、_italic_、~~del~~、[label](url)
  const re = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\n]+\*|_[^_\n]+_|~~[^~]+~~|\[[^\]]+\]\((https?:\/\/[^\s)]+)\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(t))) {
    if (m.index > last) out.push(<React.Fragment key={key++}>{t.slice(last, m.index)}</React.Fragment>);
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={key++} className="font-semibold text-ink">{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("__")) out.push(<strong key={key++} className="font-semibold text-ink">{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={key++} className="px-1 py-0.5 rounded bg-elevated font-mono text-[12px] text-gold">{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("~~")) out.push(<del key={key++} className="text-ink-dim/70 line-through">{tok.slice(2, -2)}</del>);
    else if (tok.startsWith("_") && tok.endsWith("_") && tok.length > 2) out.push(<em key={key++} className="italic text-ink-dim">{tok.slice(1, -1)}</em>);
    else if (tok.startsWith("*") && tok.endsWith("*") && tok.length > 2) out.push(<em key={key++} className="italic text-ink-dim">{tok.slice(1, -1)}</em>);
    else {
      const mm = tok.match(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/);
      out.push(mm
        ? <a key={key++} href={mm[2]} target="_blank" rel="noopener noreferrer" className="underline text-gold hover:text-ink break-all">{mm[1]}</a>
        : <React.Fragment key={key++}>{tok}</React.Fragment>);
    }
    last = m.index + tok.length;
  }
  if (last < t.length) out.push(<React.Fragment key={key++}>{t.slice(last)}</React.Fragment>);
  return out;
}

/** 把一段非代码文本渲染成块级列表（标题/列表/引用/表格/段落）。 */
function renderMdBlock(seg: string, keyBase: string): React.ReactNode[] {
  const lines = seg.split("\n");
  const nodes: React.ReactNode[] = [];
  let para: string[] = [];
  let pk = 0;
  const flushPara = () => {
    if (!para.length) return;
    nodes.push(<div key={`${keyBase}-p${pk++}`} className="whitespace-pre-wrap text-ink/90">{inlineMd(para.join("\n"))}</div>);
    para = [];
  };
  for (let li = 0; li < lines.length; li++) {
    const ln = lines[li];
    if (/^###\s/.test(ln)) {
      flushPara();
      nodes.push(<div key={`${keyBase}-h3${li}`} className="font-semibold text-[14px] mt-2.5 mb-0.5 text-ink">{inlineMd(ln.replace(/^###\s/, ""))}</div>);
    } else if (/^##\s/.test(ln)) {
      flushPara();
      nodes.push(<div key={`${keyBase}-h2${li}`} className="font-bold text-[16px] mt-3 mb-1 flex items-center gap-1.5 text-ink">
        <span className="w-1 h-[15px] rounded-full bg-gold shrink-0" />
        {inlineMd(ln.replace(/^##\s/, ""))}
      </div>);
    } else if (/^#\s/.test(ln)) {
      flushPara();
      nodes.push(<div key={`${keyBase}-h1${li}`} className="font-extrabold text-[18px] mt-3.5 mb-1 text-ink flex items-center gap-2">
        <span className="w-[3px] h-[18px] rounded-full bg-gold shrink-0" />
        {inlineMd(ln.replace(/^#\s/, ""))}
      </div>);
    } else if (/^\s*[-*]\s+/.test(ln)) {
      flushPara();
      nodes.push(
        <div key={`${keyBase}-ul${li}`} className="flex gap-2 pl-0.5 my-[1px]">
          <span className="text-gold select-none mt-[3px] text-[7px] leading-none">●</span>
          <span className="min-w-0 flex-1 text-ink/90">{inlineMd(ln.replace(/^\s*[-*]\s+/, ""))}</span>
        </div>,
      );
    } else if (/^\s*\d+\.\s+/.test(ln)) {
      flushPara();
      const mm = ln.match(/^(\s*)(\d+)\.\s+/)!;
      nodes.push(
        <div key={`${keyBase}-ol${li}`} className="flex gap-2 pl-0.5 my-[1px]">
          <span className="text-gold font-mono text-[11px] tabular select-none min-w-[1.5em] text-right shrink-0">{mm[2]}.</span>
          <span className="min-w-0 flex-1 text-ink/90">{inlineMd(ln.slice(mm[0].length))}</span>
        </div>,
      );
    } else if (/^>\s?/.test(ln)) {
      // 连续引用行聚合成一块（WorkBuddy 式金色提示卡）
      flushPara();
      const qlines: string[] = [];
      while (li < lines.length && /^>\s?/.test(lines[li])) {
        qlines.push(lines[li].replace(/^>\s?/, ""));
        li++;
      }
      li--;
      nodes.push(
        <div key={`${keyBase}-q${li}`} className="my-1.5 rounded-r-md border-l-[3px] border-gold/60 bg-gold/[0.06] px-3 py-2 text-[13px] text-ink-dim leading-relaxed">
          {qlines.map((ql, qi) => <div key={qi} className={qi ? "mt-1" : ""}>{inlineMd(ql)}</div>)}
        </div>,
      );
    } else if (/^---+\s*$/.test(ln.trim()) || /^\*\*\*+\s*$/.test(ln.trim())) {
      flushPara();
      nodes.push(<div key={`${keyBase}-hr${li}`} className="my-2 h-px bg-line" />);
    } else if (isTableSep(lines[li + 1])) {
      // 表头行 + 紧随其后的 --- 分隔行 → 整表
      flushPara();
      const head = ln.split("|").map((s) => s.trim()).filter((s) => s !== "");
      const rows: string[][] = [];
      li += 2;
      while (li < lines.length && lines[li].includes("|")) {
        rows.push(lines[li].split("|").map((s) => s.trim()).filter((s) => s !== ""));
        li++;
      }
      li--;
      nodes.push(<TableCard key={`${keyBase}-t${li}`} head={head} rows={rows} />);
    } else if (!ln.trim()) {
      flushPara();
    } else {
      para.push(ln);
    }
  }
  flushPara();
  return nodes;
}

/** 代码卡片：语言徽标 + 复制 + 超长自动折叠（Hermes shiki 卡片形态的轻量版） */
function CodeCard({ lang, code }: { lang: string; code: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const [fold, setFold] = useState(code.split("\n").length > 40);
  const lineCount = code.split("\n").length;
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1200); } catch { pushToast(t("common.copyFail"), "", "warn"); }
  };
  return (
    <div className="my-2 rounded-md border border-line bg-canvas overflow-hidden">
      <div className="flex items-center gap-2 px-2.5 py-1.5 border-b border-line/60 bg-elevated/40">
        <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-gold">{lang || "code"}</span>
        <span className="font-mono text-[9px] text-ink-mute">{t("chat.linesN", { n: lineCount })}</span>
        {fold && (
          <button onClick={() => setFold(false)} className="ml-auto font-mono text-[9.5px] text-ink-mute hover:text-ink transition-colors">{t("chat.expandAll")}</button>
        )}
        <button onClick={copy} className={`${fold ? "" : "ml-auto"} flex items-center gap-1 font-mono text-[9.5px] ${copied ? "text-green" : "text-ink-mute hover:text-ink"} transition-colors`}>
          {copied ? t("chat.copiedOk") : t("chat.copy")}
        </button>
      </div>
      {fold ? (
        <button onClick={() => setFold(false)} className="block w-full text-left font-mono text-[11.5px] text-green/90 px-3 py-2 leading-relaxed hover:bg-elevated/30 whitespace-pre-wrap transition-colors">
          {code.split("\n").slice(0, 6).join("\n")}
          {lineCount > 6 ? `\n${t("chat.foldHint", { n: lineCount })}` : ""}
        </button>
      ) : (
        <pre className="p-3 overflow-auto font-mono text-[12px] text-green/90 leading-relaxed max-h-[480px] whitespace-pre">{code}</pre>
      )}
    </div>
  );
}

function TableCard({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <div className="my-2 overflow-x-auto rounded-md border border-line">
      <table className="w-full border-collapse font-mono text-[11.5px] min-w-max">
        <thead>
          <tr>{head.map((h, i) => <th key={i} className="text-left px-3 py-1.5 border-b border-line bg-elevated/40 text-gold font-semibold whitespace-nowrap">{inlineMd(h)}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} className={ri % 2 ? "bg-elevated/20" : ""}>
              {head.map((_, ci) => (
                <td key={ci} className="px-3 py-1.5 border-b border-line/40 text-ink-dim align-top whitespace-pre-wrap">{r[ci] != null ? inlineMd(String(r[ci])) : ""}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* Markdown 渲染：围栏代码块 → CodeCard；其余文本 → 块级解析 */
function Md({ text }: { text: string }) {
  if (!text) return null;
  const out: React.ReactNode[] = [];
  const parts = text.split(/```/);
  parts.forEach((seg, i) => {
    if (i % 2 === 1) {
      const nl = seg.indexOf("\n");
      const lang = (nl === -1 ? seg : seg.slice(0, nl)).trim();
      const code = (nl === -1 ? "" : seg.slice(nl + 1)).replace(/\n$/, "");
      out.push(<CodeCard key={`c${i}`} lang={lang} code={code} />);
      return;
    }
    out.push(...renderMdBlock(seg, `b${i}`));
  });
  return <>{out}</>;
}
