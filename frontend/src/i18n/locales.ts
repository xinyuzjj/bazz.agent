// 界面文案字典 —— 中文优先（基线版本），英文版根据中文化后的 UI 设计而来
// 所有 i18n key 必须同时存在于 zh 与 en，未在 dict 中命中时 fallback 为 key 自身。

export type Dict = Record<string, string>;

export const zh: Dict = {
  // ===== 顶部主导航（Shell NAV）=====
  "nav.chat": "对话",
  "nav.markets": "行情",
  "nav.wallet": "Agent 钱包",
  "nav.cex": "Binance CEX",
  "nav.council": "广场",
  "nav.skills": "技能库",
  "nav.settings": "设置",

  // ===== 顶栏右侧胶囊 =====
  "topbar.memory": "记忆",
  "topbar.llmArmed": "LLM 已连接",
  "topbar.llmOffline": "LLM 未连接",

  // ===== 顶栏切换按钮 tooltip =====
  "topbar.theme.dark": "切换为浅色",
  "topbar.theme.light": "切换为深色",
  "topbar.lang.tooltip": "语言 / Language",

  // ===== 左侧栏 Tab（ChatView 中 SESSIONS/BOTS/FILES/TERMINAL）=====
  "leftTab.sessions": "会话",
  "leftTab.bots": "智能体",
  "leftTab.files": "文件",
  "leftTab.terminal": "终端",

  // ===== 会话列表 =====
  "sessions.master": "主控",
  "sessions.allAround": "主会话（全能）",
  "sessions.scope": "@{name} 的专属会话",
  "sessions.newConv": "新建会话",
  "sessions.newConvForAgent": "新建 @{agent} 会话",
  "sessions.empty": "（空会话）",
  "sessions.archived": "已归档",
  "sessions.groupView": "群聊视角",
  "sessions.exitBot": "退出，回到默认会话",
  "sessions.renameTip": "右键 → 重命名；悬浮 ⋮ 归档/删除",
  "sessions.menu": "归档 / 删除",
  "sessions.toArchive": "归档 / 删除",
  "sessions.toDelete": "删除",
  "sessions.toRename": "重命名",
  "sessions.toPin": "置顶",

  // ===== Bots 智能体面板 =====
  "bots.title": "智能体",
  "bots.newAgent": "新建智能体",
  "bots.newGroup": "新建群聊",
  "bots.editProfile": "编辑资料",
  "bots.delete": "删除",
  "bots.exportPack": "导出 Bot 包 (.md)",
  "bots.import": "导入",
  "bots.formTitle": "标题",
  "bots.formDescription": "简介",
  "bots.formAvatar": "头像",
  "bots.formEmoji": "表情",
  "bots.formColor": "颜色",
  "bots.formModel": "默认模型",
  "bots.formTone": "语气风格",
  "bots.formSystem": "系统提示追加",
  "bots.newGroupTitle": "新建群聊",
  "bots.groupNamePh": "例如 市场策略会 / 风控评审",
  "bots.groupNameLabel": "群聊名称",
  "bots.groupPickHint": "勾选要拉进群聊的智能体",
  "bots.startHint": "以该 Agent 身份开始对话。",
  "bots.switchHint": "在左侧 Bots 里可切换 / 新建 / 编辑你的 Agents。",
  "bots.loadFail": "加载 Bots 失败",

  // ===== 命令面板 =====
  "cmd.placeholder": "搜索会话、Bots… 或输入动作（新会话 / 新 Agent / 群聊）",

  // ===== 行情页 =====
  "markets.title": "行情",
  "markets.live": "实时",

  // ===== 钱包 =====
  "wallet.live": "实时",
  "wallet.skills": "Wallet Skills",

  // ===== 记忆 =====
  "memory.title": "持久记忆 v2",

  // ===== 紧急熔断 =====
  "panic.haltTime": "熔断时间",
  "panic.logRef": "日志编号",
  "panic.haltedTitle": "系统已挂起",
  "panic.haltTitle": "紧急熔断",

  // ===== Council/裁定 =====
  "council.statutory": "STATUTORY RULING",
  "council.modelConf": "模型置信度",
  "council.tab30d": "30天",
  "council.tab90d": "90天",
  "council.tabAll": "全部",
  "council.recents": "最近",

  // ===== Settings 表单 =====
  "settings.provider": "提供商",
  "settings.baseUrl": "Base URL",
  "settings.apiKey": "API Key",
};

export const en: Dict = {
  // ===== Top navigation (Shell NAV) — designed on top of the Chinese baseline =====
  "nav.chat": "Chat",
  "nav.markets": "Markets",
  "nav.wallet": "Agent Wallet",
  "nav.cex": "Binance CEX",
  "nav.council": "Square",
  "nav.skills": "Skills",
  "nav.settings": "Settings",

  // ===== TopBar right pills =====
  "topbar.memory": "MEMORY",
  "topbar.llmArmed": "LLM ARMED",
  "topbar.llmOffline": "LLM OFFLINE",

  "topbar.theme.dark": "Switch to light",
  "topbar.theme.light": "Switch to dark",
  "topbar.lang.tooltip": "Language",

  "leftTab.sessions": "Sessions",
  "leftTab.bots": "Bots",
  "leftTab.files": "Files",
  "leftTab.terminal": "Terminal",

  "sessions.master": "MASTER",
  "sessions.allAround": "Master session (all-round)",
  "sessions.scope": "@{name}'s dedicated session",
  "sessions.newConv": "New session",
  "sessions.newConvForAgent": "New @{agent} session",
  "sessions.empty": "(empty)",
  "sessions.archived": "[Archived]",
  "sessions.groupView": "Group view",
  "sessions.exitBot": "Exit, back to default session",
  "sessions.renameTip": "Right-click → rename; hover ⋮ archive/delete",
  "sessions.menu": "Archive / Delete",
  "sessions.toArchive": "Archive / Delete",
  "sessions.toDelete": "Delete",
  "sessions.toRename": "Rename",
  "sessions.toPin": "Pin",

  "bots.title": "Bots",
  "bots.newAgent": "New agent",
  "bots.newGroup": "New group chat",
  "bots.editProfile": "Edit profile",
  "bots.delete": "Delete",
  "bots.exportPack": "Export Bot pack (.md)",
  "bots.import": "Import",
  "bots.formTitle": "Title",
  "bots.formDescription": "Description",
  "bots.formAvatar": "Avatar",
  "bots.formEmoji": "Emoji",
  "bots.formColor": "Color",
  "bots.formModel": "Default model",
  "bots.formTone": "Tone & style",
  "bots.formSystem": "Additional system prompt",
  "bots.newGroupTitle": "New group chat",
  "bots.groupNamePh": "e.g. Market Strategy Council / Risk Review",
  "bots.groupNameLabel": "Group name",
  "bots.groupPickHint": "Tick the agents to add to the group",
  "bots.startHint": "Start chatting as this agent.",
  "bots.switchHint": "Switch / create / edit your agents in the Bots panel.",
  "bots.loadFail": "Failed to load Bots",

  "cmd.placeholder": "Search sessions, Bots… or type a command (new session / new agent / group)",

  "markets.title": "Markets",
  "markets.live": "LIVE",

  "wallet.live": "LIVE",
  "wallet.skills": "Wallet Skills",

  "memory.title": "PERSISTENT_MEMORY_V2",

  "panic.haltTime": "HALT_TIME",
  "panic.logRef": "LOG_REF",
  "panic.haltedTitle": "SYSTEM HALTED",
  "panic.haltTitle": "PANIC HALT",

  "council.statutory": "STATUTORY RULING",
  "council.modelConf": "Model Confidence",
  "council.tab30d": "30D",
  "council.tab90d": "90D",
  "council.tabAll": "ALL",
  "council.recents": "RECENTS",

  "settings.provider": "Provider",
  "settings.baseUrl": "Base URL",
  "settings.apiKey": "API Key",
};