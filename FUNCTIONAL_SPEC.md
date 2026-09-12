# Alpha Scout · 功能规格总表（UI 设计 Brief）

> 用途：UI 设计 Brief。每个"页面/面板"都标注了**显示什么数据 / 支持什么操作 / 走哪个后端接口 / 真实程度**。
> 约定：涨=红 `#f6465d`、跌=绿 `#0ecb81`（币圈惯例，勿反）。所有异步操作必须有加载/成功/失败反馈。下单类操作一律 `confirm-before-execute`。

> ## ⚠️ 时效声明（2026-09-12 校订）
>
> **本文档是 v1.3.x 时期的 UI 设计 Brief，不是现行实现规格。** 保留它的价值在于设计意图与页面树（§A/§D/§E 仍适用），
> 但 §B/§C 的功能状态已大面积过期。**恢复上下文请以 `PROJECT_STATUS.md`（v1.5.29）为准。**
>
> 已知过期点（已核对代码）：
> - §B1 写"9 工具" → 实际 **22 个 Agent 工具**：`scan_market` / `market_quote` / `check_risk` / `propose_trade` /
>   `run_skill` / `find_skills` / `list_skills` / `get_help` / `memory_write` / `search_history` / `todo_write` /
>   `schedule_task` / `clarify` / `delegate` / `meme_watch` / `mcp_call` / `onchain_ops` / `gateway_status` /
>   `read_file` / `write_file` / `run_command` / `fetch_url`
> - §B2 写"行情当前为固定 Top20" → 实际已是**全市场扫描（成交额 Top300）+ 妖币雷达 v2 四层模型**（v1.5.0 起）
> - §C1–C6 全部标注"规划中"，**实际均已落地**（见各节 ✅ 标记）
> - §E 写"现 LLM key 明文存 SQLite，须一并改加密" → **已修**（v1.5.29 `src/secrets.py`，Windows DPAPI）

---

## A. 系统骨架（先理解再设计）

- **后端** `desktop_app.py`（FastAPI，端口 8080）+ 业务模块在 `src/`。
- **前端** `frontend/`（React + Vite + Tailwind），`dist/` 由后端直接托管 → 打开即桌面级 Web 应用。
- **4 类凭据（务必在 UI 上区分清楚）**：
  1. LLM Key（OpenAI 兼容 / JustDoWork 网关）— 设置浮层填。
  2. Binance MCP OAuth（Agentic 子账户扫码）— MCP 页走浏览器授权。
  3. Exchange API Key+Secret（你自己的币安 API，**能动能真钱**）— 设置/交易所页填，**必须加密存储**。
  4. Agentic Wallet（baw CLI，MPC 无密钥，终端扫码）— 钱包页。

---

## B. 已实现功能（可点、已接真实接口）

### B1. 对话（AI Agent 核心）— 主页
- **显示**：会话列表（新建/切换/删除）· 聊天流（流式打字、工具卡、待确认审批卡）· 右侧"实时信号榜" + "连接状态"（行情/MCP/钱包/支付/定时）。
- **操作**：发指令（Enter 发送）· 审批卡「确认执行」· 多会话切换。
- **能力（LLM function-calling，**22 工具**）**：`scan_market` 扫描异常 · `check_risk` 风险教育 · `propose_trade` 生成带止损止盈方案(需确认才下单) · `market_quote` 查价 · `explain_x402` · `list_skills` · `onchain_ops` · `memory_write` 记长期偏好 · `get_help` 列能力 · `run_skill`/`find_skills` 技能调用 · `todo_write` 任务清单 · `schedule_task` 定时盯盘 · `clarify` 结构化追问 · `delegate` 并行子代理 · `meme_watch` 妖币阶段 · `mcp_call` MCP 网关 · `search_history` 历史检索 · `read_file`/`write_file`/`run_command`/`fetch_url` 文件与网络。
- **接口**：`POST /api/chat/stream`（NDJSON 流式）。
- **真实度**：✅ LLM 已接(OpenAI 兼容网关，真 function-calling)；✅ 行情/风险/扫描拉真实数据；⚠️ 下单需确认+需 Exchange/MPC 凭据。未配 LLM 自动走内置规则引擎兜底。

### B2. 行情 Market（~~当前为固定 Top20~~ → 已升级为**全市场雷达 v2**，v1.5.0 起）
- **接口**：`GET /api/market` → `scan_top20()`（Top20 USDT 对公开 API）+ `top_movers(5)`。
- **字段**：`symbol` · `price` · `change_pct`(24h) · `volume`(24h 额) · `funding_rate` · `direction`(多/空) · `reason` · `score`。
- **页面**：涨幅榜/跌幅榜(各8) + 全信号表(按 score 排序)。
- **真实度**：✅ 全真实。✅ 动态筛选已上线（见 C1），接口已扩为 `/api/market/radar` · `/api/market/longshort` · `/api/market/liquidations` 等。

### B3. 钱包 Wallet（Agentic Wallet / baw）
- **接口**：`GET /api/wallet`(30s 缓存+启动预热) · `POST /api/wallet/run` · `POST /api/wallet/install`。
- **显示(connected 后自动加载)**：概览卡(状态徽章 SETUP/IDLE/LIVE+版本) · 每日限额卡(兑换$50k/DeFi$100k/x402$20) · 资产余额卡 · 多链地址卡(可复制) · 支持公链卡 · 下单卡(市价兑换 from/to/amount + 限价 买/卖/symbol/price/qty)。
- **未登录引导**：未装→复制 `npm i -g @binance/agentic-wallet`+一键安装；已装未登录→复制 `baw auth signin`+"我已扫码·刷新状态"。
- **真实度**：✅ baw 已装可真实拉余额/地址/链/兑换/限价；⚠️ **登录需终端扫二维码(GUI 内无二维码，设计给"去终端扫码"引导，勿做假按钮)**。

### B4. 支付 x402 / B402
- **接口**：`GET /api/x402/supported`(真实查 Facilitator 配置) · `POST /api/x402/demo`。
- **显示**：资产下拉(USDT/USDC/U/USD1)+金额 → 4 步时间线(①卖家返回402 ②买家离线 EIP-712 签名 ③Facilitator 验证 ④链上结算)，每步 detail+verify/settle 结果；底部 `/supported` 真实配置。
- **真实度**：⚠️ 当前 **simulated 演示**(沙箱无 B402 生产权限)；真实结算需 Agentic Wallet 生产权限。**设计须明确标"演示"**。

### B5. Skills Hub
- **接口**：`GET /api/skills`(19 官方目录) · `POST /api/skills/install|run|remove`。
- **显示**：分组网格(binance 7 + binance-web3 12)+搜索；每卡标题/描述/「安装」或「运行+移除」。
- **真实度**：✅ 目录真实，安装 `npx skills add`、运行 `npx skills run` 真装真跑。

### B6. MCP 集成
- **接口**：`GET/POST /api/mcp` · `/api/mcp/{name}/tools` · `/api/mcp/{name}/oauth/start|poll` · `/api/mcp/{name}/token`。
- **显示**：server 表(名称/端点/状态/操作)+「连接 Binance」(OAuth 浏览器流)+「工具」(运行时 tools/list)+「粘贴 Token」+添加 server 表单。
- **真实度**：✅ OAuth 走真实 RFC 9728 挑战(实测拿到真实 authorize URL)；公开行情免鉴权直连；账户/交易需登录换 token。

### B7. 浮层
- **设置(LLM)**：11 家 provider 下拉(含 JustDoWork) · Base URL · API Key · 模型(可「拉取模型」) · 「测试连接」。
- **定时任务**：增删/启停 cron(到点自动扫描生成日报)。
- **长期记忆**：增删 key-value(跨会话记偏好，如"只做现货")。

---

## C. 原规划功能（**已全部落地** —— 保留原始设计意图供回溯）

### C1. 动态行情筛选（替代固定 Top20）— ✅ **已实现**（v1.5.0 全市场雷达 v2；v1.5.8 雷达独立 Tab）
- **逻辑**：拉 `/api/v3/exchangeInfo` 全量 USDT 对 + `/api/v3/ticker/24hr` 全量行情 → 按 **流动性(24h额) + 动量(24h%) + 资金费率(永续) + 波动率** 打分 → 返回"当前最合适 N 个"。
- **模式**：放量突破 / 超跌反弹 / 热度榜（UI 用分段切换）。
- **新增**：自选搜索框(输入 symbol 即时查) + 自选列表(本地存)。
- **接口**：升级 `GET /api/market?mode=&q=`。真实度 ✅ 公开 API。

### C2. 交易所页（现货 + 合约，接你自己的币安 API Key）— ✅ **已实现**（`ExchangeView.tsx` + `src/cex_wallet.py` / `src/binance_cli.py`）
- **连接**：填 API Key+Secret（**加密存储**，仅开交易/合约权限、关提现）。先 testnet 验证签名。
- **显示**：
  - 现货区：`GET /api/exchange/spot/account`(余额) · `GET /api/exchange/spot/open-orders`；下单表单( symbol/side/type/quantity/price )。
  - 合约区(U 本位)：`GET /api/exchange/futures/positions`(持仓) · 字段 `symbol` · `positionSide`(LONG/SHORT) · `leverage` · `entryPrice` · `markPrice` · `unrealizedPnl` · `liquidationPrice`；下单表单(含 `positionSide` + 杠杆滑块 + `reduceOnly` 平仓)。
- **操作**：下单(confirm) · 一键平仓 · 撤全部挂单 · 设杠杆。
- **接口(新增)**：`exchange_client.py`(HMAC 签名) + `POST /api/exchange/spot/order` · `POST /api/exchange/futures/order` · `POST /api/exchange/futures/leverage` · `POST /api/exchange/close-all`。
- **真实度**：⚠️ 需用户真实 Key + 沙箱先 testnet；**与钱包(baw)是两条独立通道，UI 必须分清楚"用哪个钱包下单"**。

### C3. 多 Agent 策略议会（最加分）— ✅ **已实现**（`CouncilView.tsx`；另有 v1.4.6 `delegate` 并行子代理）
- **触发**：对话页 "讨论 BTC 策略" 或策略议会页选标的。
- **流程**：并行 4 人设子代理(趋势猎手/风控官/流动性分析师/quant)，各拿 `scanner` 真实行情上下文独立给观点 → synthesizer 综合。
- **输出(结构化裁决)**：`bias`(多/空/观望) · `entryZone`(区间) · `stop` · `takeProfit` · `confidence`(0-1) · `disagreement`(分歧点) · `personas[]`(每人观点)。
- **接口(新增)**：`POST /api/strategy/debate {symbol,timeframe}` → 流式返回各 persona 观点 + 最终裁决。
- **真实度**：✅ 复用 LLM 层；⚠️ 需 LLM 已配。

### C4. 设置统管 + 各域定时任务 — ✅ **已实现**（`AdminPanels.tsx` 统管 Cron/MCP + `src/scheduler.py`；v1.4.5 起 cron 支持自定义 prompt / update 动作）
- **设置页重构**：MCP(增删 server) · x402(默认资产/演示开关) · Skills(自动化开关) 收进统一控制面板。
- **定时任务升级**：从单一 cron → 多任务，每任务带 `domain` 标签（market 日报 / run skill / monitor x402 / strategy 议会），可独立启停+下次运行时间。
- **接口**：升级 `GET/POST /api/cron` 支持 `domain` 字段。

### C5. 账户持仓/盈亏看板 + 风险熔断（靠 C2 client 长出）— ✅ **已实现**（`PanicHaltModal.tsx` 一键熔断 + `src/risk_guard.py` + `src/order_tracker.py` 持仓盈亏/强平价跟踪）
- **看板**：现货+合约持仓汇总、未实现盈亏、强平价、总资产估值(USDT)。
- **熔断**：风控 Agent 可触发 `executor` 真平仓/撤全部挂单；用户可设"单标的亏损 X% 自动平仓"规则。
- **接口**：复用 C2 的 positions/close-all + 新增 `POST /api/risk/circuit`。

### C6. 信号质量回看（轻量）— ✅ **已实现**（`src/radar_tracker.py`：妖币追踪战绩 moon/dump/expired + 10x 仓位模拟 + 失败复盘；v1.5.3 / v1.5.8）
- 每条信号落库(标的/方向/价/时间) · N 天后回看命中率。
- **接口**：`GET /api/signals/history` · `GET /api/signals/stats`。

---

## D. 建议页面树（设计稿可直接套）

```
对话 (主页)          会话列表 | 聊天流 | 实时信号榜 | 连接状态
行情                  动态筛选(三模式) | 自选搜索 | 涨幅/跌幅榜 | 全信号表
钱包 (Agentic/baw)   概览 | 限额 | 余额 | 多链地址 | 链 | 兑换/限价下单
交易所 (你的API)      连接(加密Key) | 现货账户/下单 | 合约持仓/下单/杠杆 | 一键平仓
策略议会             选标的+周期 | 4 persona 观点流 | 结构化裁决卡
设置 (统管)          LLM | MCP | x402 | Skills | 定时任务(domain)
浮层                 长期记忆 | (设置可常驻为页)
```

## E. 安全红线（设计/实现都别破）
- Exchange API Key/Secret **绝不明文存**（✅ 已修：v1.5.29 `src/secrets.py` 用 Windows DPAPI 加密 LLM key / API Key+Secret / W3 密钥 / MCP token，旧明文自动迁移）。
- 下单/平仓/撤单一律二次确认。
- 钱包登录二维码只能在终端扫，GUI 内给引导别造假按钮。
- x402 演示须标"演示"，不伪装已成交流水。
