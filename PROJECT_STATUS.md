# BAZZ.AGENT 项目状态交接（v1.5.33 · 2026-09-12 更新）

> 用途：开新任务/新会话前快速恢复上下文。读完即可继续开发，无需翻旧对话。
> 仓库：`github.com/xinyuzjj/bazz.agent`
> **主开发目录：`E:\hermes_app\binance-agent-os-scout`（`main` 主工作树，改动请落在这里）**
> 链接工作树：`C:\Users\Administrator\WorkBuddy\Worktrees\binance-agent-os-scout\main-a919d7e7`
> （分支 `workbuddy/main-a919d7e7`，同一仓库同一提交 —— 在此改动**不会**影响主目录，注意别改错地方）
>
> ⚠️ 上一版本文档停留在 v1.5.3 / 表格到 v1.5.11，与代码实际差 18 个版本。**本文档已按 v1.5.33 全量校准。**

---

## 一、项目概览

- **形态**：Windows 桌面端（Electron 壳）+ FastAPI Python 后端 + TS/Vite 前端；同一套代码也能纯浏览器跑
- **定位**：币安 AI 交易终端 —— Agent 对话、行情（现货/合约/股票化代币）、交易方案卡、CEX 连接、Agentic Wallet、广场发文、Skills Hub、多 Bot 群聊、x402 支付
- **当前版本**：**v1.5.33**（`package.json` 与最新 git tag 一致）
- **规模**：后端 `src/` 32 个 py 模块 + `desktop_app.py`（**130 个 API 端点**）；前端 12 个视图 / 40 个文件；7 个离线回归测试套件
- **工作区**：安装目录 `<安装根>/workspace`（state.db / proxies.json / 附件 / 日志 / spill / 复盘 / square_rich）；只读盘回退 `%APPDATA%\BAZZ.AGENT\workspace`
- **预置资产**：`.agents/skills/` 官方技能包 + `.agents/bots/` 5 个 Bot 人设（default-assistant / trend-hunter / liquidity-hunter / onchain-fox / risk-sentinel）

---

## 二、近期发布版本（v1.5.12 → v1.5.33）

| 版本 | 核心内容 |
|---|---|
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
| `frontend/src/i18n/locales.ts` | 双语（zh / en 两处都要加 key） |
| `frontend/src/lib/api.ts` · `live.ts` | 统一 fetch（带 X-BAZZ-Token）/ 行情实时快照 diff |
| `tests/test_v150_market.py` · `test_v151_radar_track.py` · `test_v1528_fixes.py` · `test_v1529_hardening.py` | 4 个离线回归套件（AST/桩隔离，不联网不下单） |

---

## 六、验证命令速查

```powershell
# Python 语法
python -m py_compile desktop_app.py src/xxx.py

# 前端类型 + 构建
cd frontend; npx tsc --noEmit; npx vite build

# 离线回归测试（6 个套件；用主目录自带 .venv 跑最省事）
.venv/Scripts/python.exe tests/test_v150_market.py          # ✓ 全部通过（需 requests）
.venv/Scripts/python.exe tests/test_v151_radar_track.py     # ✓ 全部通过（需 requests）
.venv/Scripts/python.exe tests/test_v1528_fixes.py          # 15/15
.venv/Scripts/python.exe tests/test_v1529_hardening.py      # 23/23
.venv/Scripts/python.exe tests/test_v1530_local_backend.py  # 20/20
.venv/Scripts/python.exe tests/test_v1531_prompt_fixes.py   # 14/14（含本机 stub 后端端到端）

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
- **v1.5.32**（HEAD `e6fc42a`）已发布：CI `release-setup` success，Release 含
  `BAZZ.AGENT-v1.5.32-setup.exe`（134.8 MB）+ `MANIFEST.json` + `SHA256SUMS`；
  **v1.5.33** 的改动已完成（见 §二 表格首行）
- 7 个离线回归套件**全部通过**：test_v150_market ✓ · test_v151_radar_track ✓ ·
  test_v1528_fixes 15/15 · test_v1529_hardening 23/23 · test_v1530_local_backend 20/20 ·
  test_v1531_prompt_fixes 14/14 · test_v1532_proxy_kernel_state 14/14 ✅
- 上一版本文档（停留在 v1.5.3 / 表格到 v1.5.11）已按 v1.5.33 全量重写；`RELEASE_NOTES.md`
  版本顺序严格降序，且已模拟 CI 截取校验（v1.5.33 小节 34 行、无历史版本标题渗入）
- **⚠️ `RELEASE_NOTES.md` 的两个坑**：① `##` 标题里**不要写别的版本号** —— CI 用
  `^##\s.*v<version>(?![.\d])` 找起点，标题里出现旧版本号会把起点抢走（v1.5.33 标题
  一开始写了「修 v1.5.32 引入的…」，导致 v1.5.32 也命中同一行）；② 最新版本块之后必须紧跟
  `## 📌 历史版本（更早版本）` 哨兵，**哨兵标题本身也不能含版本号**，否则它会把那一版的
  起点抢走；哨兵缺了则截取会吞掉下一版的 `# BAZZ.AGENT vX.Y.Z` 标题
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
