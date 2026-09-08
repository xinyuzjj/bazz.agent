<div align="center">

```
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   ██████╗  █████╗  ███████╗███████╗   █████╗  ██████╗ ███████╗ ║
║   ██╔══██╗██╔══██╗╚══███╔╝╚══███╔╝  ██╔══██╗██╔════╝ ██╔════╝ ║
║   ██████╔╝███████║  ███╔╝    ███╔╝   ███████║██║  ███╗█████╗   ║
║   ██╔══██╗██╔══██║ ███╔╝    ███╔╝    ██╔══██║██║   ██║██╔══╝   ║
║   ██████╔╝██║  ██║███████╗███████╗   ██║  ██║╚██████╔╝███████╗ ║
║   ╚═════╝ ╚═╝  ╚═╝╚══════╝╚══════╝   ╚═╝  ╚═╝ ╚═════╝ ╚══════╝ ║
║                                                              ║
║            —— 你的币安副驾：先问过你，才替你扣扳机 ——           ║
╚══════════════════════════════════════════════════════════════╝
```

**把 Binance Agent OS 四块原生能力（MCP / Agentic Wallet / x402 支付 / Skill Hub）拧成一条真闭环的 AI 交易助手**

> `Scan → Analyze → Propose → Human-Approval → Execute`
>
> **装一个没问题？——先问你。** 桌面便携版（双击即跑） · 本地 Web 版 · 同一套代码。

[![Release](https://img.shields.io/badge/Release-v1.2.18-f0b90b?logo=github&logoColor=white)](https://github.com/xinyuzjj/bazz.agent/releases/latest)
[![Portable](https://img.shields.io/badge/Download-portable.zip-2ea44f?logo=windows&logoColor=white)](https://github.com/xinyuzjj/bazz.agent/releases/latest/download/BAZZ.AGENT-v1.2.18-win32-x64-portable.zip)
[![Setup](https://img.shields.io/badge/Download-setup.exe-4a9eff?logo=windows&logoColor=white)](https://github.com/xinyuzjj/bazz.agent/releases/latest/download/BAZZ.AGENT-v1.2.18-setup.exe)
[![Pages](https://img.shields.io/badge/Live-Pages-181717?logo=githubpages&logoColor=white)](https://xinyuzjj.github.io/bazz.agent/)
[![中文](https://img.shields.io/badge/README-中文-f0b90b)](./README.zh-CN.md)

[![Track A](https://img.shields.io/badge/Binance%20Agent%20OS-Track%20A-f0b90b)](#🏆-hackathon)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)](#-技术栈)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](#-技术栈)
[![React](https://img.shields.io/badge/React%2018-61dafb?logo=react&logoColor=222)](#-技术栈)
[![Electron](https://img.shields.io/badge/Electron-31-47848f?logo=electron&logoColor=white)](#-架构)
[![Vite](https://img.shields.io/badge/Vite-5-646cff?logo=vite&logoColor=white)](#-技术栈)
[![MIT](https://img.shields.io/badge/license-MIT-success)](#-license)

![Main cockpit](./docs/screenshot.png)

> 🎬 **88 秒真实操作实录** —— 历史会话 → 行情雷达 → 一键"现货买入"跳对话等 Agent 分析 → 记忆 → 钱包 → 设置。
> [▶ 在线观看 (Pages)](https://xinyuzjj.github.io/bazz.agent/#demo) · [⬇ 下载 MP4 (3.2 MB)](./docs/video/BAZZ-demo-v1.mp4)

</div>

---

## 📑 目录

1. [它不是另一个聊天框](#-它不是另一个聊天框)
2. [核心闭环](#-核心闭环)
3. [看家本领](#-看家本领)
4. [快速开始](#-快速开始)
5. [四块原生能力](#-四块原生能力--真实协议不是摆设)
6. [技术栈](#-技术栈)
7. [架构](#-架构)
8. [安全模型](#-安全模型)
9. [目录结构](#-目录结构)
10. [Hackathon](#-hackathon)

---

## 🎯 它不是另一个聊天框

AI 助手都在**说话**。BAZZ.AGENT **做事** —— 但你必须先点头。

这**不是**给 Binance API 套层聊天皮肤。它是一个跑在你币安账户上的**智能体操作系统**：

- 读的是 Agent OS 暴露出来的**真实市场**；
- 直接驱动**四块官方能力**（MCP / Wallet / x402 / Skill Hub）；
- 凡是敏感动作（下单 / 转账 / 签名支付）一律在对话里弹出一张 `确认` 卡，**等你亲手确认**，才落在你的真实钥匙 / 无密钥 MPC 钱包上。

工作台是 Hermes 风格的三分区**交易舱**：

| 舱位 | 装了什么 |
|------|----------|
| **左** | 会话 / 人格 / 工具 / 文件 |
| **中** | Agent 对话 · 实时工具卡 · SSE 流式输出 |
| **右** | 实时信号台 · 持仓与账户面板 |

顶栏**一键**切换深/浅色主题、中/英界面，且重启后自动记住。

---

## 🔁 核心闭环

```
        ┌─────────────┐
   ┌───▶│   📡 扫描    │  全市场雷达 · Monster Radar · 24h 盯盘
   │    └──────┬──────┘
   │           ▼
   │    ┌─────────────┐
   │    │   🧠 分析    │  多模型 LLM · 多工具 · 规则引擎兜底
   │    └──────┬──────┘
   │           ▼
   │    ┌─────────────┐
   │    │   💡 建议    │  带杠杆/仓位/止盈止损的信号卡
   │    └──────┬──────┘
   │           ▼
   │    ┌─────────────┐
   │    │   ✅ 人工确认 │  ←——— 关键：你在对话里点「确认」
   │    └──────┬──────┘
   │           ▼
   │    ┌─────────────┐
   │    │   ⚡ 执行    │  CEX / Agentic Wallet / x402
   │    └─────────────┘
   └──────────────────┘（执行完回到扫描，继续盯）

   敏感动作默认需要确认；可对可信操作勾选白名单。
```

---

## ✨ 看家本领

| | 你得到的是什么 |
|---|---|
| 📡 **实时行情雷达** | 全市场异动扫描 · Monster Radar 爆发窗口 · 24h 自动盯盘 · BTC/ETH/SOL/BNB 实时报价，**零 API Key**。 |
| 🗣 **对话式智能体舱** | SSE/NDJSON 流式对话 · 实时工具卡 · 文件/语音输入 · 多供应商 LLM，附**全离线可用的确定性规则引擎**兜底。 |
| ✅ **人在回路（默认如此）** | 每笔交易 / 转账 / 签名支付都有一张确认卡——**敏感操作从不静默执行**。 |
| 🧠 **长期记忆** | 从对话自动学习跨会话偏好与风控锚点，索引、可检索，在「记忆」面板回看。 |
| 👛 **Agentic Wallet（无密钥 MPC）** | 经 `baw` 交换 BSC / Ethereum / Base / Solana，尊重官方每日限额；**私钥永不经过应用**。 |
| 💹 **真实 CEX 交易** | 经白名单 `binance-cli` profile 在真实账户做现货 / 合约 / 兑换；CEX 与 Web3 两套钥匙体系严格隔离。 |
| 🧩 **Skill Hub** | 预装 14 个官方技能 · 意图驱动 · install/run/remove 全程防路径穿越。 |
| 🔌 **插件** | 丢进 `plugins/` 即扩能力（如 `scout-signals`）——一个 `main.py` + 清单挂钩进回路，不动核心。 |
| 🤖 **多机器人工作区** | 任意创建人格化 bot（各自技能、各自记忆域），各聊各的会话。 |
| 👥 **群聊 · 派单给 bot** | 建房间 · `@提及` 多个 bot 丢任务 · 成员轮番作答 · 共享房间日志。 |
| ⏰ **定时自动化（CRON）** | 可视化 cron 面板：用大白话写"每天 09:00 扫一次市场"，可运行 / 暂停 / 手动触发。 |
| 💸 **x402 / B402 支付** | 机器对机器支付：离线 Permit2 EIP-712 签名，官方 Facilitator 验证并链上结算。 |
| 📣 **广场发文** | 文字 / 长文 / 图片 / 视频发到 Binance Square，保留所有发布记录的本地台账。 |
| 🖥 **桌面 + Web，一套代码** | 无边框 Windows 便携版（Electron + 内置 PyInstaller 后端，无需 Python/Node）*和*浏览器同一套舱。 |
| 🌓 **双语 & 可换肤** | 中 / 英 + 深金 ↔ 浅色，顶栏各一键，重启记住。 |
| 🔄 **增量自动更新** | MANIFEST + 差分包，只下载变化文件（~几 MB 而非整包），后台预下载、改完秒应用重启。 |
| 🔒 **本地优先，数据分离** | 会话 / 记忆 / 配置外置到 `%APPDATA%\BAZZ.AGENT`——更新只换程序，**永不触碰你的数据**。 |

---

## ⚡ 快速开始

### 方式一 · Windows 一键下载（推荐）

| 产物 | 怎么跑 |
|------|--------|
| 🎒 [portable.zip](https://github.com/xinyuzjj/bazz.agent/releases/latest/download/BAZZ.AGENT-v1.2.18-win32-x64-portable.zip) | 解压到任意目录 → 双击 `BAZZ.AGENT.exe` |
| 📦 [setup.exe](https://github.com/xinyuzjj/bazz.agent/releases/latest/download/BAZZ.AGENT-v1.2.18-setup.exe) | 安装到 `%LOCALAPPDATA%`，带开始菜单 / 桌面快捷方式 / 卸载器 |

- **无需 Python、无需 Node、无需安装**——Electron + PyInstaller 熔成一个应用。
- 首次启动 ~3–5 秒（后端预热），随后舱门打开。
- 数据外置在 `%APPDATA%\BAZZ.AGENT\workspace`——便携版整个文件夹随便搬，数据跟着走。
- 无边框窗口：按住顶栏拖动，右上 `─ / □ / ✕` 控制。

> 面向 Windows 10/11 x64。macOS / Linux 请从源码运行。

### 方式二 · 从源码（任意系统）

```bash
# 1) 后端 —— Python 3.11+
python -m venv .venv && .venv\Scripts\activate    # Windows
# source .venv/bin/activate                        # macOS / Linux
pip install -r requirements.txt

# 2) 前端 —— React 构建
cd frontend && npm install && npm run build && cd ..

# 3) 启动（后端同源托管 React 构建）
.venv\Scripts\python -m uvicorn desktop_app:app --host 127.0.0.1 --port 8080
# → http://127.0.0.1:8080
```

> 公开市场雷达（币安现货 ticker & 24h 统计）无需任何 Key。设置页可填任意 OpenAI 兼容 `base_url` + key
>（DeepSeek / OpenAI / Moonshot / Ollama / 自定义），配规则引擎兜底，**离线也能跑通主流程**。

### 桌面开发模式（Windows）

```bash
start-desktop.bat     # Electron 壳 + .venv 后端，一次双击
start-dev.bat         # FastAPI + Vite 热更新，盯 UI 用
```

---

## 🧩 四块原生能力 —— 真实协议，不是摆设

| | 细节 |
|---|---|
| **① MCP Server** · `agent.binance.com` | Streamable HTTP（JSON-RPC 2.0）· 协议 `2025-06-18` · OAuth 2.0 **RFC 9728** + PKCE(S256) → Bearer · 运行时 `tools/list` 发现，零硬编码工具名 · 公开行情免鉴权，账户/交易走**最小权限 scope（无提现）**。见 `src/mcp_client.py`。 |
| **② Agentic Wallet** · `baw` CLI | 无密钥 MPC，多链 BSC / ETH / Base / SOL · 官方限额：兑换 $50k/日 · DeFi $100k/日 · x402 $20/日 · Agent 只经**白名单命令垫片**调 `baw`，私钥从不见。 |
| **③ x402 / B402** · 机器付机 | B402 Facilitator `/papi/v2/b402/{supported,verify,settle}`（BSC）· 卖家回 402 → 买家**离线 Permit2 EIP-712** 签名（无 gas）→ Facilitator 验证 → 链上结算（代付 gas）· `/supported` 实时拉取，无凭据时优雅降级演示。 |
| **④ Skill Hub** · 官方技能 | 预装 14 个官方技能（CEX 5 + Web3 9），锁文件 `skills-lock.json` · `install/run/remove` 带防路径穿越 · 意图驱动 · 插件扩展同一运行时：`plugins/<id>/{main.py, plugin.json}`。 |

**与真实 CEX 通道 —— binance-cli**：两套钥匙体系**严格隔离**，本应用从不混用。

| 系统 | 钥匙 | 用途 |
|------|------|------|
| **币安 CEX**（交易所账户） | API Key + Secret（HMAC, 64 位, System Generated） | 现货/合约、余额、历史成交，经 `binance-cli` profile |
| **Web3 Agentic Wallet** | 无密钥 MPC（BX-/Ed25519 经 `baw`） | 链上交换、x402、DeFi |

---

## 🧭 技术栈

| 层 | 技术 |
|----|------|
| Agent 核心 | FastAPI + SSE/NDJSON 流式 · 意图 → 工具 → 审批 → 执行 |
| LLM | 多供应商 OpenAI 兼容层 + 确定性规则引擎兜底 |
| 桌面壳 | Electron 31（无边框）+ PyInstaller 后端打包 |
| 记忆 | SQLite（`state.db`）——会话 / 消息 / 长期偏好 |
| 前端 | React 18 + TypeScript + Vite + Tailwind（深金 ↔ 浅色 · 中/英） |
| 图表 | 纯 CSS + SVG，零图表依赖 |
| 实时 | Server-Sent Events + 轮询 |

---

## 🏗 架构

```
┌──────────────────────────────────────────────────────────────┐
│              BAZZ.AGENT (desktop & web, same core)             │
│                                                                │
│   Electron (desktop)  ─┐                                       │
│   Browser    (web)  ───┴─► React cockpit ─ HTTP/SSE ─► FastAPI │
│                                                       │        │
│   FastAPI  desktop_app.py                              │        │
│      │            │            │        │        │    │        │
│      ▼            ▼            ▼        ▼        ▼    ▼        │
│  MCP Client   x402 Client   Wallet  binance-cli  Cron  LLM     │
│  (OAuth PKCE) (B402)        (baw)   (CEX HMAC)      +rules    │
│      │                                                         │
│      └──────────────► Binance Agent Native                      │
│                        MCP / Wallet / x402 / Skill Hub        │
│                                                                │
│   SQLite (state.db): sessions · messages · memory · jobs       │
└──────────────────────────────────────────────────────────────┘
```

桌面版内嵌 PyInstaller 冻结后端（`ScoutBackend.exe`），托管同一份 React 构建——**一套代码，两种形态**。

---

## 🗽 安全模型

- 每笔真实交易 / 转账 / 签名支付都带 `needs_approval`，后端只有收到显式 `confirm: true` 才继续。
- 钥匙从不以明文落盘；CEX 钥匙按会话在 UI 输入，仅在你连接并授权后镜像进本地 `binance-cli` profile。
- Agent 从不动原始私钥——Agentic Wallet 无密钥。
- `run_command` 白名单仅限 `baw` / `binance-cli` / `node`——任意 shell 直接拒绝。
- 技能对外层做防路径穿越；广场发文带本地台账，UI 侧钥匙脱敏。

---

## 📦 目录结构

```
bazz.agent/
├── desktop_app.py            FastAPI 后端（托管 frontend/dist + /api/*）
├── launcher.py               PyInstaller 桌面后端入口
├── electron/                 桌面壳：main.cjs + preload.cjs（无边框）
├── src/
│   ├── agent_core.py         意图 → 工具编排 → 审批 → 执行
│   ├── updater.py            增量差分自动更新（MANIFEST + delta + 后台预下载）
│   ├── mcp_client.py         Binance Agentic MCP（OAuth 2.0 + 运行时发现）
│   ├── x402_client.py        x402 / B402 支付（Permit2 EIP-712）
│   ├── wallet_client.py      Agentic Wallet（baw CLI 封装）
│   ├── cex_wallet.py         Binance CEX HMAC 客户端（读 + 签）
│   ├── binance_cli.py        真实交易通道：binance-cli profile 同步
│   ├── skills_client.py      Skill Hub（install / run / remove）
│   ├── scanner.py            全市场 Binance 行情雷达
│   ├── llm.py                多供应商 LLM + 规则引擎兜底
│   ├── scheduler.py          cron 守护
│   └── state.py              SQLite 持久化
├── frontend/                 React 18 + TS + Vite + Tailwind（深/浅 · 中/英）
├── plugins/scout-signals/    示例插件
├── .agents/bots/             bot 人格 · .agents/skills/ 14 个官方技能
├── wallet_bridge/            链上钱包的 Node 桥
└── docs/                     GitHub Pages 落地页
```

---

## 🏷 Topics

`binance` · `agent-os` · `ai-agent` · `hackathon` · `trading` · `crypto` · `web3` · `mcp` · `x402` · `oauth` · `electron` · `fastapi` · `react`

---

## 🏆 Hackathon

- **活动**：Binance Agent OS Mini Hackathon —— Track A：*用 Agent OS 构建一个 AI Agent*
- **截止**：2026-09-08 23:59 UTC
- **线上落地页**：https://xinyuzjj.github.io/bazz.agent/
- **声明**：本仓库为 Hackathon 演示，非投资建议。加密交易有真实风险；每条执行路径都要求显式人工确认。

---

## 📜 License

MIT