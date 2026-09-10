# BAZZ.AGENT 项目状态交接（2026-09-10 会话收尾）

> 用途：开新任务/新会话前快速恢复上下文。读完即可继续开发，无需翻旧对话。
> 项目路径：`e:\hermes_app\binance-agent-os-scout` ｜ 仓库：`github.com/xinyuzjj/bazz.agent`

---

## 一、项目概览

- **形态**：Windows 桌面端（Electron 壳）+ FastAPI Python 后端 + TS/Vite 前端
- **定位**：币安 AI 交易终端 —— Agent 对话、行情（现货/合约/股票化代币）、交易方案卡、CEX 连接、Agentic Wallet、广场发文、Skills Hub
- **当前版本**：v1.5.3（已发版：妖币追踪 + 行情页双栏终端风重设计）
- **工作区**：安装目录 `<安装根>/workspace`（state.db / proxies.json / .skill_update.json / 附件 / 日志）

## 二、近期发布版本

| 版本 | 核心内容 |
|---|---|
| **v1.5.9** | 打包版技能执行修复 + 追踪列调整：① **run_skill 白名单归一化**——`_resolve_exe0` 支持绝对路径形式的白名单可执行文件（打包版 run_skill_cmd 用内置 runtime 绝对路径 node.exe 拼命令，旧白名单只认裸 `node` → 安装版报「命令不在白名单: F:\...\node.exe」技能全挂、开发版无此问题）；basename 命中 EXE_BIN 且文件真实存在 → 折算白名单键并用回原绝对路径启动（不走 PATH），白名单外/不存在的 exe 照拒；② 历史战绩「当前涨跌幅」列改「日期」（关单 MM-DD HH:mm）；③ i18n 补齐——markets.stage.SHORT_AMBUSH（做空埋伏/Short Ambush）+ 英文缺失的 trackPnl/trackPnlTip/holding/holdingTip/trackChg/trackDate |
| **v1.5.8** | 妖币雷达独立页 + 追踪智能持有 + 失败复盘 + 做空埋伏：① **一键分析根治**——「分析」按钮 prompt 强制先调 run_skill(coin-report)，agent_core 兜底路由识别「分析/研报/走势+交易对」强推 coin-report（_COIN_ANALYSIS_PAT + _detect_coin_symbol），run_skill 描述内置技能速查并禁直连币安 API，6 个内置技能 CLI 端口回退加固（非 200 换端口）；② **行情页改版**——妖币雷达独立第 4 Tab 全宽展示（追踪面板随迁），移除现货全市场表与 24H 成交额热度榜，多空比背离 pill 标注偏多绿/偏空红+悬浮解释，修 markets.h.range 翻译缺失；③ **做空埋伏 SHORT_AMBUSH**——高位(分位≥75%+30d涨≥30%或3d涨≥15%)+滞涨(24h≤5%且1h≤1%)+过热信号(费率峰≥0.3%衰竭/taker骤降/大户比≥2/OI顶背离) → 「做空·崩跌前」归埋伏窗口组（红底），WATCH_SHORT 自动登记做空追踪；④ **追踪 10x 口径+智能持有**——FAIL_HIT=10（逆向≥10%≈10x强平线判dump，做多看跌幅/做空看涨幅），达标≥25%不再直接关单：_reversal_now 查反转因子（费率极值回落/OI脉冲/爆仓潮）现反转→落袋，否则 radar_track_hold_start 转持有模式移动止盈 TRAIL_PCT=12（自持有期极值回撤/反弹落袋，holding/hold_ext 列自动迁移），持有中逆向≥10%仍dump优先；仓位模拟 100U×10x（_sim_pnl 爆仓封底-100U）贯通「10x盈亏」列/事件payload/复盘md，达标推送 hold 事件+金pill；⑤ **失败复盘**——dump 关单自动生成（失败路径/关单因子/大盘环境/阶段教训）入 radar_tracks.review 列 + 落盘 workspace/复盘/妖币追踪复盘_YYYYMMDD.md，前端「失败复盘」pill 展开全文；⑥ 按钮断词修复（nowrap+雷达操作列重排）。test_v151_radar_track.py 更新至持有流程（达标→持有→回撤落袋/做空镜像/爆仓优先/review 断言）全过，tsc+vite build 通过 |
| **v1.5.7** | 稳定性大修（技能全线不可用根因修复）：① **Windows accept 死亡**——Proactor 循环下客户端 RST 触发 WinError 64 后 accept 循环死亡（进程活但全部新连接无响应，行情/技能页全转圈）；desktop_app 启动设 `WindowsSelectorEventLoopPolicy` + `uvicorn.run(loop="none")`（uvicorn≥0.30 显式注入 Proactor 工厂会无视 policy）；② **skills/run·install 阻塞事件循环**——async 端点内同步等待 120-240s 子进程，技能 CLI 回调后端取行情时后端无法响应 → fetch 挂死，改 `run_in_threadpool`（实测技能运行 10.9s 期间 tracks 0.02s 响应）；③ **exec_sandbox `_os` NameError**——agent 对话跑技能必失败（v1.3.9 引入），修为 `os`；④ coin-report 裸 SYMBOL 参数丢失（`report(rest)` 空 rest 报"需要 SYMBOL"）+ 裸基础资产自动补 USDT；⑤ llm `_unwrap_choice` 防网关异常回包（choices null 静默吞错）；⑥ 中文语境代币解析（`\b` 对中文无效改手工边界）+ 费率 None 防护；⑦ **行情页移除爆仓流模块**（按需求，后端 /api/market/liquidations 保留供 market-data 技能）；⑧ **妖币追踪方向感知**——radar_tracks 加 direction 列（旧库迁移默认 LONG），雷达 WATCH_SHORT 登记为做空，SHORT 跌≥20%=moon / 涨≥25%=dump（语义按方向翻转），TrackLine 增方向 pill + 当前涨跌幅列，i18n 同步清理 markets.liq.* |
| **v1.5.3** | 妖币追踪 + 行情页重设计（含原 v1.5.2 计划内容，合并发版）：① **妖币追踪**——state 新增 `radar_tracks` 表（pending 唯一索引去重）+ `src/radar_tracker.py`（record_from_radar 吃雷达点火/吸筹结果建档：发现时间/价/评分/理由；_tick 周期监控峰值/谷值/最大涨幅跌幅；_judge_outcome 判定 moon/dump/expired 并发 radar_outcome 事件）；desktop_app 起追踪线程 + `/api/market/radar/tracks` 路由；前端雷达下追踪面板（进行中/历史/战绩，TrackLine 组件，一键跳下单），Toasts 消费 radar_outcome；tests/test_v151_radar_track.py 隔离单测全过。② **行情页双栏终端风重设计**——Hero 大盘速览条（BTC/ETH/BNB/SOL 实时卡：useLiveTick 大字号+涨跌闪烁+24h 区间位置条；全市场宽度卡）+ 双栏布局（主区：雷达/追踪/异动/全市场表；右 340px sticky 侧栏：资金费率/爆仓流/多空比/成交热度）+ 维度切换条 sticky；可视化：useFlash 价格闪烁、PosBar 24h 区间条（替代最高/最低列）、VolHeatRow 成交额 bar、LsLine 多空比双段比例条；精简：费率拥挤+极值合并 Tab 面板、删领涨领跌卡、雷达说明收 tooltip；i18n zh/en 同步新增 key（markets.hero.* / fundTab.* / h.range）。tsc + vite build 通过 |
| **v1.5.1** | 修复：妖币雷达 v2 报「list index out of range」——scanner.py BTC β 残差计算在币种 15m K 线 <96 根时 range 起点为负、c15[i-1] 越界炸掉整个 /api/market/radar（前端红条、雷达空）。修复 = 窗口起点 max(1, len-96) 收敛（数据不足从可用头算，充足行为不变）。实测 force=True 返回 36 币 + 完整阶段分布。发版：commit 040983b + tag v1.5.1 → CI 5m6s 全绿，Release assets 齐全（setup.exe / portable.zip / MANIFEST / SHA256SUMS） |
| **v1.5.0** | 行情大更新·妖币雷达 v2 四层模型：① scanner 重写核心——触发层（jump_L/flow_price/5m 速度加速度/15m RVOL/振幅 → 0-99 妖币度）、确认层（OI 四象限+15m 脉冲/funding 极值峰值回落/大户持仓比/taker 比/**实时爆仓流消费**）、语义层（六阶段：吸筹→点火→垂直拉升→派发顶→崩跌→沉寂，先到先得）、过滤层（$5M 地板/新币 30 天/刷量假量/BTC β 残差/同阶段 30min 冷却）；get_monster_coins/get_ignition_coins 同源映射 v2（异常回退 v1 日K）；② market_ws 新增 `!forceOrder@arr` 爆仓流（缓冲 800 条+分钟桶，单边 ≥$1.5M/≥5 笔/≥3×20min 基线 → toast+系统通知，同向 10min 冷却；liq_recent/liq_stats/liq_symbol_stats 供确认层与前端）；③ 新路由 /api/market/radar、/api/market/longshort（大户持仓比×散户账户比+背离标记，60s TTL）、/api/market/liquidations；④ funding_board v2（极值 ≥0.30% + 翻转检测 + toast，6h 冷却）；⑤ 前端 MarketsView：雷达 v2 单次全量拉取+阶段计数 chips+行内阶段标签/妖币度/因子摘要，新增爆仓流/多空比/funding 极值三面板，Toasts 消费 liq_burst/funding_extreme/funding_flip；⑥ meme_watch 吃 v2 同源输出（阶段标签+确认因子+触发理由）；⑦ i18n 双语补齐（含 en 缺失的 v1.4.0 alert 段）；tests/test_v150_market.py 隔离单测 20 项全过 + py_compile + tsc + vite build |
| **v1.4.6** | delegate 并行子代理（学习 Hermes delegate_tool）：① `_run_delegate`——tasks=[{name, prompt}]（≤4，池 3 并发）每个子任务全新 `_run_llm_agent` 真实执行（history=None 新会话），`_filter_tool_schemas` 从 schema 层剔除 delegate 防递归（blocked_tools 参数贯通 _run_llm_agent），子任务停在 approval/clarify 时如实记录说明；② 父级只见每任务摘要（DELEGATE_SUMMARY_BUDGET=2500 截断，中间工具过程不回流）+ 每任务工具卡；DELEGATE_TIMEOUT_TOTAL=420s 总超时（as_completed timeout，超时任务标记失败、线程自然结束不强杀）；③ clarify/delegate 进 _TOOL_UNIVERSAL（Bot 白名单候选同步 +schedule_task/clarify/delegate）；隔离库单测全过（过滤/归一化/并行/blocked 传递/截断/异常/上限裁剪） |
| **v1.4.5** | 自定义定时盯盘 + 结构化追问 + 工具输出落盘（学习 Hermes）：① schedule_task 升级——`custom_prompt` 自定义任务（prompt=完整指令，到点经 `_run_custom_prompt` 无头跑真 Agent 循环带全部工具，结果写专属会话「定时任务 · <name>」）、`update` 动作（scheduler.update_job 改名/时间/类型/指令并重算 next_run）、failure_deliver 语义（失败也投递，failure_deliver=False 只记 last_error）、无人值守遇审批/追问如实记录说明；② clarify 工具——`_run_clarify` 归一化（≤3 问×4 选项、dict choice 按 label>description>text>title 展平、recommended/recommended_index），流内 `{"type":"clarify"}` 事件 + done `needs_clarify` 收口，ChatView 选择卡（I.Alert、推荐徽标、点选回传 `（追问回答）q → a`），120s 前端超时自动按「最佳判断继续」回流（send 启动即取消定时器）；③ 工具输出落盘——`TOOL_SPILL_THRESHOLD=20KB`，`_tool_msg_from_res` 统一出口（强制路径+常规路径），超限写 `workspace/spill/<ts>_<tool>.txt` 回传截断文本+句柄（read_file 可读，spill 不在 READ_BLACKLIST）。隔离库单测全过 |
| **v1.4.4** | Agent 任务清单 + 记忆报告 + 通知加固：① todo_tool（Hermes）——state `todos` 表（去重/按文本勾选/清已完成），agent 工具 `todo_write`（add/toggle/remove/list/clear_done，llm.py TOOLS + dispatch + 通用集），`todo_context()` 每轮把未完成任务注入 system prompt（防烂尾防装完成）；② `/api/memory/export?format=md`——按 kind 分组 Markdown 报告（含来源/命中/日期），JSON 导出并存，MemoryOverlay 加 MD 按钮（I.Memory）；③ 前端空 catch 补 pushToast 错误提示（SettingsView 4 处 + ChatView 新建/删除/归档/重命名会话 + CodeCard 复制），新增 common.opFailed/copyFail 双语 key。23 项隔离库单测全过 |
| **v1.4.3** | 会话搜索 + 标题自动生成（学习 Hermes）：① `state.search_conversations`（LIKE 标题+消息正文，命中片段+条数，room 排除）→ `GET /api/conversations/search`（注册在 {cid} 路由之前）→ ChatView 会话列表防抖搜索框（结果按当前 Agent 作用域过滤，🔍 命中数徽标）；② `agent_core.auto_title`——chat 流 `_persist` 末尾起后台线程，标题仍为默认截断（首条 user 消息[:28]/新对话/@档案前缀）时用 summarize 槽位生成 4-16 字标题，写库前二次校验防改名竞态，前端 send 后 5s 二刷列表；③ Agent 新工具 `search_history`（llm.py TOOLS + dispatch + _TOOL_UNIVERSAL）；④ **重要修复：流自然完成此前从不落库**（_persist 只挂 GeneratorExit/Exception，正常播完回复/记忆/标题全丢）——补 `else: _persist()`。21 项隔离库单测全过 |
| **v1.4.2** | 上下文压缩 + 循环健壮性（学习 Hermes）：① conversations 加 `ctx_summary` 列 + 游标 `ctxcur:{cid}`（settings 表）——被裁旧历史不再蒸发，增量并入持久化滚动摘要，重复请求不重烧 summarize 模型（`_history_blocks` 重写，`run_stream`/`_run_llm_agent` 加 cid 透传，desktop_app 传 conv_id）；② repetition guard——同工具+同参数执行 ≥2 次拦截并提示模型直接作答（审批类交易工具豁免）；③ 空回复重试预算限 1 次；④ 轮次将尽（MAX-2）注入「立即汇总作答」预警。7 项单测全过 |
| **v1.4.1** | 记忆系统升级（对齐 Hermes 精选式记忆）：① memory 表加 kind/source/hits 列（旧库自动迁移）；② 注入重写——按 kind 分组、3500 字符预算、提示注入清洗（`_sanitize_mem` 剥离「忽略以上/ignore previous/伪标签」）；③ auto_memorize 合并式去重（bigram ≥0.6 原地 replace）+ 敏感信息拒绝入库（API key/助记词/密码）；④ memory_write 工具升级 add/replace/remove/read 动作模型（Agent 不能删 manual 来源）；⑤ 记忆页类型徽标 + ⚡命中次数。9 项单测全过（隔离库） |
| **v1.4.0** | 行情实时化四件套：① 现货 WS 实时流（`!miniTicker@arr` 替换 30s 轮询，`src/market_ws.py` + 前端 `lib/live.ts`）；② 微渲染（拆 memo 行组件，只重渲变化的行，`MarketRows.tsx`）；③ 订单状态跟踪卡（`src/order_tracker.py` + state.db `tracked_orders` 表，交易所页展示）；④ SL/TP 接近 0.5%/触发提醒（toast + 系统通知，冷却抑制）。※ 实测：币安已下线 `!ticker@arr` 全市场数组流；fstream 合约 WS 对部分地区不推流 → 合约维持 REST 30s。CI 修复：mihomo 内核下载步需显式 `env: BAZZ_GH_TOKEN: ${{ github.token }}`（GITHUB_TOKEN 默认不注入步骤，runner 匿名 API 限额极易耗尽） |
| **v1.3.9** | 币安技能自动更新：baw CLI + Skills Hub 技能包启动 45s 后自动检查升级（每 6h 循环），技能库页更新条可手动触发；修打包版 npx/node 解析与安装 cwd 漂移 |

## 三、关键技术结论（重要，勿回退）

1. **baw 不读 HTTP(S)_PROXY**：baw 用 Node 20 全局 fetch（内置 undici），env 代理无效。
   解法 = `proxy_pool._ensure_node_preload()` 给所有 Node 子进程挂
   `NODE_OPTIONS --require runtime/proxy-preload.cjs`（undici `EnvHttpProxyAgent`）。
   有代理 env 才激活，直连无影响。**undici 有限制**：只认 HTTP 代理，纯 socks5 直连节点下 baw 仍可能失败（内核型/http 节点无此问题）。
2. **npm 剪包陷阱**：`runtime/` 无 package.json，npm 分两次装会把先装的包全剪掉（实测 74 包被剪）。
   **baw 与 undici 必须同一条命令安装**（prepare-runtime.js 与 skill_updater._update_baw 均已遵守）。
3. **打包版无 PATH node/npx**：用户机器零 Node 环境。所有 node/npx 调用必须锚定
   `workspace.NODE_EXE` / `runtime/node/npx.cmd`（skills_client `_node_cmd()/_npx_cmd()`、exec_sandbox、wallet_runtime 均已处理，新代码照此模式）。
4. **打包态路径**：`.agents` 经 PyInstaller datas 落 `_internal/.agents`；`npx skills add` 必须
   `cwd=dirname(AGENTS_DIR)`，否则装到进程 cwd 后端读不到。Agent 沙箱路径锚 `workspace.WORKSPACE`，禁用 `__file__`/cwd 直拼。
5. **内核内置链路**：CI（release.yml「下载 mihomo 内核」步）→ `BAZZ_KERNEL_EXE` → build-desktop.js
   拷到产物 `.system/kernel/mihomo.exe` → proxy_kernel `_adopt_bundled_kernel()` 接化。
6. **发版流程**：改代码 → `package.json` 升版本 → `RELEASE_NOTES.md` 顶部加节 → commit →
   `git tag v*` → push（需代理：`git -c http.proxy=http://127.0.0.1:7897 push`）→ GitHub Actions
   自动构建 portable zip + setup.exe + MANIFEST + SHA256SUMS 并发 Release。CI 全程约 5-8 分钟。
7. **其它既有约束**：全 API 带 X-BAZZ-Token 鉴权（前端 fetch 必须走 api.ts）；SQLite WAL+RLock；
   更新器只认本仓库 Release 白名单；UI 版本 ≥1.3.7 代理池在设置页折叠卡片（无独立导航）。

## 四、待办 / 优化清单（按优先级，代理池已明确冻结）

**P1 已全部完成（v1.4.0 已发布）**：行情 WS 实时流 / 微渲染 / 订单跟踪卡 / SL·TP 提醒 —— 勿重复开发。

**记忆系统已升级（v1.4.1）+ Markdown 报告导出（v1.4.4）**：kind/source/hits 分类、注入预算+清洗、合并去重、敏感过滤、memory_write 动作工具、MD 报告导出 —— 勿重复改造。

**Hermes 学习清单进度**：✅ 记忆系统（v1.4.1）、✅ 上下文持久化压缩 + 循环健壮性（v1.4.2）、✅ 会话搜索 + 会话标题自动生成（v1.4.3）、✅ Agent 任务清单 todo_tool（v1.4.4）、✅ 自定义 cron 盯盘 + clarify 结构化追问 + 工具输出落盘（v1.4.5）、✅ delegate 子代理（v1.4.6 已发布）。候选剩余（**用户暂缓，勿主动开工**）：side_question 旁问（/btw fork+禁工具+transcript 降级）、fetch_url 增强（web_result_cache 缓存/url_safety/truncate）、turn_usage token 用量展示、verification_stop 收尾验证门、思考小件（`<thinking>` 未闭合截断恢复 / 存储边界 think 标签清洗 / effort 三档旋钮）。低价值勿做：MCP/OAuth、浏览器自动化全家桶、语音 TTS、kanban/discord/飞书/HA、image/video 生成、tirith 安全全家桶。

**P2 进度**：✅ 空 catch 补错误提示（v1.4.3+4）、✅ 记忆导出 Markdown（v1.4.4）；剩余：启动加速、广场发文草稿箱/定时发布。

**P2（下一版建议）**
1. 启动加速（splash 保底 5.2s 可压到就绪即切换）
2. 广场发文草稿箱/定时发布

**★ 当前主任务：v1.5.0 行情大更新 —— ✅ 已全部实现并发版（本节降级为历史规格，供回溯）**

> 实现落点：scanner.py（get_radar_v2/_stage_of/funding_board v2/get_longshort_board）、market_ws.py（!forceOrder@arr + liq_*）、desktop_app.py（/api/market/radar|longshort|liquidations）、MarketsView.tsx + MarketRows.tsx + Toasts.tsx、agent_core meme_watch、locales.ts 双语。妖币定义 = 暴涨暴跌的极端波动币种（≠meme 币）；meme 审计仍归 meme-rush 技能域。最新规格原文见下（勿按旧规格重复开发）。

> 妖币专项调研（学术量化/开源实现/实战派三路）已汇总固化于此。**妖币定义 = 暴涨暴跌的极端波动币种（≠meme 币）**；meme/新币安全审计（DexScreener/GMGN 类）属 meme-rush 技能域，勿混入本设计。

1. **妖币雷达 v2**（`src/scanner.py` 重写核心）：四层架构 = 触发层（价格暴力）→ 确认层（杠杆/资金驱动）→ 语义层（生命周期阶段）→ 过滤层（防假信号）
   - **触发层**（短时窗 5m/15m/1h 替换现有日K 35日窗口；任一命中进扫描池）：
     - 跳跃检验（自基线）：`L = r/σ̂`（σ̂ 用滚动 EWMA 已实现波动），`|L|>3.0` 触发（学术 99.9% 极值 4.6）
     - 泵度 flow_price（edsonschlei/freqtrade-stuff 公式直接移植）：`Σ[量×(高-低)]×阴阳加权正负相减 ÷ 499根均量×均振幅`，滚动 288 根内最大值 >1000 = 近期被泵；15m 周期最佳
     - 价格速度：≥0.5%/5min 且加速度>0（StrikeChart 实测阈值）；24h 涨跌 ≥10% 入池、≥25% critical
     - 短时暴涨暴跌：5min ±3% 带量 / 1h ±5%；连续 3 根同向放量（deepalpha 形态）
     - 量能：RVOL=量/SMA20 ≥2.0 报警、≥3×60min 均值强触发；24h 振幅 ≥15% 或 rangeRatio ≥4×
     - 合成 0-99「妖币度」分数（flow_price 归一化为核心权重）
   - **确认层**（合约驱动因子，判断持续性/砸盘风险；全 /futures/data/* 免费端点）：
     - OI 四象限（openInterestHist）：价↑OI↑=新钱趋势；价↓OI↑=新空压制；价↑OI↓=轧空虚涨（崩前兆）；价↓OI↓=多杀多
     - OI 脉冲：15min +5%；24-48h +20% 而价未动 = 吸筹
     - funding（premiumIndex）：>+0.15% 过热、>+0.30% 极值、<-0.10% 空头拥挤；高位回落+价滞涨 = 顶
     - 大户多空比：>1.5 持续 48h → 回调；<0.7 → 轧空；大户/散户背离 = 主力反向（topLongShortPositionRatio / globalLongShortAccountRatio）
     - taker 买卖比：>65% 持续 = 逼空燃料；骤降 = 动能衰竭（takerlongshortRatio）
     - 爆仓流：forceOrder 单边密集 + OI 骤降 = 庄家弃盘（**最可靠顶部信号**）
   - **语义层**（生命周期阶段标签，替代现有 5 tag；保留「启动前=吸筹/点火」「起飞中=拉升」映射兼容前端/Agent）：
     吸筹（价平量升50-200% vs 7日均 / funding 转正 / OI +20% / 买盘墙）→ 点火（破位放量 / taker买比飙升 / OI 加速）→ 垂直拉升（量1000-5000% / funding 0.1-0.5% / 点差扩3-10x）→ **派发顶部**（量高潮+长上影>实体×2 / funding 极值回落 / OI 顶背离 / 爆仓潮后 OI 骤降）→ 崩跌（OI 价齐跌 / 连环多头清算）→ 沉寂（量枯 / 回吐70-95%）
   - **过滤层**：24h 额 ≥$5M 流动性地板；上市 <30 天排除；量比 >50x 持续 = 刷量剔除、量/流动性 >50:1 = 假量；BTC β 残差调整（排除普涨普跌）；触发后 cooldown 防重复报警
2. **爆仓流面板**：`!forceOrder@arr` WS（照 market_ws.py 单例基建模式新增）实时清算流 + 单边密集/速率 z-score 提醒（toast + 系统通知 + 冷却抑制，复用 v1.4.0 SL/TP 提醒模式）
3. **多空比/大户持仓面板**：散户/大户双比值 + 背离标记（合约维度 REST 30s 轮询，同 fstream 地域限制）
4. **Funding 极值榜**：现有「资金费率拥挤」升级为极值排序榜 + 翻转检测提醒
5. **Agent 工具同步**：`meme_watch`（agent_core L530 起）吃 v2 同源数据——输出含阶段标签/OI/funding/爆仓上下文，Agent 能答「处于什么阶段、能不能追」

**P3**
5. 多会话并行 Agent 任务
6. 行情自选列表置顶
7. 合约维度实时流恢复（若币安 fstream 对地区放开；或改用 fapi REST 短轮询 5-10s）

**已修完不要重复提**：API 鉴权/CORS、更新器白名单、端口占用白屏、SQLite 并发、流中断、
行情接口去重、后端守护、子进程清理、代理池内核内置、baw 代理、技能自动更新。

## 五、关键文件地图

| 文件 | 职责 |
|---|---|
| `desktop_app.py` | FastAPI 全部路由（含 /api/skills/updates、/api/skills/update 新增于 L1556-1574） |
| `src/proxy_pool.py` | 代理池（_apply_env 注入 env + NODE_OPTIONS 预加载挂载） |
| `src/proxy_kernel.py` | mihomo 内核（内置内核接化 `_adopt_bundled_kernel`） |
| `src/wallet_client.py` / `wallet_runtime.py` | baw 调用与 runtime 解析 |
| `src/skill_updater.py` | **v1.3.9 新增**：baw+技能自动更新状态机 |
| `src/skills_client.py` | Skills Hub（`_npx_cmd/_node_cmd` 已锚定 runtime） |
| `scripts/proxy-preload.cjs` | Node fetch 代理补丁（undici EnvHttpProxyAgent） |
| `scripts/prepare-runtime.js` | CI 组装 runtime（node+baw+undici 同命令安装） |
| `build-desktop.js` | 打包（含 `BAZZ_KERNEL_EXE` 内核内置） |
| `.github/workflows/release.yml` | CI（mihomo 下载步在 runtime 准备之后） |
| `frontend/src/views/Web3SkillsView.tsx` | 技能库（顶部更新条 v1.3.9） |
| `frontend/src/i18n/locales.ts` | 双语（zh ~L642 / en ~L1810 两处都要加 key） |

## 六、验证命令速查

```powershell
# Python 语法
python -m py_compile desktop_app.py src/xxx.py
# 前端类型
cd frontend; npx tsc --noEmit
# baw 代理补丁 A/B 实测（本地代理 127.0.0.1:7897）
$env:NODE_OPTIONS='--require="E:/hermes_app/binance-agent-os-scout/runtime/proxy-preload.cjs"'
runtime\node\node.exe -e "fetch('https://api.binance.com/api/v3/time').then(r=>r.json()).then(console.log)"
# 发版
git -c http.proxy=http://127.0.0.1:7897 push origin main v1.3.x
```
