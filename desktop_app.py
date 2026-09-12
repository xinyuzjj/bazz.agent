"""BAZZ Agent - FastAPI 后端（Hermes 风格桌面 Agent）

提供：对话流式接口、多会话持久化、长期记忆、MCP 集成、定时调度、插件管理、
多模型配置、安全审批。前端 src/index.html 通过 /api/* 交互。
"""
import os
import sys
import threading

# 把 src/ 加入导入路径，使 import state / agent_core / mcp_client 等可用
# （desktop_app.py 位于项目根，业务模块在 src/）
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import json
import time
import asyncio
from typing import Optional

from fastapi import Body, FastAPI, WebSocket
from fastapi.requests import Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

# v1.5.7 修复：Windows 默认 Proactor 事件循环在客户端异常断连（RST，如技能 CLI 超时
# abort / 页面刷新）时会触发 "Accept failed on a socket"（WinError 64），此后 accept
# 循环死亡 —— 进程还活着但所有新连接无响应（表现为行情页/技能页全部转圈失败）。
# Selector 循环无此缺陷；本服务不使用 asyncio 子进程，websockets/requests 均兼容。
# 必须在事件循环创建前设置（本模块先于 uvicorn.run 执行，覆盖直接运行与 PyInstaller 入口）。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from fastapi.staticfiles import StaticFiles

import state
import agent_core
import llm
import scheduler
from mcp_client import (list_servers, add_server, remove_server, set_enabled as mcp_set_enabled,
                        call_tool, mcp_status, mcp_list_tools, list_tools_for, get_server,
                        discover_oauth, oauth_start, oauth_poll, get_token, set_token)
from x402_client import get_supported_configs, demo_x402_flow
from skills_client import get_catalog, install_skill, run_skill, remove_skill, get_bots as skills_get_bots
import wallet_client
import cex_wallet
import web3_wallet
import binance_cli
import plugin_host
import room
import updater
import proxy_pool
import proxy_kernel

APP_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(APP_DIR, "src", "index.html")
DIST = os.path.join(APP_DIR, "frontend", "dist", "index.html")

app = FastAPI(title="BAZZ Agent")

# v1.3.6：本机鉴权 —— Electron 拉起后端时注入 BAZZ_AUTH_TOKEN（每次启动随机生成），
# 打包态下所有 /api/* 必须携带 X-BAZZ-Token 头，否则 401。
# 页面与静态资源不校验（无敏感数据，Electron loadURL 也带不了自定义头）。
# dev 直接跑 desktop_app.py（无该环境变量）→ 不启用，行为与旧版完全一致。
AUTH_TOKEN = os.environ.get("BAZZ_AUTH_TOKEN", "").strip()


@app.on_event("startup")
async def _resume_pending_update():
    # v1.5.13 兜底：上次更新若 spawn 安装器静默失败（应用退了但没装上），
    # 本次启动时补跑 update-cache 里遗留的 setup.exe（后台线程，失败不阻塞启动）
    import asyncio as _aio
    try:
        await _aio.get_running_loop().run_in_executor(None, updater.resume_pending_update)
    except Exception as e:
        print(f"[updater] 恢复安装检查失败：{e}")

if AUTH_TOKEN:
    @app.middleware("http")
    async def _local_auth(request: Request, call_next):
        # OPTIONS 放行：CORS 预检请求不带自定义头，交给下层 CORSMiddleware 应答
        if request.method != "OPTIONS" and request.url.path.startswith("/api/"):
            if request.headers.get("X-BAZZ-Token", "") != AUTH_TOKEN:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

# CORS：打包态（启用 token）只放行本机来源 —— 恶意网页即使拿到 401 也读不到响应体；
# dev 态维持全放行（Vite 5173 代理 + 本地调试）。
if AUTH_TOKEN:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # 允许 Electron / Vite dev / 本地跨域访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# 启动定时调度守护线程
scheduler.start()

# v1.3.7：加载代理池并把已启用代理注入环境变量（须在任何对外请求前完成）
proxy_pool.bootstrap()

# v1.4.0：行情实时流（币安 WS，代理池 env 已就位后启动）+ 订单跟踪/SL·TP 监控
import market_ws
import order_tracker
import radar_tracker
market_ws.start()
order_tracker.ensure_started()
radar_tracker.ensure_started()  # v1.5.2：妖币启动前发现 → 后续暴涨/暴跌结局跟踪

# 后台预热钱包状态缓存（baw 冷启动慢，先算好，前端打开钱包页即秒回）
threading.Thread(target=wallet_client.warm_wallet_cache, daemon=True).start()


# ---------------- 静态 / 状态 ----------------

@app.get("/")
def index():
    # 生产构建产物优先（Electron 单源加载），否则回退 pywebview 版
    # v1.3.7.2：显式禁缓存 —— Chromium 会把旧 index.html 缓存在 userData，
    # 桌面版升级后仍加载旧页面（代理池卡片不显示），这里强制每次回源校验
    return FileResponse(
        DIST if os.path.exists(DIST) else INDEX,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/status")
def api_status():
    ms = mcp_status()
    return {
        "mcp": ms,
        "llm": {"configured": llm.is_configured(), "model": llm.get_llm_config().get("model")},
        "x402": {"status": "simulated", "detail": "演示模式（需 Agentic Wallet 生产权限）"},
        "wallet": {"status": "info", "detail": "baw CLI · 未连接"},
        "market": {"status": "live", "detail": "Binance 公开行情（无需 Key）"},
        "scheduler": {"status": "running", "detail": f"{len(scheduler.get_jobs())} 个定时任务"},
        "persona": agent_core.PERSONA_NAME,
    }


@app.get("/api/market")
def api_market():
    from scanner import scan_universe, market_movers, get_snapshot
    try:
        # 全市场口径：信号扫成交额前 150；全量快照给前端浏览/搜索/排序
        sigs = scan_universe(min_change_pct=0.5, universe_size=150, max_signals=16)
        movers = market_movers(top=5)
        # v1.3.6：快照只拉一次（此前同一接口内连拉两次，30s 轮询下 Binance 请求量/延迟翻倍）
        all_rows = get_snapshot(quote="USDT", limit=400)
        total = len(all_rows)
    except Exception as e:
        return {"signals": [], "movers": {"gainers": [], "losers": []},
                "all": [], "total": 0, "quote": "USDT", "error": str(e)}
    return {"signals": sigs,
            "movers": movers,
            "all": all_rows,
            "total": total,
            "quote": "USDT",
            "updated_at": int(time.time())}


@app.get("/api/market/overview")
def api_market_overview():
    """行情增强综述：市场宽度/突发、资金费率拥挤、24h 成交额热度榜，一次返回。"""
    from scanner import market_breadth, funding_board, volume_heat
    try:
        return {"breadth": market_breadth(),
                "funding": funding_board(top=10),
                "volume_top": volume_heat(top=15),
                "updated_at": int(time.time())}
    except Exception as e:
        return {"breadth": None, "funding": None, "volume_top": [], "error": str(e)}


@app.get("/api/market/monsters")
def api_market_monsters(force: int = 0):
    """起飞中·追涨高风险：已暴涨币跟踪（原妖币逻辑降级）。"""
    from scanner import get_monster_coins
    try:
        return get_monster_coins(force=bool(force))
    except Exception as e:
        return {"coins": [], "scanned": 0, "candidates": 0, "error": str(e)}


@app.get("/api/market/futures")
def api_market_futures():
    """合约（USDT-M 永续）+ 股票化代币合约行情，一次返回。
    futures: 全市场 U 本位永续（按成交额降序，附资金费率）
    equity: 股票化代币/传统资产永续合约定制板
    """
    from scanner import futures_snapshot, equity_board
    try:
        # v1.3.6：各拉一次复用（此前 futures/equity 各连调两次，响应时间翻倍）
        fut = futures_snapshot()
        eq = equity_board()
        return {"futures": fut,
                "equity": eq,
                "total_futures": len(fut),
                "total_equity": len(eq),
                "quote": "USDT", "market": "perpetual",
                "updated_at": int(time.time())}
    except Exception as e:
        return {"futures": [], "equity": [], "error": str(e)}


@app.get("/api/market/ignition")
def api_market_ignition(force: int = 0):
    """启动前·埋伏窗口：低位放量吸筹（点火前）——主推的妖币提前发现模式。"""
    from scanner import get_ignition_coins
    try:
        return get_ignition_coins(force=bool(force))
    except Exception as e:
        return {"coins": [], "scanned": 0, "candidates": 0, "error": str(e)}


# ---------------- v1.5.0 行情大更新：妖币雷达 v2 / 多空比 / 爆仓流 ----------------

@app.get("/api/market/radar")
def api_market_radar(force: int = 0):
    """妖币雷达 v2 全量扫描（四层模型：触发/确认/语义/过滤）。
    coins 含 stage/stage_label/score/factors/reasons，附 takeoff/ignition 分组与阶段计数。"""
    from scanner import get_radar_v2
    try:
        return get_radar_v2(force=bool(force))
    except Exception as e:
        return {"coins": [], "takeoff": [], "ignition": [], "stage_counts": {},
                "scanned": 0, "candidates": 0, "error": str(e)}


@app.get("/api/market/longshort")
def api_market_longshort():
    """多空比/大户持仓面板：大户持仓比 × 散户账户比双比值 + 背离标记（60s TTL）。"""
    from scanner import get_longshort_board
    try:
        return get_longshort_board()
    except Exception as e:
        return {"rows": [], "error": str(e)}


@app.get("/api/market/radar/tracks")
def api_market_radar_tracks():
    """妖币追踪：启动前发现记录 + 后续暴涨/暴跌结局验证（进行中/历史/战绩统计）。"""
    try:
        return radar_tracker.tracks_view()
    except Exception as e:
        return {"pending": [], "history": [], "stats": {}, "error": str(e)}


@app.get("/api/market/liquidations")
def api_market_liquidations(limit: int = 60, window: int = 300):
    """爆仓流面板：最近强平单（新→旧）+ 窗口统计（默认 5 分钟多/空爆金额与笔数）。
    数据源为 !forceOrder@arr WS 缓冲；受限地区无推送时返回空列表（前端显示重连中）。"""
    try:
        import market_ws
        return {"recent": market_ws.liq_recent(min(max(limit, 1), 200)),
                "stats": market_ws.liq_stats(window),
                "ws": market_ws.info().get("liq", {}), "ts": int(time.time())}
    except Exception as e:
        return {"recent": [], "stats": {}, "error": str(e)}



# ---------------- v1.5.4 行情增强：K 线走势 / 合约 OI / 恐惧贪婪指数 ----------------

@app.get("/api/market/klines")
def api_market_klines(symbol: str, interval: str = "1h", limit: int = 24, market: str = "spot"):
    """迷你走势图：最近 N 根 K 线收盘价（旧→新，后端 5 分钟缓存）。market=spot|futures。"""
    from scanner import klines_closes
    sym = (symbol or "").strip().upper()
    try:
        closes = klines_closes(sym, interval, limit,
                               market if market in ("spot", "futures") else "spot")
        return {"symbol": sym, "interval": interval, "closes": closes, "ts": int(time.time())}
    except Exception as e:
        return {"symbol": sym, "closes": [], "error": str(e)}


@app.get("/api/market/oi")
def api_market_oi(symbols: str):
    """合约持仓量批量查询：OI（币本位）× 最新价 → USD 名义持仓（5 分钟缓存）。symbols=逗号分隔。"""
    from scanner import futures_open_interest
    try:
        syms = [s for s in (symbols or "").split(",") if s.strip()]
        return {"items": futures_open_interest(syms), "ts": int(time.time())}
    except Exception as e:
        return {"items": [], "error": str(e)}


@app.get("/api/market/klines_ohlcv")
def api_market_klines_ohlcv(symbol: str, interval: str = "1d", limit: int = 90, market: str = "futures"):
    """完整 OHLCV K 线（旧→新，5 分钟缓存）—— square-rich-post 图表引擎用。"""
    from scanner import klines_ohlcv
    sym = (symbol or "").strip().upper()
    try:
        d = klines_ohlcv(sym, interval, limit,
                         market if market in ("spot", "futures") else "futures")
        return {"symbol": sym, "interval": interval, "market": market, "ts": int(time.time()), **d}
    except Exception as e:
        return {"symbol": sym, "opens": [], "highs": [], "lows": [], "closes": [],
                "vols": [], "times": [], "error": str(e)}


@app.get("/api/square/rich/compose")
def api_square_rich_compose(symbol: str, market: str = "futures"):
    """广场富媒体发文合成：全维度取数 → Pillow 画封面图（90d K线+成交量）+ 24h 分时图
    → 组稿（含 $cashtag/#hashtag）→ 落盘 workspace/square_rich/<SYM>_<ts>/。
    返回 {dir, title_file, text_file, cover, extra_chart, stats}，发文交给 square-post 技能。"""
    import square_rich
    sym = (symbol or "").strip().upper()
    try:
        return square_rich.compose(sym, market if market in ("spot", "futures") else "futures")
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/market/fng")
def api_market_fng():
    """恐惧贪婪指数（Crypto Fear & Greed，10 分钟缓存，含 8 天历史）。"""
    from scanner import fear_greed_index
    try:
        return fear_greed_index()
    except Exception as e:
        return {"value": None, "classification": None, "history": [], "error": str(e)}



# ---------------- v1.4.0 行情实时流（币安 WS → 后端缓存 → 前端推送） ----------------

@app.get("/api/market/tickers")
def api_market_tickers(scope: str = "spot", symbols: str = ""):
    """实时 ticker 快照（REST 兜底，WS 不可用时前端 1s 轮询这里；数据源为 WS 内存缓存）。"""
    try:
        import market_ws
        syms = [s for s in (symbols or "").split(",") if s.strip()]
        return {"scope": scope, "tickers": market_ws.tickers(scope, syms or None),
                "ws": market_ws.info().get(scope, {}), "ts": int(time.time())}
    except Exception as e:
        return {"scope": scope, "tickers": {}, "error": str(e)}


@app.get("/api/market/wsinfo")
def api_market_wsinfo():
    """实时流连接状态（前端 LIVE 徽标 / 诊断）。"""
    try:
        import market_ws
        return {"ok": True, "ws": market_ws.info()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.websocket("/api/ws")
async def api_ws(ws: WebSocket):
    """前端推送通道：行情 tick + 订单状态 + SL/TP 提醒。
    鉴权：HTTP 中间件不拦 WebSocket，这里自查 query token（BAZZ_AUTH_TOKEN 设置时）。"""
    if AUTH_TOKEN and (ws.query_params.get("token") or "") != AUTH_TOKEN:
        await ws.close(code=4401)
        return
    await ws.accept()
    import market_ws
    # 每连接独立订阅集 + 事件游标（seq 增量，多客户端互不抢占）
    subs: dict = {"spot": set(), "futures": set()}
    ev_seq = 0
    last_send = 0.0
    subs_dirty = True
    try:
        await ws.send_text(json.dumps({"type": "hello", "ws": market_ws.info()},
                                       ensure_ascii=False))
        while True:
            # 非阻塞收订阅指令：100ms 超时视为无新指令
            got = None
            try:
                got = await asyncio.wait_for(ws.receive_text(), timeout=0.1)
            except asyncio.TimeoutError:
                pass
            except Exception:
                break
            if got is not None:
                try:
                    m = json.loads(got)
                    if m.get("type") == "sub":
                        for scope in ("spot", "futures"):
                            want = m.get(scope) or []
                            if isinstance(want, list):
                                subs[scope] = {str(s).upper() for s in want[:400]}
                        subs_dirty = True
                except Exception:
                    pass
            # 推送节流：有事件/订阅变更立即推，否则最多 1s 一帧
            events = market_ws.events_after(ev_seq)
            now = time.time()
            if not (subs_dirty or events or now - last_send >= 1.0):
                continue
            ticks = {"spot": {}, "futures": {}}
            for scope, syms in subs.items():
                if syms:
                    ticks[scope] = market_ws.tickers(scope, list(syms))
            if events:
                ev_seq = events[-1]["seq"]
            subs_dirty = False
            last_send = now
            payload = {"type": "frame", "ticks": ticks, "events": events,
                       "conn": market_ws.info()}
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ---------------- v1.4.0 订单跟踪 ----------------

@app.get("/api/orders/track")
def api_orders_track():
    """下单跟踪列表（exchange 状态由监控线程自动同步）。"""
    try:
        import order_tracker
        return {"status": "ok", "orders": state.track_list()}
    except Exception as e:
        return {"status": "error", "orders": [], "message": str(e)[:200]}


@app.delete("/api/orders/track")
def api_orders_untrack(id: str):
    """停止跟踪某订单。"""
    try:
        import order_tracker
        order_tracker.untrack(id)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}

# ---------------- 对话（流式 + 持久化） ----------------

@app.post("/api/chat/stream")
async def chat_stream(req: Request):
    try:
        raw = await req.body()
        body = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        body = {}
    message = (body.get("message") or "").strip()
    conv_id = body.get("conversation_id") or body.get("conv_id")
    confirm = body.get("confirm", False)
    signal = body.get("signal")
    approval = body.get("approval")
    persona = body.get("persona")  # {name,title,description,avatar,color} 由前端 Bots 页传入
    regenerate = body.get("regenerate", False)   # 重新生成最后一段（不重复落 user 消息）
    edit_text = (body.get("edit_text") or "").strip()  # 编辑重发：改写目标 user 文本后再生成
    edit_index = body.get("edit_index")               # 目标 user 在会话中的序号（可选；缺省=最后一段）
    images = body.get("images") or []   # 用户附图：dataURL 列表（视觉直读 / OCR 兜底）
    if isinstance(images, list):
        images = [s for s in images if isinstance(s, str) and s][:6]  # 最多 6 张
    else:
        images = []
    # 界面语言（前端随每条消息带上：zh / en），英文时 agent 回复按英文输出
    locale = str(body.get("locale") or "zh")[:16]
    # 全能模式（首页默认开启）：沙箱类工具（run_command/write_file/run_skill）跳过审批直接执行；
    # 交易类（propose_trade/execute_order）仍需用户显式确认。
    if "auto_exec" in body:
        auto_exec = bool(body.get("auto_exec"))
    else:
        try:
            auto_exec = bool(json.loads(state.get_setting("auto_exec", "1") or "1"))
        except Exception:
            auto_exec = True

    if not message and not approval and not regenerate and not edit_text:
        return JSONResponse({"error": "empty"}, status_code=400)

    # 群聊房间：消息发进 kind='room' 会话 → 走房间轮次引擎（Hermes room log + per-member session）
    if conv_id:
        room_conv = state.get_conversation(conv_id)
        if room_conv and room_conv.get("kind") == "room":
            def room_gen():
                # v1.3.6：房间引擎异常不再让流静默断死，推 error 事件给前端
                try:
                    for ev in room.run_room(conv_id, message):
                        yield json.dumps(ev, ensure_ascii=False) + "\n"
                except GeneratorExit:
                    raise
                except Exception as e:
                    yield json.dumps({"type": "error", "detail": str(e)[:300]},
                                     ensure_ascii=False) + "\n"
            return StreamingResponse(room_gen(), media_type="application/x-ndjson")

    llm_cfg = None
    is_regenerate = bool(regenerate or edit_text)
    if not conv_id:
        conv_title = message[:28] or "新对话"
        if persona and persona.get("name"):
            conv_title = f"@{persona.get('name','')} · " + conv_title
        # 新会话：首条消息即固定 provider/model 快照（Hermes 防漂移）
        conv_id = state.new_conversation(title=conv_title, persona=(persona or {}).get("name") or "",
                                         provider_snapshot=llm.snapshot())
    else:
        state.set_conversation_persona(conv_id, (persona or {}).get("name") or "")
        conv = state.get_conversation(conv_id)
        snap = (conv or {}).get("provider_snapshot") or {}
        if not snap:
            snap = llm.snapshot()
            state.set_conversation_provider_snapshot(conv_id, snap)
        llm_cfg = llm.cfg_from_snapshot(snap)

    # 本次请求级 llm_cfg 覆盖（前端 composer 下拉选了别的模型时传入）。
    # 仅在主模型键上浅合并——备份链/aux 槽/provider 仍走会话快照，避免误改全局默认。
    override = body.get("llm_cfg") or {}
    if isinstance(override, dict) and override:
        if not llm_cfg:
            llm_cfg = llm.get_llm_config()
        for k, v in override.items():
            if v in (None, "", []):
                continue
            if k == "model":
                # 同一会话里临时换模型：把快照里的备份模型追加新选，避免 fallback 链断掉
                cur = llm_cfg.get("backup_models") or []
                if v not in cur:
                    llm_cfg["backup_models"] = list(cur) + [v]
                llm_cfg["model"] = v
            else:
                llm_cfg[k] = v
    if is_regenerate:
        # 回滚：删该 user 之后的全部消息，改写目标 user 文本（若编辑）
        prev_user = state.rollback_last_turn(conv_id, new_user_text=edit_text or None,
                                             upto_user_index=edit_index)
        message = prev_user or message
        state.touch_conversation(conv_id)
    else:
        state.add_message(conv_id, "user", message)
        state.touch_conversation(conv_id)

    def gen():
        final = {}
        reasoning_acc = []
        text_acc = []  # v1.3.6：累积正文增量 —— 流中断时仍能落库已生成的部分回复
        model_used = ""
        first_meta_injected = False
        # 记住上下文：取本会话此前消息（排除刚落库的当前 user 消息），传给 agent 注入历史
        history = None
        if conv_id and not approval:
            try:
                msgs = state.get_messages(conv_id)
                prev = [m for m in msgs if m.get("content") != message] if msgs else []
                hist = []
                for m in prev:
                    role = m.get("role")
                    if role in ("user", "assistant") and (m.get("content") or "").strip():
                        hist.append({"role": role, "content": m["content"]})
                if hist:
                    history = hist
            except Exception:
                history = None

        def _persist():
            """落库：正文 + 思考链 + 实际命中的 model（只在有内容时写，避免空泡）。"""
            reply = final.get("reply") or "".join(text_acc)
            if not str(reply).strip():
                return
            try:
                state.add_message(conv_id, "assistant", reply, tools=final.get("tools", []),
                                  reasoning="\n".join(reasoning_acc)[:6000],
                                  model=model_used or final.get("model") or "",
                                  data={"intent": final.get("intent"),
                                        "needs_approval": final.get("needs_approval", False)})
            except Exception:
                pass
            # 会话标题兜底（只查一次；会话可能已被用户在流中删除）
            try:
                conv_now = state.get_conversation(conv_id) or {}
                t = conv_now.get("title") or ""
                if not t or t == "新对话":
                    state.touch_conversation(conv_id, title=message[:28])
            except Exception:
                pass
            # 自动生成记忆：对话结束后沉淀用户偏好/习惯（静默，失败不影响主流程）
            try:
                agent_core.auto_memorize(message, reply, llm_cfg=llm_cfg)
            except Exception:
                pass
            # v1.4.3 会话标题自动生成：标题仍为默认截断时，后台线程用 summarize 槽位升级（失败静默）
            if conv_id and str(reply or "").strip():
                try:
                    threading.Thread(target=agent_core.auto_title, args=(conv_id,),
                                     kwargs={"llm_cfg": llm_cfg}, daemon=True, name="auto-title").start()
                except Exception:
                    pass

        try:
            for ev in agent_core.run_stream(message, confirm=confirm, signal=signal, approval=approval,
                                            persona=persona, llm_cfg=llm_cfg, images=images,
                                            auto_exec=auto_exec, history=history, locale=locale,
                                            cid=conv_id or ""):
                # 把 conversation_id 注入首事件与 done 事件，前端能持续复用同一会话
                if (not first_meta_injected) or ev.get("type") == "done":
                    ev = {**ev, "conversation_id": conv_id}
                    first_meta_injected = True
                yield json.dumps(ev, ensure_ascii=False) + "\n"
                if ev.get("model"):
                    model_used = ev["model"]
                if ev.get("type") in ("text", "delta"):
                    text_acc.append(ev.get("delta") or "")
                if ev.get("type") == "reasoning":
                    reasoning_acc.append(ev.get("text") or "")
                if ev.get("type") == "done":
                    final = ev
        except GeneratorExit:
            # 客户端中途断开（关窗/切会话）：不可再 yield，把已生成内容落库后原样上抛
            _persist()
            raise
        except Exception as e:
            # v1.3.6：流中异常不再静默断死 —— 推 error 事件（前端 ChatView 已有对应渲染）
            detail = (str(e) or e.__class__.__name__)[:300]
            try:
                yield json.dumps({"type": "error", "detail": detail, "conversation_id": conv_id},
                                 ensure_ascii=False) + "\n"
            except GeneratorExit:
                pass
            _persist()
        else:
            # v1.4.3 修复：流自然完成此前从不落库（仅 Stop 断开/异常时才写库），补上 else 分支
            _persist()

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ---------------- 会话 CRUD ----------------

@app.get("/api/conversations")
def list_conv(request: Request):
    include = (request.query_params.get("include_archived") in ("1", "true", "yes"))
    return state.list_conversations(include_archived=include)


@app.post("/api/conversations")
def new_conv():
    cid = state.new_conversation()
    return {"id": cid}


@app.post("/api/conversations/{cid}/rename")
async def rename_conv(cid: str, req: Request):
    b = await req.json()
    title = (b.get("title") or "").strip()[:60]
    if not title:
        return JSONResponse({"error": "empty title"}, status_code=400)
    if not state.get_conversation(cid):
        return JSONResponse({"error": "not found"}, status_code=404)
    state.touch_conversation(cid, title=title)
    return {"ok": True, "id": cid, "title": title}


@app.get("/api/conversations/search")
def search_conv(request: Request):
    # v1.4.3 会话搜索：LIKE 匹配标题+消息正文。注意必须注册在 /api/conversations/{cid} 之前
    q = (request.query_params.get("q") or "").strip()
    if not q:
        return []
    return state.search_conversations(q, limit=30)


# ---------------- 群聊房间 CRUD（Hermes Bot Mode rooms） ----------------

@app.get("/api/rooms")
def list_rooms():
    return {"rooms": state.list_rooms()}


@app.post("/api/rooms")
async def create_room(req: Request):
    b = await req.json()
    name = (b.get("name") or "").strip()[:40]
    members = [m for m in (b.get("members") or []) if isinstance(m, str)][:8]
    members = list(dict.fromkeys(members))  # 去重保序
    if not name:
        return JSONResponse({"error": "需要房间名"}, status_code=400)
    if len(members) < 2:
        return JSONResponse({"error": "至少选择 2 个 Agent 加入"}, status_code=400)
    existing = state.room_exists(name)
    if existing:
        return JSONResponse({"error": f"房间「{name}」已存在"}, status_code=409)
    known = {ag["name"] for ag in state.list_agents()}
    members = [m for m in members if m in known]
    if len(members) < 2:
        return JSONResponse({"error": "成员必须是已存在的 Agent 档案"}, status_code=400)
    rid = state.new_conversation(title=name, persona="__group__", kind="room", members=members,
                                 provider_snapshot=llm.snapshot())
    return state.get_conversation(rid)


@app.post("/api/rooms/{rid}/members")
async def room_member_update(rid: str, req: Request):
    b = await req.json()
    conv = state.get_conversation(rid)
    if not conv or conv.get("kind") != "room":
        return JSONResponse({"error": "房间不存在"}, status_code=404)
    members = list(conv.get("members") or [])
    name = (b.get("name") or "").strip()
    action = b.get("action") or "add"
    if action == "add" and name and name not in members:
        members.append(name)
    elif action == "remove" and name in members:
        members = [m for m in members if m != name]
    else:
        return JSONResponse({"error": "action 必须 add/remove 且给出成员名"}, status_code=400)
    if len(members) < 2:
        return JSONResponse({"error": "房间至少保留 2 个成员；解散请删除房间"}, status_code=400)
    state.update_room_members(rid, members)
    return state.get_conversation(rid)


@app.delete("/api/rooms/{rid}")
def delete_room(rid: str):
    state.delete_conversation(rid)
    return {"ok": True}


@app.post("/api/upload")
async def upload_file(req: Request):
    """真附件上传：存到 .workbuddy/attachments/（不进工作区列表），返回文本摘录供模型读取。
    非文本/二进制只返回文件信息；上限 8MB。"""
    try:
        ct = req.headers.get("content-type", "")
        if "json" in ct:
            body = await req.json()
            filename = (body.get("name") or f"file-{int(time.time())}").strip()
            b64 = body.get("data") or ""
            import base64
            raw = base64.b64decode(b64) if b64 else b""
        else:
            raw = await req.body()
            import urllib.parse
            disp = req.headers.get("content-disposition", "")
            filename = "upload.bin"
            if "filename=" in disp:
                fn = disp.split("filename=")[-1].split(";")[0].strip().strip('"')
                try:
                    filename = urllib.parse.unquote(fn)
                except Exception:
                    filename = fn
    except Exception as e:
        return JSONResponse({"error": f"解析失败: {e}"}, status_code=400)
    if len(raw) > 8 * 1024 * 1024:
        return JSONResponse({"error": "文件超过 8MB 上限"}, status_code=413)
    filename = os.path.basename(filename or "upload.bin")[:120] or "upload.bin"
    from workspace import ATTACHMENTS  # 统一落盘到 workspace
    at_dir = ATTACHMENTS
    safe = f"{int(time.time() * 1000)}-{filename}"
    path = os.path.join(at_dir, safe)
    with open(path, "wb") as f:
        f.write(raw)
    # 文本摘录（前 6KB），二进制给类型+大小
    excerpt = ""
    is_text = False
    try:
        txt = raw.decode("utf-8")
        is_text = True
        excerpt = txt[:6000]
    except Exception:
        is_text = False
    return {
        "ok": True, "name": filename, "path": path.replace("\\", "/"),
        "size": len(raw), "is_text": is_text,
        "excerpt": excerpt,
        "abs": os.path.abspath(path).replace("\\", "/"),
    }


_WORKSPACE_SKIP = {"migrated_v1", ".DS_Store", "Thumbs.db"}


def _resolve_workspace_subpath(rel: str) -> str:
    """把相对路径（来自前端 ?path=）解析为 workspace 内绝对路径，越界则拒绝。"""
    from workspace import WORKSPACE
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        return WORKSPACE
    parts = [p for p in rel.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError("path 含越界段 ..")
    target = os.path.realpath(os.path.join(WORKSPACE, *parts))
    base = os.path.realpath(WORKSPACE)
    if not (target == base or target.startswith(base + os.sep)):
        raise ValueError("path 越界 workspace")
    return target


@app.get("/api/settings/auto-exec")
def get_auto_exec():
    """首页默认：全能模式开关。读 settings.auto_exec（缺省=1=开）。"""
    try:
        v = state.get_setting("auto_exec", "1")
    except Exception:
        v = "1"
    return {"auto_exec": str(v) not in ("0", "false", "False")}


@app.post("/api/settings/auto-exec")
async def set_auto_exec(req: Request):
    """开关全能模式（前端用户切换时调用）。"""
    try:
        body = await req.json()
        on = bool(body.get("auto_exec"))
    except Exception:
        on = True
    state.set_setting("auto_exec", "1" if on else "0")
    return {"ok": True, "auto_exec": on}


@app.get("/api/settings/deep-thinking")
def get_deep_thinking():
    """深度思考总开关（缺省=1=开）。开时所有请求注入 THINKING_PROTOCOL + token 预算扩展，
    抽 <thinking>...</thinking> 放进 reasoning 字段给前端「深度思考」折叠块。"""
    try:
        v = state.get_setting("deep_thinking", "1")
    except Exception:
        v = "1"
    return {"deep_thinking": str(v) not in ("0", "false", "False")}


@app.post("/api/settings/deep-thinking")
async def set_deep_thinking(req: Request):
    try:
        body = await req.json()
        on = bool(body.get("deep_thinking"))
    except Exception:
        on = True
    state.set_setting("deep_thinking", "1" if on else "0")
    return {"ok": True, "deep_thinking": on}


@app.get("/api/approvals/whitelist")
def wl_list():
    """查看审批白名单（用户信任过的本地操作）。"""
    try:
        items = json.loads(state.get_setting("approval_whitelist", "[]") or "[]")
    except Exception:
        items = []
    return {"items": items if isinstance(items, list) else []}


@app.post("/api/approvals/whitelist")
async def wl_add(req: Request):
    body = await req.json()
    rule = str(body.get("rule") or "").strip()
    if not rule:
        return JSONResponse({"error": "rule 为空"}, status_code=400)
    try:
        items = json.loads(state.get_setting("approval_whitelist", "[]") or "[]")
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []
    if rule not in items:
        items.append(rule)
    state.set_setting("approval_whitelist", json.dumps(items, ensure_ascii=False))
    return {"ok": True, "items": items}


@app.delete("/api/approvals/whitelist")
async def wl_del(req: Request):
    rule = (req.query_params.get("rule") or "").strip()
    if not rule:
        try:
            body = await req.json()
            rule = str(body.get("rule") or "").strip()
        except Exception:
            pass
    try:
        items = json.loads(state.get_setting("approval_whitelist", "[]") or "[]")
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []
    if rule:
        items = [r for r in items if r != rule]
    else:
        items = []
    state.set_setting("approval_whitelist", json.dumps(items, ensure_ascii=False))
    return {"ok": True, "items": items}


@app.get("/api/workspace/files")
def workspace_files(path: str = ""):
    """列运行时工作区某子目录真实文件（黑名单跳过隐藏/系统文件）。"""
    try:
        base = _resolve_workspace_subpath(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not os.path.isdir(base):
        return JSONResponse({"error": "目录不存在", "path": path}, status_code=404)
    rows = []
    try:
        for e in sorted(os.scandir(base), key=lambda x: (x.is_file(), x.name.lower())):
            if e.name in _WORKSPACE_SKIP or e.name.startswith("."):
                continue
            try:
                st = e.stat()
            except Exception:
                continue
            rows.append({
                "name": e.name,
                "type": "dir" if e.is_dir() else "file",
                "size": st.st_size if e.is_file() else None,
                "mtime": int(st.st_mtime),
            })
    except Exception:
        pass
    rel = ""
    if path:
        rel = path.strip().replace("\\", "/").lstrip("/")
    return {"items": rows, "path": rel, "root": "workspace"}


_MAX_FILE_BYTES = 1.5 * 1024 * 1024  # 1.5MB 文本上限（防大文件）


@app.get("/api/workspace/read")
def workspace_read(path: str = ""):
    """读取项目根内任意真实文件（仅展示，不可写）。文件大小上限 1.5MB。"""
    try:
        target = _resolve_workspace_subpath(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not os.path.isfile(target):
        return JSONResponse({"error": "文件不存在", "path": path}, status_code=404)
    if os.path.getsize(target) > _MAX_FILE_BYTES:
        return JSONResponse({"error": f"文件过大（>{int(_MAX_FILE_BYTES/1024)}KB），暂不展示",
                             "path": path, "too_large": True}, status_code=413)
    rel = path.strip().replace("\\", "/").lstrip("/")
    try:
        with open(target, "rb") as f:
            raw = f.read()
    except Exception as e:
        return JSONResponse({"error": f"读取失败: {e}"}, status_code=500)
    # 二进制（SQLite 库/图片等）不做文本解码：含 NUL 字节即按二进制处理，
    # 避免强行 GBK replace 解出一屏乱码（state.db 之前就是这个观感）。
    if b"\x00" in raw[:4096]:
        return {
            "ok": True, "name": os.path.basename(target), "path": rel,
            "size": len(raw), "is_text": False, "binary": True,
            "content": "",
            "abs": os.path.abspath(target).replace("\\", "/"),
        }
    is_text = False
    txt = ""
    try:
        txt = raw.decode("utf-8")
        is_text = True
    except Exception:
        try:
            txt = raw.decode("gbk", errors="replace")
            is_text = True
        except Exception:
            is_text = False
    return {
        "ok": True, "name": os.path.basename(target), "path": rel,
        "size": len(raw), "is_text": is_text,
        "content": txt if is_text else "",
        "abs": os.path.abspath(target).replace("\\", "/"),
    }


_IMG_EXT_MEDIA = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".svg": "image/svg+xml", ".ico": "image/x-icon",
}
_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20MB 图片上限


@app.get("/api/workspace/raw")
def workspace_raw(path: str = ""):
    """图片原文端点（v1.5.24）：文件管理器点图片直接预览，不再落入「二进制不可预览」。

    仅扩展名白名单（png/jpg/jpeg/gif/webp/bmp/svg/ico）；路径经 _resolve_workspace_subpath
    防越界；鉴权走 /api/* 统一中间件；前端 fetch blob（带 token）后 objectURL 展示。"""
    try:
        target = _resolve_workspace_subpath(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not os.path.isfile(target):
        return JSONResponse({"error": "文件不存在", "path": path}, status_code=404)
    media = _IMG_EXT_MEDIA.get(os.path.splitext(target)[1].lower())
    if not media:
        return JSONResponse({"error": "仅支持图片预览（png/jpg/jpeg/gif/webp/bmp/svg/ico）"}, status_code=415)
    if os.path.getsize(target) > _MAX_IMAGE_BYTES:
        return JSONResponse({"error": f"图片过大（>{_MAX_IMAGE_BYTES // 1024 // 1024}MB）"}, status_code=413)
    return FileResponse(target, media_type=media)


@app.delete("/api/workspace/file")
def workspace_delete(path: str = ""):
    """删除工作区内文件/目录（文件浏览器「删除」按钮后端）。

    安全约束：仅限 workspace 内（_resolve_workspace_subpath 拒绝越界）；工作区根本身不可删。
    被进程占用的文件（如运行中的 state.db）会返回明确错误，不做静默半删。
    """
    import shutil
    rel = path.strip().replace("\\", "/").lstrip("/")
    if not rel:
        return JSONResponse({"error": "不能删除工作区根目录"}, status_code=400)
    try:
        target = _resolve_workspace_subpath(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    from workspace import WORKSPACE
    if target == os.path.realpath(WORKSPACE):
        return JSONResponse({"error": "不能删除工作区根目录"}, status_code=400)
    if not (os.path.isfile(target) or os.path.isdir(target)):
        return JSONResponse({"error": "文件不存在", "path": rel}, status_code=404)
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
    except OSError as e:
        return JSONResponse({"error": f"删除失败（文件可能被占用）: {e}"}, status_code=500)
    return {"ok": True, "path": rel}


@app.get("/api/conversations/{cid}")
def get_conv(cid: str):
    conv = state.get_conversation(cid)
    if not conv:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"conversation": conv, "messages": state.get_messages(cid)}


@app.delete("/api/conversations/{cid}")
def del_conv(cid: str):
    state.delete_conversation(cid)
    return {"ok": True}


@app.post("/api/conversations/{cid}/archive")
async def archive_conv(cid: str, req: Request):
    body = await req.json() if (req.headers.get("content-type") or "").startswith("application/json") else {}
    state.set_conversation_archived(cid, bool(body.get("archived", True)))
    return {"ok": True, "archived": bool(body.get("archived", True))}


# ---------------- 设置（含多模型 / 密钥，本地持久化） ----------------

@app.get("/api/settings")
def get_settings():
    s = state.get_all_settings()
    # 不向外暴露明文 key 的完整性，仅返回是否已配置
    llm_cfg = llm.get_llm_config()
    out = {
        "llm": {"provider": llm_cfg.get("provider"), "base_url": llm_cfg.get("base_url"),
                "model": llm_cfg.get("model"), "configured": bool(llm_cfg.get("api_key") or llm.resolve_key(llm_cfg)),
                "backup_models": llm_cfg.get("backup_models", []),
                "key_env": llm_cfg.get("key_env", ""),
                "aux": llm_cfg.get("aux", {})},
        "binance_key_set": bool(state.get_setting("BINANCE_API_KEY", "")),
    }
    return out


@app.post("/api/settings")
async def post_settings(req: Request):
    body = await req.json()
    if "llm" in body:
        cfg = body["llm"]
        # 合并已有（不破坏未暴露的密钥：空字符串 api_key 视为「不修改」）
        cur = llm.get_llm_config()
        for k, v in cfg.items():
            if v is None:
                continue
            if k == "api_key" and v == "":
                continue  # 不清除已保存的密钥
            if k == "configured":
                continue  # 仅前端展示用，不持久化
            cur[k] = v
        state.set_setting("llm", json.dumps(cur, ensure_ascii=False))
    if "BINANCE_API_KEY" in body:
        state.set_setting("BINANCE_API_KEY", body["BINANCE_API_KEY"])
    if "BINANCE_API_SECRET" in body:
        state.set_setting("BINANCE_API_SECRET", body["BINANCE_API_SECRET"])
    return {"ok": True}


# ---------------- 代理池 / 内核（v1.3.7） ----------------

@app.get("/api/proxies")
def proxies_list():
    return proxy_pool.list_pool()


@app.post("/api/proxies/import")
async def proxies_import(req: Request):
    b = await req.json()
    url = (b.get("url") or "").strip()
    text = b.get("text") or ""
    group = (b.get("group") or "").strip()
    try:
        if url:
            return {"ok": True, **proxy_pool.import_subscription(url)}
        added, updated, parsed = proxy_pool.import_text(text, group=group)
        return {"ok": True, "added": added, "updated": updated, "parsed": len(parsed)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.post("/api/proxies/refresh")
def proxies_refresh():
    return {"ok": True, "results": proxy_pool.refresh_subscriptions()}


@app.post("/api/proxies/test")
async def proxies_test(req: Request):
    b = await req.json()
    pid = b.get("id")
    try:
        if pid:
            e = next((x for x in proxy_pool.list_pool()["entries"] if x["id"] == pid), None)
            if not e:
                return {"ok": False, "error": "节点不存在。"}
            proxy_pool.test_entry(e)
            with proxy_pool._LOCK:
                for x in proxy_pool._state["entries"]:
                    if x["id"] == pid:
                        x.update({"latency_ms": e["latency_ms"], "status": e["status"],
                                  "fails": e["fails"], "error": e["error"],
                                  "last_tested": e["last_tested"]})
                proxy_pool._save()
            return {"ok": True, "results": [{"id": pid, "status": e["status"],
                                            "latency_ms": e["latency_ms"], "error": e["error"]}]}
        return {"ok": True, "results": proxy_pool.test_all()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.post("/api/proxies/active")
async def proxies_active(req: Request):
    b = await req.json()
    try:
        aid = proxy_pool.set_active(b.get("id") or "")
        return {"ok": True, "active_id": aid, "active_url": proxy_pool.active_url()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.post("/api/proxies/{pid}/edit")
async def proxies_edit(pid: str, req: Request):
    b = await req.json()
    try:
        proxy_pool.update_entry(pid, name=b.get("name"), group=b.get("group"))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.delete("/api/proxies/{pid}")
def proxies_delete(pid: str):
    proxy_pool.delete(pid)
    return {"ok": True}


@app.get("/api/proxies/kernel")
def proxies_kernel_status():
    return proxy_kernel.status()


@app.post("/api/proxies/kernel/download")
def proxies_kernel_download():
    return proxy_kernel.start_download()


@app.post("/api/proxies/kernel/start")
def proxies_kernel_start():
    r = proxy_kernel.start()
    if r.get("ok"):
        # v1.5.32：内核起来后必须立刻注入 env —— 此前只拉内核不注入，
        # 用户点「启动内核」后 HTTP(S)_PROXY 仍为空，技能照样直连超时。
        proxy_pool.apply_env()
    return r


@app.post("/api/proxies/kernel/stop")
def proxies_kernel_stop():
    r = proxy_kernel.stop()
    proxy_pool.apply_env()      # v1.5.32：内核停掉后同步清理/回退代理 env
    return r


# ---------------- LLM 连通性 / 模型目录 ----------------

@app.post("/api/llm/test")
async def llm_test(req: Request):
    body = await req.json()
    cfg = llm.get_llm_config()
    res = llm.test_connection(
        base_url=body.get("base_url") or cfg.get("base_url", ""),
        api_key=body.get("api_key") or cfg.get("api_key", ""),
        model=body.get("model") or cfg.get("model", ""),
        provider=body.get("provider") or cfg.get("provider", ""),
    )
    return res


@app.get("/api/llm/models")
def llm_models(provider: str = "", base_url: str = "", api_key: str = ""):
    cfg = llm.get_llm_config()
    models = llm.list_models(
        base_url=base_url or cfg.get("base_url", ""),
        api_key=api_key or cfg.get("api_key", ""),
        provider=provider or cfg.get("provider", ""),
    )
    return {"models": models}


# ---------------- 长期记忆 ----------------

@app.get("/api/memory")
def get_memory():
    return state.list_memory()


@app.get("/api/memory/stats")
def memory_stats():
    """长期记忆统计：总数/分类/SQLite 文件大小/最近更新/Top N。"""
    import os, time
    rows = state.list_memory()
    total = len(rows)
    cats: dict = {}
    for r in rows:
        # v1.4.1：按 kind 字段分类统计（旧数据回退 key 前缀）
        head = r.get("kind") or (r.get("key", "").split(":", 1)[0] if r.get("key") else "misc")
        cats[head] = cats.get(head, 0) + 1
    top = sorted(rows, key=lambda r: r.get("updated_at", 0), reverse=True)[:10]
    last = rows[0].get("updated_at") if rows else 0
    # 真实数据库路径（v1.3.3+ 统一在 workspace：src/state.py 的 DB_PATH = workspace/state.db；
    # state.py 启用 WAL 后同目录还有 state.db-wal/-shm 辅助文件，不计入大小展示）
    db_path = ""
    db_size = 0
    try:
        from state import DB_PATH as STATE_DB
        cand = os.path.abspath(STATE_DB)
        if os.path.isfile(cand):
            db_path = cand
            db_size = os.path.getsize(cand)
    except Exception:
        pass
    return {
        "ok": True,
        "total": total,
        "categories": [{"name": n, "count": c} for n, c in sorted(cats.items(), key=lambda x: -x[1])],
        "recent": [{"key": r["key"], "value": (r.get("value") or "")[:120], "updated_at": r.get("updated_at", 0)} for r in top],
        "last_updated_at": last,
        "db_path": db_path,
        "db_size_bytes": db_size,
        "engine": "SQLite · 本地存档（key-value）",
    }


@app.get("/api/memory/export")
def memory_export(request: Request):
    """导出长期记忆。默认 JSON；?format=md → Markdown 报告（按 kind 分组，v1.4.4）。"""
    from fastapi.responses import Response
    rows = state.list_memory()
    fmt = (request.query_params.get("format") or "").lower()
    if fmt == "md":
        rank = {"pref": "对用户的了解（偏好/习惯）", "fact": "长期记忆·事实",
                "event": "事件记录", "manual": "其他"}
        groups = {}
        for r in rows:
            groups.setdefault(rank.get(r.get("kind") or "", "其他"), []).append(r)
        stamp = time.strftime("%Y-%m-%d %H:%M")
        md = [f"# BAZZ Agent 记忆报告", "", f"- 导出时间：{stamp}",
              f"- 记忆条目：{len(rows)} 条", "",
              "> 本报告由 BAZZ.AGENT 本地导出，仅存在于你的电脑。", ""]
        for gname in ["对用户的了解（偏好/习惯）", "长期记忆·事实", "事件记录", "其他"]:
            g = groups.get(gname)
            if not g:
                continue
            md += [f"## {gname}（{len(g)} 条）", ""]
            for r in sorted(g, key=lambda x: -(x.get("updated_at") or 0)):
                when = time.strftime("%Y-%m-%d", time.localtime(r.get("updated_at") or 0))
                val = " ".join(str(r.get("value") or "").split())
                src = r.get("source") or ""
                hits = r.get("hits") or 0
                md.append(f"- **{r['key']}**（{when}{' · ' + src if src else ''}"
                          f"{' · 命中 ' + str(hits) + ' 次' if hits else ''}）：{val}")
            md.append("")
        return Response(
            content="\n".join(md),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="bazz-memory-report.md"'},
        )
    payload = {
        "exported_at": time.time(),
        "engine": "BAZZ_AGENT memory v2",
        "total": len(rows),
        "items": [{"key": r["key"], "value": r.get("value", ""), "updated_at": r.get("updated_at", 0)} for r in rows],
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bazz-memory.json"'},
    )


@app.get("/api/gateways")
def get_gateways():
    """网关健康状态聚合（MCP / Agentic Wallet / x402 / Skills Hub），供前端状态卡。"""
    from agent_core import _run_gateway_status
    res = _run_gateway_status()
    data = res.get("data", {}) or {}
    return {"gateways": data.get("gateways", []), "mcp_tools": data.get("mcp_tools", 0),
            "summary": (res.get("reply") or "")[:400]}


@app.post("/api/memory")
async def post_memory(req: Request):
    body = await req.json()
    key = body.get("key", "").strip()
    value = body.get("value", "").strip()
    if not key:
        return JSONResponse({"error": "key required"}, status_code=400)
    state.set_memory(key, value, kind=body.get("kind", "fact"), source="manual")
    return {"ok": True, "memory": state.list_memory()}


@app.delete("/api/memory/{key}")
def del_memory(key: str):
    state.delete_memory(key)
    return {"ok": True}


# ---------------- MCP 集成 ----------------

@app.get("/api/mcp")
def get_mcp():
    servers = list_servers()
    st = mcp_status()
    out = []
    for s in servers:
        reach = next((x for x in st.get("servers", []) if x["name"] == s["name"]), {})
        out.append({**s, "reachable": reach.get("reachable", False),
                    "needs_auth": reach.get("needs_auth", False),
                    "authed": reach.get("authed", False),
                    "detail": reach.get("detail", ""),
                    "discovery": reach.get("discovery")})
    return out


@app.post("/api/mcp")
async def post_mcp(req: Request):
    b = await req.json()
    add_server(b["name"], b["url"], b.get("auth", "oauth"), b.get("enabled", True), b.get("description", ""))
    return {"ok": True, "servers": list_servers()}


@app.delete("/api/mcp/{name}")
def del_mcp(name: str):
    remove_server(name)
    return {"ok": True}


@app.post("/api/mcp/{name}/toggle")
async def toggle_mcp(name: str, req: Request):
    b = await req.json()
    mcp_set_enabled(name, bool(b.get("enabled", True)))
    return {"ok": True}


@app.get("/api/mcp/{name}/tools")
def tools_mcp(name: str):
    res = list_tools_for(get_server(name) or {"name": name, "url": ""}, use_cache=False)
    return res


@app.post("/api/mcp/{name}/call")
async def call_mcp(name: str, req: Request):
    b = await req.json()
    res = call_tool(name, b.get("tool", ""), b.get("arguments", {}))
    return res


@app.post("/api/mcp/{name}/oauth/start")
async def mcp_oauth_start(name: str):
    return oauth_start(name)


@app.post("/api/mcp/{name}/oauth/poll")
async def mcp_oauth_poll(name: str, req: Request):
    b = await req.json()
    return oauth_poll(name, b.get("state", ""), timeout=int(b.get("timeout", 120)))


@app.post("/api/mcp/{name}/token")
async def mcp_set_token(name: str, req: Request):
    b = await req.json()
    set_token(name, b.get("token", ""))
    return {"ok": True}


# ---------------- 定时任务 ----------------

@app.get("/api/cron")
def get_cron():
    return scheduler.get_jobs()


@app.post("/api/cron")
async def post_cron(req: Request):
    b = await req.json()
    jid = scheduler.add_job(b.get("name", "定时任务"), b.get("schedule", "0 9 * * *"),
                            b.get("task", "daily_scan_report"), b.get("enabled", True),
                            persona=b.get("persona") or "")
    return {"ok": True, "id": jid}


@app.delete("/api/cron/{jid}")
def del_cron(jid: str):
    scheduler.remove_job(jid)
    return {"ok": True}


@app.post("/api/cron/{jid}/toggle")
async def toggle_cron(jid: str, req: Request):
    b = await req.json()
    scheduler.set_job_enabled(jid, bool(b.get("enabled", True)))
    return {"ok": True}


@app.post("/api/cron/{jid}/run")
async def run_cron_now(jid: str):
    """立即试运行一个定时任务，并把本次执行状态写回。"""
    job = scheduler.get_job(jid)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        summary = scheduler.run_job(job)
        status = "ok"
    except Exception as e:
        summary = str(e)
        status = "error"
    jobs = scheduler.get_jobs()
    for j in jobs:
        if j["id"] == jid:
            j["last_run"] = time.time()
            j["last_status"] = status
    scheduler.save_jobs(jobs)
    return {"ok": status == "ok", "status": status, "summary": summary[:1200]}


# ---------------- Agentic Wallet（baw CLI） ----------------

@app.get("/api/wallet")
def get_wallet(force: bool = False):
    return wallet_client.get_wallet_state(force=force)


@app.post("/api/wallet/run")
async def wallet_run(req: Request):
    b = await req.json()
    return wallet_client.run_command(b.get("cmd", ""))


@app.post("/api/wallet/install")
async def wallet_install():
    """v1.2.11 起弃用：baw 已内置于 APP runtime/，无需 npm 安装。前端会隐藏对应入口。"""
    return {
        "ok": False,
        "deprecated": True,
        "detail": "v1.2.11 起 baw CLI 已内置于 APP，无需 npm 安装。Node + @binance/agentic-wallet 随 APP 一起发布，开箱即用。",
    }


@app.get("/api/wallet/runtime")
def wallet_runtime():
    """v1.2.11：返回 runtime 内置状态（Node + @binance/agentic-wallet 是否就绪）。"""
    import wallet_runtime as _wr
    return _wr.wallet_runtime_status()


# ---- Agent 钱包：Binance App 扫码登录（真实 baw auth 流程） ----

@app.post("/api/wallet/signin")
async def wallet_signin():
    """发起扫码登录：返回 urlForWeb（渲染成二维码）+ pairingCode，供 Binance App 扫码。"""
    return wallet_client.signin()


@app.post("/api/wallet/verify")
async def wallet_verify(req: Request):
    b = await req.json()
    return wallet_client.verify(b.get("qrCodeId", ""), wait=int(b.get("wait", 10)))


@app.post("/api/wallet/signout")
async def wallet_signout():
    return wallet_client.signout()


# ---- Campaign / bStock 大赛（按 references/campaign.md 的过期开关返回状态） ----
@app.get("/api/wallet/campaign")
def wallet_campaign():
    """返回 bStock 大赛当前状态。来源：references/campaign.md 的固定活动窗口。
    注意：活动窗口外（current_utc > ends_utc）一律返回 expired=true，前端只展示历史规则留档，不再提示操作建议。"""
    from datetime import datetime, timezone
    starts = datetime(2026, 8, 17, 9, 0, 0, tzinfo=timezone.utc)
    ends = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    active = starts <= now < ends
    return {
        "name": "bStock AI Trading Competition",
        "operator": "Binance Wallet × bStocks × BNB Chain Agent Studio × CoinMarketCap",
        "starts_at": starts.isoformat(),
        "ends_at": ends.isoformat(),
        "server_now": now.isoformat(),
        "active": active,
        "days_remaining": max(0, (ends - now).days) if active else 0,
        "rule_doc": ".agents/skills/binance-agentic-wallet/references/campaign.md",
        "official_page": "https://web3.binance.com/en/dev-docs/products/agentic-wallet/use-cases/campaigns/bstock-pnl-contest",
        "rules_summary": [
            "只统计活动期内、通过 Agentic Wallet 买入的 bStock（type=3 后缀 B）的已实现 PnL",
            "支付代币仅 BNB / USDT / USDC / U / USD1 五种；其它支付代币买入不计入",
            "CMC x402 调用与 BNB Chain Agent Studio Stock Analyze 调用各需 ≥ 3 次（每次均收费）",
            "Realized PnL 按 FIFO 逐批次撮合计算，未平仓不计入；活动结束前请自行决定平仓时机",
            "买入需 BNB 付 gas，AI 调用免 gas；勿将 BNB 全部兑换导致首笔交易失败",
        ],
        "tier_milestones_source": "https://web3.binance.com/en/dev-docs/products/agentic-wallet/use-cases/campaigns/bstock-pnl-contest",
    }


@app.get("/api/wallet/qr")
def wallet_qr(text: str = "", box: int = 6):
    """把文本（urlForWeb）渲染成二维码 SVG，返回 {status, svg}。"""
    return wallet_client.qr_svg(text, box=box)


# ---- 链上钱包（CEX，API Key + Secret）：只读资产 ----

@app.get("/api/wallet/cex/status")
def cex_status():
    return {"configured": cex_wallet.configured(),
            "masked_key": cex_wallet.masked_key(),
            "cli": binance_cli.profile_status()}


@app.post("/api/wallet/cex/connect")
async def cex_connect(req: Request):
    """用 API Key + Secret 登录：先真实校验（GET /api/v3/account），成功才落库，
    并把同一把 Key 同步为 binance-cli 交易 profile（main · prod），供真实下单使用。"""
    b = await req.json()
    k = (b.get("api_key") or "").strip()
    s = (b.get("secret") or "").strip()
    if not k or not s:
        return {"status": "error", "code": "missing_keys", "message": "API Key 与 Secret 不能为空。"}
    # 校验通过前不落库
    r = cex_wallet.account_summary(keys=(k, s), force=True)
    if r.get("status") != "ok":
        return r
    cex_wallet.save_keys(k, s)
    # 同步交易 CLI profile（真实下单通道）；失败不阻断只读连接，仅提示
    cli = binance_cli.sync_profile(k, s)
    r["connected"] = True
    r["cli"] = cli
    r["message"] = (
        "币安 CEX（交易所账户）已连接，密钥已保存到本机。"
        + (" 交易 CLI profile 已就绪（main · prod）：可真实执行现货 / 合约 / 闪兑，下单前会要求 CONFIRM。"
           if cli.get("ok") else f" 提示：交易 CLI 同步失败（{cli.get('error') or 'binance-cli 未安装'}），当前仅只读。")
    )
    return r


@app.post("/api/wallet/cex/disconnect")
async def cex_disconnect():
    cex_wallet.clear_keys()
    binance_cli.remove_profile()  # 断开时一并移除本地交易 profile
    return {"ok": True, "configured": False}


@app.get("/api/wallet/cex/summary")
def cex_summary(force: bool = False):
    return cex_wallet.account_summary(force=force)


@app.get("/api/wallet/cex/openorders")
def cex_openorders(symbol: Optional[str] = None):
    """签名拉 /api/v3/openOrders —— 真实挂单（无交易权限也会成功，因为 /openOrders 属于读取）。"""
    if not cex_wallet.configured():
        return {"status": "error", "code": "not_configured",
                "orders": [], "message": "尚未配置 Binance API Key / Secret。"}
    api_key, secret = cex_wallet._stored()
    try:
        if symbol:
            orders = cex_wallet._get_signed("/api/v3/openOrders", {"symbol": symbol}, api_key, secret)
        else:
            orders = cex_wallet._get_signed("/api/v3/openOrders", {}, api_key, secret)
        return {"status": "ok", "orders": orders if isinstance(orders, list) else []}
    except ValueError as e:
        msg = str(e)
        if "IP_PERM" in msg:
            return {"status": "error", "code": "ip_or_permission", "orders": [],
                    "message": "挂单接口需要 API Key 启用'读取'权限（-2015）。"}
        if "KEY_FORMAT" in msg:
            return {"status": "error", "code": "wrong_key_type", "orders": [],
                    "message": "Key 类型不被识别（-2014）。"}
        return {"status": "error", "code": "api_error", "orders": [], "message": msg[:200]}
    except Exception as e:
        return {"status": "error", "code": "network", "orders": [], "message": str(e)[:200]}


@app.get("/api/wallet/cex/trades")
def cex_trades(symbol: Optional[str] = None, limit: int = 50):
    """签名拉 /api/v3/myTrades —— 真实历史成交。
    symbol 可选：不传时根据账户非零资产自动聚合所有 USDT 交易对的最近成交合并展示。"""
    if not cex_wallet.configured():
        return {"status": "error", "code": "not_configured",
                "trades": [], "message": "尚未配置 Binance API Key / Secret。"}
    api_key, secret = cex_wallet._stored()
    try:
        limit = max(1, min(int(limit or 50), 1000))
        if not symbol:
            # 自动聚合：账户里非零资产 × USDT 交易对 → 多个 myTrades 合并去重
            try:
                acct = cex_wallet._get_signed("/api/v3/account", {}, api_key, secret)
                non_zero = []
                for b in (acct.get("balances") or []):
                    try:
                        free = float(b.get("free", 0) or 0)
                        locked = float(b.get("locked", 0) or 0)
                    except Exception:
                        continue
                    if (free + locked) > 0:
                        non_zero.append(b.get("asset", ""))
                # 配对交易对：USDT / USDC / FDUSD / BTC / ETH 锚定（USDT 为主）
                QUOTES = ["USDT", "USDC", "FDUSD", "BTC", "ETH"]
                syms = []
                for asset in non_zero:
                    if asset in ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH"):
                        continue
                    for q in QUOTES:
                        if asset != q:
                            syms.append(f"{asset}{q}")
                # 去重 + 上限（避免一次过百次签名）
                seen = set()
                uniq_syms = []
                for s in syms:
                    if s not in seen:
                        seen.add(s); uniq_syms.append(s)
                uniq_syms = uniq_syms[:30]
                all_trades: list = []
                for s in uniq_syms:
                    try:
                        part = cex_wallet._get_signed("/api/v3/myTrades",
                                                     {"symbol": s, "limit": 10},
                                                     api_key, secret)
                        if isinstance(part, list):
                            all_trades.extend(part)
                    except Exception:
                        # 单个 symbol 失败不影响汇总
                        continue
                # 按 id 去重 + 按 time 倒序，截 limit
                seen_id = set(); uniq = []
                for tr in sorted(all_trades, key=lambda x: -(int(x.get("time") or 0))):
                    tid = tr.get("id")
                    if tid is None or tid in seen_id:
                        continue
                    seen_id.add(tid); uniq.append(tr)
                uniq = uniq[:limit]
                return {"status": "ok", "trades": uniq,
                        "aggregated": True, "scanned": uniq_syms,
                        "message": (f"已聚合账户 {len(uniq_syms)} 个非零 USDT 交易对"
                                    f"（含 {len(uniq)} 笔最近成交，按时间倒序）。"
                                    if uniq else "账户非零资产暂无成交记录。")}
            except ValueError as e:
                msg = str(e)
                if "IP_PERM" in msg:
                    return {"status": "error", "code": "ip_or_permission", "trades": [],
                            "message": "成交接口需要 API Key 启用'读取'权限（-2015）。"}
                if "KEY_FORMAT" in msg:
                    return {"status": "error", "code": "wrong_key_type", "trades": [],
                            "message": "Key 类型不被识别（-2014）。"}
                return {"status": "error", "code": "api_error", "trades": [], "message": msg[:200]}
            except Exception as e:
                # 账户接口拉取失败时回退到旧提示（不致命）
                return {"status": "ok", "trades": [], "need_symbol": True,
                        "message": f"账户接口暂不可用（{str(e)[:80]}），请提供 ?symbol=BTCUSDT 等具体交易对。"}

        trades = cex_wallet._get_signed("/api/v3/myTrades",
                                        {"symbol": symbol, "limit": limit},
                                        api_key, secret)
        return {"status": "ok", "trades": trades if isinstance(trades, list) else []}
    except ValueError as e:
        msg = str(e)
        if "IP_PERM" in msg:
            return {"status": "error", "code": "ip_or_permission", "trades": [],
                    "message": "成交接口需要 API Key 启用'读取'权限（-2015）。"}
        if "KEY_FORMAT" in msg:
            return {"status": "error", "code": "wrong_key_type", "trades": [],
                    "message": "Key 类型不被识别（-2014）。"}
        return {"status": "error", "code": "api_error", "trades": [], "message": msg[:200]}
    except Exception as e:
        return {"status": "error", "code": "network", "trades": [], "message": str(e)[:200]}


@app.get("/api/wallet/cex/allorders")
def cex_allorders(symbol: str, limit: int = 50):
    """签名拉 /api/v3/allOrders —— 历史订单（含已成交/已撤销/已过期）。"""
    if not cex_wallet.configured():
        return {"status": "error", "code": "not_configured",
                "orders": [], "message": "尚未配置 Binance API Key / Secret。"}
    api_key, secret = cex_wallet._stored()
    try:
        limit = max(1, min(int(limit or 50), 1000))
        orders = cex_wallet._get_signed("/api/v3/allOrders",
                                         {"symbol": symbol, "limit": limit},
                                         api_key, secret)
        return {"status": "ok", "orders": orders if isinstance(orders, list) else []}
    except ValueError as e:
        msg = str(e)
        if "IP_PERM" in msg:
            return {"status": "error", "code": "ip_or_permission", "orders": [],
                    "message": "订单接口需要 API Key 启用'读取'权限（-2015）。"}
        if "KEY_FORMAT" in msg:
            return {"status": "error", "code": "wrong_key_type", "orders": [],
                    "message": "Key 类型不被识别（-2014）。"}
        return {"status": "error", "code": "api_error", "orders": [], "message": msg[:200]}
    except Exception as e:
        return {"status": "error", "code": "network", "orders": [], "message": str(e)[:200]}


# ---------------- 自动更新（GitHub Releases：检查 / 后台下载 / 应用重启） ----------------
#   v1.2.11 曾砍成「只检查 + 打开下载页」；v1.2.13 按用户要求恢复应用内自动更新：
#     check  → download（后台线程 + 前端轮询进度）→ apply（DETACHED PS 脚本：
#     等 Electron 退出 → 杀残留 → 备份 WORKSPACE → 整目录替换 → 还原 WORKSPACE → 重启）。
#   前端任何一步失败都会降级给「打开下载页」浏览器兜底按钮。

@app.get("/api/update/check")
def update_check():
    """对比本地版本与 GitHub Release latest。网络失败给 error 字段，不抛。"""
    return updater.check()


@app.post("/api/update/download")
async def update_download(req: Request):
    """后台线程下载最新便携 zip 到 update-cache；返回立即，进度走 /api/update/status。"""
    b = await req.json()
    url = b.get("url", "")
    if not url:
        rel = updater.check()
        url = (rel.get("asset") or {}).get("url", "")
        if not url:
            return {"ok": False, "error": rel.get("error") or "未找到可下载的发布资产。"}
    # v1.3.6：URL 白名单真正启用（此前 is_trusted_asset_url 定义了但从未被调用）——
    # 仅允许本仓库 GitHub Releases 直链 / 官方 API / 签名 CDN，防恶意网页诱导下载任意 zip。
    if not updater.is_trusted_asset_url(url):
        return {"ok": False, "error": "不受信任的更新源，已拒绝下载。"}
    return updater.start_download(url)


@app.get("/api/update/status")
def update_status():
    return updater.download_status()


@app.post("/api/update/apply")
async def update_apply(req: Request):
    """应用更新：生成 PS 脚本 → DETACHED 启动 → 前端随后关窗触发替换重启。"""
    b = await req.json()
    wait_pid = int(b.get("pid") or 0)
    st = updater.download_status()
    zip_path = b.get("zip") or st.get("path") or ""
    if not zip_path or not os.path.exists(zip_path):
        return {"ok": False, "error": "更新包不存在，请先完成下载。"}
    # v1.3.6：zip 必须位于本机 update-cache 目录内（apply 收任意路径 = 替换任意目录的隐患）
    if not updater._path_inside(os.path.abspath(zip_path), updater.update_cache_dir()):
        return {"ok": False, "error": "更新包路径不受信任（仅允许 update-cache 内的包），已中止。"}
    return updater.apply(zip_path, wait_pid)


# ---- 链上钱包（Binance Web3 Wallet API，BX- Key）：官方连接器桥 ----

@app.get("/api/wallet/web3/status")
def web3_status():
    return web3_wallet.status()


@app.post("/api/wallet/web3/connect")
async def web3_connect(req: Request):
    """用 BX- API Key + Secret 连接：先请求网关余额接口做鉴权验证，通过才落库。"""
    b = await req.json()
    return web3_wallet.connect(b.get("api_key", ""), b.get("secret", ""))


@app.post("/api/wallet/web3/disconnect")
async def web3_disconnect():
    web3_wallet.clear_keys()
    return {"ok": True, "configured": False}


@app.post("/api/wallet/web3/balance")
async def web3_balance(req: Request):
    b = await req.json()
    return web3_wallet.balance(
        address=b.get("address", ""),
        chains=b.get("chains") or ["56"],
        page=int(b.get("page", 1)),
        page_size=int(b.get("pageSize", 50)),
    )


@app.get("/api/wallet/web3/agent-addresses")
def web3_agent_addresses():
    """从 Agentic Wallet（baw）读 MPC 多链地址，供链上钱包直接填。"""
    return web3_wallet.agent_addresses()


# ---------------- x402 / B402 机器支付 ----------------

@app.get("/api/x402/supported")
def x402_supported():
    return get_supported_configs()


@app.post("/api/x402/demo")
async def x402_demo(req: Request):
    b = await req.json()
    return demo_x402_flow(asset=b.get("asset", "USDT"), amount=str(b.get("amount", "0.10")))


# ---------------- Skills Hub ----------------

@app.get("/api/skills")
def get_skills():
    cat = get_catalog()
    return [{**v, "name": k} for k, v in cat.items()]


@app.post("/api/skills/install")
async def skills_install(req: Request):
    from starlette.concurrency import run_in_threadpool
    b = await req.json()
    # npx 安装最长 240s，必须丢线程池 —— 否则阻塞事件循环，期间全部接口无响应
    return await run_in_threadpool(install_skill, b.get("key", ""))


@app.post("/api/skills/install-wallet-skills")
async def skills_install_wallet():
    """一键安装官方 7 个 Wallet Skills（docs/products/wallet-skills/supported-skills）。
    仅安装本机未装的；每个独立子进程，不会因单个失败中断。"""
    from src import skills_client as _sc
    from starlette.concurrency import run_in_threadpool
    targets = sorted(_sc.WALLET_SKILLS)
    installed_now = set(_sc.list_installed())
    results = []

    def _install_all():
        out = []
        for k in targets:
            if k in installed_now:
                out.append({"key": k, "status": "skipped", "detail": "已安装"})
                continue
            r = _sc.install_skill(k)
            out.append({"key": k, "status": r.get("status"),
                        "detail": (r.get("stderr") or r.get("detail") or r.get("stdout") or "")[-200:]})
        return out

    results = await run_in_threadpool(_install_all)
    return {"status": "ok", "total": len(targets), "results": results}


@app.post("/api/skills/run")
async def skills_run(req: Request):
    from starlette.concurrency import run_in_threadpool
    b = await req.json()
    # 技能子进程最长 120s，必须丢线程池 —— 同步等待会阻塞事件循环：
    # 技能 CLI 回调本后端取行情数据时后端已无法响应 → CLI fetch 挂死到自身超时，
    # 表现为「技能根本无法使用/永远转圈」（v1.5.7 根因之一）。
    return await run_in_threadpool(run_skill, b.get("key", ""), b.get("args", ""))


@app.post("/api/skills/remove")
async def skills_remove(req: Request):
    b = await req.json()
    return remove_skill(b.get("key", ""))


# ---------------- 技能/baw 自动更新（v1.3.9） ----------------

import skill_updater  # noqa: E402


@app.get("/api/skills/updates")
def skills_updates(refresh: int = 0):
    """技能更新状态。refresh=1 强制打 npm 查最新（默认 1h 缓存内复用）。"""
    try:
        snap = skill_updater.check(force=bool(refresh))
        snap.pop("_lock", None)
        return snap
    except Exception as e:
        return {"phase": "error", "message": str(e)[:200],
                "baw": {"installed": "", "latest": "", "available": False},
                "skills": {"installed": [], "updated": [], "failed": [], "total": 0},
                "last_check": 0, "last_updated": 0}


@app.post("/api/skills/update")
async def skills_update(req: Request):
    """手动触发更新：scope = baw | skills | all（更新中重复调用幂等忽略）。"""
    b = await req.json()
    return skill_updater.start_update((b.get("scope") or "all").strip())


# 启动后台自动更新守护（45s 后首查 → 有更新自动应用 → 每 6h 循环）
skill_updater.ensure_background()


@app.get("/api/bots/activity")
def bot_activity():
    """每个 Agent 专属会话的最后活动（assistant 消息时间戳），供前端算未读徽标。"""
    return {"activity": state.bot_activity()}


@app.post("/api/bots/reply")
async def bots_group_reply(req: Request):
    """群聊：@提及的每个 Agent 依次用各自 persona 回答，追加到同一会话并持久化。

    body: { conversation_id, message, to: [botName,...], text? }
    """
    try:
        body = json.loads((await req.body()).decode("utf-8", "replace"))
    except Exception:
        body = {}
    conv_id = body.get("conversation_id")
    to = body.get("to") or []
    message = (body.get("message") or "").strip()
    if not to or not message:
        return JSONResponse({"error": "need message & to"}, status_code=400)
    to = [n for n in to if isinstance(n, str)][:4]
    if not conv_id:
        conv_id = state.new_conversation(title=f"Bot Chat · {message[:18]}", persona="__group__")
    state.add_message(conv_id, "user", message)
    state.touch_conversation(conv_id)

    def gen():
        for name in to:
            ag = state.get_agent_by_name(name)
            if not ag:
                yield json.dumps({"type": "bot", "name": name, "error": "unknown agent"}, ensure_ascii=False) + "\n"
                continue
            persona = {"name": ag["name"], "title": ag["title"], "description": ag["description"],
                       "avatar": ag["avatar"], "color": ag["color"], "config": ag.get("config") or {}}
            system = agent_core._build_system(persona)
            prompt = f"用户发来群聊消息：\n「{message}」\n\n请以你的身份给出简短回复（2-4 句）。"
            reply = ""
            try:
                reply = (llm.chat(system, prompt) or "").strip()
            except Exception as e:
                reply = f"(调用失败: {e})"
            state.add_message(conv_id, "assistant", reply,
                              data={"persona": ag["name"], "intent": "group_bot"})
            yield json.dumps({"type": "bot", "name": ag["name"], "avatar": ag["avatar"],
                              "color": ag["color"], "text": reply}, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "done", "conversation_id": conv_id}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/api/bots")
def list_bots():
    """Bots = Hermes 式 Agent 档案（one chat per agent）。"""
    state.seed_default_agents()
    return {"agents": state.list_agents()}


@app.post("/api/bots")
async def create_bot(req: Request):
    b = await req.json()
    aid = state.upsert_agent(
        name=(b.get("name") or "").strip()[:40],
        title=(b.get("title") or "").strip()[:60],
        description=(b.get("description") or "").strip()[:300],
        avatar=(b.get("avatar") or "🤖"),
        color=(b.get("color") or "#F0B90B"),
        config=b.get("config") or {},
    )
    return state.get_agent(aid)


@app.post("/api/bots/import")
async def import_bot(req: Request):
    """导入一个 bot.md 文本 → 写盘为文件式 Bot 包。body: {md: string} 或 {content: string}"""
    from bot_host import import_bot as _imp
    try:
        body = await req.json()
    except Exception:
        body = {}
    md = (body.get("md") or body.get("content") or "").strip()
    if not md:
        return JSONResponse({"error": "empty md"}, status_code=400)
    ag = _imp(md)
    return ag or JSONResponse({"error": "parse failed"}, status_code=400)


@app.post("/api/bots/{bid}")
async def update_bot(bid: str, req: Request):
    b = await req.json()
    state.upsert_agent(
        aid=bid,
        name=(b.get("name") or "").strip()[:40],
        title=(b.get("title") or "").strip()[:60],
        description=(b.get("description") or "").strip()[:300],
        avatar=(b.get("avatar") or "🤖"),
        color=(b.get("color") or "#F0B90B"),
        config=b.get("config") or {},
    )
    return state.get_agent(bid)


@app.delete("/api/bots/{bid}")
async def delete_bot(bid: str):
    state.delete_agent(bid)
    return {"ok": True}


@app.get("/api/bots/{bid}/export")
def export_bot(bid: str):
    """导出 Bot 包为 bot.md（可直接分享/导入）。"""
    from bot_host import export_bot as _exp
    md = _exp(bid)
    if not md:
        return JSONResponse({"error": "not found"}, status_code=404)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(md, media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="bot-{bid}.md"'})


# ---------------- 插件（Hermes 插件 SDK：plugins/<id>/plugin.json + main.py） ----------------

@app.get("/api/plugins")
def list_plugin_endpoint():
    """列出已安装插件（manifest + 启停 + 命令数）。"""
    enabled_map = json.loads(state.get_setting("plugins", "") or "{}")
    out = []
    for man in plugin_host.list_plugins():
        pid = man.get("id")
        out.append({
            "id": pid, "name": man.get("name", pid), "version": man.get("version", ""),
            "description": man.get("description", ""), "author": man.get("author", ""),
            "commands": [(c.get("name"), c.get("description", "")) for c in man.get("commands") or []],
            "enabled": enabled_map.get(pid, True),
        })
    return out


@app.post("/api/plugins")
async def toggle_plugin_endpoint(req: Request):
    """启停插件（对 LLM 工具注入生效）。"""
    b = await req.json()
    enabled_map = json.loads(state.get_setting("plugins", "") or "{}")
    if "name" in b and "enabled" in b:
        enabled_map[b["name"]] = bool(b["enabled"])
    state.set_setting("plugins", json.dumps(enabled_map, ensure_ascii=False))
    return {"ok": True}


@app.post("/api/plugins/{pid}/command")
async def plugin_command_endpoint(pid: str, req: Request):
    b = await req.json()
    res = plugin_host.exec_command(pid, b.get("name", ""), b.get("params") or {})
    return res


# ---------------- 币安广场（发文台账，只读展示） ----------------
# 广场 OpenAPI 官方只发不读 → 页面展示的是本机每次真实发布后落下的本地台账
# （data/square_posts.json），由 agent_core / skills_client 的 square-post 执行埋点写入。

@app.get("/api/square/posts")
def square_posts_endpoint():
    import square_store
    try:
        return square_store.payload()
    except Exception as e:
        return {"ok": False, "error": str(e), "posts": [], "stats": {},
                "key": {"present": False, "masked": ""}}


@app.post("/api/square/posts/delete")
def square_posts_delete_endpoint(payload: dict = Body(default_factory=dict)):
    """v1.5.35：删除广场台账条目。仅删除本地数据，不会调用币安 API。
    body: { ids: [string, ...] }
    返回: { ok, deleted, missing }。
    """
    import square_store
    try:
        ids = payload.get("ids")
        if not isinstance(ids, list):
            return {"ok": False, "error": "ids 必须是数组", "deleted": 0, "missing": []}
        r = square_store.delete_records(ids)
        return {"ok": True, "deleted": r["deleted"], "missing": r["missing"]}
    except Exception as e:
        return {"ok": False, "error": str(e), "deleted": 0, "missing": []}


@app.get("/api/square/key")
def square_key_endpoint():
    import square_store
    return square_store.square_key_status()


@app.post("/api/square/connect")
async def square_connect_endpoint(req: Request):
    """保存 Square OpenAPI Key 到 ~/.config/binance-square/openapi-key（0600）。"""
    import square_store
    try:
        b = await req.json()
    except Exception:
        b = {}
    key = (b.get("api_key") or "").strip()
    try:
        st = square_store.save_key(key)
        return {"ok": True, "key": st}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/square/disconnect")
def square_disconnect_endpoint():
    """删除本地保存的 Square OpenAPI Key。"""
    import square_store
    try:
        st = square_store.clear_key()
        return {"ok": True, "key": st}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}


# 托管前端构建产物（Vite 输出的 dist），使 /assets/* 等静态资源可访问。
# 必须注册在所有 @app.get/@app.post 路由之后，否则 mount("/") 会拦截 /api/*。
DIST_DIR = os.path.join(APP_DIR, "frontend", "dist")
if os.path.isdir(DIST_DIR):
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="dist")


def run_serve(preferred_port: int = 8080) -> None:
    """启动 uvicorn。若首选端口已被其它进程占用（重复启动 / 端口冲突），自动向后探测空闲端口，
    并打印清晰中文提示 —— 避免裸报错 [Errno 10048]，也避免在对话里留下乱码 exit 信息。"""
    import socket
    import uvicorn
    chosen = None
    for port in range(preferred_port, preferred_port + 16):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                chosen = port
            except OSError:
                continue
        if chosen is not None:
            break
    if chosen is None:
        sys.exit(f"无法分配可用端口（{preferred_port}~{preferred_port + 15} 全被占用），请先关闭占用该端口的程序。")
    if chosen != preferred_port:
        print(f"[BAZZ] 端口 {preferred_port} 已被占用（可能已有一个实例在运行），改用可用端口 {chosen}："
              f"http://127.0.0.1:{chosen}", flush=True)
    # loop="none"：uvicorn>=0.30 在 win32 默认显式注入 ProactorEventLoop 工厂，
    # 会无视上面的 set_event_loop_policy；none → loop_factory=None → 回退 policy（Selector）。
    uvicorn.run(app, host="127.0.0.1", port=chosen, log_level="warning", loop="none")


if __name__ == "__main__":
    run_serve(int(os.environ.get("BAZZ_PORT", "8080")))
