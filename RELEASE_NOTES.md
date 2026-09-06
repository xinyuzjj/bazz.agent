# BAZZ.AGENT v1.2.3

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

本地优先的币安 AI 交易桌面前端：妖币/点火雷达 + 多模型 Agent 对话 + 带止损止盈的交易方案 + CEX 连接 + Agentic Wallet 链上操作 + 广场发文。行情扫描与 Agent 记忆**不需要任何 API Key**；真实下单/写盘一律先确认后执行。

## 🆕 v1.2.3 更新要点

1. **对话不再被「切页面」打断**：聊天视图改为常驻挂载，生成过程中切到行情/钱包/CEX 再切回，回复会完整继续，不再中断或丢失。
2. **合约行情直接可查，无需 MCP 授权**：查 U 本位永续 / 资金费率 / 合约代币一律走本地免费公开行情通道（scan_market / market_quote），不再被错误引导去 MCP OAuth 授权；MCP 仅保留给账户级私有数据（余额 / 持仓 / 真实下单）。
3. **支持纯永续上市币**：现货端查不到的币（如 SNDKUSDT）自动回退 Binance fapi 公开接口，直接返回合约价量 + 资金费率。
4. **探索轮次加深、收尾更负责**：单次生成轮次上限提升至 14；新增「收尾自查」规则（分析类请求补齐资金费率 / 量能 / 大盘强弱等维度再给结论）；多轮失败时给出 4 条具体可换路径（fapi 公开端点 / 官方 skill / fetch_url / 本地直请求），不再空话收尾。

## 🚀 快速开始（Windows 便携版）

1. 下载并解压 `BAZZ.AGENT-v1.2.3-win32-x64-portable.zip`
2. 运行 `BAZZ.AGENT.exe` —— 首次启动自动生成 `workspace/` 工作区，运行数据（会话库 / 钱包 profile / 广场台账 / 附件 / 报告）全部收口其中
3. 右上角配置 LLM（OpenAI 兼容端点，支持多模型 + 备份链），即可开始对话
4. 行情 / 妖币雷达 / 市场扫描开箱即用，无需任何 Key
5. 真实交易：设置 → 币安 CEX 填 API Key（应用内不落盘，可 sync 到 binance-cli profile）；或 Bots 面板扫码登录 Agentic Wallet（MPC 无密钥）——均需你在界面点确认才执行

## 🔒 安全设计

- 高危指令（全仓 / 清空账户等）硬拦截；下单 / 写盘默认二次确认，可信操作可勾选白名单
- 所有运行数据仅存本机 `workspace/`，私钥 / 助记词永不上传

## 📖 更多文档

- 中文说明：`README.zh-CN.md` ｜ English：`README.md`
- 产品页：`docs/index.html`（可本地打开浏览）
