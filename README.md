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
║        —— Your Binance Co-Pilot. It asks before it fires. —— ║
╚══════════════════════════════════════════════════════════════╝
```

# ⚡ BAZZ.AGENT — An Agent OS That *Acts*

### Fuse the four native Binance Agent OS capabilities into one real trading loop

**MCP · Agentic Wallet · x402 Payments · Skill Hub** — not widgets. Real protocols, real execution.

> `📡 SCAN → 🧠 ANALYZE → 💡 PROPOSE → ✅ APPROVE → ⚡ EXECUTE`
>
> **Every other AI chatbox just *talks*. BAZZ.AGENT *acts* — but only after you nod.**

**🌐 English | [中文文档](./README.zh-CN.md)**

</div>

<div align="center">

[![Release](https://img.shields.io/github/v/release/xinyuzjj/bazz.agent?logo=github&logoColor=white&label=v1.5.26&color=f0b90b)](https://github.com/xinyuzjj/bazz.agent/releases/latest)
[![Download](https://img.shields.io/badge/⬇_Download-setup.exe-2ea44f?logo=windows&logoColor=white)](https://github.com/xinyuzjj/bazz.agent/releases/latest)
[![Delta Update](https://img.shields.io/badge/🔄_Update-delta_~MB-4a9eff)](#-incremental-auto-update)
[![Pages](https://img.shields.io/badge/🎬_Live_Demo-Pages-181717?logo=githubpages&logoColor=white)](https://xinyuzjj.github.io/bazz.agent/)
[![中文](https://img.shields.io/badge/📖_README-中文-f0b90b)](./README.zh-CN.md)

[![Track A](https://img.shields.io/badge/Binance_Agent_OS-Track_A-f0b90b)](#-hackathon)
[![Python](https://img.shields.io/badge/Python-3.11-3776ab?logo=python&logoColor=white)](#-tech-stack)
[![FastAPI](https://img.shields.io/badge/FastAPI-SSE_Streaming-009688?logo=fastapi&logoColor=white)](#-tech-stack)
[![React](https://img.shields.io/badge/React_18-TypeScript-61dafb?logo=react&logoColor=222)](#-tech-stack)
[![Electron](https://img.shields.io/badge/Electron-Frameless-47848f?logo=electron&logoColor=white)](#-architecture)
[![License](https://img.shields.io/badge/License-MIT-success)](#-license)

</div>

<div align="center">

![Main cockpit](./docs/screenshot.png)

**🎬 [88s real-footage walkthrough](https://xinyuzjj.github.io/bazz.agent/#demo)** — history → market radar → one-tap trade analyzed in chat → memory → wallet → settings · [⬇ MP4](./docs/video/BAZZ-demo-v1.mp4)

</div>

---

## 🆕 Fresh out of the lab — v1.5.19 → v1.5.26

| | Highlight | What it means |
|---|---|---|
| 🛡 | **Auto proxy failover** | Skill network call dies? The app live-tests every proxy node (api + www + image CDN, the *real* publish chain), switches to a working one and **retries by itself** — 6 attempts across 2 nodes, zero babysitting. |
| 📣 | **Rich Square publishing** | One sentence → auto market data + Pillow dark cover + 24h chart + SMC-structured article (BOS/CHoCH · OTE 0.618–0.705 · OB · FVG → position plan) → **short-post with images** to Binance Square. |
| 🖥 | **Close-to-Tray** | ✕ asks once: *minimize to tray or quit?* Tray mode keeps **cron monitors & update checks running with the window closed**. Left-click tray to return, right-click to exit. |
| 🖼 | **In-app image viewer** | Click any png/jpg/webp in the file manager → instant preview; viewer now minimizes to a corner pill so you can keep it open while trading. |
| 🔄 | **Delta auto-update** | MANIFEST diff → only changed files (~MB, not the whole bundle), SHA256-verified, background-prefetched, applied in ~1s, **with install-version guard** (never downgrades). |
| 🌐 | **Built-in proxy pool** | Import nodes/subscriptions, latency-test kernels (vless/vmess/hysteria2 via bundled mihomo), one-click enable — **all app traffic** routes through it, loopback excluded. |

---

## 🎯 It is not another chatbox

AI assistants all **talk**. BAZZ.AGENT **acts** — but you must approve first.

This is **not** a chat skin over the Binance API. It is an **agent operating system** running inside your Binance account:

- It reads the **real market** exposed by Agent OS;
- It drives the **four native capabilities** (MCP / Wallet / x402 / Skill Hub) directly;
- Every sensitive action (order / transfer / signed payment) drops a `Confirm` card into the chat — **executed only after you click it**, on your real keys / keyless MPC wallet.

The cockpit is a Hermes-style three-pane **trading desk**:

```
┌──────────────┬─────────────────────────────┬──────────────────┐
│  LEFT        │  CENTER                     │  RIGHT           │
│  Sessions    │  Agent chat                 │  Live signals    │
│  Personas    │  · SSE/NDJSON streaming     │  Market radar    │
│  Tools       │  · live tool cards          │  Positions       │
│  Files       │  · Confirm cards            │  Account         │
└──────────────┴─────────────────────────────┴──────────────────┘
        dark ⇄ light theme · 中文 ⇄ EN — one click, remembered
```

---

## 🔁 The core loop

```
   ┌─────────────┐
   │   📡 SCAN    │   Full-market radar · Monster Radar · 24h watch
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │  🧠 ANALYZE  │   Multi-model LLM · 19+ skills · rules-engine fallback
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │  💡 PROPOSE  │   Signal card: leverage / position / TP-SL
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │  ✅ APPROVE  │   ◄── the key: YOU press Confirm in the chat
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │  ⚡ EXECUTE  │   CEX / Agentic Wallet / x402 — real orders
   └──────┬──────┘
          └────────────► back to scanning ◄
```

---

## ✨ What it does

| | What you get |
|---|---|
| 📡 **Live market radar** | Full-market anomaly scan (top-300 by volume) · **Monster Radar** burst windows with track record (moon/dump/expired verdicts at 10x-leverage thresholds) · 24h auto-watch · Fear & Greed · funding / OI / top-trader long-short — **zero API key**. |
| 🗣 **Conversational cockpit** | SSE/NDJSON streaming · live tool cards · file & voice input · multi-provider LLM + **fully offline rules-engine fallback**. |
| ✅ **Human-in-the-loop** | Every trade / transfer / signed payment = a confirm card. Sensitive ops are **never silent**. Whitelist trusted flows to skip the click. |
| 🧠 **Long-term memory** | Cross-session preferences & risk anchors, indexed & searchable, viewable in the Memory panel. |
| 👛 **Agentic Wallet** | Keyless MPC on BSC / Ethereum / Base / Solana via `baw`; official daily limits enforced; **private keys never touch the app**. |
| 💹 **Real CEX trading** | Spot / futures / convert on your real account via a whitelisted `binance-cli` profile; CEX and Web3 key sets **strictly isolated**. |
| 🧩 **Skill Hub — 19 skills** | 19 official skills preloaded (CEX + Web3 + research: audit, address info, tokenized stocks, sports AI, news sentiment…), intent-routed, path-traversal-protected. |
| 📣 **Square rich-posting** | Market report → cover + chart + SMC article → published as an **image short-post**, local ledger keeps every post. |
| 🤖 **Multi-bot + group chat** | Persona bots with own skills & memory domains; make a room, `@mention` bots, they answer in turns. |
| ⏰ **CRON automation** | Write "scan the market every day at 09:00" in plain words; run / pause / trigger manually — **runs even when the window is closed to tray**. |
| 💸 **x402 / B402** | Machine pays machine: offline Permit2 EIP-712 signing, verified by the official Facilitator, settled on-chain. |
| 🖥 **Desktop + Web** | Frameless Windows installer (Electron + embedded PyInstaller backend, **no Python/Node needed**) *and* the same cockpit in a browser. One codebase. |
| 🌓 **Bilingual & skinnable** | 中文 / English + deep-gold ↔ light, one key from the top bar. |
| 🔒 **Local-first** | Sessions / memory / config live in `<install dir>\workspace` — updates replace only the program, **never your data** (backup before update, abort on backup failure). |

<details>
<summary><b>🧩 The four native capabilities — real protocols, not widgets</b> <i>(click to expand)</i></summary>

| | Detail |
|---|---|
| **① MCP Server** · `agent.binance.com` | Streamable HTTP (JSON-RPC 2.0) · protocol `2025-06-18` · OAuth 2.0 **RFC 9728** + PKCE(S256) → Bearer · runtime `tools/list` discovery, zero hardcoded tool names · public market data unauthenticated; account/trading uses a **least-privilege scope (no withdrawals)**. See `src/mcp_client.py`. |
| **② Agentic Wallet** · `baw` CLI | Keyless MPC, BSC / ETH / Base / SOL · official limits: swap $50k/day · DeFi $100k/day · x402 $20/day · the agent only calls `baw` through a **whitelisted command shim** — the private key is never seen. |
| **③ x402 / B402** · machine pays machine | B402 Facilitator `/papi/v2/b402/{supported,verify,settle}` (BSC) · seller replies 402 → buyer signs **offline Permit2 EIP-712** (no gas) → Facilitator verifies → on-chain settle (Facilitator pays gas) · `/supported` fetched live; degrades gracefully to demo without credentials. |
| **④ Skill Hub** · official skills | 19 preloaded official skills, locked via `skills-lock.json` · `install/run/remove` with path-traversal protection · intent-driven · plugins share the runtime: `plugins/<id>/{main.py, plugin.json}`. |

**Real CEX channel — `binance-cli`**: two key sets, **strictly isolated**:

| System | Key | Use |
|------|------|------|
| **Binance CEX** | API Key + Secret (HMAC, System Generated) | Spot / futures / balance via `binance-cli` profile |
| **Web3 Agentic Wallet** | keyless MPC (`baw`) | on-chain swap, x402, DeFi |

</details>

---

## ⚡ Quick start

### Option 1 · Windows, one-click (recommended)

<div align="center">

**[⬇ Download latest setup.exe](https://github.com/xinyuzjj/bazz.agent/releases/latest)** — install & go. No Python, no Node.

`setup.exe` · `delta-<ver>.zip` (auto-consumed by in-app updater) · `SHA256SUMS` · `MANIFEST.json`

</div>

- First launch warms the backend ~3–5 s behind a splash animation, then the hatch opens.
- Data lives in `<install dir>\workspace` (auto-falls back to `%APPDATA%\BAZZ.AGENT\workspace` if the drive is read-only).
- Frameless window: drag the top bar; `─ / □ / ✕` top-right; **close-to-tray** keeps background jobs alive.
- In-app updater: version-guarded, delta-pack, SHA256-verified, auto-restart after install.

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

> The market radar needs **no key**. In Settings, fill any OpenAI-compatible `base_url` + key (DeepSeek / OpenAI / Moonshot / Ollama / custom). The rules-engine fallback means **the main flow works offline too**.

### Desktop dev mode (Windows)

```bash
start-desktop.bat     # Electron shell + .venv backend, one double-click
start-dev.bat         # FastAPI + Vite hot-reload for UI work
```

---

## 🔄 Incremental auto-update

```
   app checks GitHub Releases ──► MANIFEST.json diff vs local
            │                            │
            ▼                            ▼
   version guard (never        delta-<ver>.zip: ONLY changed
   downgrade, resume-verified) files, SHA256 every file
            │                            │
            └──────────┬─────────────────┘
                       ▼
   background prefetch ──► backup workspace ──► apply (~1s)
                       ▼
   setup.exe /VERYSILENT in-place ──► auto-restart ──► done ✅
```

Network blocked? The updater rides the **same proxy pool** as everything else, with live node failover.

---

## 🧭 Tech stack

| Layer | Tech |
|----|------|
| Agent core | FastAPI + SSE/NDJSON streaming · intent → tool → approval → execute |
| LLM | Multi-provider OpenAI-compatible layer + deterministic rules-engine fallback |
| Desktop shell | Electron (frameless, tray-resident) + PyInstaller backend |
| Memory | SQLite (`state.db`) — WAL + busy-timeout + serialized writes |
| Frontend | React 18 + TypeScript + Vite + Tailwind (dark/light · CN/EN) |
| Charts | Pure CSS + SVG (sparklines, gradients) — zero chart-dependency |
| Realtime | Server-Sent Events + polling |
| Proxy | Bundled mihomo kernel + multi-node pool with live health failover |

<details>
<summary><b>🏗 Architecture</b> <i>(click to expand)</i></summary>

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

</details>

<details>
<summary><b>🔒 Security model</b> <i>(click to expand)</i></summary>

- Every real trade / transfer / signed payment carries `needs_approval`; the backend proceeds only on an explicit `confirm: true`.
- Keys are never written to disk in plaintext; CEX keys are typed into the UI per session and mirrored into the local `binance-cli` profile only after you connect and authorize.
- The agent never touches raw private keys — the Agentic Wallet is keyless.
- `run_command` whitelist: `baw` / `node` / `python` / `git` + read-only wrappers — arbitrary shell rejected; destructive patterns (`rm -rf`, `format`, `shutdown`) hard-blocked.
- Local API enforces per-launch random-token auth (when run from the desktop shell) and strict path guards: no traversal, no workspace-root deletion, binaries flagged instead of garbled.
- Skills are path-traversal-protected; Square posts keep a local ledger; keys masked in the UI.
- Update packages are SHA256-verified and version-guarded before install; workspace is backed up first, update aborts if backup fails.

</details>

<details>
<summary><b>📦 Project layout</b> <i>(click to expand)</i></summary>

```
bazz.agent/
├── desktop_app.py            FastAPI backend (serves frontend/dist + /api/*)
├── launcher.py               PyInstaller desktop backend entry
├── electron/                 Desktop shell: main.cjs + preload.cjs (frameless + tray)
├── src/
│   ├── agent_core.py         intent → tool orchestration → approval → execute
│   ├── updater.py            incremental auto-update (MANIFEST + delta + version guard)
│   ├── mcp_client.py         Binance Agentic MCP (OAuth 2.0 + runtime discovery)
│   ├── x402_client.py        x402 / B402 payments (Permit2 EIP-712)
│   ├── wallet_client.py      Agentic Wallet (baw CLI wrapper)
│   ├── cex_wallet.py         Binance CEX HMAC client (read + sign)
│   ├── binance_cli.py        real trading channel: binance-cli profile sync
│   ├── skills_client.py      Skill Hub (install / run / remove)
│   ├── scanner.py            full-market Binance market radar
│   ├── radar_tracker.py      Monster Radar track record (moon/dump/expired)
│   ├── square_rich.py        Square rich-post composer (cover + SMC article)
│   ├── proxy_pool.py         proxy pool: import / test / mihomo kernel / failover
│   ├── llm.py                multi-provider LLM + rules-engine fallback
│   ├── scheduler.py          cron daemon
│   └── state.py              SQLite persistence
├── frontend/                 React 18 + TS + Vite + Tailwind (dark/light · CN/EN)
├── plugins/scout-signals/    example plugin
├── .agents/bots/             bot personas · .agents/skills/ 19 official skills
├── wallet_bridge/            Node bridge for on-chain wallets
└── docs/                     GitHub Pages landing page
```

</details>

---

## 🏷 Topics

`binance` · `agent-os` · `ai-agent` · `trading` · `crypto` · `web3` · `mcp` · `x402` · `oauth` · `electron` · `fastapi` · `react` · `smc` · `binance-square`

---

## 🏆 Hackathon

- **Event**: Binance Agent OS Mini Hackathon — Track A: *Build an AI Agent on Agent OS*
- **Deadline**: 2026-09-08 23:59 UTC
- **Live landing page**: https://xinyuzjj.github.io/bazz.agent/
- **Disclaimer**: This repo is a Hackathon demo, not investment advice. Crypto trading carries real risk; every execution path requires explicit human confirmation.

---

<div align="center">

**If BAZZ.AGENT saves you screen-time, drop a ⭐ — it makes the radar stronger.**

`Scan → Analyze → Propose → Approve → Execute` · *It asks before it fires.*

MIT License © 2026

</div>
