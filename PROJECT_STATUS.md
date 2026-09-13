# BAZZ.AGENT 项目状态交接（v1.5.47 SMC 止损定版 + 爆款标题池 · 2026-09-13 更新）

> 用途：开新任务/新会话前快速恢复上下文。读完即可继续开发，无需翻旧对话。
> 仓库：`github.com/xinyuzjj/bazz.agent`
> **主开发目录：`E:\hermes_app\binance-agent-os-scout`（`main` 主工作树，改动请落在这里）**
> 链接工作树：`C:\Users\Administrator\WorkBuddy\Worktrees\binance-agent-os-scout\main-a919d7e7`
> （分支 `workbuddy/main-a919d7e7`，同一仓库同一提交 —— 在此改动**不会**影响主目录，注意别改错地方）
>
> ⚠️ 上一版本文档停留在 v1.5.3 / 表格到 v1.5.11，与代码实际差 18 个版本。**本文档已按 v1.5.35 全量校准。**

---

## 一、项目概览

- **形态**：Windows 桌面端（Electron 壳）+ FastAPI Python 后端 + TS/Vite 前端；同一套代码也能纯浏览器跑
- **定位**：币安 AI 交易终端 —— Agent 对话、行情（现货/合约/股票化代币）、交易方案卡、CEX 连接、Agentic Wallet、广场发文、Skills Hub、多 Bot 群聊、x402 支付
- **当前版本**：**v1.5.47（SMC 止损定版：多头买 OB 上沿/空头卖 OB 下沿并标进价、止损只锚结构失效位不凑风险数；标题池按爆款公式重写）**；
  `package.json` 与最新 git tag 均为 v1.5.47
- **规模**：后端 `src/` 32 个 py 模块 + `desktop_app.py`（**130 条路由** = 129 个 `@app.<method>(` + 1 个 `@app.websocket`）；前端 12 个视图 / 40 个文件；**10 个离线回归测试套件** + 1 套浏览器视觉验收（`tests/ui_preview_check.py`）
- **工作区**：安装目录 `<安装根>/workspace`（state.db / proxies.json / 附件 / 日志 / spill / 复盘 / square_rich）；只读盘回退 `%APPDATA%\BAZZ.AGENT\workspace`
- **预置资产**：`.agents/skills/` 官方技能包 + `.agents/bots/` 5 个 Bot 人设（default-assistant / trend-hunter / liquidity-hunter / onchain-fox / risk-sentinel）

---

## 二、近期发布版本（v1.5.12 → v1.5.47）

| 版本 | 核心内容 |
|---|---|
| **v1.5.47** | **SMC 止损定版 + 爆款标题池（用户多轮实测校准）**：① 止损**只锚结构失效位、不凑风险数**——废弃 v1.5.46「风险预算反推」（等价恒亏 10U，用户否决）；② **进价规则定版（用户指定）**：多头买 OB **上沿**、空头卖 OB **下沿**（第一触点保证成交），文案标明具体进价，止损锚对侧沿±0.5% 缓冲，距离=区宽+缓冲从进价算；③ 仓位回归演示口径（100U 本金=100U 保证金 10x→名义≈1000U），打止损亏损如实报数（数量按交易所步进取整，官方对账 78,542 做空止损 78,934.71 亏≈4.7U 与用户官方计算一致），超本金 10% 附「降名义」建议、**止损位永不为凑数而动**；④ **标题池**（调研爆款公式落地）：痛点拷问/数字+亲历教训/悬念留白/对比反差/身份共鸣/犀利引语/反常识 七类，四风格共享 `_pick_title` 方向池各 12~14 条随机 + 结构事件标题 60% 优先，同币连发不重样，≤25 字不承诺收益。**验证**：sizing **11/11**（官方对账口径+「恒亏/反推/放弃」旧措辞守卫）、styles **17/17**（40 抽≥5 种/事件标题必进候选/防串币）、invalidation 10/10、fallback 8/8；`py_compile` 0 |
| **v1.5.46** | **仓位算法按 SMC 止盈止损重设计（用户实测：挂 OB 限价接却按现价算出止损距离 13.7%、亏 137% 再建议降到 1x）**：`_size_line` 改 SMC 风险口径——**距离从入场价算**（`_plan_levels` 新增 `entry_px`：OB/OTE 区限价取区中位、现价入场取现价）；**名义 = 单笔风险 10U ÷ 止损距离**、杠杆 = 名义÷本金封顶 10x（止损贴结构 ≈0.5% 时自然到 10x/1000U 演示口径；宽止损自动缩仓恒亏 ≈10U；>10% 连 1x 都装不下 → 如实建议放弃）；止盈行补**盈亏比**（从入场价算）。**验证**：`test_v1540_square_sizing.py` 整体重写 **11/11**（贴结构封顶/风险反推/宽止损缩仓/放弃分支/距离必须从入场价复算/盈亏比/旧文案含用户原文必须被拒/源码护栏/端到端止损一致）；styles 14/14、invalidation 10/10、kline_fallback 8/8 全绿；`py_compile` 0 |
| **v1.5.45** | **rich 稿 90 日 K 线不足 → 4h/1h 降级保出稿（用户实测出图失败）**：`_collect` 新增可用性检测（`_k_usable`：≥5 根且非占位平线），1d×90 不可用 → **4h×540（≈90日）→ 1h×720（≈30日）** 逐级兜底；降级后 `k90_label` 记录实际周期，封面「90d 高/低」「90日区间位置」chips、文章走势段、页脚全部跟随（不再写死 90日）；三档全空（刚上线/已下架）才返回带指引错误。**数字照实计算只换周期，绝不伪造数据**。**验证**：新增 `tests/test_v1545_kline_fallback.py` **8/8**（桩 scanner 验证降级链/平线触发/全空不伪造/文案跟随护栏）；styles 14/14、sizing 12/12、invalidation 10/10 全绿；`py_compile` 0。**踩坑**：同消息批量并行 Edit 同一文件会互相覆盖（本版 square_rich 三处、PROJECT_STATUS 标题两次被吞）——**同一文件的多处修改必须逐条顺序 Edit** |
| **v1.5.44** | **保存配置补互动回馈（用户实测：保存成功无任何提示）**：SettingsView `save()` 原来成功/失败都静默 → 成功弹 toast「已保存 · 保存配置 · <主模型名>」（i18n `common.saved` zh/en 新增），失败弹「操作失败」+ 真实错误详情（异常不再吞掉）。**验证**：`test_v1543_model_pickers.py` 扩到 **5/5**（save 必须 toast + 键正确）；`tsc --noEmit` 0、`vite build` 成功 |
| **v1.5.43** | **模型选择三处改造（用户实测：对话框模型选不了；「拉取模型」几百个铺成墙）**：① **对话框模型下拉只显示设置里选了的**——ChatView `loadAvailableModels` 原把全量目录塞进下拉（Nous 300+），现只合并 主模型 + 备用 fallback + aux 槽位，去重；② **设置页主模型 datalist → `<select>` 下拉**（选项=拉取目录+厂商预设+当前值，当前值不在清单保留为独立选项防丢）；③ **备用模型按钮墙 → 「下拉添加 + 已选 chip」**（chip 点 ✕ 移除、主模型 ★ 标记）；aux 三槽位同步改下拉（datalist 移除）。**验证**：新增 `tests/test_v1543_model_pickers.py` **4/4**（禁拉全量目录 / 必须 select / 禁按钮墙 / i18n 双语）；`tsc --noEmit` 0、`vite build` 成功 |
| **v1.5.42** | **订阅登录授权页改开系统默认浏览器（用户实测：点「连接」弹的是软件内置窗口）**。根因：SubscriptionAuth.tsx 三处授权链接用裸 `window.open()`，Electron 渲染层的默认行为是**再开一个应用内 BrowserWindow**（无浏览器登录态、部分厂商授权页在 WebView 里触发风控、PKCE 回调流程卡住）→ 三处（PKCE 自动拉起 / 设备码打开验证页 / 重新打开浏览器按钮）全部改走 `api.openExternal()` → preload `bazz:open-url` → 主进程 `shell.openExternal`（v1.2.11 就有的链路，只放行 http(s)）。**验证**：`test_v1541_llm_auth.py` 扩到 **22/22**（新增护栏：订阅组件禁止裸弹窗调用 + 链路完整性校验）；`tsc --noEmit` 0 |
| **v1.5.41** | **LLM 订阅 OAuth 登录（四家订阅直连，免 API Key）**：新增 `src/llm_auth.py` —— ① **GitHub Copilot** 设备码流（browser-in）；② **ChatGPT/Codex** PKCE 本地回环（127.0.0.1:1455）+ Responses API 适配（`/chat/completions` → SSE 流式解析，含 `message` item 正文提取）；③ **Anthropic Claude (Pro/Max)** OAuth 回环 + Messages API 适配（system 拆头、多模态 content 分块、max_tokens 默认，响应统一回 OpenAI 形状）；④ **Nous Portal** 设备码 + sk- API Key 兜底。`llm.py` 的 `_post/test_connection/stream_chat` 挂订阅路由（未登录抛带指引错误，fallback 链接住）；token 存 `llm_oauth` 敏感 key 走 state.py DPAPI 加密钩子，到期自动刷新且 **rotating refresh token 刷新后立即回存**；`desktop_app.py` 新增 `/api/llm/auth/*` 5 条路由（走既有鉴权中间件）；前端新增 `SubscriptionAuth.tsx` 订阅登录卡片（状态/账号/到期/断开重连/点「使用」直切主模型）+ api.ts 5 个接口 + i18n 双语。**验证**：`tests/test_v1541_llm_auth.py` **21/21**（PKCE URL/state、SSE 解析、rotating 回存、_post/stream 路由等）；既有套件全绿；`tsc --noEmit` 0、`vite build` 成功 |
| **v1.5.40** | **广场文章：四风格库 + 消息面 + 仓位口径修正**。① **仓位算法**（用户指出「这个是错误的，100U 本金 = 100U 保证金，开单金额 1000U（十倍）」）：旧实现从「单笔只亏 5U」反推名义，一句话里出现两个「10x 保证金」（34U / 31U），且建议「降到 9x」**方向是反的**（旧口径下降杠杆只会让保证金变大，310÷9≈34U>31U）→ 改成「**100U 本金 = 100U 保证金，10x 杠杆 → 开单名义 ≈ 1000U**；止损位 X（距离 Y%），打止损亏 ≈ NU」，偏重（亏损>本金 10%）时的降杠杆建议**按「把单笔亏损压到本金 10% 左右」反推**（`lv = 10 ÷ (100 × 距离%)`，此口径下降杠杆同步缩小名义，方向才对；止损 8% → 建议 1x 亏 8U）。`_size_line` 模块常量 `_CAPITAL_U=100/_LEVERAGE=10/_RISK_TARGET_U=10`；仓内「单笔亏 5U」全部清除。② **四种风格**（用户要求「每次的风格换一换试试」，四种全选）：`_facts()` 把方向/点位/计划/反向剧本/消息面**只算一次**，四个渲染器 `_style_review/diary/qa/blunt`（冷静复盘/交易员日记/自问自答/直给结论）经 `_RENDER` 分发；`compose(style=…)` 可锁定，不传则随机，`meta.json` 记 `style/style_label`；`_article()` 保持 3 元组旧签名不破；`_market_story` 拆成 `_market_bits()` + 兼容包装。**核心不变式：只换叙述不换数字** —— 回归逐字比对四风格入场/止损/反向剧本一致 + AST 护栏禁止渲染器调用 `_plan/_invalid_line/_bias/...` 自己重算。③ **消息面**：`scanner.coin_news(symbol)`（PANews 中文快讯 + CoinDesk/Cointelegraph RSS，零 Key，5 分钟 TTL，情绪关键词启发式），**Python 直接抓而不走 `run_skill()`** —— 后者网络失败会触发 `_ensure_proxy_or_empty()` 自动切代理（用户对「偷偷连代理」敏感）；文章补一段「相关标题 N 条，利多 X/利空 Y/中性 Z…」+ 引用真实标题（带来源时间），只作陈述不覆盖 K 线硬数据，**取不到就整段省略、发文绝不失败**；1~2 字母 ticker 只认原文独立大写（`op` 不误命中 `operation`）。④ **文案精简**（用户点名）：页脚「封面图是 90 日 K线…」整行删；新闻句「（某源没抓到…）」「消息只是背景板…」删。**验证**：新增 `tests/test_v1540_square_sizing.py` **12/12**（含把用户旧原文喂进判据必须判不合格的守卫用例 + AST 护栏）+ `tests/test_v1540_square_styles.py` **14/14**（四风格互不相同、点位剧本逐字一致、渲染器禁重算、消息面静默降级、短 ticker 防误命中）；v1.5.39 护栏上移到 `{_article,_article_full,_facts}`；全量 **13 套件通过**；`tsc --noEmit` 0。**踩坑**：WorkBuddy 沙箱强制把 `HTTPS_PROXY` 钉成 51554（`export`/`VAR=x cmd` 都覆盖不了），且 requests 的合并顺序是 **env 覆盖 session.proxies** → 本机测试必须 `scanner._session.trust_env=False` + 显式 proxies（产品代码不写死代理）；PANews 在本机被 TLS 拦（7897/直连都挂），用户机器待验；compose 目录名只到秒且行情有缓存，连发必须 `time.sleep(1.1)` 否则互相覆盖 |
| **v1.5.39** | **修复广场技能自动文章的「反向剧本」自相矛盾（用户实测）**。原句「入场…现价 2,533.98 · 仓位算法：止损位 **2,495.06** … · 反向剧本：4h 收盘跌破 **1,503.32**（90 日低点下方）且收不回来」—— 止损 2,495 与作废线 1,503 **相差 40%**。**根因**：`_article()` 里 `if bias != "short":` 那段从**另一个数据源**（`min(k90.lows)*0.995`）另算作废价，跟 `_plan()` 的止损毫无关联；三个后果 ① 止损 2,495 先到，1,503 那句永远触发不了 = 没有作废条件；② 把「这笔单作废」写成「大趋势作废」，口径混淆；③ 紧邻的「仓位算法」已给 2,495.06，数字打架。**连带 bug**：`bias != "short"` 把 **neutral（观望）也当多头**，观望场景下照样输出「多头逻辑全部作废」。**修复**：① 抽出 **`_plan_levels(stat,bias,smc)`** 集中算「入场/止损/止盈」，`_plan()` 与新增的 **`_invalid_line()`** **共用同一份结果** → 作废线必然锚在本单止损上，结构上不可能再矛盾；② `_invalid_line()` 分 long/short/neutral 三态出文案，long 再附「离止损最近的更深结构位」（4h 摆动低点 / OTE 下沿 / 近 20 根 4h 低点，须在现价下方 15% 内）作结构确认；③ **90 日低点不再当止损用** —— 新增 `_macro_level()`，只有落在现价下方 **20% 内**才附带一提并标注「那是大趋势的事，跟这笔单的止损不是一回事」，远了就**直接不说**；④ `_size_line()` 从内联提升为模块级。**修复后输出**：「· 反向剧本：4h 收盘跌回 **2,495.06** 下方（本单止损位）就别恋战，按计划砍仓；若连 2,446.83（OTE 下沿）都收不回来，多头结构才算真的走坏。」**验证**：新增 `tests/test_v1539_square_invalidation.py` **10/10** —— 含**守卫用例**（把用户原句 `LEGACY_LINE` 喂给断言助手，**必须判定不合格**，证明断言真抓得住旧 bug）+ **AST 护栏**（`_article()` 不得再出现 `lo*0.995` / `bias != "short"`；v1.5.40 起上移到 `{_article,_article_full,_facts}`）；全量套件通过；`tsc --noEmit` 退出码 0 |
| **v1.5.38** | **代理池整体回退到 v1.5.35 的行为（用户明确要求）**。v1.5.37 修了四个缺陷，但同时改了**行为**，实测三处改动让体验变差：① **「代理池加载慢」** —— 面板改成先等 `/api/proxies` 返回再渲染（加载骨架），且 `status()` 里新增了解析订阅文件的 `provider_summary()`；② **「连接卡，要很久才会好」** —— 「启动内核」改成非阻塞（后台线程）后请求立刻返回，界面只能显示「启动中…」，整段内核启动时间都变成"卡住"；③ **「直连下行情等内容都无法显示」**（最要命）—— 改成「启动一律直连」后，国内直连根本到不了 Binance，每次开 APP 都得手动去连一次代理才有行情。**回退内容**：`bootstrap()` 恢复 `_load() → _apply_env() → _revive_kernel_async()`（每次启动自动连上上次的节点）；`/api/proxies/kernel/start` 恢复同步等待；面板恢复即时渲染（去掉骨架屏与失败重试块）；订阅文件恢复原样落盘、`status()` 去掉 provider 摘要；`ProxyPoolView.tsx` / i18n 代理文案 / `tests/test_v1532_proxy_kernel_state.py` 全部回到 v1.5.35；v1.5.37 新增的 `tests/test_v1537_proxy_identity.py` 已删除。**唯一保留的修复（用户确认）**：`teardown_proxy()` —— 旧实现直连分支只调 `k.select("DIRECT")`，**内核照跑**且 `NODE_OPTIONS` 里的 `--require=proxy-preload.cjs` **只加不摘**，于是「取消连接」并没有真的回直连：本机仍有 mihomo 在监听、代理期间起来的 Node 子进程继续把 fetch 交给它，端口一失效就谁也连不上（用户报的「一取消连接整个应用都没网」正是它）。保留版按**停内核 → 摘预加载 → 清 env** 三步彻底还原，`_apply_env()` 直连分支也调 `_drop_node_preload()`。**验证**：新增 `tests/test_v1538_proxy_teardown.py` **5/5**（行为 + 环境 + 四种 `--require=` 形态 + AST 护栏；**已验证能抓住旧行为**：临时改回 `select("DIRECT")` → 3/5，恢复后 5/5）；全量 **10 套件通过**；`tsc --noEmit` 退出码 0、`vite build` 成功；与 v1.5.35 逐行比对，`proxy_kernel.py`/`desktop_app.py` **完全一致**，`proxy_pool.py` 只多 53 行（那一条修复）。**提醒**：回退后订阅仍是整份 Clash 配置原样落盘；若出现「显示连上但流量没走代理」，先重新导入一次订阅，仍不通则需要把 v1.5.37 的 `proxies:` 抽段逻辑单独加回来（本次未纳入）。**踩坑**：发布小节标题里**不要嵌其它版本号** —— v1.5.38 标题原本写了「回退到 v1.5.35」，结果被 v1.5.35 的 CI 截取正则命中，导致那一版出现 2 行匹配 |
| **v1.5.37** | **代理池功能性缺陷修复（用户报：开机自动连代理 / 取消连接后全应用断网 / 提示需装插件 / 点安装卡死）**。四句抱怨 = 四个独立缺陷 + 第五个隐藏根因。① **开机静默接管流量**：`bootstrap()` 旧流程是 `_load()` 把上次 `active_id`（现场正是一个 vless 内核型节点）恢复成「已启用」→ `_apply_env()` → 后台 `_revive_kernel_async()` 拉起 mihomo，于是**每次开 APP 都在几秒内把整机流量切进代理池**；现场 `kernel.log` 在 `18:20:58 / 18:23:15` 两次自动启动即铁证。→ **启动一律直连**：`_state` 新增 `last_active_id`，bootstrap 里 `last_active_id = active_id; active_id = ""`，`_revive_kernel_async()` **整个删除**；界面新增「恢复上次：<节点名>」按钮，用户显式点才生效。② **取消连接 ≠ 回直连**：直连分支只调 `kernel.select("DIRECT")`，**内核照跑**，且 `_ensure_node_preload()` 只往 `NODE_OPTIONS` 加 `--require=proxy-preload.cjs`、**从不摘除**——代理期间起过的 Node 子进程一直攥着 undici `EnvHttpProxyAgent`，继续把请求送往本地端口 → 「点了取消连接，整个应用反而没网」。→ 新增 `teardown_proxy()`（停内核 + 摘预加载 + 清 `HTTP(S)_PROXY/ALL_PROXY` 三件套），`set_active("")` 直接调它；新增 `_drop_node_preload()`（正则摘 `_PRELOAD_TOKEN_RE`，保留其他 flags，能处理带空格的路径）。③ **钱包「需要安装插件」是误报**：网络被①②搞坏后探测失败 → `wallet.installNeed` 误判运行时不在，属下游症状，根因修掉即消失。④ **点安装卡死**：`proxy_kernel.start()` 在 `with _LOCK:` **内部**做 40×0.5s 控制面等待 = **持锁最长 20 秒**，而同一把锁被 `list_pool()`（`/api/proxies`）、`start_download()`、`stop()` 共用 → 前端 15s / 1.5s 两套轮询全堵死。→ 等待循环**移到锁外**、新增模块级 `_starting` 幂等标记（重复点击返回 `{ok,starting:true}` 不重复 Popen）、启动改走**非阻塞** `start_kernel_async()`（后台线程，就绪后再 `_apply_env()`）、`status()` 新增 `starting` 字段供前端反馈进度。⑤ **隐藏根因：订阅文件格式**。`save_provider()` 原先只要原文出现过 `proxies:` 就把**整份 clash 配置**原样落盘——现场 `providers/sub_*.yaml` 就是 **566 行完整配置**（`mixed-port`/`dns`/`proxy-groups`/`rules` + `proxies:` 24 节点），而 mihomo 的 `proxy-providers:{type: file}` **只解析顶层 `proxies:`**，于是 provider 载入 0 节点、`BAZZ` 组实际只剩 `DIRECT`：**界面节点齐全、延迟测得出，流量却从没走过代理**。→ 新增 `_extract_proxies_block()` 只抽 `proxies:` 段落盘、0 节点则**拒绝写入并报错**，`provider_summary()` 供 `status()` 暴露每个订阅实际解析出多少节点，节点为 0 时界面亮警示条。⑥ **界面不再「说谎」**：面板原先用写死默认值渲染（`{active_id:"", entries:[], kernel:{installed:false}}`），`/api/proxies` 未返回时先显示假的「直连 · 使用中」，请求失败又被 `catch` 吞掉 → 改为**加载骨架 + 失败错误块 + 重试按钮**；顺手修「延迟列错误复用状态列渲染函数」（两列显示同一内容）与协议徽章对比度（`sky-300`/`violet-300` 在浅色底仅 3.44:1 → 换主题令牌）。⑦ **验证**：新增 `tests/test_v1537_proxy_identity.py` **19/19**（provider 只留 proxies 段 / 拒绝空 / flow+block 两写法 / 启动即直连并记住选择 / 启动绝不拉起内核 / 切直连彻底拆栈 / 摘预加载含空格路径 / 停内核重置内核型 active / 锁外等待 / 重复点击不重复拉起 / 界面无假默认值 / 延迟状态分列 / 空态指向正确 / 中英键齐全）；全量 **10 套件回归通过**（v1.5.32 套件的旧断言 `test_bootstrap_revives_kernel` → `test_bootstrap_starts_direct_and_keeps_last_choice` 已按新语义更新）；`tsc --noEmit` 退出码 0。⑧ **踩坑**：源码扫描型断言不能用**纯文本切片**——`bootstrap()` 文档字符串里专门写了「为什么不再调用 `_revive_kernel_async()`」，字符串匹配会把这段说明误判成调用 → 改用 **AST `ast.walk` 只看 `ast.Call`**，注释/文档/字符串里的函数名一律不参与（两个测试文件同步修正） **⚠️ 本版已被 v1.5.38 整体回退** |
| **v1.5.36** | **界面视觉系统重构（导航回到顶部）+ 隔离预览 / 浏览器验收链路**。① **布局**：上一稿把导航做成左侧一整列（`.workspace-sidebar` + `--sidebar-width`），**用户明确否掉**（「还是喜欢这个放在上面」）→ 导航回到顶部单条 `.app-topbar` = 品牌 \| 横向导航（8 项）\| 工具（搜索 / 连接状态 / 主题 / 语言 / 窗口控制）；面包屑那一行删除，版本号移进品牌副标题。② **窄屏换行而不是横向滚动**：`.topbar-nav` 一旦装不下就**不能靠滚动** —— 滚动条隐形，末尾几项等于藏起来（390px 下「技能库 / 设置」点不到，是浏览器验收实测抓出来的）。规则：≤1000px 整条 bar 转两行（品牌+工具一行 / 导航独占下一行并内部换行），≥1001px 保持单行 + `overflow-x:auto` 兜底；断点 1420（去导航图标）/ 1120（收副标题 + 连接状态文字）/ 1000（转两行）/ 900（会话页单列）/ 760 / 520。③ **顺手修三处遗留**：**(a)** 钱包页头部 `LIVE · LIVE · 已登录` —— `wallet.signedIn` 文案本身已含 `LIVE ·`，代码又拼了一次；**(b)** 窄列卡片头部挤压 —— 左侧 7/5 子栅格里 `PanelHead` / `LivePanel` 的标题与「重新获取 / 刷新」抢位（按钮被压到 41px 宽折成两行；`LivePanel` 的 `<code>` 缺 `min-w-0`，把「刷新」挤成 22×53），修法 = 行容器 `flex-wrap` + 动作 `shrink-0 whitespace-nowrap` + code `min-w-0 flex-1`；**(c)** `confirmDialog` 传了 `title` 就把 message 整句吞掉（广场删帖弹窗只剩「删除」二字标题）→ message 下移到 `detail` 行渲染。④ **隔离预览 + 浏览器验收（新增资产）**：`frontend/preview.html` + `preview.config.ts` + `src/preview/{main.tsx,mock.ts}` 独立入口，内存 fixture 覆盖约 48 个端点，fetch / XHR 全拦、CSP `connect-src 'none'`、在真实桌面桥里直接抛错拒绝运行；`tests/ui_preview_check.py` 用真浏览器走查 **8 视图 × 明暗 × 4 档宽度（1440/1024/760/390）= 84 项断言 + 25 张截图 + verification.json**（含广场删帖/清空失败的二次确认、会话流式回复与切页保持、以及「所有请求都是本地静态资源」）。⑤ **踩坑（都写进测试注释了）**：agent-browser CLI 的守护进程会**继承父进程 stdout**，Python 用 `capture_output` 抓输出会在**第一次调用就死锁** → 一律重定向到文件 + `Popen.wait(timeout)` + 超时 kill；该 CLI 的 **`find role textbox` 解析不了普通 `<input>`**（4 种参数写法全失败），**图标按钮的 `aria-label` / `title` 也不参与 `--name` 匹配** → 改 `fill/click <css>`；判定卡片头部碰撞必须**同时比 y 轴**，否则换行后的动作会被误判成重叠；预览构建 `emptyOutDir:false`，旧 hash 资源会累积；沙箱**批量删除守卫**（50 次/轮）命中时抛的是 `SystemExit`，`except Exception` 抓不到 |
| **v1.5.35** | **两处「用起来别扭」：更新时不再弹「关闭还是最小化」；广场失败文章可删除**。① **更新弹窗（真实故障路径）**：应用内点「安装更新」竟弹出「关闭还是最小化到托盘」询问 —— 根因是 `useUpdater` 复用了「用户点 X」的 `bazzWindow.close()` 链路（`mainWin.on("close")` → `bazz:ask-close` → 渲染层弹窗 → `bazz:answer-close`）。更糟的是用户一旦选「最小化到托盘」，Electron 进程**根本不退**，而更新脚本会等待主进程退出**最多 180 秒**（45 次 × 4 秒），超时即 `ERR wait-electron-timeout`、更新直接失败 —— 这弹窗不只是碍眼，它是更新失败的一条真实路径。修复：新增**专用退出通道** `bazz:quit-for-update`（`preload.cjs` 暴露 `quitForUpdate()`，`main.cjs` 直通 `isQuitting=true; app.quit()`，**不进询问链路**），`useUpdater` 改走它并保留 `close()` 兜底（老主进程无此通道时不崩）；`installer.iss` 的 `CloseApplications=yes` 改 **`no`**（原 `yes` 会让 Inno 的 Restart Manager 先发 `WM_CLOSE`，同样引出询问框，而 ScoutBackend / mihomo / runtime node 都是**无窗口进程**根本关不掉 → 必然弹「Select action」卡住安装），关闭一律交给 `[Code] PrepareToInstall` 的 `taskkill /F`。**结论：「关闭还是最小化」只服务于用户主动点 X，程序性退出一律直通。** ② **广场失败文章可删除**：台账只进不出，失败记录越堆越多。新增 `src/square_store.py::delete_records(ids)`（按 id 批删，`deleted` 计实际删除数、`missing` 计不存在的 id，**只有真删掉东西才落盘**）+ 端点 `POST /api/square/posts/delete`（`{ids:[...]}` → `{ok,deleted,missing}`，**仅操作本地台账，不调币安 API**）+ 前端失败卡片「删除」按钮（垃圾桶图标，`status !== "posted"` 才显示）与「失败」筛选下的「清空所有失败」，**两者都走 `confirmDialog` 二次确认**，清空时提示条数；新增 5 个 i18n key × 2 语言。③ **验证**：新增 `tests/test_v1535_square_delete.py` **12/12**（单元 5 + AST 6 + TestClient 端到端 1，含 401 未带令牌），**已验证能抓住旧行为**（回退 5 文件 → 0/12）；全套 9 套件回归通过；`tsc --noEmit` 退出码 0、`npm run build` 成功。④ **踩坑**：FastAPI **没有全局 `request` 对象**，按 Flask 习惯写 `request.get_json()` 会 500 `name 'request' is not defined`，正确写法是签名里 `payload: dict = Body(default_factory=dict)` |
| **v1.5.34** | **文件查看器图片预览：后端能力早已就绪，界面这端从未接线**。① **现象**：在 Files 面板点开 `chart_24h.png`，查看器只显示「二进制文件，不可文本预览（共 33.8K）」，图片看不到。② **根因（典型「做了半截」）**：后端更早版本就加了图片原文端点 `/api/workspace/raw`（20MB 上限、扩展名白名单、`FileResponse` 直出），`api.ts` 也早有配套的 `workspaceRawBlob()` —— 但**全仓没有第二个调用点**；同时 `ChatView` 的 `fileModal` 类型声明了 `img_url?: string` 却**从未被赋值**，渲染分支 `fileModal.img_url ? <img ...>` 因此永远走不到，所有文件一律落到 `!is_text` 的「二进制不可预览」兜底。补充：图片**不能**复用文本通道 `/api/workspace/read` —— 那是文本接口（1.5MB 上限）且**含 NUL 字节即判二进制**，PNG 文件头就带 NUL，必然被挡。③ **修复**：`openFile()` 按扩展名分流（图片走 `workspaceRawBlob()` → `URL.createObjectURL()` → 写入 `img_url`，且分流必须排在 `workspaceRead` 之前）；objectURL 在**关闭 / 切换 / 卸载**三处全部 `revokeObjectURL()`；文件列表给图片加**缩略图**（`THUMB_LIMIT = 30` 限制请求数）；查看器内图片可点击打开原图 + 底部「打开原图」按钮；错误文案解包（`jget` 把整个 JSON body 塞进 `Error.message`，现在解出 `error` 字段）。④ **验证**：新增 `tests/test_v1534_image_preview.py` **7/7**，并**已验证能抓住旧行为**（临时回退 `ChatView.tsx` → **1/7**，恢复后 **7/7**；唯一「通过」的渲染顺序断言恰好印证事故本质 —— 分支写好了，只是永远走不到）。其中 `test_workspace_raw_blob_has_a_caller` 是**核心护栏**：能力存在但没人调用 = 功能不存在。另含前后端扩展名白名单**交叉校验**（AST 取后端 `_IMG_EXT_MEDIA` 键集合 vs 前端 `IMG_EXT_RE` 正则，并做真实正则匹配验证含大写/非图片/结尾锚定）。前端 `tsc --noEmit` 退出码 0、`npm run build` 成功 |
| **v1.5.33** | **修 v1.5.32 自己引入的启动竞态**。v1.5.32 给 `_recover()` 加的「探活失败即清空端口缓存」是**无条件**的，而 `start()` 里端口是**先写、后起进程**：`_write_config()` 写好端口 → `open(LOG_PATH)` → `Popen()`，在后两步之间 `_proc` 仍是 `None`。若前端此刻正好轮询 `/api/proxies`（`status()` → `is_running()` → `_recover()`），内核尚未就绪 → 探活失败 → **把刚写好的端口清零** → 等待循环 40 次都在请求 `http://127.0.0.1:0/version` → 误报「内核启动超时」，并把 `mixed_port: 0` 写进 `state.json` —— **症状与「代理没启用」完全一致，等于把刚修好的 bug 换个入口又放回来**。修复：① 新增 `_recovered` 标记区分「落盘恢复来的端口」与「本进程 `start()` 刚写的端口」，`_recover()` **只清前者**；② `_write_config()` 写入时置 `_recovered=False`；③ `stop()` 同步归零端口缓存（此前停掉内核后 `mixed_port()` 仍返回过期端口）；④ `_revive_kernel_async()` 在线程内**重读** active 节点（启动期间用户在界面上换过节点时不会再把旧节点选回去）。测试 `tests/test_v1532_proxy_kernel_state.py` 扩到 **14/14**，并**已验证新断言能抓住旧行为**（临时回退 `src/proxy_kernel.py` → 12/14，两条 FAIL） |
| **v1.5.32** | **内核型代理静默失效 → 广场发文 `UND_ERR_CONNECT_TIMEOUT` 根因修复** + 子进程 GBK 解码崩溃。① **根因**：代理池启用的是内核型节点（`vless` 等），代理入口是 mihomo 的本地混合端口，而该端口只被记在 `proxy_kernel` 的三个**模块级变量**里（`_proc` / `_mixed_port` / `_ctrl_port`），且 `is_running()` 一上来就 `if _proc is None: return False` —— 只有「本进程亲手 Popen 出 mihomo」才认得内核。于是后端重启 / 同机第二个实例 / 上一实例把 mihomo 留成孤儿进程时，新进程 `is_running()=False` → `mixed_port()=0` → `proxy_pool.proxy_url()` 返回 `None` → `_apply_env()` 走 else 分支把 `HTTP(S)_PROXY` **全部 pop 掉** → 技能子进程继承不到任何代理 → Node/undici 直连 → `UND_ERR_CONNECT_TIMEOUT`（~10.6s）。现场铁证：`127.0.0.1:9099/version` 返回 `HTTP 200 {"version":"v1.19.30"}`（内核客观在跑）而同进程 `is_running()` 返回 `False`。② **修复**：`proxy_kernel` 新增 `state.json` 落盘（pid + 实际端口）+ `_recover()`（state.json → config.yaml 兜底恢复端口 + 控制面探活），`is_running()` / `mixed_port()` / `version()` 全部接上；`stop()` 支持收掉别的进程拉起的内核（**先核对镜像名确为 `mihomo.exe`** 防 PID 复用误杀）；`/api/proxies/kernel/start` 补调 `apply_env()`（此前只拉内核不注入 env，「启动内核」等于没启用代理）、`/stop` 同步清理；`bootstrap()` 后台救活内核并**重选节点**（mihomo 重启后 selector 回默认）；`ensure_working_proxy()` 先救活「用户选中的那个内核节点」再找别的候选；新增 `proxy_pool.env_snapshot()` 并让 `/api/proxies` 返回 `env` 字段（一眼看清代理注入没有）。③ **附带**：7 文件 10 处 `subprocess.run(text=True)` 未指定编码 → zh-CN Windows 按 GBK 严格解码，`_readerthread` 抛 `UnicodeDecodeError` 直接死掉、`proc.stdout` 变空（技能「跑了却没输出」）→ 统一补 `encoding="utf-8", errors="replace"` + AST 护栏。④ **验证**：`tests/test_v1532_proxy_kernel_state.py` **12/12**；真实环境实测新进程 `is_running()=True / mixed_port()=7899 / proxy_url()=http://127.0.0.1:7899 / HTTP_PROXY 已注入`；端到端（技能实际 Node/undici 路径）修复前 `content/add` 与 `public.bnbstatic.com` 均 `UND_ERR_CONNECT_TIMEOUT`（10686ms / 10589ms），修复后 **HTTP 404 @1295ms / HTTP 403 @629ms** |
| **v1.5.31** | 提示词侧两处缺陷：① **`market-data` 宣传了不存在的子命令**——`SKILL.md` 的 description 把「资金费率 / funding」列为触发词，但 CLI 的 `CMDS` 只有 klines/fng/oi/longshort/liq/overview/bundle，模型调用 `market-data "funding BTCUSDT"` 只得 `{"error":"未知命令 \"funding\""}`；资金费率当时仅作为 `bundle` 的一个字段存在，为拿单币费率要跑完整分析包（90d 日K + 24h + FNG + OI + 多空比）代价过高。→ 新增真正的 `funding [SYM[,SYM...]]` 子命令（返回 `funding_pct_8h` 每 8h 费率 + `annualized_pct` 年化；无参数则返回费率最高/最低各 10 个），未知命令报错补 `hint` 指出最可能的替代写法，`SKILL.md` 命令表与用法示例同步登记。② **模型把 HTTP 状态码当文件路径**——技能报错文本含 `:8080 HTTP 401: {"error":"unauthorized"}`，模型随后执行 `grep <pattern> 401`，把状态码当路径传入，只得到干巴巴的「路径不存在: 401」无从纠正。→ `exec_sandbox` 新增 `_not_a_path_hint()`（识别裸数字/HTTP 状态码、URL、`-` 开头选项，命中才追加、正常路径零噪音），接入全部 4 个路径报错点（`文件不存在`/ls `目录不存在`/grep `路径不存在`/find `目录不存在`）；`llm.py` 的 `run_command` 工具描述正面写清「不要把数字、HTTP 状态码、URL 或错误消息片段当路径传」。测试 `tests/test_v1531_prompt_fixes.py` **14/14**（含端到端：起本机 stub 后端真跑 funding），并新增通用护栏 `test_skill_md_commands_all_dispatchable`（逐技能比对 SKILL.md 命令表与 CLI 实际命令，防止再出现同类问题）；同时修正 `test_v1530_local_backend.py` 的模块 docstring（此前把已被推翻的「NO_PROXY 是根因」当实测结论写入） || **v1.5.30** | 技能全线 401 根因修复（本机回环令牌被沙箱误删）：① **根因两层叠加**——(a) v1.5.29 的 F06 沙箱环境清洗按**子串**匹配，`BAZZ_AUTH_TOKEN` 含 `TOKEN`/`AUTH` 被当凭据剥离；但它其实是**应用自己的本机回环令牌**（Electron 每次启动 `crypto.randomBytes(24)` 生成，仅用于访问 127.0.0.1 自身后端），技能必须携带才能过 `/api/*` 鉴权 → 一律 401（v1.5.28 及更早继承完整 `os.environ`，无此问题）；(b) 技能 CLI 端口回退用单个 `lastErr`，8080 的 **401 被 8081 的连接错误覆盖**，抛出「本地后端不可达: fetch failed」，把鉴权问题伪装成连通性问题；② **修复**——`exec_sandbox` 新增 `_ENV_ALLOW_EXACT` 精确放行 `BAZZ_AUTH_TOKEN`（第三方凭据照常剥离，安全边界不变）；5 个技能 CLI（coin-report/market-data/portfolio-review/risk-guard/track-monitor）的 `jget()` 逐端口错误全部保留；`proxy_pool._ensure_no_proxy()` 把 NO_PROXY 由 `setdefault` 改**强制并集**；`proxy-preload.cjs` 显式传 `noProxy`；`skills_client`/`agent_core` 本机后端故障前置判定并按 401/连通性分诊。测试 `tests/test_v1530_local_backend.py` 20/20；**`test_v1529_hardening.py` 的 F06 断言已修正**（原断言要求 `BAZZ_AUTH_TOKEN` 必须被剥离，正是它把回归固化成了预期行为）→ 23/23 |
| **v1.5.29** | 审查缺陷修复（授权边界·密钥防护·沙箱加固·供应链锁定）11 项 + 性能 3 项：① **F03** 下单方案资金语义失真——现货通道此前按 margin×leverage 算量（高杠杆请求变超额现货买单）、止损恒 97%/止盈恒 108% 却宣称「最大亏损=margin×10%」；现现货直接拒绝杠杆/做空语义、数量按 margin/price 真实口径、删除虚假承诺文案并明示「止损止盈仅为到价提醒」；② **F05** 插件命令与 MCP 网关（可触达账户级真实下单）绕过审批——现与沙箱同标准，未确认一律流审批卡，「信任并执行」按 `mcp:<server>.<tool>` / `plugin:<pid>` 粒度加白；③ **F09** 并行 tool_calls 整批执行后才查审批 + 线程池异常整批重放（重复下单/重复写文件）——现审批是调度屏障，遇首个需审批工具立即停流，per-call 异常兜底为错误结果、彻底删除重放路径；④ **F07** 会话快照恢复跨服务密钥错配（A 的端点 + B 的密钥）——现 provider 一致才继承当前 key，不一致置空安全失败；⑤ **F06** 沙箱逃逸——子进程环境剥离凭据类变量（**⚠️ 该条过宽，误伤 BAZZ_AUTH_TOKEN，已在 v1.5.30 修正**），读/写/wrapper 三类路径统一 realpath 校验，符号链接越界一律拒绝；⑥ **F11** 前端 auto_exec 请求失败 fail-open（未知状态当已开启）→ 改 fail-closed；⑦ **F12** 调度器整份覆盖任务状态致并发丢任务 → 新增 `state.update_cron_job()` RLock 内读-改-写；⑧ **F14** 禁用插件仍可被调用 → `list_command_schemas()` 不注入 + `exec_command()` 双重校验 enabled；⑨ **F15** API Secret / LLM Key / MCP Token 明文入 SQLite → 新增 `src/secrets.py`（Windows DPAPI，ctypes+crypt32 零新依赖；非 Windows 降级显式标 `plain:`；旧明文兼容并自动迁移）；⑩ **F16** 供应链可变引用（`@latest`/main 分支）→ 锁 `@binance/agentic-wallet@1.10.0` + `undici@6.21.1` 集中常量并防回滚，技能包取消后台静默升级只留手动入口；⑪ **F17** `last_persona_conv` 重复定义覆盖正确实现（丢 group/room 排除）→ 删除重复版本。**性能**：5.1 scanner `_dedupe_fetch()` in-flight 扇出去重（K 线/资金费率/OI 历史三处接入，30s 超时兜底）；5.2 订单轮询改「先执行再 sleep」消除启动延迟；5.3 前端三条 NDJSON 流补 res.ok、逐 delta await rAF 改缓冲+每帧批量 flush、会话切换竞态用请求代号+AbortController 双隔离、live.ts 快照 diff 合并保留未变币引用、vite dev 代理补 `ws: true`。测试 `tests/test_v1529_hardening.py` 23/23 |
| **v1.5.28** | 审查缺陷修复·交易链路 6 项 P1/P2（第三方源码工程审查报告，提交 944ca16）：① **F02** 钱包已成交/待确认被误报「下单失败」（`place_report()` 只认 CEX 的 `status=="ok"`，钱包 `TRADE_FINISHED/TRADE_PENDING` 全落 error 分支 → 误报失败+跟踪跳过+诱导重复下单）→ 统一归一 `TRADE_FINISHED→FILLED` / `TRADE_PENDING→PENDING`（待查证不自动重试），保留原始状态与回执；② **F10** MCP 调用成功被显示失败（`_run_mcp_call` 用 `res.get("ok")`，而 `call_tool()` 成功返回 `{"status":"ok"}`）→ 两种契约兼容；③ **F04** 成交后止损止盈提醒静默（`_CLOSED` 含 FILLED → 成交即停监控，「未成交有提醒、真成交反而静默」）→ 拆两组终态，查单轮询仍含 FILLED、提醒监控改用 `_CLOSED_FOR_ALERTS`；④ **F01** 更新完整性校验 100% 失效（资产名转小写后与全大写常量比，永不相等 → 校验形同虚设）→ 统一小写比较 + **fail-closed**（官方整包拿不到校验和一律拒绝安装）；⑤ **F08** 强制兜底工具名变布尔值（`forced = forced_cand and (...)` 得 `True`，分派器字符串操作抛 TypeError）→ 条件表达式显式保留工具名；⑥ **F13** 妖币雷达日报必然 TypeError（`_run_meme_scan()` 误传 `limit=8`，真实签名是 `force/top_n/min_qv`，且把 `dict{coins:[...]}` 当 list 迭代）→ 按真实契约调用。测试 `tests/test_v1528_fixes.py` 15/15 |
| **v1.5.27** | 全局应用内弹窗（告别系统原生白框）：新增 `ConfirmDialog` 深色玻璃卡片（遮罩模糊+淡入缩放，危险操作自动红主题，Esc/Enter/点遮罩，记住选择自绘勾选框，i18n `dialog.*`）；**替换 11 处 `window.confirm`**（ChatView ×5 删会话/房间/Agent/文件/踢成员、ProxyPoolView、AdminPanels ×2 删 Cron/MCP、MemoryOverlay ×2）；点 X 的「最小化到托盘/退出」询问也从 Electron 原生 dialog 改为应用内弹窗（IPC 双向 ask-close→answer-close，页面未就绪 1.5s 兜底隐藏到托盘） |
| **v1.5.26** | 点关闭 → 弹窗询问「最小化到托盘 / 退出应用」+ 可勾选「记住我的选择」；最小化到托盘后窗口隐藏但后台任务（定时监控/行情/更新检查）继续跑，首次缩托盘有气泡；托盘图标左键回主窗、右键菜单（打开/退出）；偏好存 `userData/window-prefs.json`；无托盘资源的裸 dev 环境回退直接退出 |
| **v1.5.25** | 文件查看器窗口控制：标题栏加最小化（−）→ 收起为右下角浮条（文件名+恢复+关闭），看盘时文件保持打开不丢；浮条点文件名或 ↑ 恢复；X/Esc 彻底关闭（图片 objectURL 同步释放）；图标库新增 Minus |
| **v1.5.24** | 文件管理器图片可直接预览：新增后端原文端点 `/api/workspace/raw`（扩展名白名单 png/jpg/jpeg/gif/webp/bmp/svg/ico、路径防越界、20MB 上限、走统一鉴权）；前端带 token 拉 blob → 弹窗内渲染 `<img>`（深色底居中、最高 58vh、关闭释放 objectURL）；state.db 等非图片二进制行为不变 |
| **v1.5.23** | 发文抗抖加固：① 代理节点检测从「单点 ping」升级为「发文全链路」——`_url_alive` 要求 **api ping + www.binance.com + public.bnbstatic.com 三端点全过**（实测踩坑：节点 ping 通但 www/S3 超时，检测照样放行、发文必败），任一不过即换节点；② square-post 的 `api()` 与 S3 `uploadToS3()` 对网络类错误（ETIMEDOUT/ECONNRESET/ECONNREFUSED/UND_ERR）**自动重试 3 次**（3s/6s 退避），业务错误（401/参数）立即抛；叠加 APP 侧换节点 → 单次发文最多 6 次尝试、跨 2 节点 |
| **v1.5.22** | 广场发文形态调整（实测驱动）：**实锤广场 OpenAPI 长文（contentType=2）正文是 `bodyTextOnly` 纯文本、永远插不了图**，只有单封面；带图只能走短贴（contentType=1，最多 4 图）。square-rich-post **默认改短贴多图**（封面图 + 24h 分时图 + 标题全文一贴发出，正文直接见图）；要传统长文加 `--article`；Agent 提示词与 SKILL.md 同步分流；短贴超时 240s / 输出缓冲 16MB |
| **v1.5.21** | 广场发文「上传超时失败」根治：Node undici 默认连接超时 10s，慢代理/币安 S3 握手稍慢即 `UND_ERR_CONNECT_TIMEOUT` → proxy-preload 全局挂载改 `connect 30s / headers 60s / body 120s`（scripts/ 与 runtime/ 同步）。沙箱命令解析两处修复：cat/ls/echo 等 wrapper 命令被误判「不在白名单」已放行；Windows 下 shlex posix 转义吞反斜杠（`--reuse F:\1\...` → `F:1...`）改 `posix=quoted` 保留原样并剥包裹引号。技能防呆：market-data klines 无数据改为直接报错（此前返回 `n:0` 的 OK，模型把垃圾参数当成功继续跑）；square-rich-post `--reuse` 自动锚定 workspace/square_rich 兜底 |
| **v1.5.20** | 安装包「无法自动关闭应用」修复：mihomo 内核 / runtime node / 后端都是**无窗口进程**，Inno 的 Restart Manager 关不掉（发文测试会拉起它们并持文件锁）→ 卡在 Closing applications。手动安装：`installer.iss` 新增 `PrepareToInstall` 预处理，taskkill 强杀 BAZZ.AGENT.exe / ScoutBackend.exe / mihomo.exe，node/python 按**路径锚定安装根**强杀（不误杀用户同名进程）；应用内更新：更新脚本杀残留进程名单补 mihomo 与 runtime node/python，setup 命令追加 `/FORCECLOSEAPPLICATIONS` 双保险 |
| **v1.5.19** | ① 技能网络失败自动切代理重试（命中 fetch failed/超时/ECONNRESET 等 → 实测代理池 → 找到能连通币安的节点带代理重试一次，输出标 `[auto-proxy]`；exec_sandbox 与 skills_client 双覆盖）；② 广场发文路由修复（此前一律路由到 square-post 裸发文本，Agent 自己手写简版文绕过富媒体管线 → 改两级路由：生成文章/行情文/深度分析默认 **square-rich-post**，只有现成正文/短帖/视频才走 square-post，改稿重发 `--reuse`）；③ 9 个已装技能补入提示词路由（news-sentiment / portfolio-review / track-monitor / query-token-audit / query-address-info / binance-tokenized-securities-info / binance-trading-signal / binance-sports-ai-analyzer 等此前从未绑定） |
| **v1.5.18** | 安装进度窗口：APP 退出后由更新脚本拉起**置顶 WinForms 跑马灯对话框**（步骤文案 + 动画），静默安装期间用户不再面对「什么都没发生」 |
| **v1.5.17** | 恢复版本守卫：拒绝安装比本地版本更旧的更新包（防降级），并清理陈旧缓存残留 |
| **v1.5.16** | 更新器 spawn 从不执行修复（去掉 `DETACHED_PROCESS`——它让 powershell 静默退出）；square-rich-post 升级 4h SMC 口径 |
| **v1.5.15** | square rich-post 技能上线（Pillow 封面 + 固定结构组稿 + `$cashtag/#hashtag`）；square-post `--text-file` 修复；台账 DATA_DIR 修复 |
| **v1.5.14** | 更新器 spawn 修复 + 启动自恢复；thinking 泄漏修复；中文币名后缀（CJK）解析修复 |
| **v1.5.13** | CJK 币名识别 + 防猜测规则（禁止模型编造交易对） |
| **v1.5.12** | 下单链路修复；审批卡补 margin/leverage；thinking 泄漏修复；追踪日期列修复 |

> 更早版本（v1.5.11 及以前）的完整要点见 `RELEASE_NOTES.md`；v1.3.x–v1.5.11 的脉络可一屏看全：
> `git log --tags --simplify-by-decoration --oneline --date=short --pretty="%ad %d %s"`

---

## 三、关键技术结论（重要，勿回退）

1. **baw 不读 HTTP(S)_PROXY**：baw 用 Node 20 全局 fetch（内置 undici），env 代理无效。
   解法 = `proxy_pool._ensure_node_preload()` 给所有 Node 子进程挂
   `NODE_OPTIONS --require runtime/proxy-preload.cjs`（undici `EnvHttpProxyAgent`）。
   有代理 env 才激活，直连无影响。**undici 有限制**：只认 HTTP 代理，纯 socks5 直连节点下 baw 仍可能失败（内核型/http 节点无此问题）。
2. **npm 剪包陷阱**：`runtime/` 无 package.json，npm 分两次装会把先装的包全剪掉（实测 74 包被剪）。
   **baw 与 undici 必须同一条命令安装**（prepare-runtime.js 与 skill_updater 均已遵守）。
3. **打包版无 PATH node/npx**：用户机器零 Node 环境。所有 node/npx 调用必须锚定
   `workspace.NODE_EXE` / `runtime/node/npx.cmd`（skills_client `_node_cmd()/_npx_cmd()`、exec_sandbox、wallet_runtime 均已处理，新代码照此模式）。
4. **打包态路径**：`.agents` 经 PyInstaller datas 落 `_internal/.agents`；`npx skills add` 必须
   `cwd=dirname(AGENTS_DIR)`，否则装到进程 cwd 后端读不到。Agent 沙箱路径锚 `workspace.WORKSPACE`，禁用 `__file__`/cwd 直拼。
5. **内核内置链路**：CI（release.yml「下载 mihomo 内核」步）→ `BAZZ_KERNEL_EXE` → build-desktop.js
   拷到产物 `.system/kernel/mihomo.exe` → proxy_kernel `_adopt_bundled_kernel()` 接化。
6. **发版流程**：改代码 → `package.json` 升版本 → `RELEASE_NOTES.md` 顶部加节 → commit →
   `git tag v*` → push（需代理：`git -c http.proxy=http://127.0.0.1:7897 push`）→ GitHub Actions
   自动构建 **setup.exe + delta + MANIFEST.json + SHA256SUMS** 并发 Release。CI 全程约 5-8 分钟。
   （v1.5.11 起 portable.zip 退役，setup.exe 是唯一全量包；delta 基线是上一版 MANIFEST 对比，不依赖上一版 zip。）
7. **广场发文只能靠短贴带图**（v1.5.22 实锤）：contentType=2 长文正文是纯文本、插不了图；带图必须走
   contentType=1 短贴（≤4 图）。square-rich-post 默认短贴多图，`--article` 才出长文形态。
8. **发文链路网络三坑**：① undici 默认 connect 10s 太短 → proxy-preload 全局改 connect 30s/headers 60s/body 120s；
   ② 代理节点检测必须 **api + www.binance.com + public.bnbstatic.com 三端点全过**（只 ping api 会放行坏节点）；
   ③ mihomo 内核被自动拉起时有数秒重启空窗（ECONNREFUSED）→ square-post 网络错误自愈重试 3 次。
9. **安装器必须强杀无窗口进程**：mihomo / runtime node / 后端无窗口，Inno Restart Manager 关不掉 →
   `installer.iss` `PrepareToInstall` 按路径锚定 taskkill，应用内更新脚本同步 + `/FORCECLOSEAPPLICATIONS`。
10. **更新器两条硬规则**：① 资产名比较必须统一小写（曾经全大写常量 vs 小写资产名 → 校验永远拿不到、形同虚设）；
    ② **fail-closed** —— 官方整包拿不到 SHA256SUMS 一律拒绝安装，不静默跳过；版本守卫防降级 + 清陈旧缓存。
11. **密钥加密**：`src/secrets.py` 用 Windows DPAPI（ctypes 调 crypt32，零新依赖）加密 settings 里的
    llm / BINANCE_API_KEY / BINANCE_API_SECRET / W3 密钥 / mcp_servers / mcp_token:*；
    非 Windows 自动降级并显式标 `plain:` 前缀；旧明文可读，下次保存自动迁移为密文。
12. **沙箱加固**（v1.5.29，v1.5.30 修正）：白名单解释器子进程环境剥离**第三方**凭据类变量
    （保留代理池/NODE_OPTIONS/PATH 运行必需项）。**⚠️ 注意例外**：`_ENV_ALLOW_EXACT` 精确放行
    `BAZZ_AUTH_TOKEN` —— 它是应用自己的本机回环令牌（Electron 每次启动随机生成，仅访问 127.0.0.1 自身后端），
    不是第三方凭据；v1.5.29 的按子串匹配曾把它一起剥离，导致技能全线 401（v1.5.30 修复）。
    新增第三方凭据类变量时**不要**往 `_ENV_ALLOW_EXACT` 里加。
    读/写/wrapper 三类路径解析统一 realpath 校验，符号链接解析后越界一律拒绝；
    路径报错对「明显不是路径」的实参（裸数字/状态码/URL/选项）附针对性提示（v1.5.31）。
13. **审批是调度屏障**（v1.5.29）：一批 tool_calls 遇到首个 `needs_approval` 工具执行后**立即停流等用户**，
    其后工具一律不执行；per-call 异常兜底为错误结果，**严禁整批重放**（会重复下单/重复写文件）。
14. **供应链锁版本**：`@binance/agentic-wallet@1.10.0`、`undici@6.21.1` 集中常量管理并防回滚；
    技能包不再后台静默升级，只保留手动入口并打印来源日志。
15. **其它既有约束**：全 API 带 `X-BAZZ-Token` 鉴权（前端 fetch 必须走 `api.ts`）；SQLite WAL + RLock；
    更新器只认本仓库 Release 白名单；UI 版本 ≥1.3.7 代理池在设置页折叠卡片（无独立导航）。

---

## 四、待办 / 优化清单

**✅ 第三方源码审查报告（提交 944ca16）已全部清零**：F01–F17 共 17 项缺陷 + 性能 5.1/5.2/5.3 已在
v1.5.28（F01/F02/F04/F08/F10/F13）+ v1.5.29（F03/F05/F06/F07/F09/F11/F12/F14/F15/F16/F17 + 性能）修复完毕，无遗留。

**P1 已全部完成**（v1.4.0）：行情 WS 实时流 / 微渲染 / 订单跟踪卡 / SL·TP 提醒 —— 勿重复开发。

**记忆系统已升级**（v1.4.1 + v1.4.4）：kind/source/hits 分类、注入预算+清洗、合并去重、敏感过滤、
memory_write 动作工具、MD 报告导出 —— 勿重复改造。

**Hermes 学习清单**：✅ 记忆系统（v1.4.1）、✅ 上下文持久化压缩 + 循环健壮性（v1.4.2）、✅ 会话搜索 +
标题自动生成（v1.4.3）、✅ 任务清单 todo_tool（v1.4.4）、✅ 自定义 cron 盯盘 + clarify 结构化追问 +
工具输出落盘（v1.4.5）、✅ delegate 子代理（v1.4.6）。
候选剩余（**用户暂缓，勿主动开工**）：side_question 旁问（/btw fork+禁工具+transcript 降级）、
fetch_url 增强（web_result_cache/url_safety/truncate）、turn_usage token 用量展示、
verification_stop 收尾验证门、思考小件（`<thinking>` 未闭合截断恢复 / 存储边界 think 标签清洗 / effort 三档旋钮）。
低价值勿做：MCP/OAuth 全家桶、浏览器自动化全家桶、语音 TTS、kanban/discord/飞书/HA、image/video 生成、tirith 安全全家桶。

**P2（下一版建议）**
1. 启动加速（splash 保底 5.2s，可压到就绪即切换）
2. 广场发文草稿箱 / 定时发布

**P3**
3. 多会话并行 Agent 任务
4. 行情自选列表置顶
5. 合约维度实时流恢复（若币安 fstream 对地区放开；或改用 fapi REST 短轮询 5-10s）

**已修完不要重复提**：API 鉴权/CORS、更新器白名单与校验、端口占用白屏、SQLite 并发、流中断、
行情接口去重、后端守护、子进程清理、代理池内核内置、baw 代理、技能自动更新、
安装器卡「无法关闭应用」、更新包降级、密钥明文入库。

---

## 五、关键文件地图

| 文件 | 职责 |
|---|---|
| `desktop_app.py` | FastAPI 全部路由（**130 个端点**：approvals / bots / chat / conversations / cron / gateways / llm / market / mcp / memory / orders / plugins / proxies / rooms / settings / skills / square / status / update / upload / wallet / workspace / x402） |
| `launcher.py` | PyInstaller 桌面后端入口 |
| `electron/main.cjs` · `preload.cjs` | 桌面壳（无边框 + 托盘 + 关窗询问 IPC） |
| `installer.iss` | Inno Setup 安装器（含 `PrepareToInstall` 强杀文件锁进程） |
| `build-desktop.js` · `ScoutBackend.spec` | 打包（含 `BAZZ_KERNEL_EXE` 内核内置） |
| `scripts/proxy-preload.cjs` | Node fetch 代理补丁（undici `EnvHttpProxyAgent` + 超时放宽） |
| `scripts/prepare-runtime.js` | CI 组装 runtime（node + baw + undici **同一条命令**安装） |
| `.github/workflows/release.yml` | CI（mihomo 下载步在 runtime 准备之后；产出 setup.exe + delta + MANIFEST + SHA256SUMS） |
| `src/agent_core.py` | intent → 工具编排 → 审批 → 执行；含 `meme_watch`、`_run_mcp_call`、`_run_delegate`、`auto_title` |
| `src/llm.py` | 多 provider LLM + 规则引擎兜底 + TOOLS 注册表 |
| `src/state.py` | SQLite 持久化（WAL + RLock + `update_cron_job` 原子读改写） |
| `src/secrets.py` | **v1.5.29 新增**：Windows DPAPI 密钥加密（零新依赖，非 Windows 降级 `plain:`） |
| `src/exec_sandbox.py` | Agent 工具沙箱（命令白名单 + 路径 realpath 校验 + 凭据变量剥离） |
| `src/executor.py` | 下单执行（CEX / 钱包双通道，`place_report()` 状态归一） |
| `src/risk_guard.py` | 风控规则 |
| `src/market_ws.py` | 行情 WS（`!miniTicker@arr` 现货实时 + `!forceOrder@arr` 爆仓流 + 分钟桶） |
| `src/scanner.py` | 全市场妖币雷达 v2（四层模型 + `_dedupe_fetch` 扇出去重） |
| `src/radar_tracker.py` | 妖币追踪战绩（moon/dump/expired + 智能持有 + 失败复盘） |
| `src/order_tracker.py` | 订单状态跟踪 + SL/TP 到价提醒（`_CLOSED_FOR_ALERTS`） |
| `src/cex_wallet.py` · `binance_cli.py` | Binance CEX HMAC 客户端 + `binance-cli` profile 同步 |
| `src/wallet_client.py` · `wallet_runtime.py` · `web3_wallet.py` | Agentic Wallet（baw CLI 包装 / runtime 解析 / 链上） |
| `src/x402_client.py` | x402 / B402 支付（Permit2 EIP-712 离线签名） |
| `src/mcp_client.py` | Binance Agentic MCP（OAuth 2.0 RFC 9728 + PKCE + 运行时 tools/list） |
| `src/skills_client.py` · `skill_updater.py` · `skill_launcher.mjs` | Skills Hub（安装/运行/移除 + 自动更新状态机 + 启动器） |
| `src/square_rich.py` · `square_store.py` | 广场富媒体发文（封面+分时图+组稿）/ 本地台账 |
| `src/proxy_pool.py` · `proxy_kernel.py` | 代理池（多节点健康 failover + 三端点检测）/ mihomo 内核接化 |
| `src/scheduler.py` · `reporter.py` | cron 守护 / 日报生成 |
| `src/room.py` · `bot_host.py` | 多 Bot 群聊房间 / Bot 宿主 |
| `src/plugin_host.py` · `plugins/scout-signals/` | 插件宿主（enabled 双重校验）/ 示例插件 |
| `src/ocr_engine.py` | 图片 OCR |
| `src/updater.py` | 增量自动更新（MANIFEST diff + delta + 版本守卫 + fail-closed 校验） |
| `src/workspace.py` | 工作区路径解析（NODE_EXE / WORKSPACE / AGENTS_DIR） |
| `frontend/src/views/*.tsx` | 12 个视图：ChatView / MarketsView / ExchangeView / WalletView / Web3SkillsView / SquarePostView / CouncilView / MemoryOverlay / SettingsView / ProxyPoolView / AdminPanels / PanicHaltModal |
| `frontend/src/components/Shell.tsx` · `frontend/src/index.css` | **界面骨架 + 视觉系统（改导航就改这两处）**：`.app-shell` 是纵向 flex，顶部单条 `.app-topbar` = 品牌 \| `.topbar-nav`（8 项，装不下时换行不滚动）\| 工具；`index.css` 用 RGB 通道 token（`--canvas-rgb` / `--ink-rgb` / `--gold-rgb` …）表达明暗两套主题，卡片、按钮、表格、胶囊、输入框全部走这一层 |
| `frontend/preview.html` · `frontend/preview.config.ts` · `frontend/src/preview/{main.tsx,mock.ts}` | **隔离 UI 预览**：独立 vite 入口，构建产物落 `outputs/ui-preview`（已 gitignore）。内存 fixture 覆盖约 48 个端点，fetch / XHR 全拦、CSP `connect-src 'none'`；`main.tsx` 一旦检测到 `bazzWindow` 直接抛错拒绝运行。**生产入口 `src/main.tsx` 不引入 fixture** |
| `tests/ui_preview_check.py` | **浏览器视觉验收**（先起 `127.0.0.1:5186` 静态服务指向 `outputs/ui-preview`）：8 视图 × 明暗 × 3 档宽度 = 64 项断言，产出 25 张截图 + `verification.json` |
| `frontend/src/i18n/locales.ts` | 双语（zh / en 两处都要加 key） |
| `frontend/src/api.ts` · `frontend/src/lib/live.ts` | 统一 fetch（带 X-BAZZ-Token）/ 行情实时快照 diff。**注意 `api.ts` 在 `src/` 根下，不在 `src/lib/`** |
| `desktop_app.py` `/api/workspace/raw` ↔ `api.ts` `workspaceRawBlob()` ↔ `ChatView.tsx` `openFile()` | **图片预览链路**（v1.5.34 接通）：`/raw` 是图片原文通道（20MB、扩展名白名单、`FileResponse`），与文本通道 `/read` 分离（`/read` 1.5MB 且含 NUL 即判二进制，PNG 头部就带 NUL 必被挡）。扩展名白名单**两端必须一致**，有交叉校验测试 |
| `desktop_app.py` `POST /api/square/posts/delete` ↔ `api.ts` `squarePostsDelete()` ↔ `SquarePostView.tsx` `delPost` / `clearAllFailed` | **广场台账删除链路**（v1.5.35 接通）：仅删本地台账（`src/square_store.py::delete_records`），**不调币安 API**。端点签名必须写 `payload: dict = Body(default_factory=dict)` —— FastAPI **没有全局 `request` 对象**，写 `request.get_json()` 会 500 |
| `electron/preload.cjs` `quitForUpdate()` ↔ `electron/main.cjs` `bazz:quit-for-update` ↔ `useUpdater.ts` | **程序性退出通道**（v1.5.35）：与「用户点 X」的 `bazz:win-close` / `bazz:answer-close` 询问链路**彻底分开**。更新走这条，直接 `app.quit()` 不弹窗；否则用户选「托盘」→ 进程不退 → 更新脚本等 180s 超时失败 |
| `tests/` 9 个套件：`test_v150_market.py` · `test_v151_radar_track.py` · `test_v1528_fixes.py` · `test_v1529_hardening.py` · `test_v1530_local_backend.py` · `test_v1531_prompt_fixes.py` · `test_v1532_proxy_kernel_state.py` · `test_v1534_image_preview.py` · `test_v1535_square_delete.py` | 离线回归套件（AST/桩隔离，不联网不下单） |

---

## 六、验证命令速查

```powershell
# Python 语法
python -m py_compile desktop_app.py src/xxx.py

# 前端类型 + 构建
cd frontend; npx tsc --noEmit; npx vite build

# 离线回归测试（10 个套件；用主目录自带 .venv 跑最省事 —— 它带 requests）
.venv/Scripts/python.exe tests/test_v150_market.py          # ✓ 全部通过（需 requests）
.venv/Scripts/python.exe tests/test_v151_radar_track.py     # ✓ 全部通过（需 requests）
.venv/Scripts/python.exe tests/test_v1528_fixes.py          # 15/15
.venv/Scripts/python.exe tests/test_v1529_hardening.py      # 23/23
.venv/Scripts/python.exe tests/test_v1530_local_backend.py  # 20/20
.venv/Scripts/python.exe tests/test_v1531_prompt_fixes.py   # 14/14（含本机 stub 后端端到端）
.venv/Scripts/python.exe tests/test_v1532_proxy_kernel_state.py  # 14/14
.venv/Scripts/python.exe tests/test_v1534_image_preview.py  # 7/7（源码/AST + 前后端白名单交叉校验）
.venv/Scripts/python.exe tests/test_v1535_square_delete.py  # 12/12（store 单元 + AST + TestClient 端到端含 401）
.venv/Scripts/python.exe tests/test_v1538_proxy_teardown.py # 5/5（取消连接必须停内核 + 摘预加载 + 清 env）

# 隔离 UI 预览 + 浏览器视觉验收（改前端视觉后必跑；不连真实后端）
cd frontend; npx vite build --config preview.config.ts       # 产物落 outputs/ui-preview
# 另开一个终端： python -m http.server 5186 --bind 127.0.0.1 --directory outputs/ui-preview
.venv/Scripts/python.exe tests/ui_preview_check.py           # 64 checks + 25 张截图 + verification.json

# baw 代理补丁 A/B 实测（本地代理 127.0.0.1:7897 / mihomo 7899）
$env:NODE_OPTIONS='--require="<repo>/runtime/proxy-preload.cjs"'
runtime\node\node.exe -e "fetch('https://api.binance.com/api/v3/time').then(r=>r.json()).then(console.log)"

# 发版（push 必须走代理，沙箱自带的 55773 连不上 GitHub）
git -c http.proxy=http://127.0.0.1:7899 push origin main v1.5.x
```

**环境说明**：`E:\hermes_app\binance-agent-os-scout` 自带 `.venv`（Python 3.11.15，含 `requests`），
上述命令可直接跑；链接工作树那份没有 `.venv`，需自建或用 `~/.workbuddy-ai/binaries/python/envs/default`。

---

## 七、本次核验结论（2026-09-12）

- **主开发目录已确认为 `E:\hermes_app\binance-agent-os-scout`**（`main` 主工作树）；
  `C:\...\Worktrees\main-a919d7e7` 是链接工作树（分支 `workbuddy/main-a919d7e7`）。
  此前改动曾误落在链接工作树上 —— 已全部迁移回主目录，两边 `git diff | git hash-object` 哈希一致 ✅
- **v1.5.38**（代理池回退到 v1.5.35 行为 + 保留「取消连接真的断开」）已完成并发布：`package.json` / tag 均为 v1.5.38
- **10 个离线回归套件全部通过**（用主目录自带 `.venv` 跑，它带 `requests`）：
  test_v150_market ✓ · test_v151_radar_track ✓ · test_v1528_fixes 15/15 · test_v1529_hardening 23/23 ·
  test_v1530_local_backend 20/20 · test_v1531_prompt_fixes 14/14 · test_v1532_proxy_kernel_state 14/14 ·
  test_v1534_image_preview 7/7 · test_v1535_square_delete 12/12 · **test_v1538_proxy_teardown 5/5** ✅
  （v1.5.37 新增的 `test_v1537_proxy_identity.py` 随代理池回退一起删除）
- **⚠️ 跑测试必须用项目自带 `.venv`**（`./.venv/Scripts/python.exe`，Python 3.11 + requests 2.34）；
  用 workbuddy 托管的 Python 3.13.12 **没有 `requests`**，会让所有导入 `proxy_kernel` / `proxy_pool`
  的测试以 `ModuleNotFoundError` 集体 FAIL —— 那是**环境问题不是代码问题**，别误判
- **⚠️ 源码扫描断言一律走 AST，且要看准**：`"xxx()" not in body` 这类纯文本切片会被**文档字符串和注释**
  骗到（v1.5.37/v1.5.38 连踩两次：`bootstrap()` 文档里写着 `_revive_kernel_async()`、
  `set_active` 注释里写着 `k.select("DIRECT")`）→ 统一用 `ast.walk` 只看 `ast.Call`；
  还要注意 **`ast.walk(if_node)` 会连 `orelse` 一起走**，只查某个 `if` 分支时必须遍历 `.body`
- **⚠️ 编辑表格类长行时先确认是否已存在同名行**：v1.5.38 加发布表格行时把 v1.5.37 那行一起复制进去，
  导致重复；改完务必 `grep -c "^| \*\*v<版本>\*\* |"` 复核为 **1**
- **界面改版（v1.5.36）已通过浏览器视觉验收**：`tests/ui_preview_check.py`
  **84 checks · 84 passed · 0 failed**，产出 25 张截图 + `verification.json`（8 视图 × 2 主题 × 4 档宽度）。
  `tsc --noEmit` 退出码 0；`vite build --config preview.config.ts` 成功
- **⚠️ 浏览器验收的三条硬经验**（v1.5.36 实测，都已在测试里落地）：
  ① agent-browser CLI 的守护进程**继承父进程 stdout** → Python 用 `capture_output=True` 抓输出会在
  **第一次调用就死锁**（表现为日志 0 字节、进程假死）；必须把 stdout/stderr 重定向到文件 +
  `Popen.wait(timeout)` + 超时 kill。
  ② 该 CLI 的 **`find role textbox` 定位不了普通 `<input>`**（四种参数写法全返回 Element not found），
  且**纯图标按钮的 `aria-label` / `title` 不参与 `--name` 匹配** → 一律用 `fill <css>` / `click <css>`。
  ③ 断言别只看「有没有报错」：真正抓到 bug 的是**几何断言** —— `.view-<id>` 是否真的挂上、
  `scrollWidth <= innerWidth`、以及**顶栏底边 ≤ 内容顶边**（顶栏被钉死高度、内容却是两行时，
  导航会溢出压住正文，肉眼极容易漏）
- **⚠️ 沙箱批量删除守卫**：一轮会话内删除次数超过阈值（50）会抛 **`SystemExit`** ——
  `except Exception` **抓不到**，会让脚本静默提前退出（表现为「Completed 0 checks / EXIT=0」）。
  测试脚本里别做批量删除；确需清理用 `> logfile` 之外的显式手段并 `except BaseException`
- **⚠️ 预览构建 `emptyOutDir: false`**：`outputs/ui-preview/assets` 会累积历史 hash 资源，
  排查「改了 CSS 却没生效」时**先确认 `preview.html` 引用的 hash 是新的**
- 上一版本文档（停留在 v1.5.3 / 表格到 v1.5.11）已按 v1.5.35 全量重写；`RELEASE_NOTES.md`
  版本顺序严格降序，且已模拟 CI 截取校验（v1.5.35 / v1.5.34 / v1.5.33 / v1.5.32 … 各**恰好命中 1 行**）
- **⚠️ `RELEASE_NOTES.md` 的两个坑**：① `##` 标题里**不要写别的版本号** —— CI 用
  `^##\s.*v<version>(?![.\d])` 找起点，标题里出现旧版本号会把起点抢走（v1.5.33 标题
  一开始写了「修 v1.5.32 引入的…」，导致 v1.5.32 也命中同一行）；② 最新版本块之后必须紧跟
  `## 📌 历史版本（更早版本）` 哨兵，**哨兵标题本身也不能含版本号**，否则它会把那一版的
  起点抢走；哨兵缺了则截取会吞掉下一版的 `# BAZZ.AGENT vX.Y.Z` 标题
- **⚠️ 给后端写端到端测试的三条经验**（v1.5.35 实测）：① 别用 `subprocess.Popen` 起后端再连 ——
  **同一脚本内始终连不上**（手动后台起 + curl 却 200）；改用 FastAPI 的 `TestClient` 直接
  `import desktop_app` 打端点，又快又稳。② 鉴权中间件是在 `if AUTH_TOKEN:` 装饰器块里、
  **模块 import 时**注册的，测试里 `import` 之后再去改 `desktop_app.AUTH_TOKEN` **无效**，
  必须在 `import` **之前**设 `os.environ["BAZZ_AUTH_TOKEN"]`。③ 用「下一个顶层语句」当函数体
  结束标记很脆 —— 两个 `useCallback` 一挪位置断言就假失败；改用**大括号配对**切函数体
  （并跳过字符串字面量，见 `test_v1535_square_delete.py::_slice_arrow_body`）
- **⚠️ 已知易误判点：`square_rich` 的「90 日区间」口径**。`src/square_rich.py` 用
  `min(k["lows"]) .. max(k["highs"])`（**真实高低**），而行情网关/用户核对时常拿
  `min(close) .. max(close)`（**收盘区间**）去比 —— 两个数字都对，只是口径不同，曾据此
  误判「稿件数据错误」。真正需要留意的是**措辞**：真实高低口径下价格常落在区间下沿而非「中轴」。
  核对时先确认口径再改稿，不要直接把收盘区间当成「实测值」覆盖。
- **⚠️ 打包版注意**：安装目录（如 `F:\1\BAZZ.AGENT`）的 Python 代码编译进
  `resources/scout-bundle/ScoutBackend/ScoutBackend.exe`，`_internal` 内无 `.py` 明文 ——
  **源码改动对已安装版本无效，必须重新构建发版**；只有 `.agents/skills/**/cli.mjs` 是明文
- 遗留（非本次范围）：紧急熔断目前只是前端本地状态、后端无路由；`place_oco_order()` 无调用方；
  无撤单接口、无 trades 台账 —— 交易闭环尚缺保护单/撤单/盈亏统计
