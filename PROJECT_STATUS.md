# BAZZ.AGENT 项目状态交接（2026-09-10 会话收尾）

> 用途：开新任务/新会话前快速恢复上下文。读完即可继续开发，无需翻旧对话。
> 项目路径：`e:\hermes_app\binance-agent-os-scout` ｜ 仓库：`github.com/xinyuzjj/bazz.agent`

---

## 一、项目概览

- **形态**：Windows 桌面端（Electron 壳）+ FastAPI Python 后端 + TS/Vite 前端
- **定位**：币安 AI 交易终端 —— Agent 对话、行情（现货/合约/股票化代币）、交易方案卡、CEX 连接、Agentic Wallet、广场发文、Skills Hub
- **当前版本**：v1.4.4（已发布 Latest，4 资产齐全）
- **工作区**：安装目录 `<安装根>/workspace`（state.db / proxies.json / .skill_update.json / 附件 / 日志）

## 二、近期发布版本

| 版本 | 核心内容 |
|---|---|
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

**Hermes 学习清单进度**：✅ 记忆系统（v1.4.1）、✅ 上下文持久化压缩 + 循环健壮性（v1.4.2）、✅ 会话搜索 + 会话标题自动生成（v1.4.3）、✅ Agent 任务清单 todo_tool（v1.4.4）；候选剩余（按价值）：用户自定义 cron 盯盘（cronjob_tools）、旁问模式（side_question）。低价值勿做：MCP/OAuth、浏览器自动化全家桶、语音 TTS。

**P2 进度**：✅ 空 catch 补错误提示（v1.4.3+4）、✅ 记忆导出 Markdown（v1.4.4）；剩余：启动加速、广场发文草稿箱/定时发布。

**P2（下一版建议）**
1. 启动加速（splash 保底 5.2s 可压到就绪即切换）
2. 前端空 catch 补错误提示（SettingsView L52-69 等）
3. 广场发文草稿箱/定时发布
4. Agent 记忆导出 Markdown 报告

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
