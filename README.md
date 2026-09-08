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
║            —— Your Binance Co-Pilot. It asks before it fires. —— ║
╚══════════════════════════════════════════════════════════════╝
```

**An AI trading copilot that fuses all four native Binance Agent OS capabilities**
**(MCP / Agentic Wallet / x402 Payments / Skill Hub) into one real loop.**

> `Scan → Analyze → Propose → Human-Approval → Execute`
>
> **Every other AI chatbox just *talks*. BAZZ.AGENT *acts* — but only after you nod.** Portable desktop · Local web · Same codebase.

**🌐 English | [中文文档](./README.zh-CN.md)**

[![Release](https://img.shields.io/badge/Release-v1.2.18-f0b90b?logo=github&logoColor=white)](https://github.com/xinyuzjj/bazz.agent/releases/latest)
[![Portable](https://img.shields.io/badge/Download-portable.zip-2ea44f?logo=windows&logoColor=white)](https://github.com/xinyuzjj/bazz.agent/releases/latest/download/BAZZ.AGENT-v1.2.18-win32-x64-portable.zip)
[![Setup](https://img.shields.io/badge/Download-setup.exe-4a9eff?logo=windows&logoColor=white)](https://github.com/xinyuzjj/bazz.agent/releases/latest/download/BAZZ.AGENT-v1.2.18-setup.exe)
[![Pages](https://img.shields.io/badge/Live-Pages-181717?logo=githubpages&logoColor=white)](https://xinyuzjj.github.io/bazz.agent/)
[![中文](https://img.shields.io/badge/README-中文-f0b90b)](./README.zh-CN.md)

[![Track A](https://img.shields.io/badge/Binance%20Agent%20OS-Track%20A-f0b90b)](#-hackathon)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)](#-tech-stack)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](#-tech-stack)
[![React](https://img.shields.io/badge/React%2018-61dafb?logo=react&logoColor=222)](#-tech-stack)
[![Electron](https://img.shields.io/badge/Electron-31-47848f?logo=electron&logoColor=white)](#-architecture)
[![Vite](https://img.shields.io/badge/Vite-5-646cff?logo=vite&logoColor=white)](#-tech-stack)
[![MIT](https://img.shields.io/badge/license-MIT-success)](#-license)

![Main cockpit](./docs/screenshot.png)

> 🎬 **88s real-footage walkthrough** — history → market radar → one-tap "Spot Buy" dropped into an Agent chat and analyzed → memory → wallet → settings.
> [▶ Watch on Pages](https://xinyuzjj.github.io/bazz.agent/#demo) · [⬇ MP4 (3.2 MB)](./docs/video/BAZZ-demo-v1.mp4)

</div>

---

## 📑 Table of Contents

1. [It is not another chatbox](#-it-is-not-another-chatbox)
2. [The core loop](#-the-core-loop)
3. [What it does](#-what-it-does)
4. [Quick start](#-quick-start)
5. [The four native capabilities](#-the-four-native-capabilities--real-protocols-not-widgets)
6. [Tech stack](#-tech-stack)
7. [Architecture](#-architecture)
8. [Security model](#-security-model)
9. [Project layout](#-project-layout)
10. [Hackathon](#-hackathon)

---

## 🎯 It is not another chatbox

AI assistants all **talk**. BAZZ.AGENT **acts** — but you must approve first.

This is **not** a chat skin over the Binance API. It is an **agent operating system** running inside your Binance account:

- It reads the **real market** exposed by Agent OS;
- It drives the **four native capabilities** (MCP / Wallet / x402 / Skill Hub) directly;
- Every sensitive action (order / transfer / signed payment) drops a `Confirm` card into the chat — **executed only after you click** it, on your real keys / keyless MPC wallet.

The cockpit is a Hermes-style three-pane **trading desk**:

| Pane | Holds |
|------|-------|
| **Left** | Sessions / personas / tools / files |
| **Center** | Agent chat · live tool cards · SSE streaming |
| **Right** | Live signal feed · positions & account panel |

The top bar toggles **dark/light theme** and **Chinese/English** in one click — remembered across restarts.

---

## 🔁 The core loop

```
        ┌─────────────┐
   ┌───▶│   📡 SCAN    │  Full-market radar · Monster Radar · 24h watch
   │    └──────┬──────┘
   │           ▼
   │    ┌─────────────┐
   │    │   🧠 ANALYZE │  Multi-model LLM · multi-tool · rules engine fallback
   │    └──────┬──────┘
   │           ▼
   │    ┌─────────────┐
   │    │   💡 PROPOSE  │  Signal card with leverage / position / TP-SL
   │    └──────┬──────┘
   │           ▼
   │    ┌─────────────┐
   │    │   ✅ APPROVE  │  ←── key: you press Confirm in the chat
   │    └──────┬──────┘
   │           ▼
   │    ┌─────────────┐
   │    │   ⚡ EXECUTE │  CEX / Agentic Wallet / x402
   │    └─────────────┘
   └──────────────────┘ (after execution, back to scanning)

   Sensitive actions require approval by default; whitelist trusted ones if you want.
```

---

## ✨ What it does

| | What you get |
|---|---|
| 📡 **Live market radar** | Full-market anomaly scan · Monster Radar burst windows · 24h auto-watch · real-time BTC/ETH/SOL/BNB prices — **zero API key**. |
| 🗣 **Conversational agent cockpit** | SSE/NDJSON streaming · live tool cards · file/voice input · multi-provider LLM, plus a **fully offline deterministic rules engine** fallback. |
| ✅ **Human-in-the-loop (default)** | Every trade / transfer / signed payment has a confirm card — **sensitive ops are never silent**. |
| 🧠 **Long-term memory** | Learns cross-session preferences & risk anchors from your chats; indexed, searchable, viewable in the Memory panel. |
| 👛 **Agentic Wallet (keyless MPC)** | Swap BSC / Ethereum / Base / Solana via `baw`, respecting official daily limits; **private keys never touch the app**. |
| 💹 **Real CEX trading** | Spot / futures / convert on your real account via whitelisted `binance-cli` profile; CEX and Web3 key sets strictly isolated. |
| 🧩 **Skill Hub** | 14 official skills preloaded · intent-driven · install/run/remove with path-traversal protection. |
| 🔌 **Plugins** | Drop into `plugins/` to extend (e.g. `scout-signals`) — one `main.py` + manifest hooks into the loop without touching core. |
| 🤖 **Multi-bot workspaces** | Spin up persona bots (own skills, own memory domains), each with its own conversations. |
| 👥 **Group chat · assign to bots** | Make a room, `@mention` several bots, drop a task, they answer in turns and share a room log. |
| ⏰ **Scheduled automation (CRON)** | Visual cron panel: write "scan the market every day at 09:00" — run / pause / trigger manually. |
| 💸 **x402 / B402 payments** | Machine-to-machine payments: offline Permit2 EIP-712 signing, verified by the official Facilitator and settled on-chain. |
| 📣 **Square publishing** | Text / long-form / image / video to Binance Square, with a local ledger of everything you've posted. |
| 🖥 **Desktop + Web, one codebase** | Frameless Windows portable (Electron + embedded PyInstaller backend, no Python/Node needed) *and* the same cockpit in a browser. |
| 🌓 **Bilingual & skinnable** | Chinese / English + deep-gold ↔ light, each one-key from the top bar, remembered on restart. |
| 🔄 **Incremental auto-update** | MANIFEST + delta pack — downloads only the changed files (~a few MB vs the whole bundle), prefetches in the background, applies in about a second. |
| 🔒 **Local-first, data separated** | Sessions / memory / config live outside the app in `%APPDATA%\BAZZ.AGENT` — updates replace only the program, **never touch your data**. |

---

## ⚡ Quick start

### Option 1 · Windows, one-click download (recommended)

| Artifact | How to run |
|------|--------|
| 🎒 [portable.zip](https://github.com/xinyuzjj/bazz.agent/releases/latest/download/BAZZ.AGENT-v1.2.18-win32-x64-portable.zip) | Unzip anywhere → double-click `BAZZ.AGENT.exe` |
| 📦 [setup.exe](https://github.com/xinyuzjj/bazz.agent/releases/latest/download/BAZZ.AGENT-v1.2.18-setup.exe) | Installs to `%LOCALAPPDATA%` with Start Menu / desktop shortcuts / uninstaller |

- **No Python, no Node, no install** — Electron + PyInstaller fused into one app.
- First launch warms the backend for ~3–5 s, then the hatch opens.
- Data lives outside the app at `%APPDATA%\BAZZ.AGENT\workspace` — move the whole portable folder and your data comes with it.
- Frameless window: drag by the top bar; `─ / □ / ✕` on the top-right.

> For Windows 10/11 x64. macOS / Linux: run from source.

### Option 2 · From source (any OS)

```bash
# 1) Backend — Python 3.11+
python -m venv .venv && .venv\Scripts\activate    # Windows
# source .venv/bin/activate                        # macOS / Linux
pip install -r requirements.txt

# 2) Frontend — React build
cd frontend && npm install && npm run build && cd ..

# 3) Run (backend serves the React build)
.venv\Scripts\python -m uvicorn desktop_app:app --host 127.0.0.1 --port 8080
# → http://127.0.0.1:8080
```

> The public market radar (Binance spot tickers & 24h stats) needs **no key**. In Settings, fill any OpenAI-compatible `base_url` + key
> (DeepSeek / OpenAI / Moonshot / Ollama / custom). A rules-engine fallback means **the main flow works offline too**.

### Desktop dev mode (Windows)

```bash
start-desktop.bat     # Electron shell + .venv backend, one double-click
start-dev.bat         # FastAPI + Vite hot-reload for UI work
```

---

## 🧩 The four native capabilities — real protocols, not widgets

| | Detail |
|---|---|
| **① MCP Server** · `agent.binance.com` | Streamable HTTP (JSON-RPC 2.0) · protocol `2025-06-18` · OAuth 2.0 **RFC 9728** + PKCE(S256) → Bearer · runtime `tools/list` discovery, zero hardcoded tool names · public market data unauthenticated; account/trading uses a **least-privilege scope (no withdrawals)**. See `src/mcp_client.py`. |
| **② Agentic Wallet** · `baw` CLI | Keyless MPC, multi-chain BSC / ETH / Base / SOL · official limits: swap $50k/day · DeFi $100k/day · x402 $20/day · the agent only calls `baw` through a **whitelisted command shim** — the private key is never seen. |
| **③ x402 / B402** · machine pays machine | B402 Facilitator `/papi/v2/b402/{supported,verify,settle}` (BSC) · seller replies 402 → buyer signs **offline Permit2 EIP-712** (no gas) → Facilitator verifies → on-chain settle (Facilitator pays the gas) · `/supported` fetched live; degrades gracefully to demo without credentials. |
| **④ Skill Hub** · official skills | 14 preloaded official skills (CEX 5 + Web3 9), locked via `skills-lock.json` · `install/run/remove` with path-traversal protection · intent-driven · plugins share the same runtime: `plugins/<id>/{main.py, plugin.json}`. |

**Real CEX channel — binance-cli**: two key sets are **strictly isolated**; this app never mixes them.

| System | Key | Use |
|------|------|------|
| **Binance CEX** (exchange account) | API Key + Secret (HMAC, 64-char, System Generated) | Spot / futures, balance, fills, via `binance-cli` profile |
| **Web3 Agentic Wallet** | keyless MPC (BX-/Ed25519 via `baw`) | on-chain swap, x402, DeFi |

---

## 🧭 Tech stack

| Layer | Tech |
|----|------|
| Agent core | FastAPI + SSE/NDJSON streaming · intent → tool → approval → execute |
| LLM | Multi-provider OpenAI-compatible layer + deterministic rules-engine fallback |
| Desktop shell | Electron 31 (frameless) + PyInstaller backend packaging |
| Memory | SQLite (`state.db`) — sessions / messages / long-term preferences |
| Frontend | React 18 + TypeScript + Vite + Tailwind (deep-gold ↔ light · CN/EN) |
| Charts | Pure CSS + SVG, zero chart-dependency |
| Realtime | Server-Sent Events + polling |

---

## 🏗 Architecture

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

The desktop build embeds a PyInstaller-frozen backend (`ScoutBackend.exe`) that serves the same React build — **one codebase, two shapes**.

---

## 🗽 Security model

- Every real trade / transfer / signed payment carries `needs_approval`; the backend proceeds only on an explicit `confirm: true`.
- Keys are never written to disk in plaintext; CEX keys are typed into the UI per session and mirrored into the local `binance-cli` profile only after you connect and authorize.
- The agent never touches raw private keys — the Agentic Wallet is keyless.
- `run_command` whitelist is limited to `baw` / `binance-cli` / `node` — arbitrary shell is rejected outright.
- Skills are protected against path traversal externally; Square posts keep a local ledger; keys are masked in the UI.

---

## 📦 Project layout

```
bazz.agent/
├── desktop_app.py            FastAPI backend (serves frontend/dist + /api/*)
├── launcher.py               PyInstaller desktop backend entry
├── electron/                 Desktop shell: main.cjs + preload.cjs (frameless)
├── src/
│   ├── agent_core.py         intent → tool orchestration → approval → execute
│   ├── updater.py            incremental auto-update (MANIFEST + delta + background prefetch)
│   ├── mcp_client.py         Binance Agentic MCP (OAuth 2.0 + runtime discovery)
│   ├── x402_client.py        x402 / B402 payments (Permit2 EIP-712)
│   ├── wallet_client.py      Agentic Wallet (baw CLI wrapper)
│   ├── cex_wallet.py         Binance CEX HMAC client (read + sign)
│   ├── binance_cli.py        real trading channel: binance-cli profile sync
│   ├── skills_client.py      Skill Hub (install / run / remove)
│   ├── scanner.py            full-market Binance market radar
│   ├── llm.py                multi-provider LLM + rules-engine fallback
│   ├── scheduler.py          cron daemon
│   └── state.py              SQLite persistence
├── frontend/                 React 18 + TS + Vite + Tailwind (dark/light · CN/EN)
├── plugins/scout-signals/    example plugin
├── .agents/bots/             bot personas · .agents/skills/ 14 official skills
├── wallet_bridge/            Node bridge for on-chain wallets
└── docs/                     GitHub Pages landing page
```

---

## 🏷 Topics

`binance` · `agent-os` · `ai-agent` · `hackathon` · `trading` · `crypto` · `web3` · `mcp` · `x402` · `oauth` · `electron` · `fastapi` · `react`

---

## 🏆 Hackathon

- **Event**: Binance Agent OS Mini Hackathon — Track A: *Build an AI Agent on Agent OS*
- **Deadline**: 2026-09-08 23:59 UTC
- **Live landing page**: https://xinyuzjj.github.io/bazz.agent/
- **Disclaimer**: This repo is a Hackathon demo, not investment advice. Crypto trading carries real risk; every execution path requires explicit human confirmation.

---

## 📜 License

MIT