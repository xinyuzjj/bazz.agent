# BAZZ.AGENT v1.3.1 大版本：交易卡片 + 双交易通道 + 行情增强

## Context（为什么做）

用户要求一次大更新 1.3.1，三个诉求：

1. **交易卡片前端没做好**：Agent 生成的「交易单」目前只是审批卡 + 一个「确认执行」按钮，缺少完整的富卡片 UI 展示交易数据。
2. **交易方式由 Agent 自动判断**：用户去叫 Agent 分析买入，Agent 会**自己决定**是从**交易所（API 密钥**，已有 infra）还是从 **Agent 钱包**哪条通道执行——不是让用户选择。**不靠 MCP、不靠 OAuth**。用户明确：交易所界面本来就有密钥输入框，所以要打通「用界面输入的密钥下单」。
3. **行情界面要更丰富**，不只是妖币雷达。用户选定新增三个板块：**24h 成交额/热度榜、资金费率板块、市场宽度/突发**。

> 关键修正（用户反馈）：交易卡片 **不做「两条执行通道让用户选」**。改为：Agent 决策通道，`signal` 里带上 Agent 选定的 `route`（`"exchange" | "wallet"`），卡片只展示「交易数据 + Agent 选定的通道徽标 + 单一确认执行」。

现状事实（已查证）：
- 后端 [agent_core.py](file:///e:/hermes_app/binance-agent-os-scout/src/agent_core.py#L458-L514) `_run_execute` 产出 `approval.action="execute_order"` + 完整 `signal`（symbol/direction/price/stop_loss/take_profit/quantity/max_loss_usdt 等）。
- 前端 [ChatView.tsx](file:///e:/hermes_app/binance-agent-os-scout/frontend/src/views/ChatView.tsx#L1494) 只有一个 `approve(m.approval)` 按钮。
- [executor.py](file:///e:/hermes_app/binance-agent-os-scout/src/executor.py#L94-L115) `confirm_and_place` 用 `.env` 密钥下限价单——但用户实际密钥在 [cex_wallet.py](file:///e:/hermes_app/binance-agent-os-scout/src/cex_wallet.py#L32-L109)（存 SQLite settings / 界面输入），二者没打通。
- 行情 [MarketsView.tsx](file:///e:/hermes_app/binance-agent-os-scout/frontend/src/views/MarketsView.tsx) 现有：顶部动量、妖币雷达、智能异动信号、涨跌幅 movers、全市场表。
- 行情后端 [scanner.py](file:///e:/hermes_app/binance-agent-os-scout/src/scanner.py) 已有 `scan_universe`、`top_movers`、`market_movers`、`get_funding_rates`、妖币雷达。

## 目标

- **A. 富交易卡片**：前端渲染完整交易数据 + Agent 选定的**执行通道徽标**（交易所密钥 / Agent 钱包）+ **单一确认执行**按钮。通道由 Agent 决策，用户不选。
- **B. 打通交易所密钥下单**：Agent 决策走交易所时，用「ExchangeView 界面输入的密钥」（非 .env）签名下真实现货订单；Agent 决策走钱包时，走现有钱包审批执行链路。
- **C. 行情增强**：后端新增 24h 成交额/热度榜、资金费率板块、市场宽度数据；前端新增对应板块并优化布局。

## 改动方案

### A. 富交易卡片（前端）
新建 `frontend/src/components/TradeCard.tsx`：
- props：`signal`、`onConfirm(signal)`、`i18n t`。
- 渲染：标的+方向徽标、**执行通道徽标**（`signal.route==="exchange" ? "交易所密钥" : "Agent 钱包"`）、现价、入场区间、止损、止盈、数量、预估最大亏损、资金费率、24h 涨跌、杠杆（如有）。
- 单一动作：`onConfirm(signal)` 确认执行（沿用现有 `approve(m.approval)`，后端按 `signal.route` 走对应通道）。
- 在 [ChatView.tsx L1466-1496](file:///e:/hermes_app/binance-agent-os-scout/frontend/src/views/ChatView.tsx#L1466-L1496) 把现有「下单详情」+单按钮替换成 `<TradeCard/>`。
- i18n [locales.ts](file:///e:/hermes_app/binance-agent-os-scout/frontend/src/i18n/locales.ts)：仿现有 `chat.ord*` 命名，新增 `trade.routeExchange` / `trade.routeWallet` / `trade.confirmExec` 等双语 key。

后端 → 前端：`signal` 增加 `route` 字段（Agent 决策），卡片读取展示。

### B. 打通交易所密钥下单（后端，Agent 决策通道）
1. **Agent 决策通道**：[agent_core.py](file:///e:/hermes_app/binance-agent-os-scout/src/agent_core.py#L458-L514) `_run_execute` 构建 `signal` 时填 `route`：
   - 申请/检查可用通道：钱包 runtime 就绪 → `wallet`；否则有交易所密钥 → `exchange`；两者皆无 → 缺密钥提示。
   - 参考 signals 里是否带 `hedge`/渠道偏好；无则按「钱包优先，缺则交易所」默认。
2. **交易所下单**（走界面密钥，不触 MCP/OAuth）：
   - [cex_wallet.py](file:///e:/hermes_app/binance-agent-os-scout/src/cex_wallet.py) 新增 `place_order(symbol, side, qty, price)` 对 `/api/v3/order` POST，复用其 HMAC 签名；并暴露 `get_keypair()`（读 SQLite settings 的 `binance_api_key/secret`，缺则回退 `.env`，与 ExchangeView 存入一致）。
   - [executor.py](file:///e:/hermes_app/binance-agent-os-scout/src/executor.py#L94-L115) `confirm_and_place` 改为优先用 cex_wallet 存的密钥（而非仅 .env）：`route==="exchange"` → `cex_wallet.place_order(...)`；`route==="wallet"` → 走钱包执行。
3. **钱包下单**：沿用现有审批/`send` 链路（baw 钱包），不改。

### C. 行情增强（后端 + 前端）
后端 [scanner.py](file:///e:/hermes_app/binance-agent-os-scout/src/scanner.py)：
- `market_breadth()`：全市场涨/跌家数、上涨占比、平均|涨跌|、极端动量标的数，用于「市场宽度/突发」。
- `funding_board(top=10)`：全市场资金费率按正/负排序分批，标记拥挤方向。
- `volume_heat(top=15)`：按 24h `quote_volume` 排序的成交额/热度榜。
- [desktop_app.py](file:///e:/hermes_app/binance-agent-os-scout/desktop_app.py) 新增 `GET /api/market/overview` 一次返回 `{breadth, funding_top, funding_flop, volume_top}`（复用 get_snapshot 的 TTL 缓存，不重复打接口）。

前端 [MarketsView.tsx](file:///e:/hermes_app/binance-agent-os-scout/frontend/src/views/MarketsView.tsx)：
- 新增三块（沿用 glass/pill/btn-ghost 风格）：
  1. **市场宽度/突发**卡：涨/跌家数、比值条、上涨占比、平均涨跌、异常波动数。
  2. **资金费率板块**：正负资金费率靠前的币列表（LONG/SHORT 拥挤度 tag）。
  3. **24h 成交额/热度榜**：成交额由高到低的币行（价格/涨跌/成交额）。
- `useEffect` 拉 `/api/market/overview`（并入现有 30s 轮询），顶部加新鲜度角标。
- 新增 i18n key（`markets.breadth*` / `markets.fundingBlock*` / `markets.volumeHeat*`）双语。

## 验证
- 前端：`cd frontend && npm run build` 通过（TS 编译）。
- 后端：`python -c "import sys;sys.path.insert(0,'src');import scanner;print(scanner.market_breadth());print(scanner.funding_board(5));print(scanner.volume_heat(5))"` 冒烟返回非空。
- 下单：`python -m py_compile src/cex_wallet.py src/executor.py`；用 `cex_wallet.place_order` 对测试 symbol 下单需真实密钥，仅确认签名串生成正确（不实际下单）。
- 端到端：app 内让 Agent 生成交易 → 卡片显示完整数据 + Agent 选定的单一执行通道徽标 + 单个「确认执行」按钮 → 按 `signal.route` 走交易所密钥/钱包对应通道。

## 发布
- `package.json` → `1.3.1`，写 RELEASE_NOTES.md，commit + push main + re-tag `v1.3.1`（GitHub 443 不稳时带重试）。