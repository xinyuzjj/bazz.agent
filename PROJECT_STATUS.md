# BAZZ.AGENT 项目状态交接（v1.5.64 广场分析拆成两套引擎 · 2026-09-16 更新）

> 用途：开新任务/新会话前快速恢复上下文。读完即可继续开发，无需翻旧对话。
> 仓库：`github.com/xinyuzjj/bazz.agent`
> **主开发目录：`E:\hermes_app\binance-agent-os-scout`（`main` 主工作树，改动请落在这里）**
> 链接工作树：`C:\Users\Administrator\WorkBuddy\Worktrees\binance-agent-os-scout\main-a919d7e7`
> （分支 `workbuddy/main-a919d7e7`，同一仓库同一提交 —— 在此改动**不会**影响主目录，注意别改错地方）
>
> ⚠️ **远端分支是 `main`，不是 `master`**（`git push origin main`；写 `master` 会报
> `src refspec master does not match any`）。**push 必须挂 Clash 代理**
> （`HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy = http://127.0.0.1:7897`），
> 不挂会报 `schannel: failed to receive handshake` / `CONNECT tunnel failed 502`。
>
> ⚠️ 上一版本文档停留在 v1.5.47，与代码实际差 14 个版本。**本文档已按 v1.5.64 全量校准（2026-09-16）。**
> 后续发展方向见 **`outputs/roadmap-v1.5.61.html`**（P0 校准收口 → P1 战绩回灌 → P2 交易闭环 → P3 产能与工程债；
> `outputs/` 已在 `.gitignore` 里，属本地产物、不入库）。

---

## 一、项目概览

- **形态**：Windows 桌面端（Electron 壳）+ FastAPI Python 后端 + TS/Vite 前端；同一套代码也能纯浏览器跑
- **定位**：币安 AI 交易终端 —— Agent 对话、行情（现货/合约/股票化代币）、交易方案卡、CEX 连接、Agentic Wallet、广场发文、Skills Hub、多 Bot 群聊、x402 支付
- **当前版本**：**v1.5.64（广场分析拆成两套引擎：代币走 SMC / 妖币走剧本三轴；两个挂起决策仪表化）**；
  `package.json` 与最新 git tag 均为 v1.5.64
- **规模**：后端 `src/` **35 个** py 模块 + `desktop_app.py`（**140 条路由**，含 1 个 `@app.websocket`）；前端 12 个视图 / 40 个文件；**22 个离线回归测试套件**（`tests/test_*.py`）+ 1 套浏览器视觉验收（`tests/ui_preview_check.py`）
- **工作区**：安装目录 `<安装根>/workspace`（state.db / proxies.json / 附件 / 日志 / spill / 复盘 / square_rich / **square_monster**）；只读盘回退 `%APPDATA%\BAZZ.AGENT\workspace`
- **预置资产**：`.agents/skills/` 官方技能包 + 8 个内置技能（含 **square-rich-post** 代币 SMC / **square-monster-post** 妖币剧本）+ `.agents/bots/` 5 个 Bot 人设（default-assistant / trend-hunter / liquidity-hunter / onchain-fox / risk-sentinel）

---

## 二、近期发布版本（v1.5.12 → v1.5.64）

| 版本 | 核心内容 |
|---|---|
| **v1.5.64** | **广场分析拆成两套引擎（用户定位：「分析代币和分析妖币要用不同的技巧；代币有 SMC，妖币按你的想法来」）**。① **为什么必须分开**：SMC（结构/BOS・CHoCH/OTE/OB/FVG）成立的前提是「价格由众多参与者的共同行为堆出来」，**控盘盘的 K 线是画出来的** —— 你看到的 OB 就是诱多区，「扫流动性」就是专门去点你止损的那一下；用 SMC 分析妖币等于拿散户的地图找庄家的门。② **新增 `src/square_monster.py`（约 900 行）妖币剧本引擎**：三轴 —— **位阶**（七格主剧本 吸筹→点火→已拉升→垂直拉升→派发顶部→崩跌→沉寂，**一票否决**，只有吸筹/点火且不晚才允许进场，晚的判据直接复用雷达自己的价格硬挡线 10% / OI 堆积线 5%，不另发明口径）、**控盘度**（「无现货/合约独大/换手畸高」结构性指纹 + 「空头付钱/拉升无爆仓」持仓侧指纹；**控盘度高 ≠ 坏消息**，它说明剧本真有人在演，含义是「离场必须果断」）、**燃料**（空头付钱最猛 / OI 蓄力 / OI 脉冲 / 大户拥挤 / 买盘主导 / 爆仓结构，其中「费率自极值回落」与 `radar_tracker._reversal_now` 同口径）。③ **出场四段全部镜像 `radar_tracker` 不许漂移**（逆向 10% / 顺向 25% / 自极值回撤 12% / 反转因子；测试逐一断言相等），且把一句实话写进稿子：**10x 下 10% 止损就是强平线 —— 打到止损等于本金归零，不存在「小亏一点」**，这条线不是「保护你」是「逼你认输」，能调的只有仓位。妖币用**固定 10%** 而非结构位（控盘盘的结构失效位是画出来的）。④ **只做多、绝不做空**：`plan.direction` 写死 long，源码不存在给空头出计划的路径（依据：3 笔做空全部被轧空击穿 10%，最大有利仅 0.0/0.0/1.4%）。⑤ **不给点位也是结论**：位阶不在允许窗口时 `plan=null` 且稿中明说，`paper_orders` 那时不建单（正确行为非漏单）。⑥ **接线**：后端 `/api/square/monster/compose` + `/api/square/monster/analyze`；新技能 **`square-monster-post`**（4 套妖币专属风格：剧本拆解体/埋伏笔记体/别接盘体/说人话体，与代币的 4 套刻意不重名——名字不同是为了让 agent 不能顺手互换）；产物 **`cover.png`（90 日走势 + 剧本进度带 + 三轴卡）** 与 **`chart_playbook.png`（1080×1080 竖版剧本卡，手机可读）**，结构与代币引擎同构（`square-post` 发布链路没改一行、`paper_tracker` 直接读 plan）；agent 提示词改成**先按标的分流 + 写明两者绝不可互相替代**，判断不了就先跑 monster（它不在视野内会明确报错并提示改用 rich）。⑦ **两个挂起决策仪表化**：新增 `state.radar_recheck_decisions()` + agent 工具 `meme_watch(mode='audit')` —— 决策 2（设不设最低分门槛）与决策 3（挡不挡「登记时已下跌」）从「等样本」变成「随时可问还差几笔」；**决策 2 的关键改进是先剔除 OPT-04 会挡掉的那批样本再看 score 还有没有判别力**（不剔除的话 score 只是 chg24 的影子，属重复计数），函数同时给「全部样本」与「OPT-04 之后」两张分桶表；**只给证据不改规则**，测试钉住。⑧ **验证**：新增 `tests/test_v166_monster_engine.py`（10 组 90+ 断言，含「monster 不得 import 任何 SMC 分析函数」的引擎分离护栏）；**全量 22 套件通过、`tsc --noEmit` 干净**；**真实数据端到端**跑过：真雷达 → `EIGENUSDT` 判「吸筹·第 1/7 格 → 可埋伏」，出稿 + 两图落盘，plan 入场 0.1908 / 止损 0.1717。⑨ **视觉验收修掉三处**：面积渐变把曲线下填色整片盖掉（改先铺底再填）、三轴卡文字超行**截半句话**（改带省略号）、价格线压在高/低标签上读不出来（改带底衬标签） |
| **v1.5.62** | **妖币雷达重做选币（用户定位：「止损 10% 与 10x 杠杆都不动，亏的根因是找妖币的方法错了」）**。① **只登记「尚未启动」的币**（登记即入场、不设候选态）：当日涨幅 ≥ 3% 的行不再进登记组、改划「已拉升」展示组——依据是 3 笔 moon 的登记依据**全无「24h +X%」**，而 9 笔「登记时已涨 7.4%~24.8%」全是 dump；回放 20 笔做多 −1086.5U/15.0% → **只留未启动 7 笔 +213.5U/42.9%**。② **绝不做空**：3 笔做空 3/3 全亏（CAKE/THETA/SAGA 被轧空，最大有利仅 0.0/0.0/1.4%），`SHORT_AMBUSH` 移出登记组只留展示；负费率改为**只标注+扣分、不改方向**（反例 VTHO −0.776% 却 +31.3%），费率评分由 `abs()` 改**方向化**。③ **候选池扩到「现货 ∪ 合约全市场」**：纯合约妖币（RAVE / LAB）此前完全不可见；用合约接口 `underlyingType == "COIN"` 挡掉近 200 个 `TRADIFI_PERPETUAL`（NVDA/XAU/QQQ…，实测会占掉 110 池位中的 57 个且大半是 TradFi），Alpha 币与 meme/中文盘保留；纯合约币 K 线走合约回退。生产实测：86 现货 + 24 加密纯合约、TradFi 零泄漏、**LABUSDT 已进池**。④ **控盘代理层**（链下、零新增外部依赖）：`无现货`/`合约独大`、`换手畸高`（>10x）、`空头付钱`（≤−0.15%）、`拉升无爆仓`（带数据可用性守卫），命中扣分并在雷达行出红色「控盘」标。**测试**：新增 `test_v162_radar_select.py` / `test_v163_manip_guard.py`，**全量 19 套件全绿**、`tsc --noEmit` 退出码 0 |
| **v1.5.61** | **广场发文模拟挂单（文章观点自动建档、7 天定胜负）**：发文成功后按文章 SMC 计划**自动建模拟挂单**（100U 保证金 × 10x，不碰真实资金）→ `paper_tracker` daemon 线程 60s 复查（5m K 线驱动纯函数 `_eval` 状态机）→ 7 天观察期到期强制结算。**状态机**：`pending`（挂单中）→ `open`（已入场）→ `win`/`loss`；同根 K 同时触 TP+SL **保守判 loss**；7 天未入场 → `noentry`；盈亏按 10x 杠杆折算**保证金收益率（%）**。**观望不建单**：`neutral`/`noplay`（止损距离超 8% 护栏）的文章 `plan=null`，绝不硬造方向。建单钩子 `_paper_order_from_run` 放在 `record_from_run`（agent / 手动两条发文路径全覆盖），`run_dir` 唯一索引幂等。广场页新增**双标签**「发文台账 \| 模拟挂单」+ 7 张统计卡（总单/挂单中/持仓/获利/亏损/未入场/胜率）+ 订单卡列表（现价、入场区、止损止盈、盈亏着色）、支持单条删除（二次确认，仅本地数据）；新增 `GET /api/square/paper` 与 `POST /api/square/paper/delete`，启动钩子 + 路由双保险拉起线程。**新增模块 `src/paper_tracker.py`（153 行）**；`state.paper_add/list/stats/update/remove`。**测试**：`tests/test_paper_orders.py` **17/17**（`_eval` 状态机直测 + 源码护栏） |
| **v1.5.60** | **入场结构位止损距离 ≤8% 护栏（10x 杠杆活着到止损的前提）**：用户实测两篇翻车（LSK 旧 OB 距现价 **-85%** 还挂单；牛来空头 OB 区宽 **58%**）→ 拍板定版：**入场 → 止损距离必须 ≤8%**（10x 强平线 ~9% 的前提），超限结构位**一律不用**；结构位降级链 **OB → OTE → 近 10 根摆动点 → 观望（noplay）**，空头镜像；`noplay` 只输出观望劝退行、**不带仓位算法**。**「我的看法」与上文自洽**：4h 结构与最终 bias 方向相反时必须说破级别分歧（如「日线大趋势分量更重 权重 3:1」），禁止上下文打架；**老结构如实标注**：OB 距现价超 25% 标「很远的老底/老顶，短期参考意义有限」。**修 bug**：`_pick_tp` 裸 `None` 解包（stat 无 k90 时 cands 混入 None）。**测试**：sizing 7 个过时用例更新到护栏口径（锚位断言 5→6 处）+ 新增护栏测试（暴涨/暴跌合成行情 → noplay）；**修复 test_v1541 的 `sys.modules["state"]` 桩在 pytest 收集期污染全局导致 test_v1535 e2e 假失败**（v1.5.41 起的存量问题）。全量回归 **237 passed** |
| **v1.5.59** | **大趋势判定定版 + 反向 OB 两态显示 + OTE 口径改 0.62-0.79**（用户拍板）。① **日线大趋势「趋势延续优先」**（v1.5.57 新增 `_htf_trend`）：锚定最近显著摆动点的**实体边缘**，**实体 K 收盘破位才翻转** —— 影线捅破不算、中途高点走低只算上涨中的调整；4h 图左上角标注「大趋势·日线 多头/空头/方向不明」。② **反向 OB 两态**（v1.5.58）：已实体收盘破结构最低/最高点 → **实线框**标「CHoCH 空头OB / CHoCH 多头OB」；尚未破 → **虚线框**标「跌破后的空头ob位 / 升破后的多头ob位」（用户指定也要显示）。③ **OTE 口径改版**：最优入场窗口 0.618-0.705 → **0.62-0.79**，一般用**中间值 0.702** 作入场参考位；新增 `ote_entry` 字段、计划入场价取 0.702；正文固定附「4h OTE 参考」行（窗口 + 中间值实际价），「我的看法」补 OTE 概念介绍；4h 结构图 OTE 金框自动跟随新窗口。**测试 23/23**（趋势延续/实体破位翻转/新旧锚取代护栏、反向 OB 实体破极值点才成立、虚线框护栏、OTE 0.62/0.79/0.702 窗口与中间值校验）。真实实测：BTC 锚 76,152（≈用户口径 76,165），影线捅破 76,000 收 77,191 **仍判多头**；ETH 多头 OTE 中间值 2,466.73 ≈ OB 上沿进价 2,465.82，双 SMC 位互相印证 |
| **v1.5.56** | **日线封面不标 SMC，另出 4h 结构图单独标记**（用户要求「日线图不需要标记 SMC，另生成一张 4 小时图标记出来」）：`draw_cover` 增 `kkey`/`smc_zones` 参数，日线封面（默认）**不画** SMC 标记；新增 **`draw_cover_4h`** —— 4h 蜡烛 + 多头/空头 OB（红绿矩形框）+ FVG（最近 3 条）+ OTE 窗口，**矩形区间框只出现在这张图**；compose 产物新增 **`chart_4h.png`**，短贴发布自动附图（**cover + 24h + 4h 共 3 张**）；4h 图高/低胶囊与信息卡文案标「近 20 日·4h」（120 根 4h ≈ 20 日），不与日线混淆；k4h 不可用时静默跳过该图、不影响出稿。**测试**：fallback **14/14**（新增 3 例：日线默认无标记护栏 / 4h 图出图 / 缺 k4h 报错）、styles 17/17、sizing 16/16 |
| **v1.5.55** | **封面图叠加 SMC 区域标记（OB / FVG / OTE 矩形区间框）+ 主图放大、量区压小**（用户要求）：**SMC 区域矩形框**画在蜡烛下层、实线描边、标签在框内左端 —— 多头 OB（绿框 12%）/ 空头 OB（红框 12%）/ FVG 缺口（按距现价取最近 3 条，现价下方绿、上方红，8%）/ OTE 斐波那契窗口（金色框 9%）；**最小可视高度 10px**（窄区间也能看出是「框」）；**不用虚线**（用户指定矩形区间）。**布局**：价格区 216px → **380px**（`y1 = H-236`），量区压至 **66px**（`vy1 = H-150`）。真实 BTC 实测 OB/FVG/OTE 色带聚焦现价附近供需、标签无遮挡；fallback 11/11、styles 17/17、sizing 16/16 |
| **v1.5.54** | **封面 K 线终版：恢复虚线网格 + 大量柱区样式（用户以真实 BTC 样图拍板）** —— 看过真实 BTC 数据渲染后指定按 v1.5.51 风格定版，**撤销 v1.5.53「极简无网格」与 v1.5.52「压矮量区」**：恢复横向虚线网格 + 日期锚点竖向淡网格（价格轴 SUB 色）；价格区 104 ~ H-320、蜡烛实体宽度 **0.62 步长**、矩形实体、**影线收暗 30%**；成交量区恢复**高排**（H-100），量能起伏与价格走势对照更直观；高/低胶囊标签、现价虚线胶囊、底部信息卡保留。真实 BTC 实测（fapi 90d）：现价 77,176 / 90 日 57,758~82,282 / 费率 +0.0081% / OI 79.94 亿 / FNG 61。fallback 11/11、styles 17/17 |
| **v1.5.53** | **封面 K 线定版「极简无网格」（用户三选一拍板）**：前两版样式仍不好看 → 出三种风格样图（霓虹渐变 / 空心经典 / 极简无网格）供挑选，拍板 **C · 极简无网格**：网格去掉虚线与竖线、只留极淡横线 + 更柔的价格轴文字；蜡烛**影线收暗至 45%**（柔和衬托实体），圆角细身实体保持 v1.5.52 比例；高/低胶囊、现价虚线胶囊、量区矮排、底部信息卡不变。fallback 11/11、styles 17/17 |
| **v1.5.52** | **封面 K 线图比例修正：价格区拉高、量区压矮、细身圆角蜡烛**（用户反馈「蜡烛图太扁、不喜欢扁宽样式；量柱区域希望弄小一点」）：价格区高度 296px → **380px**（`y1 = H-236`），蜡烛纵向舒展；蜡烛实体宽度 0.62 → **0.55 步长**、圆角矩形绘制、去扁感；成交量区高度 192px → **66px**（`vy1 = H-150`）；最小实体高度 1.2px → **1.6px**（十字星也能看清）。fallback 11/11、styles 17/17 |
| **v1.5.51** | **广场发文 K 线图美化：零新依赖、纯 Pillow 重绘**（用户反馈「生成的 K 线图希望更好看」；**不引 matplotlib**）。**封面主图（90d 蜡烛）**：背景纵向微渐变（深蓝黑 → 币安黑）告别死黑底 + 顶部金色描边呼应品牌色；网格虚线化 + 日期锚点补竖向淡网格；区间高/低虚线 + 胶囊标签（描边着色，深底可读）；蜡烛影线收暗一层、实体更立体；成交量柱收暗一档；「成交量」标签移左上；现价横线虚线化 + 右轴价格胶囊；底部信息条升级为**圆角卡片**（label/value 分层）。**24h 分时图**：面积填充改纵向渐变渐隐（贴线最亮 → 底部没入背景，mask 贴多边形）；折线**三层描边**（外圈辉光 → 中层过渡 → 亮芯）；高低点光晕圆点 + 胶囊标签；网格与现价线虚线化、与封面风格统一。fallback 11/11、styles 17/17、sizing 16/16、invalidation 10/10 |
| **v1.5.50** | **止盈瞄准 SMC 流动性区域（摆动点 → FVG → OB 块阶梯）**（用户指示：止盈按 SMC 来不要乱改；但 SMC 目标太近、盈亏比不好时要换其他止盈方法，目标可以瞄向流动性区域 —— FVG、OB 块等。现场触发：SNDK 空单止盈 1,620.06 距入场仅 0.4%、盈亏比 ≈0.3，用户质问「这个盈亏比是认真的吗」）。**止盈阶梯 `_pick_tp`**：① **首选 SMC 摆动点**（前高/前低 = 裸露流动性池），RR ≥1.5 直接用；② **太近（RR<1.5）→ 换其他流动性目标**，按近 → 远依次尝试取第一个 RR 达标的：FVG 缺口中位（标签「FVG 缺口回补」）→ 对侧 OB 块边缘（只在止盈方向上才纳入）→ 近 30 根 4h 极值 → 90 日极值；③ **标签如实标注实际采用的目标类型、不伪装成摆动点**；④ 全不达标取最远目标、RR 如实回报，由 `_plan` 决定「小仓/劝退」话术（v1.5.49 逻辑保留）。**测试**：sizing 扩到 **16/16**（摆动点达标不换 / 摆动点太近换 90 日极值且标签如实 / RR<1 劝退 / 1~1.5 小仓注脚 / 强 RR 无注脚）；fallback 11/11、styles 17/17、invalidation 10/10 |
| **v1.5.49** | **止盈呈现诚实化**：RR 从**入场参考价**如实计算（此前按现价算，挂单价接的单盈亏比全失真）；**RR<1**（目标贴入场、止损远 = 结构质量差）→ 如实劝退且**不再给仓位算法**；**1≤RR<1.5** → 标「盈亏比一般，只试小仓」；**RR≥1.5** → 正常计划 |
| **v1.5.48** | **代币化股票出稿修复：市场自动纠偏（spot / futures 双向兜底）**。**用户实测**：SNDK（SanDisk 代币化股票）广场发文失败 —— 「square-rich-post 需要 K 线数据生成封面图，但 SNDKUSDT 无 K 线数据」。**根因**：SNDK **不在币安现货市场**（现货 `/api/v3/klines` 返回 **400**），但它在 **U 本位合约（fapi）有完整永续 K 线**（实测 90 根 1d、24h ticker 正常）；发文链路把代币化股票当 `spot` 处理 → 1d/4h/1h 三档降级全在打现货 API → 全 400 → 误报「无 K 线数据」。**数据一直在，只是查错了市场。** **修复 `_collect` 市场自动纠偏**：请求市场 **1d+4h 都不可用、而另一市场 1d 可用** → 整体切换市场再继续出稿；与既有 RAYUSDT 场景（合约平线 → 回退现货）**正交互补、双向都通**；切换后 `fallback` 记录 `market→futures/spot`，费率/OI/多空比等衍生品维度照常带上。真实行情验证：`SNDKUSDT` 传 `spot` → 自动切 `futures`、90 根 1d 正常。**测试**：`test_v1545_kline_fallback.py` 扩到 **11/11**（新增 spot→futures 切换、futures→spot 切换、另一市场平线不误切三用例）；styles 17/17、sizing 11/11、invalidation 10/10 |
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
15. **妖币雷达的触发层必须方向感知**（v1.6.2，勿回退）：`_trigger_hit` / `_radar_score` 里
    任何对 `chg24` / `jump_L` / `speed5m` / `amp24` 取 `abs()` 的写法都会把雷达变成
    **波动率探测器** —— 谁暴动最厉害就登记谁，而暴动之后正是均值回归概率最高的。
    实测证据：19/22 单的触发依据含「24h 振幅 ≥15%」，dump 组登记时 24h 涨幅中位 18.1%。
    「点火（启动前）」与「已拉升（EXTENDED）」必须分开：后者不进 ignition 组、不登记。
    回归护栏见 `tests/test_v162_radar_select.py`（含 AST 断言：这两个函数内禁止对动量项取 abs）。
16. **妖币雷达「登记即入场」，且只有「尚未启动」的币可登记**（v1.6.3，勿回退）：不再设候选观察期
    （用户明确否决），所以过滤条件只能取自**登记时刻的快照特征**。定版：`change24_pct >= _TRIG_UP_CHG24(3.0)`
    的行**不进 ignition 登记组**，改划 takeoff 仅展示（不删除，保留「看见但太晚」的可解释性）。
    实测依据：3 笔 moon 登记依据全无「24h +X%」，9 笔「登记时已涨 7.4%~24.8%」全是 dump；
    只留未启动 → +213.5U / 42.9%（原来 −1086.5U / 15.0%）。
    同时 **`SHORT_AMBUSH` 不得回到登记组**（3 笔做空 3/3 全亏、最大有利仅 0.0/0.0/1.4%），
    且**负费率禁止硬阻断做多**（VTHO −0.776% 却 +31.3% moon，只标注+扣分、不改 side）。
    回归护栏见 `tests/test_v163_manip_guard.py`。
17. **其它既有约束**：全 API 带 `X-BAZZ-Token` 鉴权（前端 fetch 必须走 `api.ts`）；SQLite WAL + RLock；
    更新器只认本仓库 Release 白名单；UI 版本 ≥1.3.7 代理池在设置页折叠卡片（无独立导航）。

---

## 四、待办 / 优化清单

### 4.0 后续发展方向（2026-09-16 定版，完整版见 `outputs/roadmap-v1.5.61.html`）

> **妖币雷达专项**（编号任务表 + 证据 + 待拍板）见 `outputs/todo-v1.6.4.html`，
> 已落地的四项见 §7.4。本节以下为**产品整体**方向（公众号战绩回灌 / 交易闭环 / 工程债）。

**P1 · 战绩回灌（价值最高，时间敏感 —— 7 天结算意味着不早点收数据就要再等一周）**
1. `paper_orders` 表补 `style` / `rr` / `bias_source` 列（**当前只有 `status/pnl_pct/run_dir/post_id`，
   所以只能统计总胜率、回答不了「哪种风格/哪类标题/哪个 RR 档更容易赢」**），历史单按 `run_dir` 回读
   `meta.json` 回填；
2. 分维度聚合（风格 × 标题类 × 方向 × RR 档 × 币种）+ **最小样本门槛（n<20 标「样本不足」、不参与调权）**；
3. `_pick_style` / `_pick_title` 接权重表（高胜率加权、低胜率降权，**保留保底随机率**防只吃老路）；
4. 周度战绩复盘稿（真实结算数据出稿，本身即广场选题）。

**P2 · 交易闭环补齐（三个实测缺口 → **已完成两个**，剩一个）**
1. ✅ **撤单**（v1.5.63 完成）：`DELETE /api/v3/order` 签名 + `POST /api/orders/cancel` 路由 +
   订单卡「撤单」按钮（只对交易所 route、活跃态、有 order_id 的单显示）；`-2011` 按成功收敛。
   注意区分：`DELETE /api/orders/track` 只是**本地不再跟**，不是撤单；
2. ✅ **接上 `place_oco_order()`**（v1.5.63 完成）：改写为委托 `cex_wallet.place_oco`（原先一直在传**非法的 `legs` 数组**，
   且全仓无调用方所以没人发现），新增 `executor.place_protective` 在成交后挂 OCO，止损 **−10%**（刻意不可配置）；
3. ⬜ **trades 台账 + 已实现盈亏**：成交/手续费/已实现盈亏入库，接现有订单跟踪卡（**仍未做**）。

**P3 · 产能与工程债**
1. 草稿箱 / 定时发布（用户一天连发 3 篇，手动盯发很累）；
2. 多币批量出稿（一次生成 N 个币，人工挑）；
3. 启动加速（splash 保底 5.2s，可压到就绪即切换）；
4. **代理「不偷偷连」**：`ensure_working_proxy()` 目前技能失败会自动切节点（v1.5.19 行为），
   用户对此敏感 → 改成显式提示 + 开关。

**❌ 明确不做（用户拍板，勿再提议）**
- **熔断后端化 / 紧急熔断后端路由 —— 完全不需要**（2026-09-16 用户明确否决）。
  `PanicHaltModal.tsx` 保持前端本地状态即可，不要再补 `POST /api/panic/halt`；
- MCP / OAuth 全家桶、浏览器自动化全家桶、TTS、语义生图/视频、Hermes 旁问与 token 用量小件
  （前者本文档早已标「低价值勿做」，后者用户此前已表态暂缓）。

### 4.1 历史待办

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

**P2（旧编号 · 已并入 4.0 的 P3）**
1. 启动加速（splash 保底 5.2s，可压到就绪即切换）
2. 广场发文草稿箱 / 定时发布

**P3（旧编号 · 保留）**
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
| `desktop_app.py` | FastAPI 全部路由（**137 个端点**：approvals / bots / chat / conversations / cron / gateways / llm / market / mcp / memory / orders / plugins / proxies / rooms / settings / skills / square / status / update / upload / wallet / workspace / x402） |
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
| `src/square_rich.py` · `square_store.py` | 广场富媒体发文（封面 + 24h 分时 + 4h 结构图 + 组稿）/ 本地台账（发布成功入账钩子即在此建模拟挂单） |
| `src/paper_tracker.py` | **v1.5.61 新增**：广场发文模拟挂单复查（纯函数 `_eval` 状态机 pending→open→win/loss/noentry、5m K 线驱动、60s daemon 复查、7 天强制结算）；`state.paper_add/list/stats/update/remove` 落 `paper_orders` 表 |
| `frontend/src/views/SquarePostView.tsx` | 广场页双标签「发文台账 \| 模拟挂单」+ 7 张统计卡 + 订单卡列表（现价/入场区/止损止盈/盈亏着色/单条删除） |
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
| `tests/` **17 个套件**（`test_*.py`）：`test_v150_market` · `test_v151_radar_track` · `test_v1528_fixes` · `test_v1529_hardening` · `test_v1530_local_backend` · `test_v1531_prompt_fixes` · `test_v1532_proxy_kernel_state` · `test_v1534_image_preview` · `test_v1535_square_delete` · `test_v1538_proxy_teardown` · `test_v1539_square_invalidation` · `test_v1540_square_sizing` · `test_v1540_square_styles` · `test_v1541_llm_auth` · `test_v1543_model_pickers` · `test_v1545_kline_fallback` · **`test_paper_orders`** | 离线回归套件（AST/桩隔离，不联网不下单） |

---

## 六、验证命令速查

```powershell
# Python 语法
python -m py_compile desktop_app.py src/xxx.py

# 前端类型 + 构建
cd frontend; npx tsc --noEmit; npx vite build

# 离线回归测试（17 个套件；用主目录自带 .venv 跑最省事 —— 它带 requests）
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
.venv/Scripts/python.exe tests/test_v1539_square_invalidation.py  # 10/10（反向剧本必须锚本单止损 + AST 护栏）
.venv/Scripts/python.exe tests/test_v1540_square_sizing.py  # 16/16（SMC 风险口径 + 止盈阶梯 + 旧文案守卫）
.venv/Scripts/python.exe tests/test_v1540_square_styles.py  # 17/17（四风格数字逐字一致 + 渲染器禁重算）
.venv/Scripts/python.exe tests/test_v1541_llm_auth.py       # 22/22（PKCE/SSE/rotating 回存 + 授权页禁裸弹窗）
.venv/Scripts/python.exe tests/test_v1543_model_pickers.py  # 5/5（模型下拉 + 保存 toast）
.venv/Scripts/python.exe tests/test_v1545_kline_fallback.py # 14/14（90d→4h→1h 降级 + spot/futures 双向纠偏）
.venv/Scripts/python.exe tests/test_paper_orders.py         # 17/17（模拟挂单 _eval 状态机 + 源码护栏）

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

## 七、本次核验结论（2026-09-16）

### 7.0 本次校准（v1.5.47 → v1.5.61 全量补齐）

- **文档此前落后 14 个版本**：头部与版本表停在 v1.5.47，实际 `package.json` / 最新 tag 均为 **v1.5.61**；
  已补 **v1.5.48 ~ v1.5.61 共 14 行**版本说明。
- **规模口径三处纠正**：`src/` **34** 个 py 模块（原文 32）；`desktop_app.py` **137** 条路由（原文 130）；
  测试套件 **17 个** `test_*.py`（原文「10 个套件」，且原文只列出其中 10 个）。
  实际清单：v150_market / v151_radar_track / v1528_fixes / v1529_hardening / v1530_local_backend /
  v1531_prompt_fixes / v1532_proxy_kernel_state / v1534_image_preview / v1535_square_delete /
  v1538_proxy_teardown / v1539_square_invalidation / v1540_square_sizing / v1540_square_styles /
  v1541_llm_auth / v1543_model_pickers / v1545_kline_fallback / **paper_orders**。
- **交易闭环缺口实测**（不是推测，均为 grep 结果）：
  ① `src/executor.py:69` 的 `place_oco_order()` **全仓无调用方** —— 成交后从未挂过保护单；
  ② `desktop_app.py` 全文搜 `cancel` **命中 0 次** —— 没有任何撤单接口；
  ③ **无 trades 台账、无已实现盈亏** —— 真实战绩算不出来，只能看模拟单。
  三条均已登记进 §4.0 P2。
- **明确不做**：**熔断后端化 —— 用户拍板完全不需要**（原文 §七 结尾把「紧急熔断只有前端本地状态、
  后端无路由」当遗留项记着，现正式作废该条，见 §4.0 末「明确不做」）。
- **`.venv` 无 pytest**：v1.5.60 发布说明里的「全量回归 237 passed」应是另一环境跑的；
  本机 `.venv`（Python 3.11 + requests 2.34）**没有 pytest**，套件均为手写 assert 脚本，
  直接 `python tests/test_xxx.py` 跑即可（末尾会打印 `N/N`）。
- 后续发展方向见 **`outputs/roadmap-v1.5.61.html`**。

### 7.1 妖币「选币方法」修复（v1.6.2，代码已改 · **已随 v1.5.62 发版**）

**用户判断**：止损 10% 与 10x 杠杆**均不动**，亏损的根因是**寻找妖币的方法错了**。数据支持这个判断。

**真实战绩**（安装版 `F:\1\BAZZ.AGENT\workspace\state.db`，22 单已关单）：
19 dump / 3 moon = 胜率 **13.6%**，模拟 **-1420.5U / 2200U 本金（-64.6%）**。

**根因：触发层方向无关。** `_trigger_hit` 旧实现 11 条规则里 7 条用 `abs()` 或涨跌双向 ——
命中率最高的是「24h 振幅 ≥15%」（**22 单里 19 单命中 = 86%**），
雷达实际是个**波动率探测器**：谁当天暴动最厉害就登记谁，而暴动之后正是均值回归概率最高的。
配套三处同源缺陷：`_radar_score` 所有动量项用 `abs()`（崩跌与拉升同分、振幅越大分越高）、
候选池 `key=abs(chg24)` 取前 24（把追高写死在排序里）、`_stage_of` 把 3%~25% 全归「点火」。

**改动（4 个文件）**：
1. `src/scanner.py::_trigger_hit` → 三通道：机会型（**必须顺向**）/ 风险型（只产崩跌·做空语义）/
   波动率通道（只用于取确认因子，**不构成做多机会**）；新增常量 `_TRIG_UP_CHG24=3.0`、
   `_TRIG_MAX_CHG24=12.0`（追高线）、`_TRIG_DOWN_CHG24=-12.0`、`_TRIG_AMP_OK=20.0`；
2. `_radar_score` → 动量项只认顺向；涨幅越过 12% 起**扣分**（12%→25% 扣满 18）、
   振幅越过 20% 起**扣分**（20%→40% 扣满 10）、当日下跌倒扣；
3. `_stage_of` → 新增 **EXTENDED（已拉升）**：`chg24 > 12%` 且未到 VERTICAL 时归此阶段
   （旧实现误判为 IGNITION「启动前」）；`SHORT_AMBUSH` 增加**破位确认**
   `chg1h <= -2.0 or chg3 <= -5.0`（旧实现只要「滞涨」，实测 3 单全被轧空）；
4. `_cand_rank` 取代 `abs(chg24)` 排序；`state.RADAR_REENTRY_COOLDOWN = 24h`（关单后拒绝重登）；
   前端 `MarketRows.tsx` STAGE_META + i18n 中英补 `EXTENDED`（顺带补上 en 缺失的 SHORT_AMBUSH 键）。

**EXTENDED 只进 takeoff 组、不进 ignition 组** → 保持界面可见，但不再被登记进跟踪表。

**历史回放（同一批 22 单）**：

| | 单数 | 盈亏 |
|---|---|---|
| 现状（全部登记） | 22 | **-1420.5U** |
| 新规则（保留 11 单） | 11 | **-323.9U** |
| 被剔除的 | 11 | -1096.6U ← **全部是亏损单，3 个 moon 一个没误杀** |

胜率 13.6% → **27.3%**，保本线是 28.6% —— **离转正只差 1.3 个百分点**。
剩余 8 个亏损单全是「登记后直接破位下行（未启动即失败）」，光靠过滤救不回来。
⚠️ 这里原本写的「下一刀动**入场确认**（等第一次上攻站稳再进）」**已被用户否决** ——
用户定版 **登记即入场、不设候选态**（见 §7.2），改为在**登记时刻的快照特征**里找过滤条件。

**验证**：新增 `tests/test_v162_radar_select.py`（触发方向化 / 评分去绝对值 / EXTENDED /
做空破位 / 分组护栏 / 冷却期 / AST 护栏 / i18n 双语），**18 个套件全绿**；`tsc --noEmit` 退出码 0。

### 7.2 妖币控盘代理 + 只登记未启动 + 扩池到合约全市场（v1.6.3，代码已改 · **已随 v1.5.62 发版**）

**触发问题**：用户指出「你对于妖币的理解还不够，去搜一下什么是妖币，不然 rave 等代币」。
补齐认知后确认：**妖币 = 庄家控盘 96%+ 的收割机器**，不是「适合抄底的波动怪物」。三个真实样本：

- **RAVE**：0.25 → 27.9 美元（9 天 +10800%），24h 内 −90%；90% 供给在 3 个钱包；
  **空头爆仓占全部爆仓 82%**；费率年化 −1000%~−4000%。
- **LAB**：只上 Binance Alpha、**无币安现货**；ZachXBT 指内幕方控盘 **>95%**；链上持币 20,105；
  市值 58 亿 vs 流动性 900 万（**≈640 倍**）；7/6 $18.45 → 7/10 $0.82（3 天 −94%）；
  收割方式金句：「**只需维持横盘，做空者账户便会因费率清零**」。
- **MNT（Mantle）**：用户核对后判定**不是妖币**（合规 L2、费率中性、无控盘报道）→ **本轮不管**。

**链上接口核实**：币安公开 Web3 API **拿不到控盘率** —— `query-token-audit` 只有蜜罐/税率/合约验证，
`query-token-info.dynamic` 的 `holders` 是**持币人数**（非集中度），`query-address-info` 只查单个地址。
→ 改用**全链下代理**（`_manip_flags`，零新增接口）：① 现货成交占比 <5%（合约独大）
② 合约成交/持仓 >10x（换手畸高）③ 费率 ≤−0.15%（空头付钱）④ 当日 ≥8% 但 5m 爆仓 <5 万（拉升无爆仓）。
命中即扣分，并在前端出红标 `控盘 …`（hover 出 `manip_note`）。

**真实关单回放把初版规则否掉了一半**（安装版 23 单已关单 = 20 笔做多 + 3 笔做空，胜率 13.0%，−1386.5U）：

1. **「绝不做空」成立** —— 3 笔做空 3/3 全亏（CAKE +10.0% / THETA +10.3% / SAGA +11.0%），
   而**最大有利仅 0.0% / 0.0% / 1.4%**（从未跌过），复盘原文都是「直接轧空上行（未给回踩）」
   → `SHORT_AMBUSH` **移出 ignition 登记组**、改入 takeoff 仅展示组（顶部风险仍可见）。
2. **负费率不是做空的判别信号** —— 初版想做「负费率硬降级做多」，被 **VTHO 反例**否掉
   （费率 −0.776% 却 +31.3% moon）：合约里空头拥挤 → 费率为负，而**低负费率恰是挤空燃料**。
   → 改为**只标注 + 扣分、不改 `side`**（避免重蹈「过滤过紧误杀赢家」）。
   同时 `_radar_score` 的费率项由 `abs(funding)` 改为**方向化**（负费率不再加分）。
3. **真正的判别信号是「登记时是否已经启动」** —— 3 笔 moon 的登记依据里**全无「24h +X%」**
   （即 chg24 < 3%）；9 笔「登记时已涨 7.4%~24.8%」的**全是 dump**。
   （拆 `reasons_json` 必须逐条前缀匹配 —— 全文正则 `24h\s*([+-][\d.]+)%` 会把
   「OI 24h +8.3%」误读成 24h 涨跌，这个坑已踩过。）

**定版规则（用户拍板「登记后就直接入场、不需要候选态」）**：

```python
_started = (lambda r: (r.get("change24_pct") or 0.0) >= _TRIG_UP_CHG24)   # 3.0
rows_ign = [r for r in rows if r["stage"] in ("ACCUMULATION", "IGNITION") and not _started(r)]
rows_tk += [r for r in rows if r["stage"] in ("ACCUMULATION", "IGNITION") and _started(r)]
```

已启动的行**不删除**，划归 takeoff（仅展示），保留「雷达看见但太晚」的可解释性；
阈值复用 `_TRIG_UP_CHG24`，与依据生成同源，不会漂移。这是 v1.6.2「EXTENDED >12% 追高线」的**再收紧**
—— 实测连 3%~12% 这一段也晚了。

**回放**：

| | 单数 | 胜率 | 盈亏 |
|---|---|---|---|
| 现状（20 笔做多全登记） | 20 | 15.0% | **−1086.5U** |
| 只留未启动 | **7** | **42.9%** | **+213.5U** |

**验证**：新增 `tests/test_v163_manip_guard.py`（控盘 4 信号 / 爆仓数据可用性守卫 / 评分方向化 /
负费率只标注不阻断 / SHORT_AMBUSH 不登记 / 只登记未启动 / AST 护栏 / 链路护栏），
`test_v162_radar_select.py` 分组断言同步更新；**全量 19 套件全绿**，`tsc --noEmit` 退出码 0。

**扩池落地（用户拍板「扩到合约全市场」，已实现）**：雷达候选池由「只取现货」改为
**现货 ∪ 合约全市场**，按 `max(现货成交额, 合约成交额)` 排序取前 110（用 max 是为了救
「现货极浅但合约火热」的币，只看现货额它们会被 top_n 切掉，而那正是妖币的样子）。

扩池当场暴露两个必须处理的新问题，都已解决：

1. **TradFi 污染**：合约全市场混着近 200 个 `TRADIFI_PERPETUAL`（NVDA / TSLA / XAU / XAG / BZ /
   QQQ / SPY / SKHYNIX / SAMSUNG / DRAM / MSTR / HOOD…）。实测会占掉 110 个池位里的 **57 个**，
   且一大半是 TradFi —— 真正的加密纯合约币反而被挤出去。原先的 `EQUITY_PERPS` 白名单只有 31 条、
   实测只捞到 11 个，**不够用**。改用合约接口的 `underlyingType == "COIN"`（`/fapi/v1/exchangeInfo`，
   6 小时缓存）：权威字段、随上线自动更新，且 **Alpha 币（LAB 就是）与 meme/中文盘全在这一类里，必须保留**。
   过滤后生产实测：**86 现货 + 24 加密纯合约、TradFi 零泄漏、LABUSDT 已进池**。
2. **纯合约币会被静默丢弃**：`_klines_raw` 只打现货 K 线接口，纯合约币返回空 → 死在
   `_daily2_fetch` 的「不足 25 根」与逐币的 `len(c15) < 30` 两处。已加 `fut_fallback` 回退合约 K 线。

**新增口径字段（勿混用）**：`_radar_pool` 给每行打 `rank_qv`（排序/门槛）、`spot_qv`
（**现货成交额独立口径，纯合约币为 0**）、`no_spot`。控盘代理必须读 `spot_qv` ——
纯合约币的 `quote_volume` 是**合约口径**，拿它当现货额会算出「现货占比 50%」，
恰好把最该报警的币放过（这个坑已用测试锁住）。

**遗留观察（部分已落地，见 §7.4）**：扩池后生产实测登记组只有 2 个币且当日都在跌
（EIGEN −4.3% / XLM −9.63%，score 8 / 5）。「只登记未启动」只挡「已经涨起来的」，
**不挡「已经跌下去的」** —— 而 §7.1 回放里剩余 8 个亏损单正是「登记后直接破位下行（未启动即失败）」。
要不要再加一道「登记时不得已经破位下行」的过滤，**仍待用户拍板**（样本仅 8 笔，有过度拟合风险，
而历史上从未出现过一笔「登记时已下跌」的 moon，没有反例能证明该挡）；
给登记组设**最低分门槛**同理待拍板（读码确认 `radar_track_add` 目前**没有任何 score 下限**）。

**告警**：本机沙箱访问不到币安（直连与沙箱代理都 000），跑真实行情冒烟必须临时挂本机 Clash
（`http_proxy/https_proxy = http://127.0.0.1:7897`，四个大小写变量都要设 —— 只设大写会被沙箱的
小写变量盖掉，表现为池子静默为空）。

### 7.3 v1.5.38 时期核验（2026-09-12，历史留档）

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
- **⚠️ 打包版注意**：安装目录（如 `F:\1\BAZZ.AGENT`）的 Python 代码同时存在于
  `resources/scout-bundle/ScoutBackend/ScoutBackend.exe`（PyInstaller 包）与
  `_internal/src/*.py`（**明文，可直接核对**）—— 两者内容一致。
  **源码改动对已安装版本无效，必须重新构建发版**；只有 `.agents/skills/**/cli.mjs` 是明文
  （该条 2026-09-16 修正：原文写「`_internal` 内无 `.py` 明文」，实测明文存在，
  可直接 grep 安装版代码判断「新逻辑到底跑没跑」，见 §7.4）。
- 遗留（已登记进 §4.0 P2）：`place_oco_order()` 无调用方、无撤单接口、无 trades 台账 ——
  交易闭环尚缺 **保护单 / 撤单 / 盈亏统计** 三块。
  （原文此处还列了「紧急熔断后端无路由」，**该条已于 2026-09-16 作废**：用户拍板熔断后端化完全不需要。）

### 7.5 妖币雷达 v1.6.5 落地（2026-09-16，代码已改 · **随 v1.5.63 发版**）

完整登记清单与逐条落地口径见 `outputs/todo-v1.6.4.html`（10 条编号任务；状态徽章已更新）。

**这一批把登记清单里能落地的全部落地了**（21 个测试套件全绿、`tsc --noEmit` 退出码 0）：

| 编号 | 改动 | 关键点 |
|---|---|---|
| OPT-04 | 登记涨幅门槛分段（**用户拍板**） | 硬挡线 `_TRIG_LATE_CHG24 = 10.0`（原 3.0）；`3~10%` 段**降分 6 分、不挡**，交 OI 判据二次筛；触发线 `_TRIG_UP_CHG24 = 3.0` 不动 → **两条线解耦**。⚠️ **放宽是样本中性的**：3%~11.3% 之间 0 笔样本，测试里留了「该段样本数 = 0」的断言钉住这件事 |
| OPT-06 | 持仓质量「疑似假启动」提示 | `radar_tracker._fake_start`：跟踪 ≥24h 且**顺向历史极值** < 5% 打标；`tracks_view` 出 `fake_start` + `age_h`，前端 `TrackLine` 出红色徽章。**只提示不自动离场**（VTHO 反例：早期回撤 7.9% → +31.3%） |
| OPT-07 | ACCUMULATION 按 stage 分组胜率 | `radar_tracks_stats()` 新增 `by_stage` / `stages`；胜率分母**只用已关单**；前端战绩面板按组分列。**不删、不降权**（原文要求攒到 10 笔） |
| OPT-08 | 解除阻塞（**规则本身仍不动**） | 交付 `state.radar_snapshot_crosstab()` + `snap_axis_bucket()`：5 条快照轴按与判据同源边界分桶 × 结局交叉统计，门禁 `RADAR_SNAP_MIN_N = 20`；**NULL 单独计 unknown，不并进第一桶**。当前 `ready=False, n=0` |
| OPT-09 | 交易闭环：撤单 + 保护单 | `cex_wallet.cancel_order` / `cancel_open_orders`（签名 DELETE，`-2011` 按成功收敛）+ `place_oco`（**修了两处**：原 `executor.place_oco_order` 传非法 `legs` 数组、且从无调用方）；`POST /api/orders/cancel` 路由 + 前端撤单按钮；`PROTECT_STOP_PCT = 10.0` 下单即挂保护单（`signal.protect=True` + confirm 门内，挂不上只附 `protect_warning`） |
| OPT-10 | 安装版升级 | 发 `v1.5.63`，**覆盖安装后新雷达才开始跑、快照才开始攒** |

**本轮自己抓出来的两个错，都已修**：
1. **`_fake_start` 做空行用错轴**：第一版对 SHORT 也读 `max_gain_pct`，而做空的顺向是「跌」——
   等于拿反向指标判「假启动」，会把已经跌了 20% 的空单标成假启动。
   真实库实测：**修前标 16 条 → 修后 5 LONG + 3 SHORT**。现在按方向分轴，测试锁死；
   阈值仍只在 LONG 样本上校准过，做空侧是语义推广，已在注释写明。
2. **OPT-06 的命中口径**：原来笼统说「11/17 笔 dump」。真实口径是「**活过 24h 的 10 笔 dump 里 7 笔被标**」——
   17 笔里有 7 笔在 24h 前就逆向破 10% 关单了，根本走不到被标记那一步。源码注释、前端文案、测试三处都改成这个分母。

**真实库实测（安装版 55 行 / 31 pending，副本验证迁移）**：
- `by_stage`：IGNITION 3 moon / 13 dump = **18.8%**；ACCUMULATION **0 moon / 4 dump = 0%**；SHORT_AMBUSH 0 / 4 = 0%；
- `fake_start` 命中 8 条（5 LONG + 3 SHORT），皆跟踪 81~128h 且顺向浮盈 < 5%；
- `radar_snapshot_crosstab()` → `n=0, ready=False`（老库无 `snap_*`，v1.6.4 才加的列）。

**决策 2 / 3 补挖的证据（结论：仍然不能定）**：
- **最低分门槛**：已关单 LONG 按 score 分桶胜率 `0-9 → 0/1`、`10-14 → 0/4`、`15-19 → 1/1`、
  `20-24 → 25%`、`25+ → 10%` —— **完全不单调，最高分段最差**。原因是 score 与 chg24 混杂
  （9 笔「已涨」样本分数全在 25~58 且全是 dump）。OPT-04 挡掉 ≥10% 后这批本来就会出局，
  score 门槛基本是重复计数 → **建议先不设，等新样本看残余判别力**。
- **已下跌挡不挡**：按理由原文分桶，**「登记时已下跌」的已关单样本 0 笔**（XLMUSDT 是唯一一条，
  且仍在 pending）。0 样本 → 一挡就是拍脑袋 → **不挡**，等这类样本攒到 5 笔以上再看。

### 7.4 妖币雷达 v1.6.4 落地（2026-09-16，代码已改 · **已随 v1.5.63 发版**）

完整登记清单见 `outputs/todo-v1.6.4.html`（10 条编号任务 + 3 项待拍板）。

**先纠正两条我们自己写错的东西**：
1. **「实测连 3%~12% 这段也晚了」——数据不支持。** 9 笔「登记时已涨」样本的 `chg24`
   最小是 **11.3%**（ETHFI），其余 13.9%~21.9%，**3%~11.3% 之间一笔样本都没有**。
   有证据的只是「≥11.3% 该挡」，3% 这个门槛是**外推**。已改掉源码注释与方案里的这句话。
2. **安装版「查不到代码」是错的**：`F:/1/BAZZ.AGENT/resources/scout-bundle/ScoutBackend/_internal/src/scanner.py`
   **有明文**（1984 行 vs 仓库 2319 行）。对 `_radar_pool` / `futures_crypto_syms` / `spot_qv` /
   `_TRIG_UP_CHG24` / `_started` 逐个 grep **全部 0 命中** → 安装版 = 1.5.61，新雷达一行没跑。

**本轮四项改动（20 个测试套件全绿，前端 `tsc --noEmit` 通过）**（后续 OPT-04/06/07/08/09 见 §7.5，累计 21 套）：

| 编号 | 改动 | 关键点 |
|---|---|---|
| OPT-01 | `radar_tracker._current_price` 三级兜底 | 现货 WS → **合约 WS** → 合并快照；`_tick` 用具名 `setdefault` 合并 `futures_snapshot()`（**现货优先**，不让合约价差污染现货币） |
| OPT-02 | `radar_tracks` 补 5 个登记快照列 | `snap_chg24/snap_oi24/snap_amp24/snap_funding/snap_rvol15`；**缺值写 NULL 不写 0**（`state._snap_val`）；老库走 `ALTER TABLE` 迁移 |
| OPT-03 | 新增 OI 判据 `_TRIG_MAX_OI24 = 5.0` | 登记条件由「价格未启动」扩为「价格未启动 **且** 持仓未堆积」；`_piled()` 对未测到 **fail-open** |
| OPT-05 | 「空头付钱」扣分 10 → 3 | VTHOUSDT 费率 −0.776% 却是 +31.3% moon，判别力弱；风险提示语义保留 |

**OPT-03 的证据（依据原文级统计，前缀 `fullmatch` 避免把 `OI 24h` 误读成 `24h` 涨跌）**：
- `OI 24h` 为**正**的样本 **9 笔 → 9 笔全 dump**（+7.4% ~ +106.3%）；3 笔 moon **从无正值 OI**；
- `OI 24h` 为负只 2 笔（SEI −6.2 dump / VTHO −14.5 moon）→ 负值无判别力；
- 叠加回放（`chg24>=3` 或 `oi24>=5` 即不登记）：20 笔 → 存活 **9 笔（3 moon / 6 dump）**，
  胜率 15.0% → **33.3%**，**零误杀**。
- 阈值取 5.0 是**故意的零外推**：依据生成处本身就是 `abs(oi_chg24) >= 5` 才写进 reasons。
  更严的 `oi_chg24 <= 0` 在 (0, 5) 区间无样本，属外推，未采用。

**生产冒烟（挂本机 Clash，独立临时 `BAZZ_WORKSPACE`，勿污染仓库夹具）**：
池 110（纯合约 25 个：HYPE / AKE / AIN / 龙虾 / PONS…，TradFi 未见）；扫描正常；
登记组落到 EIGEN（−2.16 / oi24 +0.45）与 XLM（−9.59 / oi24 −7.65），**5 个快照列全部写库成功**。
取价链路实测：`HYPEUSDT` **不在现货快照里**，但合并快照有 78.844 →
`_current_price` 返回 78.844（修复前会是 `None` → 永久卡 pending）。

**原待用户拍板 3 项 → 见 §7.5 的处理结果**：① 已按「不挡 + 降分」落地（用户拍板）；
②③ 补挖证据后**仍不能定**（②分数与涨幅混杂、③0 笔样本），已写明建议与再评估条件。
**运维待办**：安装版若仍停在 1.5.61，需覆盖安装到 **v1.5.64**，否则 §7.4~§7.6 的全部改动都不生效。

---

### 7.6 广场分析拆成两套引擎：代币 SMC / 妖币剧本三轴（v1.6.6，代码已改 · **随 v1.5.64 发版**）

**用户原话**：「我让 agent 分析代币和分析妖币生成的广场内容要用不同的技巧，分析代币就有 smc，
分析妖币根据你的想法来。」—— 这不是「把同一套指标调两组参数」，是**两套分析范式**。

**为什么必须分开（这是整套设计的立足点，勿回退）**
SMC 成立的前提是「价格由众多参与者的共同行为堆出来」—— 结构位、订单块之所以有用，
是因为那里真的有人挂单。**控盘盘不满足这个前提**：它的 K 线是画出来的，你看到的 OB 就是诱多区，
你看到的「扫流动性」就是庄家专门去点你止损的那一下。**用 SMC 分析妖币 = 拿散户的地图找庄家的门。**

**妖币看的是控盘意图 —— 剧本三轴**

| 轴 | 看什么 | 关键约定 |
|---|---|---|
| **① 位阶** | 七格主剧本演到哪：吸筹 → 点火 → 已拉升 → 垂直拉升 → 派发顶部 → 崩跌 → 沉寂 | **一票否决**：只有吸筹/点火**且不晚**才允许进场。做空埋伏/异动是旁支，直接不进 |
| **② 控盘度** | 「无现货 / 合约独大 / 换手畸高」结构性指纹 + 「空头付钱 / 拉升无爆仓」持仓侧指纹 | **控盘度高 ≠ 坏消息**：它说明剧本真有人在演，含义是「离场必须果断」（控盘盘没有自然买盘接你的货） |
| **③ 燃料** | 空头付钱（最猛）/ OI 蓄力 / OI 脉冲 / 大户拥挤 / 买盘主导 / 爆仓结构 | 「费率自极值回落」与 `radar_tracker._reversal_now` **同口径** |

「晚不晚」的判据**直接复用雷达自己的两条闸门**（`_TRIG_LATE_CHG24 = 10.0` 价格硬挡线、
`_TRIG_MAX_OI24 = 5.0` OI 堆积线），**不另发明口径** —— 否则稿子里说的位阶会和用户在 app 里看到的对不上。

**出场四段：一个数都不改，全部镜像 `radar_tracker`**

| 段 | 值 | 镜像来源 |
|---|---|---|
| 认输线 | 逆向 10% | `radar_tracker.FAIL_HIT` |
| 达标线 | 顺向 25% | `radar_tracker.GAIN_HIT` |
| 移动止盈 | 自持有期极值回撤 12% | `radar_tracker.TRAIL_PCT` |
| 提前落袋 | 反转因子命中 | `radar_tracker._reversal_now` |

`tests/test_v166_monster_engine.py::test_constant_mirrors` **逐一断言相等**，漂移即红。

**⚠️ 必须写进稿子的一句实话**：在 10x 下 **10% 止损就是强平线** ——
100U 本金 ×10x = 1000U 名义，逆向 10% 正好亏 100U。**打到止损等于本金归零，不存在「小亏一点」。**
这条线不是「保护你」，是**逼你认输**；能调的只有仓位，不是这条线。这不是软件不够聪明，是杠杆的算术。
另外妖币**用固定 10% 而不是结构位**止损，原因与上面同源：控盘盘的「结构失效位」是画出来的，
挂结构止损等于把止损位送给庄家。

**只做多、绝不做空；不给点位也是结论**
- `plan.direction` **写死 `long`**，源码里不存在给空头出计划的路径（有测试断言）。
  依据：安装版 3 笔做空**全部被轧空击穿 10% 强平线，最大有利仅 0.0% / 0.0% / 1.4%** —— 从一开始就没跌过。
- 位阶不在允许窗口时 `plan = null` 且稿中明说「本次不给点位」，**绝不硬造方向**；
  副作用 `paper_orders` 那时不建单，这是**正确行为而非漏单**（`_paper_order_from_run` 遇空 plan 直接返回 None）。

**接线（一处漏了就等于没接上）**
- 后端：`/api/square/monster/compose`（出稿）+ `/api/square/monster/analyze`（只看结论）；
- 新技能 `.agents/skills/square-monster-post/`（`SKILL.md` + `scripts/cli.mjs`）；
  **4 套妖币专属风格**（`playbook` 剧本拆解体 / `hunt` 埋伏笔记体 / `warn` 别接盘体 / `plain` 说人话体）
  与代币引擎的 4 套（`review/diary/qa/blunt`）**刻意不重名**——名字不同是为了让 agent 不能顺手互换（有测试）；
- 产物：`cover.png`（90 日走势 + **剧本进度带** + 三轴卡 + 底部读数条）、
  **`chart_playbook.png`（1080×1080 竖版剧本卡，手机可读：结论 + 进度带 + 这一格/下一格 + 控盘与燃料 + 出场四段）**；
  `meta.json` 结构与代币引擎**同构**（含 `plan` / `axes` / `exit_segments` / `missing`）→
  **`square-post` 发布链路没改一行、`paper_tracker` 直接读 plan**；
- agent 路由：`agent_core.py` 提示词改成**先按标的分流**并写明「**两者绝不可互相替代**」；
  判断不了就先跑 monster —— 它发现该币不在妖币雷达视野内会**明确报错并提示改用 rich**，不会硬编一篇。

**两个挂起决策仪表化（「挂着等」是最糟糕的形态）**
- 新增 `state.radar_recheck_decisions(direction="LONG")` + agent 工具 **`meme_watch(mode='audit')`**。
  决策 2（设最低分门槛）/ 决策 3（挡「登记时已下跌」）从「等样本」变成**随时可问「还差几笔」**：
  样本不够如实说 gap，够了直接给结论并给建议。
- **决策 2 的关键改进**：先**剔除 OPT-04 会挡掉的那批样本**再看 score 还有没有判别力 ——
  不剔除的话 score 只是 chg24 的影子（高分样本恰好都是「已涨」的 dump），属**重复计数**。
  函数同时输出「全部样本」与「OPT-04 之后」两张分桶表，让这件事看得见。
- 门禁：单桶 n≥5 才看胜率（`DECISION_BUCKET_MIN_N`）；「登记时已下跌」n≥5 才下结论
  （`DECISION_DOWN_MIN_N`）。**只给证据不改规则**（有测试钉住），加闸门是产品决策要用户拍板。

**验证**
- 新增 `tests/test_v166_monster_engine.py`（10 组 / 90+ 断言）：引擎分离（**monster 不得 import
  任何 SMC 分析函数** `_smc/_bias/_plan_levels/...`）、常量镜像逐一相等、位阶一票否决、
  合议优先级（**重度控盘 + 满油但位阶在派发 → 仍离场**）、只做多、不给点位、四风格不重名且结论一致、
  折行截断带省略号、产物同构、符号不在视野内报错、决策复核 waiting↔ready 双向 + OPT-04 剔除口径、
  接线（提示词 / 工具 schema / 技能发现 / description 无残留 markdown / 前端文案）。
- **全量 22 个套件通过；`tsc --noEmit` 退出码 0。**
- **真实数据端到端**（挂 Clash 代理连真币安）：真雷达 110 池 / 触发 62 → `EIGENUSDT` 判
  「吸筹 · 第 1/7 格 → 可埋伏」，出稿 + 两图（75KB / 98KB）落盘，
  `plan` = 入场 0.1908 / 止损 0.1717（= 入场 ×0.9 ✓）。
- **视觉验收修掉三处**（用户对视觉要求高，这类问题必须自己先看出来）：
  ① 面积渐变把曲线下填色整片盖掉（`_vgrad` 画在 `polygon` 之后）→ 改成**先铺底再填**；
  ② 三轴卡文字超行被**截半句话**（读稿人以为稿子坏了）→ 新增 `_wrap_clamp()` 加省略号；
  ③ 价格线常正好压在高/低/现价标签上导致读不出来 → 新增 `_chip_label()` **带深色底衬**。

**engine 分离的护栏（后来者最容易踩的坑）**
`square_monster.py` 里 `from square_rich import ...` **只有一处，且是显式白名单**
（颜色 / 字体 / 格式化 / 画图原语 / 取数 / 仓位口径）。**一旦有人往里加 `_smc` / `_bias` /
`_plan_levels` / `_pick_tp`，这套框架就会悄悄退化成 SMC 的皮，而且不会报任何错。**
测试用 `hasattr` 白名单 + 源码里「`from square_rich import` 只出现一次」双重钉住。

