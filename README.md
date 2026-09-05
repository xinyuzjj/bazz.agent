# BAZZ.AGENT — Agent OS Alpha Scout

> **A Binance-native AI trading copilot** built on top of the four official
> Agent OS capabilities — MCP Server, Agentic Wallet, x402/B402 machine
> payment and Skill Hub.

[![Track A](https://img.shields.io/badge/Binance%20Agent%20OS-Track%20A-f0b90b)](#hackathon)
[![Live Demo](https://img.shields.io/badge/Live%20Demo-Pages-f0b90b?logo=githubpages&logoColor=white)](https://xinyuzjj.github.io/bazz.agent/)
[![GitHub Repo](https://img.shields.io/badge/GitHub-bazz.agent-181717?logo=github&logoColor=white)](https://github.com/xinyuzjj/bazz.agent)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)](#stack)
[![React](https://img.shields.io/badge/React-18-61dafb?logo=react&logoColor=222)](#stack)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](#stack)
[![Vite](https://img.shields.io/badge/Vite-5-646cff?logo=vite&logoColor=white)](#stack)
[![License](https://img.shields.io/badge/license-MIT-success)](#license)

Agent OS Alpha Scout is a web-first AI trading copilot that wraps the four
official Binance Agent OS building blocks into one focused cockpit:

| Capability | What it does in this app |
|------------|--------------------------|
| **MCP Server** (`agent.binance.com`) | OAuth-2.0 PKCE agent login, runtime tool discovery, accounts/trades/balances — over Streamable HTTP |
| **Agentic Wallet** (`baw` CLI) | Keyless on-chain wallet; quote / swap / market & limit orders with daily $50k cap |
| **x402 / B402** | Permit2 EIP-712 machine-to-machine payments, BNB Smart Chain |
| **Skill Hub** | 14 official Binance skills (`npx skills add`) — discover / install / run / remove |

It is **not** a chat skin on top of Binance — it is a full agent loop:
**scan → analyze → propose → human approval → execute**, with a hard
`needs_approval` gate before any real trade, transfer, or signed payment.

> 🇨🇳 中文简介：项目是 Binance Agent OS Mini Hackathon（Track A）参赛作品，
> 把四块官方能力（MCP / Agentic Wallet / x402 / Skill Hub）整合成一个 web
> 端的交易副驾。后端 FastAPI + SSE 流式对话，前端 React 18 + Vite +
> Tailwind，Hermes 风格暗金主题；公开行情无需 key 即跑，真实交易 / 链上
> 操作必须人工确认。

**🖥️ Live Pages 展示页**：https://xinyuzjj.github.io/bazz.agent/（GitHub Pages 落地页，含产品截图、四能力卡与架构说明）

![Main UI](./docs/screenshot.png)

---

## ⚡ Quick Start

```bash
# Backend (Python 3.11+, FastAPI + uvicorn)
python -m venv .venv && .venv\Scripts\activate     # Windows
# source .venv/bin/activate                          # macOS/Linux
pip install -r requirements.txt

# Frontend (React + Vite)
cd frontend && npm install && npm run build && cd ..

# Launch the web app (backend serves the React build at the same origin)
.venv\Scripts\python -m uvicorn desktop_app:app --host 127.0.0.1 --port 8080
# → http://127.0.0.1:8080
```

> **No credentials required** for the public-market scanners (Binance
> spot ticker & 24h stats are key-free).
> Settings page accepts any OpenAI-compatible `base_url` + API key
> (DeepSeek / OpenAI / Moonshot / Ollama / custom).

### Hot keys

| Shortcut | Action |
|----------|--------|
| `Enter`  | Send message |
| `Shift+Enter` | New line |
| `Ctrl/Cmd + K` | Open command palette |
| `↑ / ↓` | Cycle past inputs |

---

## 🧭 Stack

| Layer | Tech |
|-------|------|
| Agent shell | **FastAPI** + SSE/NDJSON streaming |
| LLM | Multi-provider OpenAI-compatible layer with rule-engine fallback |
| Memory | SQLite (`.scout.db`) — sessions / messages / long-term preferences |
| Frontend | **React 18 + TypeScript + Vite + Tailwind** (Hermes dark/gold theme) |
| Charts & widgets | Pure CSS + SVG (no charting libs) |
| Realtime | Server-Sent Events, polling |

---

## 🏗 Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│              Agent OS Alpha Scout (Web Cockpit)                  │
│                                                                  │
│   React + Vite + Tailwind (UI) ─── HTTP / SSE ───► FastAPI        │
│                                                                  │
│   FastAPI (desktop_app.py)                                       │
│        │            │            │        │         │             │
│        ▼            ▼            ▼        ▼         ▼             │
│   MCP Client   x402 Client   Wallet   Scheduler  LLM (compat)    │
│   (OAuth)      (B402 Fac.)   (baw)    (cron)     + rule engine   │
│        │                                                          │
│        └──────────────► Binance Agent Native                       │
│                          (MCP / Wallet / x402 / Skill Hub)        │
│                                                                  │
│   SQLite (.scout.db): sessions / messages / settings / memory    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 🧩 The Four Native Capabilities (real protocol, not stubs)

### 1. MCP Server — `agent.binance.com`

- **Transport**: MCP over Streamable HTTP (JSON-RPC 2.0, `application/json` or `text/event-stream`)
- **Protocol**: `2025-06-18`
- **Auth**: OAuth 2.0 via **RFC 9728 resource-metadata challenge** → PKCE S256 browser flow → `Bearer` token
- **Discovery**: tools discovered at runtime via `tools/list` — no hard-coded tool names
- **Scopes (least-privilege)**: market reads (no auth), balances / positions / bills, spot / margin / convert / USDⓈ-M / COIN-M trades, sub-account transfers — **no withdrawal scope**
- See `src/mcp_client.py`, APIs at `/api/mcp/*`

### 2. Agentic Wallet — `baw` CLI (keyless)

```bash
npm i -g @binance/agentic-wallet
baw auth signin           # QR code → Binance app → approve
baw wallet status
baw market-order swap     # quote / swap / list
baw limit-order buy --json
```

- Multi-chain (BSC / Ethereum / Base / Solana)
- $50k / day swap · $100k / day DeFi · $20 / day x402 caps (Binance-set)
- Agent only ever invokes `baw` via a **whitelisted `run_command` shim** — no private keys are ever seen by the agent

### 3. x402 / B402 — machine payments

- B402 Facilitator: `/papi/v2/b402/{supported,verify,settle}` (BNB Smart Chain, `eip155:56`)
- Flow: seller returns `402` → buyer signs **offline Permit2 EIP-712** (no gas) → facilitator verifies → on-chain settlement (facilitator pays gas)
- `/supported` is fetched live; demo mode is used when prod credentials are absent
- See `src/x402_client.py`

### 4. Skill Hub — 14 official Binance skills

```bash
npx skills add https://github.com/binance/binance-skills-hub/tree/main/skills/<group>/<skill>
```

- All 14 official skills shipped pre-installed under `.agents/skills/` (lockfile: `skills-lock.json`)
- `install / run / remove` with **path-traversal protection**
- Intent-driven activation (you say what you want; the skill decides which API to call)
- Plugins extend the same runtime via `plugins/<id>/{main.py, plugin.json}`

---

## 🔌 Backend API surface

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/status` | Global status (MCP / LLM / market / cron) |
| `POST` | `/api/chat/stream` | NDJSON streaming chat: `meta / tool / text / data / done / saved` |
| `GET` | `/api/market` | Real-time Binance signals (no key needed) |
| `GET / POST` | `/api/wallet` | Agentic Wallet status & whitelisted commands |
| `GET` | `/api/x402/supported` | Live B402 facilitator config |
| `POST` | `/api/x402/demo` | End-to-end x402 demo |
| `GET` | `/api/skills` | Skill catalog (14 official) |
| `POST` | `/api/skills/{install,run,remove}` | Skill lifecycle |
| `GET` | `/api/mcp` | MCP servers + OAuth discovery |
| `POST` | `/api/mcp/{name}/oauth/{start,poll}` | PKCE OAuth flow |
| `POST` | `/api/mcp/{name}/token` | Manual token ingest |
| `GET` | `/api/mcp/{name}/tools` | Runtime tool discovery |
| `GET/POST/DELETE` | `/api/cron /api/memory /api/settings` | Schedules / memory / settings |

---

## 🔐 Security model

- All real-trade / on-chain / sensitive tools go through `needs_approval`; the UI shows a confirm card and the backend only proceeds after explicit `confirm: true`.
- No API keys are written to disk in clear text (`.scout.db` stores settings, never secrets — keys live in the OS keychain or env).
- The agent never touches raw private keys — Agentic Wallet is keyless (MPC).
- `run_command` is whitelisted to `baw`, `binance-cli`, `node` — arbitrary shell is rejected.
- Skills are sandboxed against path traversal.

---

## 📦 Project layout

```
bazz.agent/
├── desktop_app.py            FastAPI backend (serves frontend/dist + /api/*)
├── src/
│   ├── agent_core.py         intent → tool orchestration → approval → exec
│   ├── mcp_client.py         Binance Agentic MCP (OAuth 2.0 + tool discovery)
│   ├── x402_client.py        x402 / B402 payments (Permit2 EIP-712)
│   ├── wallet_client.py      Agentic Wallet (baw CLI wrapper)
│   ├── skills_client.py      Skill Hub (install / run / remove)
│   ├── plugin_host.py        Built-in plugins loader (scout-signals …)
│   ├── bot_host.py           File-based bot personas (.agents/bots/<slug>/)
│   ├── scanner.py            Binance public-market scanner (full universe)
│   ├── llm.py                Multi-provider LLM layer + rule-engine fallback
│   ├── scheduler.py          cron daemon (daily scan + report)
│   └── state.py              SQLite persistence
├── frontend/                 React 18 + TS + Vite + Tailwind
├── plugins/scout-signals/    example plugin
├── .agents/bots/             five pre-configured bot personas
├── .agents/skills/           14 official Binance skills (cached)
├── wallet_bridge/            tiny Node bridge for @binance-web3/wallet
└── docs/                     GitHub Pages landing (index.html + screenshot)
```

---

## 🏷 Topics

`binance` · `agent-os` · `ai-agent` · `hackathon` · `trading` · `crypto`
· `web3` · `mcp` · `x402` · `oauth` · `fastapi` · `react`

---

## 🎯 Hackathon

- **Event**: Binance Agent OS Mini Hackathon — Track A: *Build an AI Agent with Agent OS*
- **Track deadline**: 2026-09-08 23:59 UTC
- **Live Pages**: https://xinyuzjj.github.io/bazz.agent/
- **Disclaimer**: This project is a hackathon demo. Nothing here is investment advice — crypto trading carries real risk. Every execution path requires explicit human approval.

---

## License

MIT