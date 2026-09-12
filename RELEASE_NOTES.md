# BAZZ.AGENT v1.5.39

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.39 更新要点（广场自动文章：「反向剧本」不再给出与止损自相矛盾的作废价）

用户实测发现：广场技能自动生成的文章里，「反向剧本（作废条件）」那一行会写一个
**同一篇文章自己都不认的价位**。原句长这样 ——

> · 入场：优先挂 2,507.60 ~ 2,523.74 的多头 OB 区回踩接（限价），现价 2,533.98 直接追的盈亏比一般
> · 仓位算法：止损位 **2,495.06**（距离 1.5%）。100U 本金、单笔亏 5U → 做多名义 ≈ 326U……
> · 反向剧本：4h 收盘跌破 **1,503.32**（90 日低点下方）且收不回来，上面这些多头逻辑全部作废，砍仓别犹豫。

止损写 2,495.06、作废线却写 1,503.32，**差了整整 40%**。这不是文案问题，是逻辑 bug。

---

### 一、根因

- `_article()` 里那段 `if bias != "short":` 从**另一个数据源**（90 日低点 `min(k90.lows)`）
  另算了一个作废价 `lo * 0.995`，**跟 `_plan()` 用的止损完全没关联**。
- 于是同一段计划里出现两个价位：读者真按「止损 2,495」砍仓，走得早；真按「跌破 1,503 才作废」
  扛单，那要多亏 40%。三个后果：
  1. **自相矛盾**：止损 2,495 早就先打到了，1,503 那句永远触发不了，等于没有作废条件；
  2. **口径混淆**：「这笔单作废」被写成了「大趋势作废」，两件事；
  3. **数字打架**：紧挨着的「仓位算法」已经给了 2,495.06，读者不知道信哪个。
- 连带 bug：`bias != "short"` 把 **neutral（观望）也当成多头**。观望场景下根本没有多单，
  文章却照样输出「上面这些多头逻辑全部作废」。

### 二、修复

- **抽出 `_plan_levels(stat, bias, smc)`**：把「入场 / 止损 / 止盈」三级点位集中算一遍，
  返回 `{price, entry, stop, tp1, ...}`。`_plan()` 与新的 `_invalid_line()` **共用同一份结果** ——
  作废线从此**必然锚在这笔单自己的止损上**，结构上就不可能再自相矛盾。
- **新增 `_invalid_line(stat, bias, smc)`** 生成反向剧本，替换掉原来那段 `if/else`：
  - **long**：作废线 = 本单止损（原文「4h 收盘跌回 X 下方（本单止损位）就别恋战，按计划砍仓」）；
    再配一个「离止损最近的更深结构位」（4h 摆动低点 / OTE 下沿 / 近 20 根 4h 低点，
    且必须落在现价下方 15% 以内）作为「结构坏掉」的确认；
  - **short**：作废线 = 本单止损上方，附「放量收复 MA20 并站稳」作为逻辑作废的确认；
  - **neutral**：不再硬塞多头剧本，改为「站稳 X 转偏多 / 跌破 Y 转偏空，两边都不给就继续空仓等」。
- **90 日低点不再当止损用**：新增 `_macro_level()`，只有当它落在现价下方 **20% 以内**时才附带一提，
  且明确标注「那是大趋势的事，跟这笔单的止损不是一回事」。远了就**直接不说**（1,503 就属于被省略的）。
- `_size_line(price, stop, direction)` 从 `_plan()` 内联函数提升为模块级，与 `_plan_levels()` 解耦。

**修复后同一篇 ETH 文章输出**：

> · 反向剧本：4h 收盘跌回 **2,495.06** 下方（本单止损位）就别恋战，按计划砍仓；若连 2,446.83（OTE 下沿）都收不回来，多头结构才算真的走坏。

（作废线 = 止损 2,495.06，两边一致；90 日低点 1,503 因离现价太远被正确省略。）

### 三、验证

- 新增 `tests/test_v1539_square_invalidation.py` **10/10**：
  - 多头作废线 == 本单止损（不是 90 日低点）；
  - 远端 90 日低点**不得**出现在作废线里；
  - 作废价与止损的距离 < 5%（防再次跑偏）；
  - 邻近的日线级别大位**被标注为另一回事**，不与止损混写；
  - neutral 有独立文案（不再假装有多单）；
  - 空头作废线用本单止损；
  - **守卫用例**：把用户原句 `LEGACY_LINE` 喂给断言助手，**必须判定为不合格** ——
    证明这组断言真的抓得住旧 bug，而不是「改完就都过」；
  - 源码 AST 护栏：`_article()` 里不得再出现 `lo * 0.995` / `bias != "short"` 这种旧写法。
- 全量 **10 套件回归通过**；`tsc --noEmit` 退出码 0。

---

# BAZZ.AGENT v1.5.38

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.38 更新要点（代理池整体回退：恢复开机自动连代理与即时响应，只保留「取消连接真的断开」这一条修复）

这是对 v1.5.37 的**回退**。v1.5.37 把代理池的四个缺陷一起修了，但同时改了它的**行为**，
实测下来那三处行为改动让体验明显变差，用户明确要求回到 v1.5.35。

---

### 一、为什么要回退（用户反馈的三个症状）

- **「代理池加载慢」** —— v1.5.37 把面板改成了先等 `/api/proxies` 返回再渲染（加载骨架），
  加上 `status()` 里新增了解析订阅文件的 `provider_summary()`，面板打开变成"等一下才出来"。
- **「连接卡，要很久才会好」** —— v1.5.37 把「启动内核」改成了非阻塞（后台线程），
  请求立刻返回后界面只能显示「启动中…」，于是整段内核启动时间都变成了"卡住不动"。
- **「直连下行情等内容都无法显示」** —— 这是最要命的一条。v1.5.37 改成「启动一律直连」，
  但国内直连根本到不了 Binance，于是每次开 APP 都必须手动去连一次代理才有行情。

### 二、回退内容（恢复成 v1.5.35 的行为）

- **`bootstrap()`** 恢复为 `_load() → _apply_env() → _revive_kernel_async()` ——
  **每次启动自动连上上次用的节点**，行情开箱即可用。
- **`/api/proxies/kernel/start`** 恢复同步等待，「启动内核」点完即是结果，不再一直显示「启动中」。
- **代理池面板** 恢复即时渲染，去掉加载骨架与失败重试块。
- **订阅文件** 恢复原样落盘（不再抽取 `proxies:` 段），`status()` 不再附带 provider 解析摘要。
- 前端 `ProxyPoolView.tsx`、i18n 代理相关文案、`tests/test_v1532_proxy_kernel_state.py`
  全部回到 v1.5.35 版本；v1.5.37 新增的 `tests/test_v1537_proxy_identity.py` 已删除。
- 版本号回到 `1.5.35` 的代理逻辑，但 **`package.json` 继续前进到 `1.5.38`**，避免与已发布的
  v1.5.37 混淆。

### 三、唯一保留的修复：「取消连接」必须真的断开

v1.5.37 里有一处改动是**修 bug 而不是改行为**，用户确认保留 ——
旧实现的直连分支只调 `k.select("DIRECT")`：**内核照跑**，而且
`NODE_OPTIONS` 里的 `--require=proxy-preload.cjs` **只加不摘**。
结果「取消连接」并没有真的回到直连 —— 本机仍有一个 mihomo 在监听，
代理启用期间启动的 Node 子进程继续把 fetch 交给它，一旦那个端口随后失效，
这些进程就谁也连不上。用户当初报的「一取消连接，整个应用都没有网了」正是它。

保留的 `teardown_proxy()` 按「**停内核 → 摘预加载 → 清 env**」三步彻底还原：

1. `_kernel().stop()` 停掉 mihomo；
2. `_drop_node_preload()` 从 `NODE_OPTIONS` 里正则摘掉预加载令牌（保留其它标志位，
   能正确处理带空格、单引号、双引号、无引号四种路径形态）；
3. 清空 `HTTP_PROXY / HTTPS_PROXY / ALL_PROXY` 的大小写共六个变体。

另外 `_apply_env()` 走直连分支时也会调 `_drop_node_preload()` —— 否则只是清了环境变量，
预加载补丁还挂在那儿。

### 四、验证

- 新增 `tests/test_v1538_proxy_teardown.py` **5/5**：
  含行为断言（`set_active("")` 必须调 `stop()`）、环境断言（三个代理变量必须被清、预加载必须摘掉）、
  形态覆盖（带空格 / 单引号 / 双引号 / 无引号的 `--require=` 都能摘、且不误删 `--max-old-space-size` 之类的其它标志）、
  以及 AST 护栏（`set_active` 的直连分支不许再出现 `select()` 调用）。
  **已验证该测试能抓住旧行为**：把直连分支临时改回 `k.select("DIRECT")` → **3/5**，恢复后 **5/5**。
- 全量 **10 个离线套件通过**；`tsc --noEmit` 退出码 0；`vite build` 成功。
- 回退后与 v1.5.35 逐行比对：`src/proxy_kernel.py`、`desktop_app.py` 与 v1.5.35 **完全一致**；
  `src/proxy_pool.py` 只多出上述那一条修复（53 行）。

### 五、一个必须提醒的点

回退之后，**订阅导入仍然是把整份 Clash 配置原样落盘**（v1.5.35 的行为）。
如果发现「节点显示连接成功、延迟也测得出，但流量其实没走代理」，请把订阅重新导入一次；
若仍然不通，说明 mihomo 的 `type: file` provider 确实不能吃整份配置，那条抽取逻辑需要单独加回来
（v1.5.38 未纳入，因为它不属于本次回退范围）。

## 🆕 v1.5.37 更新要点（代理池：不再开机静默接管流量；取消连接真的回到直连；点一下不再卡死）

这一版修的是代理池的**功能性缺陷**，不是外观。用户原话是四句：「一进去是默认使用连接代理池的，这不对」、
「当我取消连接，整个应用都没有网了」、「并且显示需要安装插件」、「当我点安装直接卡死」。
每一句都对应一个独立缺陷，底下还有第五个（订阅文件格式）——它才是「节点都在、延迟也测得出，但流量根本没走代理」的真根因。

---

### 一、开机不再静默接管流量（启动一律直连）

旧实现里 `bootstrap()` 走的是「`_load()` 把上次的 `active_id` 恢复成已启用 → `_apply_env()` → 后台异步拉起 mihomo」。
现场 `proxies.json` 里的 `active_id` 正好是一个 vless 内核型节点，于是**每次开 APP 都会在几秒后悄悄把整机流量切进代理池** ——
用户从没同意过。现场 `kernel.log` 在 18:20:58 / 18:23:15 两次自动启动，就是它留下的痕迹。

现在的语义是：**启动一律直连**。上次的选择不丢，改记进新字段 `last_active_id`，
界面上出现一个「恢复上次：<节点名>」按钮，**用户显式点它才生效**。

### 二、「取消连接」= 真正回到直连（不是只切一下选择器）

旧代码在直连分支里只调了 `kernel.select("DIRECT")`，**内核照跑**；更麻烦的是
`_ensure_node_preload()` 只往 `NODE_OPTIONS` 里加 `--require=proxy-preload.cjs`、**从来不摘**。
结果是代理启用期间启动过的 Node 子进程，一直攥着 undici 的 `EnvHttpProxyAgent`，
把请求继续送往那个本地端口 —— 表现就是「我明明点了取消连接，整个应用却没网了」。

新增 `teardown_proxy()`：**停内核 + 从 `NODE_OPTIONS` 摘掉预加载 + 清空 `HTTP(S)_PROXY/ALL_PROXY`**，
三件事一起做才算「断开」。`set_active("")` 现在直接调它。

### 三、钱包「需要安装插件」是网络被掐断后的误报

钱包页拿到的是一次失败探测，于是判定「运行时不在了」。它不是独立 bug，
是上面第一条 + 第二条把全局网络弄坏之后的下游症状。根因修掉，这个提示自然消失。

### 四、点「安装」不再卡死（控制面等待移出锁外）

`proxy_kernel.start()` 原本在 `with _LOCK:` **内部**做完 40 次 × 0.5 秒的控制面探活等待 —— 也就是**持锁最长 20 秒**。
同一个锁还被 `list_pool()`（`/api/proxies`）、`start_download()`、`stop()` 依赖，
前端那两套轮询（15 秒 / 1.5 秒）全被堵死 → 界面看起来就是「点一下直接卡死」。

现在：等待循环**移到锁外**；启动改走非阻塞的 `start_kernel_async()`（后台线程），
`status()` 新增 `starting` 字段用于反馈进度；重复点击是幂等的，不会重复拉起内核进程。

### 五、订阅文件格式：内核型节点其实一直没在干活

`save_provider()` 原先只要原文里**出现过** `proxies:` 就把**整份 clash 配置**（`mixed-port` / `dns` /
`proxy-groups` / `rules`，566 行）原样落盘。而 mihomo 的 `proxy-providers: {type: file}` **只认 `proxies:` 这一段**，
其余部分它根本不解析。于是 provider 加载到 0 个节点，`BAZZ` 分组实际上只剩 `DIRECT` ——
界面上节点齐全、延迟也能测出来，**流量却从没走过代理**。

新增 `_extract_proxies_block()`：只抽 `proxies:` 段落盘；抽不到或节点数为 0 就**拒绝写入并报错**；
`status()` 新增 `providers` 摘要（每个订阅文件实际解析出多少个节点），界面在节点数为 0 时给出警示条。
**导入订阅后请重新导入一次**，让旧的坏文件被正确格式覆盖。

### 六、界面不再「说谎」

代理池面板原先用一份**写死的默认值**渲染（`active_id: ""`、`kernel.installed: false`），
在 `/api/proxies` 还没返回时会先显示一句假的「直连 · 使用中」；请求失败也被 `catch` 吞掉，界面停在假状态上。
现在改为：先显示**加载骨架**，请求失败显示错误块 + 重试按钮，绝不用默认值冒充真实状态。

顺手修的两处：**「延迟」列之前错误地复用了「状态」的渲染函数**（两列显示同一个东西）；
协议徽章用了浅色底上对比度只有 3.44:1 的 `sky-300` / `violet-300`，换成主题色令牌。

### 验证

- 新增 `tests/test_v1537_proxy_identity.py`（19 项）：覆盖 provider 只留 `proxies:` 段 / 拒绝空文件 /
  flow 与 block 两种写法 / 启动即直连且记住上次选择 / 启动绝不拉起内核 / 切直连彻底拆栈 /
  预加载摘除能处理带空格的路径 / 停内核会重置内核型 active / 控制在锁外等待 / 重复点击不重复拉起 /
  界面无假默认值 / 延迟与状态分列 / 空态提示指向正确位置 / 中英双语键齐全。
- 全量回归 10 个套件通过（v1.5.32 的旧断言已按新语义更新：不再要求 bootstrap 自动拉起内核）。

## 🆕 v1.5.36 更新要点（主界面改版：导航回到顶部；窄屏换行不再藏入口；隔离预览验收链路）

这一版外观上是「导航挪了个位置」，实际底下有三件事：**上一稿的方向被否掉要回退**、
**窄屏下有几个入口根本点不到**、以及**终于有一套不碰真实账户就能跑完的界面验收**。

---

### 一、主导航从左侧栏回到顶部（方向性回退）

上一稿把主导航做成了左侧一整列（`.workspace-sidebar` + `--sidebar-width`），视觉上「像个后台」，
但实际用起来不顺手 —— **这一稿被否掉了**，导航回到顶部单条。

现在的结构就一行：

```
BAZZ.AGENT · v1.5.36 │ 对话 行情 Agent钱包 BinanceCEX 广场 技能库 记忆 设置 │ 🔍 ●LLM已连接 ☾ EN ─□✕
      品牌            │                      横向导航 8 项                    │        工具区
```

- 面包屑那一行**整行删除**（顶部导航已经指明位置，再报一遍位置是冗余）
- 版本号移进品牌副标题，由 vite 注入的 `__APP_VERSION__` 提供 —— **不写死**，从根上杜绝版本号漂移
- `--sidebar-width` 与 `.workspace-sidebar` / `.workspace-body` 相关规则全部清掉，不留死代码

### 二、窄屏要「换行」，不能「横向滚动」

这是本版最容易被忽略、后果最直接的一条。

`.topbar-nav` 一开始用 `overflow-x:auto` 兜底 —— 看着没问题：窗口窄了横向滚一下就行。
**问题是滚动条是隐形的**（`scrollbar-width: none`，为了让界面干净）。于是 390px 宽时，
末尾的「技能库 / 设置」**滚出了可视区，又没有任何提示**，等于**点不到**。

> 这个不是看截图发现的，是浏览器验收脚本在 390px 下断言「每个导航项都必须在视口内」时抓出来的。

规则改为：

| 宽度 | 行为 |
| --- | --- |
| ≥ 1001px | 保持单行（`overflow-x:auto` 仅作最后兜底） |
| ≤ 1000px | **整条 bar 转两行**：品牌 + 工具一行，导航**独占下一整行**并在行内 `flex-wrap` |

断点全表：**1420**（收起导航图标）· **1120**（收起品牌副标题 + 连接状态文字）·
**1000**（转两行）· **900**（会话页转单列）· **760** · **520**。

**顺带修掉一个更隐蔽的**：`≤760` 那条断点里给 `.app-topbar` 写死了 `height: 58px`，
但此时顶栏内容**已经是两行**了（品牌/工具 + 导航）—— 固定高度把第二行压到内容区上面去，
**顶栏直接盖住正文**。修法是**去掉固定高度**，只留 `padding` + `gap` 让 flex 自己撑开。

### 三、顺手修的三处遗留

| 问题 | 根因 | 处理 |
| --- | --- | --- |
| 钱包页显示 `LIVE · LIVE · 已登录` | `wallet.signedIn` 文案**本身已含** `LIVE ·` 前缀，代码又拼了一次 | 去掉代码里那次拼接 |
| 窄列卡片头部按钮被压扁（「重新获取」只剩 41px 宽、折成两行；`LivePanel` 的刷新被挤成 22×53） | 行容器缺 `flex-wrap`，标题 span 没有 `min-w-0` 抢位 | 行容器加 `flex-wrap`，动作按钮 `ml-auto shrink-0 whitespace-nowrap`，`<code>` 加 `min-w-0 flex-1` |
| 广场删帖确认弹窗**只剩「删除」两个字**，没有说明 | `confirmDialog` 传了 `title` 就把整个 `message` **吞掉**（`title ?? ""` 分支没把 message 转成 `detail`） | `title = o?.title ?? message`，且 `title !== message` 时把 message 下移到 `detail` 行渲染 |

### 四、新增：隔离预览 + 浏览器视觉验收（本次最重要的资产）

改界面最怕的是「改好看了、别处坏了」，而真实应用一启动就连真实账户、真实行情、真实代理 ——
不能拿它做回归。所以这次补了一条**全程离线**的验收链路：

- **隔离预览入口**：`frontend/preview.html` + `preview.config.ts` + `src/preview/{main.tsx,mock.ts}`
  - 内存 fixture 覆盖约 **48 个接口**，界面照常渲染，但**没有任何一个请求出网**
  - `fetch` / `XMLHttpRequest` 全部被拦；页面 CSP 里 **`connect-src 'none'`**
  - 额外一道保险：**检测到自己在真实桌面桥（Electron preload）里就直接抛错拒绝运行** ——
    预览永远不会误连真实后端，更不会碰账户
- **验收脚本** `tests/ui_preview_check.py`：真浏览器（`agent-browser` CLI）走查
  **8 视图 × 明暗主题 × 1440 / 1024 / 760 / 390 四档宽度 = 84 项断言**，产出 **25 张截图** + `verification.json`
  - 三项**几何断言**（不靠肉眼）：① 顶栏底边 ≤ 内容区顶边（**比 y 轴**，换行后也不误判）；
    ② 页面无横向溢出（`scrollWidth ≤ innerWidth`）；③ **每个导航项都必须落在视口内**
  - 业务侧覆盖：广场删帖 / 清空所有失败的**二次确认框**、会话**流式回复**与**切页后保持**

### 五、踩坑记录（都写进脚本注释了，留个底）

- **`agent-browser` CLI 会把 Python 卡死**：它的守护进程**继承父进程的 stdout**，
  Python 用 `capture_output=True` / `communicate()` 会一直等 EOF → **第一次调用就死锁**（日志 0 字节）。
  改为 **stdout/stderr 重定向到文件 + `Popen.wait(timeout)` + 超时 kill**
- 该 CLI 的 `find role textbox` **解析不了普通 `<input>`**（4 种参数写法全失败）；
  **纯图标按钮的 `aria-label` / `title` 也不参与 `--name` 匹配** → 一律改用 `fill / click <css 选择器>`
- 预览构建开了 `emptyOutDir: false`，旧 hash 资源会一直累积 —— 排查时先确认 `preview.html`
  引用的到底是不是新 hash
- 沙箱的**批量删除守卫**（50 次/轮）命中时抛的是 `SystemExit`，`except Exception` **抓不到** ——
  表现是脚本「静默 0 项通过、退出码 0」

### 验证

- **浏览器视觉验收：84 / 84 通过，0 失败**（8 视图 × 明暗 × 1440/1024/760/390；
  含 25 张截图与 `verification.json`）
- 前端 `tsc --noEmit` 退出码 **0**，`npm run build` 成功
- 误报排除：390px 两个入口不可点、760px 顶栏压内容、`confirmDialog` 吞消息 ── 三个都是
  **先由断言/几何测量抓出来**再改的，不是照着截图猜的

> **附：本次发版顺手修掉一个仓库自身的毛病。**
> `.git/packed-refs` 被某个 Windows 工具写成了 **CRLF 行尾**，导致 78 个历史 tag 的名字**全部带上 `\r`**
> —— `git tag` 报一片 `ignoring ref with broken name refs/tags/v1.5.3?`，末尾直接
> `fatal: unexpected line in .git/packed-refs`，**新 tag 根本打不出来**。
> 备份原文件后去掉 `\r`，78 个 tag 与 `git log` / `git status` 全部恢复正常。
> 这跟产品无关，但**不修就发不了版**，记在这里免得下次再撞。

## 🆕 v1.5.35 更新要点（更新时不再弹「关闭还是最小化」；广场失败文章可删除）

本版两件事，都是「用起来别扭」那一类：一件是**更新流程会卡在一个不该出现的询问弹窗上**，
另一件是**广场台账里的失败记录只进不出，越堆越多**。

---

### 一、点「安装更新」后，直接退出，不再弹「关闭还是最小化到托盘」

#### 现象
在应用内点「安装更新」，本该静默关掉、交给安装脚本接管替换，实际却弹出
「关闭还是最小化到系统托盘」的询问框。更糟的是：**一旦选了「最小化到托盘」，更新就废了**。

#### 根因：退出通道串了岗
「关闭还是最小化」这个询问是为**用户主动点窗口 X** 设计的（Electron 的
`mainWin.on("close")` → `bazz:ask-close` → 渲染层美化弹窗 → `bazz:answer-close` 回传）。

而 `useUpdater` 在更新流程里直接复用了 **`bazzWindow.close()`** —— 和「用户点 X」走的是
同一条链路，于是把那个询问框也一并触发了。

麻烦还在后面：更新脚本（脱离子进程的 PowerShell）会**等待 Electron 主进程退出，最多 180 秒**
（45 次 × 4 秒）。选「最小化到托盘」意味着进程**根本不退** —— 干等 180 秒后以
`ERR wait-electron-timeout` 告终，**更新直接失败**。

也就是说：这个弹窗不只是碍眼，它是**更新失败的一条真实路径**。

#### 修复：给「程序性退出」单独开一条通道
| 位置 | 改动 |
| --- | --- |
| `electron/preload.cjs` | 新增 `quitForUpdate: () => ipcRenderer.send("bazz:quit-for-update")` |
| `electron/main.cjs` | 新增直通处理器 `ipcMain.on("bazz:quit-for-update", () => { isQuitting = true; app.quit(); })` —— **不进询问链路** |
| `frontend/src/hooks/useUpdater.ts` | 改用 `quitForUpdate()`；保留 `close()` 兜底（老版本 Electron 主进程还没有这条通道时不会崩） |
| `installer.iss` | `CloseApplications=yes` → **`no`** |

安装包那处是顺带修的：`CloseApplications=yes` 会让 Inno 的 Restart Manager 先发 `WM_CLOSE`，
同样可能引出那个询问框；而 `ScoutBackend` / mihomo / runtime node 都是**无窗口进程**，
Restart Manager 根本关不掉它们，结果就是安装程序弹「Select action」卡住。
现在关闭一律交给 `[Code] PrepareToInstall` 里的 `taskkill /F`，行为确定。

**结论**：「关闭还是最小化」**只服务于用户主动点 X**；更新这类程序性退出一律直通。

---

### 二、广场：失败的文章可以删了（单条删除 + 一键清空）

#### 现象
广场面板的发文台账只进不出。发文失败的记录（含报错原文）一直堆在列表里，
既没法单条清理，也没法批量清掉，翻列表时全是历史垃圾。

#### 修复
1. **后端** `src/square_store.py` 新增 `delete_records(ids)`：按 id 批量删除，`deleted` 计实际删除数、
   `missing` 计不存在的 id；**只有真删掉了东西才落盘**
2. **端点** `POST /api/square/posts/delete`（`desktop_app.py`）：`body: { ids: [...] }`，
   返回 `{ ok, deleted, missing }`。**仅操作本地台账，不会调用币安 API**（这点特意写进 docstring）
3. **前端** `SquarePostView.tsx`：
   - 失败卡片（`status !== "posted"`）右上角出现「删除」按钮（垃圾桶图标）
   - 切到「失败」筛选且有失败项时，过滤栏右侧出现「清空所有失败」
   - **两者都走 `confirmDialog` 二次确认**，清空时提示条数 `清空全部 @N 篇失败文章？`
   - 删除成功后 `load(true)` 静默刷新
4. 新增 5 个 i18n key × 2 语言（`square.delPost` / `delPostTip` / `clearFailed` / `clearFailedTip` / `delFail`）

> 顺带一提，写这个端点时踩了个 FastAPI 的坑：习惯性按 Flask 写了 `request.get_json()`，
> 但 **FastAPI 没有全局 `request` 对象**，端点直接 500 `name 'request' is not defined`。
> 正确写法是签名里显式声明 `payload: dict = Body(default_factory=dict)`（并 import `Body`）。

### 验证
- 新增 `tests/test_v1535_square_delete.py` —— **12/12**
   - 单元 5 条：`delete_records` 基本删除 / missing 计数 / 空值与非法输入过滤 / 落盘
  - AST 6 条：`PostCard` 接收 `onDelete`、失败卡片渲染删除按钮、`delPost` / `clearAllFailed`
    处理函数完整（确认弹窗 + 调 API + 判 `ok=false` + 刷新）、过滤栏按钮、i18n 双语齐全
  - 端到端 1 条：用 **FastAPI `TestClient`** 直接打端点，覆盖单删 / 批删 / missing /
    错误体 / 空数组 / 落盘 / **401 未带令牌**
- **已确认能抓住旧行为**：临时回退 5 个文件 → **0/12**，恢复后 **12/12**
- 全套回归（**9 个套件**）：test_v150 ✓ · test_v151 ✓ · test_v1528 15/15 · test_v1529 23/23
  · test_v1530 20/20 · test_v1531 14/14 · test_v1532 14/14 · test_v1534 7/7 · test_v1535 **12/12**
- 前端 `tsc --noEmit` 退出码 0，`npm run build` 成功

> 两条踩坑记录，留给下次：
> ① 起后端做端到端时，**`subprocess.Popen` 在同一脚本内始终连不上**（手动后台起 + curl 却 200），
> 改用 `TestClient` 直接 import 更省事也更快。
> ② 鉴权中间件是在 `if AUTH_TOKEN:` 装饰器块里**模块 import 时**注册的，
> 测试里 `import` 之后再去改 `desktop_app.AUTH_TOKEN` 无效 —— 必须在 `import` **之前**
> 设 `os.environ["BAZZ_AUTH_TOKEN"]`。
> ③ 用「下一个顶层语句」当函数体结束标记很脆 —— 两个 `useCallback` 挪位置后断言就假失败。
> 已改为**大括号配对**切函数体（并跳过字符串字面量）。

## 🆕 v1.5.34 更新要点（文件查看器图片预览：后端能力早已就绪，界面这端从未接线）

### 现象
在 Files 面板点开 `chart_24h.png`，查看器只显示：

> 二进制文件，不可文本预览（共 33.8K）

图片看不到。

### 根因：一条腿的活儿干了两遍，最后一根线没接
后端在更早的版本就加了图片原文端点 `/api/workspace/raw`（20MB 上限、扩展名白名单、
`FileResponse` 直出），`api.ts` 也早就有配套的 `workspaceRawBlob()`：

```ts
workspaceRawBlob: async (path: string): Promise<Blob> => {
  const r = await fetch(BASE + "/workspace/raw?path=" + encodeURIComponent(path), { headers: authHeaders() });
  if (!r.ok) throw new Error(await r.text());
  return r.blob();
},
```

**但全仓没有第二个调用点。** 同时 `ChatView` 的 `fileModal` 类型里声明了 `img_url?: string`，
却**从来没有被赋值过** —— 渲染分支 `fileModal.img_url ? <img ...>` 因此永远走不到，
所有文件一律落到 `!is_text` 的「二进制不可预览」兜底。

顺带说清为什么图片**不能**复用文本通道 `/api/workspace/read`：那是文本接口（1.5MB 上限），
且**含 NUL 字节即判定为二进制** —— PNG 的文件头就带 NUL，必然被挡在门外。

### 修复
1. `openFile()` 按扩展名分流：图片走 `workspaceRawBlob()` → `URL.createObjectURL()` → 写入 `img_url`
   （分流必须排在 `workspaceRead` 之前，否则 PNG 先撞 NUL 判定）
2. **objectURL 生命周期**：关闭查看器、切换文件、组件卸载三处都 `revokeObjectURL()`，一处不漏
3. 文件列表给图片加**缩略图**（`THUMB_LIMIT = 30`，避免一次列表打几十个请求）
4. 查看器里的图片可**点击打开原图**，底部另有「打开原图」按钮
5. 错误文案解包：`jget` 会把整个 JSON body 塞进 `Error.message`，现在解出 `error` 字段再展示

### 验证
- 新增 `tests/test_v1534_image_preview.py` —— **7/7**
- **已确认能抓住旧行为**：临时回退 `ChatView.tsx` → **1/7**，恢复后 **7/7**。
  唯一「通过」的是渲染顺序断言 —— 恰好印证事故本质：分支写好了，只是永远走不到
- 其中 `test_workspace_raw_blob_has_a_caller` 是本次事故的**核心护栏**：
  能力存在但没人调用 = 功能不存在
- 前后端扩展名白名单**交叉校验**（AST 取后端 `_IMG_EXT_MEDIA` 键集合 vs 前端 `IMG_EXT_RE` 正则），
  并做真实正则匹配验证（含大写、非图片扩展名、结尾锚定）
- 全套回归：test_v150 ✓ · test_v151 ✓ · test_v1528 15/15 · test_v1529 23/23
  · test_v1530 20/20 · test_v1531 14/14 · test_v1532 14/14 · test_v1534 **7/7**
- 前端 `tsc --noEmit` 退出码 0，`npm run build` 成功

## 🆕 v1.5.33 更新要点（修上一版引入的启动竞态：内核端口被并发探活清零）

### 背景
v1.5.32 修好了「内核型代理跨进程识别」，但它给 `_recover()` 加的「探活失败即清空端口缓存」
是无条件的 —— 而 `start()` 里端口是**先写、后起进程**的：

```python
_write_config()        # 写入 mixed_port / ctrl_port
logf = open(LOG_PATH, "a", ...)
_proc = subprocess.Popen(...)   # ← 在这两行之间 _proc 仍是 None
```

若前端此刻正好轮询 `/api/proxies`（`status()` → `is_running()` → `_recover()`），
内核尚未就绪 → 探活失败 → **把刚写好的端口清零** → 等待循环 40 次都在请求
`http://127.0.0.1:0/version` → 误报「内核启动超时」，并把 `mixed_port: 0` 写进 `state.json`。

**症状与「代理没启用」完全一致 —— 等于把刚修好的 bug 换个入口又放回来。**

### 修复
1. 新增 `_recovered` 标记：区分端口是「落盘恢复来的」还是「本进程 `start()` 刚写的」；
   `_recover()` **只清前者**，绝不碰后者
2. `_write_config()` 写入端口时置 `_recovered = False`（本进程权威）
3. `stop()` 同步归零端口缓存 —— 此前停掉内核后 `mixed_port()` 仍会返回过期端口
4. `_revive_kernel_async()` 在线程内**重读** active 节点 —— 启动期间用户已在界面上换过节点时，
   不会再把旧节点选回去

### 验证
- `tests/test_v1532_proxy_kernel_state.py` 扩到 **14/14**，新增两条：
  `test_recover_does_not_clobber_inprocess_ports`、`test_stop_resets_ports`
- **已确认这两条能抓住旧行为**：临时回退 `src/proxy_kernel.py` → **12/14**（两条 FAIL），
  恢复后 **14/14** —— 不是摆设断言
- 全套回归：test_v150 ✓ · test_v151 ✓ · test_v1528 15/15 · test_v1529 23/23
  · test_v1530 20/20 · test_v1531 14/14 · test_v1532 **14/14**

## 📌 历史版本（更早版本）

# BAZZ.AGENT v1.5.32

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.32 更新要点（内核型代理静默失效 · 广场发文超时根因修复 + 子进程 GBK 解码崩溃）

### 现象
广场发文（`square-rich-post` / `square-post`）失败，报
`API /content/add 网络抖动（UND_ERR_CONNECT_TIMEOUT）`，约 10.6s 后超时。
**直连失败，代理池里「已启用」的节点同样失败** —— 而节点明明测得出延迟（407ms）。

### 根因：内核型代理的端口只存在「进程内存」里
代理池里启用的是 **内核型**节点（`vless` / `hysteria2` / `vmess` / `trojan` …），
它的代理入口是 mihomo 在本地起的混合端口，而这个端口只被记在三个**模块级变量**里：

```python
_proc = None          # 只有「本进程亲手 Popen 出 mihomo」时才有值
_mixed_port = 0       # 仅在 _write_config() 内赋值，而它只被 start() 调用
_ctrl_port = 0

def is_running():
    if _proc is None:
        return False      # ← 一行就把「内核客观在跑」判成没跑
```

于是只要出现下面任一种情况，新进程就彻底「看不见」内核：
后端重启 / 同机跑了第二个后端实例 / 上一个实例把 mihomo 留成了孤儿进程。

连锁反应：

```
is_running() → False
  → proxy_url(内核型节点) → None
    → _apply_env() 走 else 分支，把 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 全部 pop 掉
      → 技能子进程（继承进程 env）拿不到任何代理 → Node/undici 直连 → UND_ERR_CONNECT_TIMEOUT
```

**现场证据**：同一时刻 `127.0.0.1:9099/version` 返回 `HTTP 200 {"version":"v1.19.30"}`
（内核客观在跑），而进程内 `is_running()` 返回 `False`、`mixed_port()` 返回 `0`。

### 修复
1. **`proxy_kernel` 新增跨进程状态恢复**：启动成功即把 `pid + 实际端口` 落盘到
   `.system/kernel/state.json`；`_recover()` 按 `state.json` → `config.yaml` 顺序恢复端口，
   再用控制面 `/version` 探活确认。`is_running()` / `mixed_port()` / `version()` 全部接上
2. **`stop()` 能收掉「别的进程拉起的」内核**：从 `state.json` 取 pid，**先核对镜像名确为
   `mihomo.exe`**（防 PID 复用误杀无关进程）再 `taskkill /T /F`；退出时清理 `state.json`
3. **`/api/proxies/kernel/start` 补调 `apply_env()`** —— 此前只拉起内核却不注入 env，
   用户点「启动内核」等于没启用代理；`/stop` 同步清理
4. **`bootstrap()` 后台救活内核并重选节点**（mihomo 重启后 selector 会回到默认，
   不重选就仍然走不到用户选的那个节点）；放后台线程，不阻塞后端启动
5. **`ensure_working_proxy()` 先救活「用户选中的那个内核节点」**再去找别的候选 ——
   此前直接跳过 active 去试候选，等于用户自己选的节点永远不会被救活
6. **新增 `proxy_pool.env_snapshot()`**，`/api/proxies` 响应增加 `env` 字段，
   一眼看清代理到底注入进程没有（此前只能靠翻日志猜）

### 附带修复：子进程文本模式未指定编码 → GBK 解码崩掉读取线程
zh-CN Windows 上 `subprocess.run(text=True)` 默认按 **locale(GBK) 严格**解码。
`baw` / `npx` 输出含非 ASCII 时，`subprocess.py` 的 `_readerthread` 抛
`UnicodeDecodeError` 直接死掉，`proc.stdout` 变空 —— 技能「明明跑了却没有任何输出」，
排障时极具误导性。7 个文件共 10 处统一补 `encoding="utf-8", errors="replace"`，
并新增 AST 护栏测试禁止再出现裸 `text=True`。

### 验证
- 新增 `tests/test_v1532_proxy_kernel_state.py`：**12/12 通过**
  （跨进程恢复 / `config.yaml` 兜底 / 探活失败不残留假端口 / env 注入 / 救活 active 内核节点 /
  内核启停端点接线 / `stop()` 清状态 / GBK 护栏）
- **真实环境实测**（用户机器，mihomo 在 7899/9099 运行中）：模拟「后端重启后的新进程」→
  `is_running()=True`、`mixed_port()=7899`、`proxy_url()=http://127.0.0.1:7899`、
  `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` 全部注入、`NO_PROXY` 含 `127.0.0.1`
- **端到端**（技能实际走的 Node/undici 路径）：

  | 场景 | `square/content/add` | `public.bnbstatic.com` |
  |---|---|---|
  | 无代理 env（故障复现） | `UND_ERR_CONNECT_TIMEOUT` **10686ms** | `UND_ERR_CONNECT_TIMEOUT` **10589ms** |
  | 有代理 env（修复后） | **HTTP 404 @1295ms** | **HTTP 403 @629ms** |

- 全套回归：test_v150 ✓ · test_v151 ✓ · test_v1528 **15/15** · test_v1529 **23/23**
  · test_v1530 **20/20** · test_v1531 **14/14** · test_v1532 **12/12**

# BAZZ.AGENT v1.5.31

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.31 更新要点（提示词侧两处缺陷：技能子命令名不副实 · 状态码被当路径）

### 缺陷一：`market-data` 宣传了不存在的子命令
`SKILL.md` 的 description 把「资金费率 / funding」列为触发词，但 CLI 的 `CMDS` 里只有
`klines / fng / oi / longshort / liq / overview / bundle`。模型据此调用
`run_skill market-data "funding BTCUSDT"`，只会拿到 `{"error":"未知命令 \"funding\""}`。
资金费率当时仅作为 `bundle` 的一个字段存在 —— 为拿单个币的费率要跑完整分析包
（90d 日K + 24h + 恐惧贪婪 + OI + 大户多空比），代价过高且易超时。

**修复**
1. 新增真正的 `funding [SYM[,SYM...]]` 子命令：给 SYM 返回 `funding_pct_8h`（每 8h 费率）
   与 `annualized_pct`（年化）；**不给参数**则返回费率最高/最低各 10 个（谁在多付钱）
2. 未知命令的报错补 `hint`：直接把最可能的替代写法点出来，不再只丢一个 `available` 列表
3. `SKILL.md` 命令表 + 用法示例同步登记

### 缺陷二：模型把 HTTP 状态码当成文件路径
v1.5.30 之前技能报错文本里含 `:8080 HTTP 401: {"error":"unauthorized"}`，模型随后执行
`grep <pattern> 401`，把状态码 `401` 当成路径传入，只得到干巴巴的「路径不存在: 401」，
无从纠正、容易反复重试同一个错。

**修复**
1. `src/exec_sandbox.py` 新增 `_not_a_path_hint()`：对「明显不是路径」的实参补一句针对性提示
   （裸数字/HTTP 状态码、URL、以 `-` 开头的选项），命中才追加，正常路径零噪音
2. 接入全部 4 个路径报错点：`文件不存在` / `目录不存在`（ls）/ `路径不存在`（grep）/ `目录不存在`（find）
3. `src/llm.py` 的 `run_command` 工具描述正面写清：路径参数必须是真实存在的文件或目录，
   不要把数字、HTTP 状态码、URL 或错误消息片段当路径传

### 验证
- 新增 `tests/test_v1531_prompt_fixes.py`：**14/14 通过**
  （含端到端 —— 起本机 stub 后端真跑 `funding`，覆盖单标的 / 多标的含缺号 / 无参数两端极值 / 拼错给 hint）
- 新增通用护栏 `test_skill_md_commands_all_dispatchable`：逐技能比对 `SKILL.md` 命令表与 CLI 实际命令，
  防止再出现「文档宣传了 CLI 没有的命令」
- 全套回归通过：test_v1530_local_backend 20/20 · test_v1529_hardening 23/23 · test_v1528_fixes 15/15
  · test_v150_market ✓ · test_v151_radar_track ✓
- 同时修正 `tests/test_v1530_local_backend.py` 的模块 docstring —— 它此前把已被推翻的
  「NO_PROXY 是根因」当作实测结论写入，会误导后来者

# BAZZ.AGENT v1.5.30

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.30 更新要点（技能全线 401 根因修复：本机回环令牌被沙箱误删）

### 现象
v1.5.29 起，开启代理后 `coin-report` / `market-data` / `risk-guard` / `track-monitor` / `portfolio-review`
全部失败，报 `{"error":"本地后端不可达(8080/8081): fetch failed"}`，并附
`[auto-proxy] 首次网络失败，已自动启用代理 … 重试（仍失败）`。
用户据此判断为代理故障，反复更换节点/订阅均无效——**症状指向代理，根因却在鉴权**。

### 根因（两层叠加，缺一不可）
1. **令牌被沙箱误删（v1.5.29 F06 引入的回归）**
   `exec_sandbox._ENV_DENY_SUBSTR` 按**子串**匹配清洗子进程环境，`BAZZ_AUTH_TOKEN` 因含
   `TOKEN` / `AUTH` 被判定为凭据而剥离。但它不是第三方凭据 —— 它是**应用自己的本机回环令牌**
   （Electron 每次启动 `crypto.randomBytes(24)` 随机生成，仅用于访问 127.0.0.1 上的自身后端）。
   技能 CLI 必须携带它才能通过 `/api/*` 鉴权，剥离后一律拿到 **401**。
   v1.5.28 及更早版本子进程继承完整 `os.environ`，因此无此问题。
2. **真实错误被 CLI 掩盖**
   技能 CLI 的端口回退用单个 `lastErr`，循环结束时只剩最后一个端口的错误。
   8081 通常未监听 → 真正的原因（`:8080` 返回 **HTTP 401**）被连接错误覆盖，
   最终抛出「本地后端不可达: fetch failed」，把鉴权问题伪装成连通性问题，极难排查。

### 修复
1. `src/exec_sandbox.py`：新增 `_ENV_ALLOW_EXACT` 精确放行 `BAZZ_AUTH_TOKEN`（应用自有回环令牌）；
   第三方凭据（API Key / Secret / 密码等）照常剥离，安全边界不变
2. 5 个技能 CLI 的 `jget()`：逐端口错误**全部保留**并逐个列出，401 不再被掩盖
3. `src/proxy_pool.py`：`NO_PROXY` 由 `setdefault` 改为**强制并集**（属加固 —— 初判曾把它当作根因，
   复测证明有误：用户机器上 `NO_PROXY` 在 User/Machine 级均为空，`setdefault` 本该生效，
   代理并未劫持本机地址。保留改动是因为「用户已存在 NO_PROXY 时 setdefault 失效」仍是真实隐患）
4. `scripts/proxy-preload.cjs`：`EnvHttpProxyAgent` 显式传 `noProxy`，本机地址永不进代理（同上，加固）
5. `src/skills_client.py` + `src/agent_core.py`：本机后端故障**前置判定**，命中即按
   `HTTP 401` / `fetch failed` 分诊给出准确指引，不再拿代理空跑重试、不再误导用户去代理池

### 验证
- 新增 `tests/test_v1530_local_backend.py`：**20/20 通过**（离线 AST/桩，不联网）
- `tests/test_v1529_hardening.py`：**23/23 通过**（其中 F06 断言已修正 —— 原断言要求
  `BAZZ_AUTH_TOKEN` 必须被剥离，正是它把回归固化成了「预期行为」）
- 其余套件全部通过：test_v1528_fixes 15/15 · test_v150_market ✓ · test_v151_radar_track ✓
- **真机对照实测**（对运行中的后端直接跑 CLI）：
  - 改前：`本地后端不可达(8080/8081): fetch failed`
  - 改后：`本地后端不可达(8080/8081) — :8080 HTTP 401: {"error":"unauthorized"} | :8081 fetch failed`
- 代理旁路 A/B（死代理探针，NO_PROXY 缺失/空串/不含本机三种场景）：改前一律 502，改后一律 200
- `py_compile` 4 个 py 文件通过；`node --check` 5 个 CLI 通过；改动文件全部保持 LF

# BAZZ.AGENT v1.5.29

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.29 更新要点（审查缺陷修复：授权边界 · 密钥防护 · 沙箱加固 · 供应链锁定）

> 来源：同一份第三方源码工程审查报告（提交 944ca16）。本次修复其余全部 11 项缺陷
> + 3 项性能问题，每项均附离线回归测试（tests/test_v1529_hardening.py，23/23 通过）。

### F03 · 下单方案资金语义失真（P1）
- 提案按 margin×leverage 计算数量，但执行层只有现货限价单（无杠杆）→ 高杠杆请求变成超额现货买单；
  止损恒 97%/止盈恒 108% 却宣称「最大亏损 = margin×10%」——虚假承诺
- 现货通道直接拒绝杠杆与做空语义（明确提示而非静默降级）；数量按 margin/price 真实口径；
  名义金额如实展示；删除「最大亏损」文案并明示「止损止盈仅为到价提醒，不会自动挂保护单」

### F05 · 副作用出口绕过授权（P1）
- 插件命令（`<pid>.<cmd>`）与 MCP 网关调用（可触达账户级真实下单）在全能模式下未校验
  confirmed 即执行
- 现与沙箱工具同标准：未确认一律流审批卡；「信任并执行」按 `mcp:<server>.<tool>` /
  `plugin:<pid>` 粒度加白；两类出口纳入调度审批屏障

### F09 · 并行工具整批执行后才查审批 + 异常整批重放（P1）
- 一批 tool_calls 先全部执行完才检查 needs_approval → 审批卡之前的后续工具已被执行；
  线程池异常时整批顺序重跑 → 已执行的副作用工具再执行一遍（重复下单/重复写文件）
- 现审批是调度屏障：遇到首个需审批工具执行后立即停流等用户，其后工具一律不执行；
  per-call 异常兜底为错误结果，彻底删除整批重放路径

### F07 · 会话快照恢复跨服务密钥错配（P1）
- cfg_from_snapshot 用旧快照 provider/base_url + 当前 api_key → 「A 的端点 + B 的密钥」
  把凭据泄露到错误服务
- 现成组解析：provider 一致才继承当前 key；不一致则 api_key 置空、key_env 只取快照记录的旧名，
  请求因缺 key 安全失败

### F06 · 沙箱环境与路径逃逸（P1）
- 白名单解释器（python/node）可传参执行任意代码且继承完整环境变量 → BAZZ_AUTH_TOKEN、
  API Key、代理凭据可被窃取；文件工具不解析符号链接 → 工作区内链接可指向沙箱外
- 子进程环境剥离一切凭据类变量（保留代理池/NODE_OPTIONS/PATH 运行必需项）；
  读/写/wrapper 三类路径解析统一增加 realpath 校验，符号链接解析后越界一律拒绝

### F11 · 前端 auto_exec 请求失败 fail-open（P1）
- getAutoExec() 网络失败时 return true = 未知状态被当作「已开启自动执行」
- 现 fail-closed：失败一律视为未开启，走人工确认安全路径

### F12 · 调度器整份覆盖任务状态（P2）
- 定时任务执行前读全部 jobs、执行后整份写回 → 并发修改互相丢任务
- state 层新增 `update_cron_job(job_id, patch)`（RLock 内读-改-写），每任务执行后仅回写自身状态

### F14 · 禁用的插件仍可被调用（P2）
- 启停状态只影响界面展示，LLM 工具注入与执行均未校验
- 现 `list_command_schemas()` 不注入禁用插件，`exec_command()` 执行时双重校验 enabled

### F15 · API Secret / LLM Key / MCP Token 明文入 SQLite（P2）
- 新增 `secrets.py`：Windows DPAPI（ctypes + crypt32，零新依赖）加密敏感 settings
  （llm / BINANCE_API_KEY / BINANCE_API_SECRET / W3 密钥 / mcp_servers / mcp_token:*），
  非 Windows 自动降级并显式标注 `plain:` 前缀；旧明文读取兼容、下次保存自动迁移为密文

### F16 · 技能更新供应链：可变引用（P2）
- baw 安装用 `@latest`、undici 用可变 spec、GitHub 目录引用 main 分支 → 内容随时可被替换
- 现锁定具体版本（`@binance/agentic-wallet@1.10.0` / `undici@6.21.1`）集中常量管理并防版本回滚；
  技能包不再后台静默升级，仅保留手动入口并打印来源日志

### F17 · last_persona_conv 重复定义（P2）
- 同名函数定义两次，后者覆盖前者并丢失 group/room 会话排除条件
- 删除重复版本，保留带 kind 过滤的正确实现

### 性能优化
- **5.1 雷达扇出合并**：scanner 新增 `_dedupe_fetch()` in-flight 去重（相同缓存键的并发请求合并为一次，
  带 30s 超时兜底防死锁），K 线/资金费率/持仓量历史三处扇出接入，冷启动 REST 调用大幅减少
- **5.2 订单轮询节奏**：_tick 由「先 sleep 再执行」改为「先执行再 sleep」，消除启动即延迟
- **5.3 前端流式与渲染**：三条 NDJSON 流补齐 res.ok 检查；逐 delta await rAF 改为缓冲 + 每帧批量 flush；
  流结束/中止补齐终态；会话切换竞态用请求代号 + AbortController 双重隔离；
  live.ts 快照 diff 合并（未变币保留旧引用，杜绝无意义重渲）；vite dev 代理补 `ws: true`

### 回归验证
- 新增 `tests/test_v1529_hardening.py`：23 个离线用例全部通过
- 既有套件全部通过：test_v1528_fixes 15/15、test_v150_market ✓、test_v151_radar_track ✓
- 前端 `tsc --noEmit` + `vite build` 通过；全部改动文件 py_compile 通过

# BAZZ.AGENT v1.5.28

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.28 更新要点（审查缺陷修复：交易链路 6 项 P1/P2）

> 来源：第三方源码工程审查报告（提交 944ca16）。本次修复其中 6 项确定性缺陷，
> 每项均附离线回归测试（tests/test_v1528_fixes.py，15/15 通过）。

### F02 · 钱包已成交/待确认被误报「下单失败」（P1）
- `executor.place_report()` 此前只认 CEX 通道的 `status=="ok"`，钱包通道返回的
  `TRADE_FINISHED`/`TRADE_PENDING` 全部落入 error 分支 → 界面误报失败、跟踪登记跳过、诱导重复下单
- 现统一归一：`TRADE_FINISHED→FILLED`、`TRADE_PENDING→PENDING`（待查证，不自动重试），保留原始状态与命令回执供审计

### F10 · MCP 调用成功被显示为失败（P1）
- `agent_core._run_mcp_call()` 用 `res.get("ok")` 判断，而 `mcp_client.call_tool()` 成功返回 `{"status":"ok"}`
- 现两种契约兼容：`status=="ok" or bool(ok)`

### F04 · 订单成交后止损止盈提醒静默（P1）
- `order_tracker` 的 `_CLOSED` 把 FILLED 算终态 → 成交后 `_check_sl_tp()` 直接返回，
  「未成交时有提醒，真正成交后反而静默」
- 拆分两组终态：查单轮询仍含 FILLED（成交后不再查单），提醒监控改用 `_CLOSED_FOR_ALERTS`
  （不含 FILLED）→ **持仓存续期间止损/止盈持续监控**

### F01 · 更新完整性校验 100% 失效（P1）
- `updater._fetch_checksums/_fetch_manifest` 把资产名转小写后与全大写常量比较，永不相等 →
  永远拿不到校验和 → 安装时「缺失则跳过」形同虚设
- 修复①：统一小写比较；修复②：**fail-closed**——官方整包拿不到校验和（网络异常/缺 SHA256SUMS）
  一律拒绝安装，不再静默跳过（本地增量包仍走逐文件清单校验）

### F08 · 强制兜底工具名变布尔值（P1）
- `forced = forced_cand and (...)` 在允许条件下得到 `True`，分派器做字符串操作抛 TypeError
- 改为条件表达式显式保留工具名，不合法时置空

### F13 · 妖币雷达日报必然 TypeError（P2）
- `scheduler._run_meme_scan()` 误传 `limit=8`（真实签名 `force/top_n/min_qv`），且把返回的
  `dict{coins:[...]}` 当 list 迭代 → 日报永远失败
- 现按真实契约调用并显式读取 `coins`

### 回归验证
- 新增 `tests/test_v1528_fixes.py`：15 个离线用例全部通过（AST/函数提取 + 桩隔离，不联网不下单）
- 既有测试套件（test_v150_market / test_v151_radar_track）49 项全部通过

# BAZZ.AGENT v1.5.27

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.27 更新要点（全局美化弹窗：告别系统原生白框）

### 1. 全新应用内弹窗组件（ConfirmDialog）
- 深色玻璃卡片（glass-bright）+ 遮罩背景模糊 + 淡入缩放动画，与主界面设计系统完全统一
- 普通确认用金色主按钮；删除类危险操作自动切换红色警示主题
- 支持 Esc 取消 / Enter 确认 / 点遮罩关闭；「记住我的选择」金色自绘勾选框
- 文案跟随应用语言（中/英 i18n 新增 dialog.* 键）

### 2. 全部原生 confirm 替换（11 处）
- 删除会话 / 群聊房间 / Agent / 工作区文件 / 踢出群成员（ChatView ×5）
- 代理池节点删除（ProxyPoolView）、Cron 任务删除 / MCP 服务删除（AdminPanels ×2）
- 记忆条目删除 / 一键清空（MemoryOverlay ×2）——原 window.confirm 系统白框全部下线

### 3. 关窗询问弹窗同步美化
- 点 X 的「最小化到托盘 / 退出应用」询问由 Electron 原生 dialog 改为应用内弹窗（IPC 双向：主进程 ask-close → 渲染层弹窗 → answer-close）
- 竖排选项卡 + → 箭头指示，退出应用红色警示；Esc/点遮罩 = 取消，留在当前窗口
- 兜底：页面未就绪收不到 IPC 时 1.5s 后自动隐藏到托盘，关闭操作不卡死

# BAZZ.AGENT v1.5.26

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.26 更新要点（点关闭 → 可选最小化到系统托盘）

### 关闭行为升级
- 点 X（或标题栏关闭）不再直接退出：弹窗询问 **「最小化到托盘 / 退出应用」**，可勾选「记住我的选择，不再询问」
- **最小化到托盘**：窗口隐藏，应用与后台任务（定时监控、行情拉取、更新检查）继续运行；首次缩托盘有气泡提示
- 托盘图标：**左键**回到主窗口，**右键**菜单（打开 / 退出）
- 选择「退出」或托盘菜单「退出」才真正退出（后端随之一并结束）
- 偏好存 userData/window-prefs.json；无托盘图标资源的裸 dev 环境回退为直接退出

# BAZZ.AGENT v1.5.25

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.25 更新要点（文件查看器：最小化 / 关闭窗口控制）

### 查看器窗口控制
- 文件查看弹窗标题栏新增 **最小化（−）** 按钮：收起为右下角浮条（文件名 + 恢复 + 关闭），看盘/操作时文件保持打开不丢
- 点浮条文件名或 ↑ 恢复按钮回到完整窗口；X 或 Esc 彻底关闭（图片 objectURL 同步释放）
- 图标库新增 Minus；tsc + vite build 验证通过

# BAZZ.AGENT v1.5.24

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.24 更新要点（文件管理器图片可直接预览）

### 图片预览支持
- 此前文件列表里点图片（png/jpg 等）一律显示「二进制文件，不可文本预览」——查看器只有文本一个分支
- 新增后端原文端点 `/api/workspace/raw`（扩展名白名单 png/jpg/jpeg/gif/webp/bmp/svg/ico，路径防越界，20MB 上限，走 /api/* 统一鉴权）
- 前端点图片 → 带 token 拉取 blob → 弹窗内直接渲染 `<img>`（深色底居中、最高 58vh），关闭时释放 objectURL
- state.db 等非图片二进制行为不变（仍提示不可文本预览 + 删除按钮）

# BAZZ.AGENT v1.5.23

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.23 更新要点（发文抗抖加固：检测测全链路 · 网络错误自动重试）

### 1. 代理节点检测从「单点 ping」升级为「发文全链路」
- **实测踩坑**：v1.5.21 发文 4 连败——节点 ping api.binance.com 通，但 www.binance.com / S3 图片域超时，检测照样放行、发文必败
- `_url_alive` 现在要求**三端点全过**：api ping（200）+ www.binance.com + public.bnbstatic.com（任何 HTTP 响应算连通）；任一不过即判该节点不可用，ensure_working_proxy 会换下一个

### 2. square-post 网络错误内部自动重试
- mihomo 内核被 APP 自动拉起的**重启空窗**（数秒）会让发文瞬间 ECONNREFUSED——此前一次即死
- `api()` 与 S3 `uploadToS3()` 现在对网络类错误（ETIMEDOUT/ECONNRESET/ECONNREFUSED/UND_ERR 等）**自动重试 3 次**（3s/6s 退避），业务错误（401/参数）仍立即抛
- 叠加 APP 侧的换节点重试，单次发文最多 6 次尝试、跨 2 个节点

# BAZZ.AGENT v1.5.22

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.22 更新要点（广场发文默认「短贴多图」，正文直接可见图）

### 发文形态调整（实测反馈驱动）
- **实锤**：广场 OpenAPI 的长文（contentType=2）正文是 `bodyTextOnly` 纯文本，**永远插不了图**，只有单封面；带图只能走短贴（contentType=1，最多 4 图）
- square-rich-post **默认改为短贴多图**：封面图 + 24h 分时图 + 标题全文一贴发出，正文里直接看到图（已实测发布成功，两图齐全）
- 要传统长文形态加 `--article`；`--reuse` 复用不受影响
- Agent 提示词与 SKILL.md 同步更新分流规则；短贴超时 240s / 输出缓冲 16MB

### v1.5.21 实测补充
- 你的代理节点经 7899 对 bapi / api / S3 三域全通，发文链路真机验证通过（帖子 ID 365432062242575 / 365434349443879）
- 注意：mihomo 内核崩溃后 APP 会自动拉起，**重启空窗几秒内发文会 ECONNREFUSED**——属瞬时故障，重试即可（v1.5.19 起已自动重试一次）

# BAZZ.AGENT v1.5.21

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.21 更新要点（广场发文链路 · 沙箱命令解析修复）

### 1. 广场发文「上传超时失败」根治（RUNE 发文实测）
- **根因**：Node undici 默认连接超时 10s，代理节点/币安 S3 域名握手稍慢就抛 `UND_ERR_CONNECT_TIMEOUT`，发文必失败
- proxy-preload 全局挂载改为 `connect 30s / headers 60s / body 120s`，慢代理也能跑完图片上传（scripts/ 与 runtime/ 同步）

### 2. 沙箱命令解析两处修复
- **cat/ls/echo 被误杀**：白名单解析只认可执行名，wrapper 命令全被拒「不在白名单」，报错清单却仍列出它们——已补 wrapper 放行
- **反斜杠被吞**：Windows 下 shlex posix 转义吃掉路径反斜杠（`--reuse F:\1\...` → `F:1...`）导致复用目录校验失败——改 posix=quoted 保留原样并剥包裹引号

### 3. 技能防呆 + 复用容错
- market-data klines 无数据改为**直接报错**（此前返回 n:0 的 OK，模型把垃圾参数当成功继续跑；合约下架币提示切 spot）
- square-rich-post `--reuse` 自动锚定 workspace/square_rich 兜底，bad 路径给出尝试列表
- 实测确认：7899 代理链路当时是通的（代码里代理检测用 api.binance.com/ping，但方形图上传走 bapi/presignedUrl——S3 域名超时才导致失败，现已覆盖）

# BAZZ.AGENT v1.5.20

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.20 更新要点（安装包「无法自动关闭应用」修复）

### 安装前强杀文件锁进程
- **现象**：安装/更新时弹「Setup was unable to automatically close all applications」卡在 Closing applications——mihomo 内核 / runtime node / 后端都是**无窗口进程**，Inno 的 Restart Manager 关不掉（发文测试会把它们拉起来，持着安装根里的文件锁）
- **手动安装**：installer.iss 新增 PrepareToInstall 预处理——taskkill 强杀 BAZZ.AGENT.exe / ScoutBackend.exe / mihomo.exe，node/python 按**路径锚定安装根**强杀（不误杀用户自己的同名进程），杀完才进安装阶段
- **应用内自动更新**：更新脚本杀残留进程名单补上 mihomo 与 runtime node/python，setup 命令追加 `/FORCECLOSEAPPLICATIONS` 双保险

# BAZZ.AGENT v1.5.19

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.19 更新要点（技能网络兜底 · 广场发文路由 · 技能绑定补全）

### 1. 技能网络失败自动切代理重试
- 技能执行失败且命中网络错误特征（fetch failed / 超时 / ECONNRESET 等）时，自动实测代理池：先测当前节点 → 按延迟逐个激活实测（内核型自动拉起 mihomo 并切 selector）→ 找到能连通币安的节点后**带代理重试一次**
- Agent 主路径（exec_sandbox）与手动运行路径（skills_client）双覆盖；输出标注 `[auto-proxy]` 结果
- square-post 报错带出底层原因码（如 ETIMEDOUT）与「设置 → 代理池」自助指引；失败分诊区分「已重试仍失败（节点全挂）」与「池子无可用节点」

### 2. 广场发文路由修复
- 此前所有「发广场」都路由到 square-post 裸发文本，Agent 会自己手写简版文绕过富媒体管线（丢 SMC 推理链 / 仓位算法 / GitHub 链接 / 封面）
- 现在改为两级路由：**生成文章 / 行情文 / 深度分析发文 → 默认 square-rich-post**（自动取数 + Pillow 封面 + 固定结构组稿 + $cashtag/#hashtag）；只有用户给了现成正文 / 短帖 / 视频才走 square-post；改稿重发用 `--reuse <目录>`
- rich 发布同样记入广场台账

### 3. 9 个已装技能补入提示词路由
- news-sentiment（新闻情绪）/ portfolio-review（资产复盘）/ track-monitor（妖币复查）/ query-token-audit（代币审计）/ query-address-info（地址持仓）/ binance-tokenized-securities-info（代币化美股）/ binance-trading-signal（合约聪明钱）/ binance-sports-ai-analyzer（赛事预测）此前从未绑定，Agent 遇到相关需求不会调用——已按各技能真实 CLI 用法逐一接入，并留空参返回用法指引的兜底
- 工具注册表与 run_command 白名单全量核对：无死引用、无阻断

## 📜 历史版本

# BAZZ.AGENT v1.5.18

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.18 更新要点（安装进度窗口）

> 补录：本段依据 tag `v1.5.18` 的提交信息回溯整理，非发布当时撰写。

### 安装进度窗口
- APP 退出后由更新脚本拉起**置顶 WinForms 跑马灯对话框**（步骤文案 + 动画），静默安装期间用户不再面对「什么都没发生」的空白等待

# BAZZ.AGENT v1.5.17

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.17 更新要点（恢复版本守卫 · 防降级）

> 补录：本段依据 tag `v1.5.17` 的提交信息回溯整理，非发布当时撰写。

### 恢复版本守卫
- 拒绝安装比本地版本更旧的更新包（防降级回滚）
- 清理陈旧缓存残留

# BAZZ.AGENT v1.5.16

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.16 更新要点（更新器 spawn 修复 · 富媒体发文升级）

> 补录：本段依据 tag `v1.5.16` 的提交信息回溯整理，非发布当时撰写。

### 1. 更新器 spawn 从不执行修复
- 去掉 `DETACHED_PROCESS`——它会让 powershell 静默退出，导致更新脚本根本没跑起来
### 2. square-rich-post 升级
- 发文口径升级为 4h SMC 结构

# BAZZ.AGENT v1.5.15

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.15 更新要点（广场富媒体发文 · square-post 长文修复）

### 1. 新技能 square-rich-post：一句话发「全维度行情拆解」富媒体文
- 自动取 90d OHLCV/费率/OI/大户多空比/恐惧贪婪 → Pillow 画深色封面图（90日 K线+成交量+高低位标注+信息条）与 24h 分时图 → 组稿（$cashtag + #hashtag 服务端解析，事实与推测分栏）→ 调 square-post 发布带封面文章
- 后端新路由 /api/square/rich/compose（进程内复用 scanner 数据，5 分钟缓存）；产物落 workspace/square_rich/<SYM>_<ts>/
- 图表引擎用已打包的 Pillow 实现，**零新增依赖**（不引 matplotlib，包体积不涨）；打包态无独立 python，Python 能力一律走后端路由
- **合约占位脏数据防线**：SETTLING/下架合约（如 RAYUSDT）fapi 返回全等价格 0 量平线（此前 coin-report 取到 0.248 假数据的根因）——平线检测自动回退现货并在文中如实标注
- 台账打通：square-rich-post 发布自动记入 Square 台账页；llm 工具提示词引导 Agent 优先用富媒体发文

### 2. square-post 长文发布修复（「命令解析失败」根因）
- cli.mjs 新增 `--text-file` / `--title-file`：多段落正文先写文件再传路径，彻底绕开 shlex 引号/换行解析崩溃
- SKILL.md 明确「长文必须走文件」；台账 _extract_meta 同步支持从文件读回正文
- 修复 square_store.DATA_DIR 未定义导致台账写入静默失败（App 内发布从未成功记账的存量 bug）

## 历史

---

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.14 更新要点（自动更新修复 · 思考泄漏修复 · 中文代币参数修复）

### 1. 自动更新链路修复（重要）
- **安装器 spawn 静默失败修复**：裸 powershell 依赖 PATH，Electron 拉起的后端 PATH 被裁剪时找不到 → 应用退出但安装器没跑（v1.5.13 实测踩坑）。改用 SystemRoot 绝对路径 spawn
- **开机恢复兜底**：启动时检测 update-cache 遗留的 setup.exe + 安装脚本 → 自动补跑安装；连续 3 次失败停止重试并保留现场，新一轮更新自动清零计数

### 2. Agent 深度思考修复
- 修复推理模型（deepseek-v4-flash 等）同时返回原生 reasoning_content 与正文 <thinking> 块时，elif 短路导致思考泄漏成正文的问题；两种思考现在合并进深度思考面板

### 3. 中文展示名代币参数修复
- coin-report 传 牛来USDT 会被拼成 牛来USDTUSDT（后缀判断正则仅 ASCII）；改为结尾匹配，中文带后缀原样、裸名才补 USDT

## 历史

---

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.13 更新要点（中文币名识别 · 防猜测规则）

- **中文币名识别**：行情页中文展示名代币（如『牛市』）可直接识别——『帮我分析 牛市』→ 牛市USDT，不再误判成其他交易对
- **口语剥离**：自动剥离 帮我/分析一下/大盘/怎么样 等口语与大盘语，整句不再被当成币名
- **常见别名**：比特币→BTC、以太坊→ETH、狗狗币→DOGE、瑞波→XRP 等直接映射
- **防猜测规则**：技能报错（Alpha 链上代币不在币安行情内）时改走 query-token-info 链上分析或反问用户，严禁换别的交易对来猜

## 历史

---

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.12 更新要点（下单链路修复 · 审批卡片补全 · 推理泄漏修复）

### 1. 下单执行链路（P0/P1 修复）
- **交易所精度规则**：下单前按 exchangeInfo 的 stepSize/tickSize 用 Decimal 取整（1h 缓存），根治 LOT_SIZE/PRICE_FILTER -1013 拒单；数量不足最小下单单位时明确报错
- **钱包命令修正**：`baw token swap`（不存在的命令 → `unknown command 'token'`）改为 `baw market-order swap --fromTokenQty ... --fromToken <合约地址> --toToken <合约地址> --binanceChainId 56 --json`
- **钱包通道护栏**：DEX 链上仅 BNB/USDT 有已知合约地址，其他币种给明确指引连 CEX，不再发必败命令
- **swap 终态轮询**：orderId 仅代表已提交，轮询 market-order list 3×8s 到终态——FAILED 如实报错、FINISHED 报成功、超时报 PENDING，不再把链上失败当成功
- **错误文案按通道路由**：钱包通道失败不再误报「交易所连接问题」

### 2. 审批卡片补全
- 新增「本金」「杠杆」两行；propose_trade 支持 margin_usdt / leverage 参数（默认 50U / 1×）；方案文本同步展示名义价值
- 卡片金额兜底计算（quantity×price），中英文案补齐

### 3. Agent 推理泄漏修复
- `<thinking>` 块提取改为**全部闭合块**合并 + 二次剥离未闭合尾巴（模型多块推演、末块被截断时不再漏进正文）
- 沙箱白名单报错区分「路径不存在」与「可执行名未命中白名单」，便于定位打包环境问题

### 4. 妖币追踪
- 修复历史战绩「日期」列时间戳按毫秒解析显示 1970 年的问题（后端为秒制）

## 历史

---

# BAZZ.AGENT v1.5.14

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.14 更新要点（更新器修复 · 启动自恢复 · 思考泄漏 · CJK 币名）

> 补录：本段依据 tag `v1.5.14` 的提交信息回溯整理，非发布当时撰写。

### 修复
- 更新器 spawn 修复 + 启动自恢复（更新中断后可自愈）
- thinking 泄漏修复（模型思考内容不再漏进正文）
- CJK 币名后缀解析修复

# BAZZ.AGENT v1.5.13

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.13 更新要点（CJK 币名识别 · 防猜测规则）

> 补录：本段依据 tag `v1.5.13` 的提交信息回溯整理，非发布当时撰写。

### 中文币名与防幻觉
- CJK 语境下的币名识别（`\b` 对中文无效，改手工边界）
- 防猜测规则：禁止模型编造不存在的交易对

# BAZZ.AGENT v1.5.12

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.12 更新要点（下单链路 · 审批卡 · 思考泄漏 · 追踪日期）

> 补录：本段依据 tag `v1.5.12` 的提交信息回溯整理，非发布当时撰写。

### 修复
- 下单链路修复
- 审批卡补 margin / leverage 展示
- thinking 泄漏修复
- 妖币追踪「日期」列修复

# BAZZ.AGENT v1.5.11

## 🆕 v1.5.11 更新要点（portable.zip 退役 · setup.exe 成为唯一全量包）

### 1. Release 精简
- portable.zip 不再生成/发布：Release 资产只剩 **setup.exe + delta-<ver>.zip + SHA256SUMS + MANIFEST.json**
- 增量差分（delta）不受影响——它的基线是上一版 MANIFEST.json 清单对比，从来不依赖上一版 zip

### 2. 应用内自更新回退路径切换
- delta 被跳过时（版本跨太大/变化超 60%/超 120MB），全量回退从「下载 portable.zip 整目录替换」改为「**下载 setup.exe 静默安装**」：
  - `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /DIR=<当前安装根>` 原地覆盖
  - 安装器无 [InstallDelete]、workspace 不在 [Files] 清单 → **用户数据天然保留**，无需备份/还原
  - 安装完成自动校验新 exe 并重启；SHA256SUMS 强校验不变
- 静默安装先等应用退出并释放文件锁；setup.exe 会先挪出安装根，避免边读边写

### 3. 兼容性说明
- 从 v1.5.10 及更早版本升级到本版：走 delta（正常）或旧逻辑整包替换（最后一次用 portable.zip）均不受影响；**此后**的更新全部走新链路
- 便携解压目录（无注册表安装记录）更新时 /DIR 强制指回当前目录，不会装到默认路径

---

# BAZZ.AGENT v1.5.10

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.10 更新要点（Agent ↔ 技能链路健壮性）

### 1. run_skill 超时与截断放宽
- 超时 90s → **180s**：coin-report 一次拉 90 日 K 线 + 费率 + OI + 多空比 + 恐惧贪婪 + 战绩，后端冷缓存 / 慢代理时 90 秒跑不完会超时失败（表现即「分析失败」）
- 输出截断 6000 → **12000** 字符：market-data `klines limit=90` 的 JSON 约 8KB，旧上限会截断导致模型拿残缺数据硬分析

### 2. 直跑技能 CLI 的报错指引
- 模型绕过 run_skill 用 run_command 直跑 `.agents/skills/*/scripts/cli.mjs` 时，报错从「命令不在白名单」改为明确指引「已安装技能请用 run_skill 工具执行」——减少无效重试轮次

### 3. 审计确认（无改动）
- 技能 CLI 落盘位置（妖币/复盘 目录）经沙箱 cwd 锚定 workspace，正确；雷达价格纯 WS 快照零外呼；追踪状态机除零/爆仓优先/过期守卫齐全

---

# BAZZ.AGENT v1.5.9

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.9 更新要点（打包版技能执行修复 + 追踪列调整）

### 1. 打包版 Agent 调技能必失败 · 根因修复
- **现象**：安装版（如 `F:\1\BAZZ.AGENT`）里 Agent 调 coin-report / market-data 全部报「命令不在白名单（baw, cat, …）: F:\1\BAZZ.」——技能一次都用不了，开发版无此问题
- **根因**：`run_skill_cmd` 按规范用**内置 runtime 的绝对路径** node.exe 拼命令（打包用户机器没有 PATH node），但沙箱 `_run_one` 的白名单只认裸键名 `node`，`F:\...\runtime\node.exe` 被整段拒绝
- **修复**：`_resolve_exe0` 白名单归一化——绝对路径形式若 basename 命中白名单可执行文件（node/npx/npm/git/baw/python 等）且文件真实存在，折算为白名单键并用**回原绝对路径**启动（不走 PATH）；不存在的路径 / 白名单外的 exe（如 cmd.exe）照旧拒绝，沙箱安全性不变

### 2. 妖币追踪列调整
- 历史战绩移除「当前涨跌幅」列，新增「**日期**」列（关单时间 MM-DD HH:mm，悬浮显示完整时间）
- 进行中列表保留「当前涨跌幅」（现价 vs 发现价）

### 3. 中英文补齐
- 补 `markets.stage.SHORT_AMBUSH`（做空埋伏 / Short Ambush）——修复做空埋伏行显示原始 key
- 补英文缺失的 `markets.trackPnl` / `trackPnlTip` / `holding` / `holdingTip` / `trackChg` / `trackDate`

---

# BAZZ.AGENT v1.5.8

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.8 更新要点（妖币雷达独立页 + 追踪智能持有 + 失败复盘 + 做空埋伏）

### 1. 一键分析根治（走技能，不再直连 API 失败）
- 行情页「分析」按钮生成的提问明确要求 Agent 先调 `run_skill(coin-report)` 拉本机全维度数据（90 日 K 线 / 动量 / 区间分位 / 费率 / OI / 大户多空比 / 恐惧贪婪），不再出现「分析失败无法获取信息」
- Agent 兜底路由升级：识别「分析/研报/走势/解读 + 明确交易对」时强推 coin-report（此前强推全市场扫描，逼模型直连币安 REST 在受限地区必失败）；persona 禁用时不误推
- `run_skill` 工具描述内置本机技能速查（coin-report / market-data 子命令示例 + 禁止直连币安 API 提示）
- 全部内置技能 CLI（coin-report / market-data / track-monitor / risk-guard / portfolio-review / news-sentiment）端口回退加固：8080 被带鉴权的其他服务占用时自动切 8081，任何非 200 都换端口再试

### 2. 行情页改版
- **妖币雷达独立成页**：顶部维度栏新增第 4 个 Tab（现货 / 合约 / 股票化代币 / 妖币雷达），雷达卡全宽展示；妖币追踪也归入雷达 Tab（雷达卡下方），现货页更干净
- 移除现货页全市场交易对表（全部/涨幅榜/跌幅榜）与「24H 成交额热度榜」卡片
- **多空比背离标注方向**：背离 pill 按大户方向显示「背离·偏多」（绿）/「背离·偏空」（红），悬浮附完整解释（方向跟随大户）
- 修复表头显示原始 key（`markets.h.range` 中文翻译缺失）

### 3. 妖币雷达：做空埋伏（SHORT_AMBUSH）
- 判定（三重共振）：高位（90 日分位 ≥75% 且 30d 涨 ≥30% 或 3d 涨 ≥15%）+ 滞涨（24h ≤+5% 且 1h ≤+1%）+ 过热信号（费率峰值 ≥0.3% 且 taker 衰竭 / taker 买盘骤降 / 大户比 ≥2 拥挤 / OI 顶背离）
- 命中 → 「做空 · 崩跌前」阶段，归入埋伏窗口组（红底行）；自动登记为做空方向追踪——埋伏窗口从此多空两侧都有

### 4. 妖币追踪：10x 合约口径 + 达标智能持有
- **失败阈值改 10x 口径**：逆向波动 ≥10%（≈10x 强平线）即判失败关单（做多看最大跌幅 / 做空看最大涨幅），不再等到 -20%——10x 杠杆下早就爆了
- **达标不再直接结束**（真妖币不会只到 25%）：顺向 ≥25% 时判定动能——现反转因子（费率极值回落 / OI 脉冲 / 爆仓潮）→ 立即落袋；否则转**持有模式移动止盈**，自持有期极值回撤（做多）/反弹（做空）≥12% 才落袋；持有中逆向 ≥10% 仍判爆仓失败（优先）
- **仓位模拟 100U × 10x**：每只妖币默认 100U 本金开 10 倍合约（爆仓封底 -100U）；追踪表新增「10x 盈亏」列，结局通知 toast 带模拟盈亏
- 达标转持有时推送「妖币达标 · 继续持有」应用内 toast + 系统通知；进行中列表显示金色「持有中」pill

### 5. 失败复盘（dump 自动归因）
- 做多/做空判失败关单时自动生成复盘：失败路径（假突破回落 vs 直接破位 / 轧空）、关单时因子（费率 / OI / 大户比 / taker / 爆仓额）、当时大盘环境、按阶段教训（吸筹证伪 / 点火假突破 / 逼空）
- 入库 `radar_tracks.review`（旧库自动迁移）+ 落盘 `workspace/复盘/妖币追踪复盘_YYYYMMDD.md` 按天归档；历史战绩 dump 行显示「失败复盘」pill，点击展开全文

### 6. 界面细节修复
- 雷达操作列按钮文字断词修复（「分/析」「现货买/入」不再折行），列宽重排
- 追踪面板说明文案同步 10x 持有规则（中英）

---

# BAZZ.AGENT v1.5.7

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.7 更新要点（技能可用性根治 + 妖币追踪方向感知）

### 1. 内置技能全线不可用 · 根因修复（三连 bug 叠加）
- **Windows accept 死亡**：默认 Proactor 事件循环下，客户端异常断连（如技能 CLI 超时中断 / 页面刷新）触发 `Accept failed on a socket`（WinError 64）后 accept 循环**永久死亡**——进程还活着但所有新连接无响应，表现为行情页 / 技能页全部转圈。现启动即切换 Selector 事件循环（uvicorn≥0.30 会显式注入 Proactor 工厂无视 policy，已同步 `loop="none"` 修正）
- **技能运行阻塞后端**：`/api/skills/run·install` 在事件循环线程里同步等待 120-240s 子进程，期间技能 CLI 回调本后端取行情数据时后端无法响应 → fetch 挂死到自身超时，技能表现为「根本无法使用」。已全部移入线程池（实测：技能运行 10.9s 期间行情接口 0.02s 响应）
- **Agent 对话跑技能必失败**：`exec_sandbox` 内 `_os` 未定义（v1.3.9 引入的 NameError），对话里让 agent 执行任何技能都会报错，已修复

### 2. 技能自身修复
- coin-report：裸代币参数丢失（如 `SOLUSDT` 直接报「需要 SYMBOL」）；裸基础资产（`WLD` / `SOL`）自动补 USDT 后缀
- 全部内置技能执行超时 10s → 60s（后端冷缓存首扫不再误杀）；Windows 中文输出统一 UTF-8 解码，不再乱码

### 3. 对话分析代币修复
- 模型网关异常回包（HTTP 200 但 choices 为空）此前被静默吞掉，现明确报错并记录原因
- 中文语境代币名解析修复（`分析WLD` 之前匹配不上，`\b` 对中文无效改手工词边界）
- 资金费率为 None 时的崩溃防护

### 4. 行情页：移除爆仓流（按需求）
- 强平实时模块整体下线（含大单高亮 / 按币筛选），中英文案同步清理；后端接口保留供 market-data 技能使用

### 5. 妖币追踪：方向感知（做多 / 做空）
- 追踪记录新增 **做多 / 做空** 标签与 **当前涨跌幅** 列（进行中 = 现价 vs 发现价，历史 = 结局价 vs 发现价）
- 做空记录（雷达做空信号自动登记）：**跌 ≥20% 记暴涨兑现（做空盈利）**，涨 ≥25% 记失败；做多规则不变——**做多跌 ≥20% 判定失败**
- 旧数据自动迁移（默认做多），雷达战绩统计口径同步按方向翻转

---

# BAZZ.AGENT v1.5.6

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.6 更新要点（技能库完善 + 安装残留根治）

### 1. Agent 技能再添两件（研究侧能力补全）
- **portfolio-review**：资产快照 + 周期复盘——币安 CEX 非零余额估值、近 N 天真实成交统计（买卖笔数 / 金额 / 净流向 / 分币种）、挂单与订单跟踪，Markdown 周报落盘 `workspace/复盘/`
- **news-sentiment**：加密新闻抓取 + 关键词情绪统计（零 Key）——中文快讯（PANews）+ 英文头条（CoinDesk / Cointelegraph RSS），latest / coin / sentiment 三命令，可与分析技能组合补消息面

### 2. 技能库「内置技能」分组
- 6 个随应用分发的本地技能（market-data / coin-report / track-monitor / risk-guard / portfolio-review / news-sentiment）在技能库独立分组展示：金色「内置」标签、预设一键命令、随版本更新、不可移除（后端拦截）
- 中英文案齐全，总安装计数包含内置技能

### 3. 技能安装/更新残留根治
- 启动与每次安装/更新后自动清理 skills CLI 异常残留：悬空 junction、指向 `.agents/.agents` 坏商店的联接、嵌套商店本体；好目录与有效联接一律不动
- junction 检测改用 `os.readlink`（Python 3.11 打包版兼容，原 `os.path.isjunction` 是 3.12 API 会静默失效）
- 安装命令成功 ≠ 落地：安装后校验 SKILL.md 存在，下载被网络拦截时报错引导检查代理

### 4. 版本号修复
- 顶栏版本号此前硬编码「v2.4.9」，现从 package.json 构建时注入真实版本（v1.5.6）

---

# BAZZ.AGENT v1.5.5

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.5 更新要点（Agent 技能四件套 + 行情页大更新）

### 1. Agent 技能四件套（研究侧能力补全）
- **market-data**：本地行情数据网关（K 线 / 资金费率 / 持仓量 OI / 大户多空比 / 爆仓流 / 恐惧贪婪 / bundle 一键分析包）——走本机后端（5 分钟缓存 + 代理池出口），根治 Agent 直连币安 15s 超时与输出截断问题，分析代币不再「取不到历史数据」
- **coin-report**：一键生成标准研报落盘 `workspace/妖币/<币种>_研报_*.md`，自动填好 90 日结构（区间位置 / 收盘分位）、衍生品、市场环境、追踪战绩，「结论与计划」留白由 Agent 基于事实填写
- **track-monitor**：妖币追踪定时复查，自发现以来涨跌超阈值（默认 ±15%）自动标记暴涨 / 暴跌预警；配合定时任务实现全自动盯盘（应用关着也跑）
- **risk-guard**：下单前护栏——固定风险仓位计算（资金 / 风险% / 入场 / 止损 → 数量 / 名义值 / 保证金 / 强平距离警告）+ CEX 当前敞口检查

### 2. 行情页六项升级（v1.5.4 同包内容）
- **币种详情浮层**：点击任意行情行（现货 / 合约 / 股票化 / 雷达 / 追踪 / 异动 / 爆仓）弹出，实时价 + 24H/7D 走势切换 + 费率 / OI / 大户多空比 + 下单与分析入口
- **迷你走势图**：Hero 大盘卡内置 SVG 渐变面积线（涨绿跌红，全组件共享缓存）
- **恐惧贪婪指数卡**：数值五档着色 + 8 日历史柱状图
- **爆仓流大单高亮**：≥$100K 强平单金框标记 + 图例，支持按币种筛选
- **合约表 OI 列**：USD 名义持仓，可见行批量拉取（后端 5 分钟缓存，前端 60s 刷新）
- **妖币追踪胜率可视化**：结局占比堆叠条 + moon / dump / expired 统计卡

### 3. 后端配套
- 新增 `/api/market/klines`（K 线收盘价）、`/api/market/oi`（合约持仓量批量）、`/api/market/fng`（恐惧贪婪指数）三条路由

---

# BAZZ.AGENT v1.5.4

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.4 更新要点（行情页六项升级）

### 1. 币种详情浮层
- 点击任意行情行（现货 / 合约 / 股票化 / 雷达 / 追踪 / 异动 / 爆仓）弹出详情：实时价 + 24H/7D 走势切换 + 资金费率 / 持仓量 / 大户多空比 + 下单与分析入口，Esc 或点遮罩关闭

### 2. 迷你走势图
- Hero 大盘卡内置 SVG 渐变面积线（涨绿跌红），全组件共享 5 分钟缓存，详情浮层复用

### 3. 恐惧贪婪指数卡
- Hero 行新增情绪卡：数值五档着色（极度恐惧 → 极度贪婪）+ 8 日历史柱状图

### 4. 爆仓流大单高亮 + 按币筛选
- ≥$100K 强平单金框标记 + 图例；新增币种筛选输入框，聚焦关注标的

### 5. 合约表 OI 列
- USD 名义持仓列，可见行批量拉取（后端 5 分钟缓存，前端 60s 刷新）

### 6. 妖币追踪胜率可视化
- 追踪面板新增胜率统计卡 + moon / dump / expired 结局占比堆叠条

### 7. 后端配套
- 新增 `/api/market/klines`（K 线收盘价）、`/api/market/oi`（合约持仓量批量）、`/api/market/fng`（恐惧贪婪指数）三条路由

---

# BAZZ.AGENT v1.5.3

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.3 更新要点（妖币追踪 + 行情页双栏终端风重设计）

### 1. 妖币追踪：启动前发现 → 结局自动验证
- 妖币雷达在**点火 / 吸筹阶段**（启动前）首次发现妖币时自动建档：记录发现时间、发现价、妖币度评分与触发理由（同币种进行中跟踪去重）
- 后台持续监控价格轨迹：峰值 / 谷值 / 最大涨幅 / 最大跌幅实时更新
- **自动判定结局**：moon（暴涨兑现）/ dump（暴跌兑现）/ expired（到期失效），历史战绩面板按时间倒序展示全部历史
- 结局触发 toast + 系统通知；雷达下方新增「进行中 / 历史」追踪面板，可一键跳转下单

### 2. 行情页重设计：双栏终端风
- **Hero 大盘速览条**：BTC / ETH / BNB / SOL 实时价大字卡（秒级 WS 推送 + 涨跌闪烁动画 + 24h 区间位置条）+ 全市场宽度卡（上涨占比 / 涨跌比 / ±20% 突发数）
- **双栏布局**：左主区（妖币雷达 → 妖币追踪 → 智能异动 → 全市场表）+ 右侧 340px 信息流侧栏（资金费率 → 爆仓流 → 多空比 → 成交额热度），滚动时侧栏固定；窄窗口自动回退单栏
- **维度切换条 sticky**：滚动时固定在顶部（毛玻璃背景）

### 3. 数据可视化增强
- **价格闪烁**：任何行价格变动时绿/红一次性高亮（WS 实时驱动）
- **24h 区间位置条**：现货行 / 合约行内直观显示当前价在 24h 高低区间的位置（替代原最高/最低两列纯数字）
- **成交额热度榜**：每行带相对成交额彩色 bar；**多空比面板**：每行带大户多/空双段比例条（1:1 中线）
- 侧栏行组件统一 hover 高亮与间距节奏；关键数字放大、标签层级清晰

### 4. 模块精简
- 资金费率拥挤 + 费率极值合并为侧栏「资金费率」面板（极值榜 / 多空拥挤 Tab 切换，翻转提示置顶）
- 移除底部 24H 领涨/领跌卡（与全市场表涨/跌 Tab 重复）
- 雷达头部说明收进 tooltip，一行放下模式切换 + 方向过滤；合约 / 股票化代币维度同步表头升级

---

# BAZZ.AGENT v1.5.1

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.1 更新要点（修复）

### 修复：妖币雷达报错「list index out of range」
- 修复妖币雷达 v2 在部分币种 15m K 线不足 96 根时（新上市币 / 数据缺口），BTC β 残差计算出现负索引越界，导致整个雷达请求失败、界面显示红色错误条的问题
- 现在数据不足时自动从可用区间起始计算；数据充足时评分行为完全不变
- 实测恢复正常：单次扫描返回 30+ 币种与完整阶段分布（垂直拉升 / 点火 / 吸筹 / 崩跌 / 沉寂）

---

# BAZZ.AGENT v1.5.0

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.5.0 更新要点（行情大更新 · 妖币雷达 v2 四层模型）

### 1. 妖币雷达 v2：四层模型（触发 → 确认 → 语义 → 过滤）
- **触发层**：短时价格暴动检测（跳跃因子 jump_L / flow_price 主买流 / 5m 速度与加速度 / 15m 相对放量 RVOL / 24h 振幅），0-99 妖币度评分
- **确认层**：合约杠杆与资金驱动因子——OI 24h/48h 变化与 15m 脉冲（四象限）、funding 极值与峰值回落、大户持仓多空比、taker 买卖比骤降、**实时爆仓流消费**（5 分钟单币强平额/方向）
- **语义层**：六阶段生命周期自动判定——吸筹 → 点火 → 垂直拉升 → 派发顶部 → 崩跌 → 沉寂，行内阶段标签着色
- **过滤层**：$5M 成交额地板、新币 30 天排除、刷量/假量检测（单笔均额过低）、BTC β 残差去大盘噪声、同阶段 30 分钟冷却去重
- 「启动前·埋伏」与「起飞中·追涨」同源 v2 引擎（异常自动回退 v1 日K 兜底），雷达头部新增阶段分布计数与引擎标识

### 2. 爆仓流面板（!forceOrder@arr 实时强平）
- 新增全市场强平单实时订阅：内存缓冲最近 800 条 + 分钟桶统计；面板显示 5 分钟多/空爆金额与笔数 + 最近强平单流水（点击任意币可跳下单）
- **单边密集爆发提醒**：1 分钟单边清算 ≥ $1.5M、≥5 笔且 ≥3× 20 分钟基线 → 应用内 toast + 系统通知（同方向 10 分钟冷却）

### 3. 多空比 · 大户持仓面板
- 新增大户持仓多空比（topLongShortPositionRatio）× 散户账户比（globalLongShortAccountRatio）双比值面板
- **背离标记**：大户与散户方向相反（主力反向）时高亮提示

### 4. funding 极值榜 + 翻转提醒
- 费率极值榜（|rate| ≥ 0.30%）；相邻两期结算费率**变号翻转**在面板置顶提示
- funding 极值 / 翻转事件 → toast + 系统通知（同币同类型 6 小时冷却）

### 5. 其他
- 新增 /api/market/radar（雷达 v2 全量）、/api/market/longshort（多空比）、/api/market/liquidations（爆仓流）三条路由
- meme_watch 工具输出升级：阶段标签 + 确认因子上下文（flow/RVOL/费率/OI/大户比/taker/爆仓额）+ 触发理由
- 雷达前端改单次全量拉取（省一半请求）；i18n 双语 key 补齐（含 en 缺失的 alert 文案）

---

## 🆕 v1.4.6 更新要点（delegate 并行子代理）

### 1. 并行子代理委派（学习 Hermes delegate_tool）
- 新增 `delegate` 工具：把**相互独立**的子任务拆成 tasks=[{name, prompt}]（最多 4 个），每个子任务由一个**全新 Agent 并行真实执行**（带全部工具：行情/技能/文件/命令，但不能再次委派）
- 典型场景：多标的独立研究（BTC/ETH/SOL 各查各的）、多路径同时排查、批量重复性子任务；说「并行 / 同时 / 分开查」就会触发
- **父级只看每个子任务的最终摘要**（子代理中间工具过程不回流），摘要按预算截断，防止撑爆上下文
- 防递归：子代理工具集自动剔除 delegate（spawn depth=1）；prompt 必须自包含（子代理看不到父对话）；总超时保护，超时任务如实标记不挂死
- Bot 工具白名单候选新增 schedule_task / clarify / delegate 三个，档案可按需开关

---

## 🆕 v1.4.5 更新要点（自定义定时盯盘 + 结构化追问 + 工具输出落盘）

### 1. 自定义定时盯盘任务（学习 Hermes cronjob）
- schedule_task 全面升级：除了内置的「全市场扫描日报」「妖币雷达日报」，新增 **custom_prompt 自定义任务**——你用一句话描述周期性需求（如「每天 9 点总结 BTC 行情并给出关键位」「每小时检查资金费率异常」），Agent 到点会**带着全部工具无头真实执行**，结果写入专属会话「定时任务 · 任务名」
- 新增 update 动作：可改任务名 / 时间 / 类型 / 执行指令；失败也会投递到会话（Hermes failure_deliver 语义），不会静默丢失
- 无人值守场景遇到需审批/追问的操作会如实记录说明，不会卡死调度

### 2. 结构化追问 clarify（学习 Hermes clarify_tool）
- 需求存在关键分叉（币种/周期/方向/预算不明且猜错代价高）时，Agent 会发**选择题卡片**让你点选，而不是瞎猜
- 每题最多 4 个选项、标注推荐项；120 秒未选择自动按「最佳判断继续」完成原任务，不会卡住对话
- 只用于关键决策——能用合理默认值继续的不会频繁打扰

### 3. 工具输出落盘/截断（学习 Hermes tool_output_limits）
- 工具返回超过 20KB 的超长输出不再硬塞给模型：**完整内容自动存盘**到 workspace/spill/，回传截断文本+句柄路径
- Agent 需要完整内容时可用 read_file 读句柄，长网页/大报告不再撑爆上下文，回答更稳更快

---

## 🆕 v1.4.4 更新要点（Agent 任务清单 + 记忆报告 + 通知加固）

### 1. Agent 任务清单 todo_write（学习 Hermes todo_tool）
- Agent 可把多步任务**拆解登记成清单**（add/toggle/remove/list/clear_done），持久化到本地数据库
- **未完成任务每轮自动注入提示**：跨轮、跨天都不会烂尾，Agent 不会在没做完时装作已完成
- 问问它「帮我盯着这几件事」「任务完成了吗」就能看到清单勾选状态

### 2. 记忆报告导出（Markdown）
- 记忆页新增「导出记忆报告 (MD)」：按 **偏好/事实/事件** 分组排版，含更新日期、来源与命中次数
- 原 JSON 备份导出保留，两者并存

### 3. 操作失败不再静默（前端加固）
- 网关加载、插件加载、深度思考开关、新建/删除/归档/重命名会话、代码复制等操作失败时**右下角弹出错误 toast**，排查问题不再两眼一抹黑

---

# BAZZ.AGENT v1.4.3

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.4.3 更新要点（会话搜索 + 会话标题自动生成）

### 1. 会话搜索（学习 Hermes session_search）
- 会话列表新增搜索框：**标题 + 全部消息正文**模糊匹配，跨所有历史对话找内容
- 命中会话显示**命中片段**（关键词上下文）与 🔍 命中条数，按最近排序，支持进入某 Agent 后只搜该 Agent 的记录
- Agent 也获得 `search_history` 工具——问「我之前说过什么 / 上次聊到哪」时可直接检索你的历史会话
- 群聊房间不参与搜索（另有入口管理）

### 2. 会话标题自动生成（学习 Hermes title_generator）
- 首轮回复结束后，标题仍为「首条消息截断」的会话会在后台用摘要模型自动升级为 **4-16 字语义标题**（如「BTC 加仓分析」「合约费率扫描」）
- 只升级默认标题：**你手动改过的名字永不覆盖**（生成期间改名也有竞态保护）
- 带 Agent 档案前缀（@交易员 · …）的会话升级后保留前缀
- 模型输出做清洗：剥 JSON/代码围栏/引号，拒绝「答案形状」的超长输出

### 3. 修复：对话正常完成时回复未落库
- 此前助手回复**只在点「停止」或流异常时才写入本地数据库**，流正常播完反而丢失——重启后该条回复消失
- 现在正常完成同样落库（含思考链、命中模型、自动记忆与自动标题）

---

# BAZZ.AGENT v1.4.2

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.4.2 更新要点（长对话记忆压缩 + Agent 循环防呆）

### 1. 长对话不再「失忆」（上下文持久化压缩）
- 此前对话超过 12 条后，最老的内容会被**直接丢弃**——半小时前定下的止损位、看过的标的结论可能凭空消失
- 现在被裁掉的旧对话会自动**增量压缩成滚动摘要**并持久化到本地数据库，摘要持续更新、越聊越准
- 摘要跨轮复用（不重复消耗摘要模型调用），新会话轮次直接带着「前情提要」继续聊
- 对话回滚（重新生成）后自动兼容，摘要不丢不错位

### 2. Agent 不再「原地打转」（重复调用防护）
- 模型偶尔会陷入死循环：用一模一样的参数反复调用同一个工具，烧光调用轮次也出不了结果
- 现在相同工具+相同参数**执行过 2 次后自动拦截**，直接提示模型基于已有结果作答——省 API 调用、回答更快
- 交易类动作不拦截（参数不变的重试是合理行为）

### 3. 空回复与轮次双保险
- 模型偶发「零输出」时的自动重试限制为 **1 次**，不再空转烧轮次
- 调用轮次即将用尽时自动**提前提醒模型汇总作答**——不再出现「跑满 14 轮被硬掐断、却没有结论」的情况

---

# BAZZ.AGENT v1.4.1

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.4.1 更新要点（记忆系统升级 · 对齐 Hermes 精选式记忆）

### 1. 记忆有了类型与来源（不再靠关键词猜）
- 每条记忆现在带有**类型**（偏好/事实/事件）与**来源**（自动提炼 / Agent 写入 / 手动），注入对话时按类型分组、偏好优先
- 历史旧记忆自动兼容（默认归为「事实」），无需任何手动迁移

### 2. 记忆注入总量封顶 + 防注入攻击
- 注入对话的记忆总量限制在 **3500 字符**内（偏好优先、新的在前），记忆再多也不会撑爆上下文
- **安全清洗**：记忆文本里混入的「忽略以上指令 / ignore previous / 伪系统标签」等提示注入内容，进入对话前一律剥离——堵住「聊天诱导写记忆 → 毒化后续所有会话」的口子

### 3. 自动记忆不再重复堆积
- 提炼新记忆时先对照已有记忆，**内容相似直接原地更新**（保留原条目），不再换个说法就多存一条
- 每条记忆注入对话时记录**命中次数**，记忆页可见 ⚡ 活跃度

### 4. 敏感信息绝不入记忆
- API Key / 私钥 / 助记词 / 密码等内容，无论自动提炼还是「记住：…」指令写入，一律**拒绝入库**并提示

### 5. Agent 记忆工具全面升级
- 「记忆」从单一「记住」升级为完整动作模型：**新增 / 更新 / 删除 / 检索**，Agent 可自主管理长期记忆
- Agent 不能删除你手动创建的记忆；聊天里说「记住：我只做现货」「忘掉那条偏好」都能正确处理
- 记忆页新增类型徽标（偏好/事实/事件）与命中次数显示

---

# BAZZ.AGENT v1.4.0

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.4.0 更新要点（行情实时化 + 订单跟踪 + 风控提醒）

### 1. 行情 WebSocket 实时流（现货 30s 轮询 → 秒级实时）
- 现货行情接入币安全市场免费 WS 流（`!miniTicker@arr`，约 1s 推送一次），**替换 30 秒轮询**——价格、24h 涨跌、成交额秒级跳动，无需 API Key
- 前端单例 WebSocket + 按需订阅（引用计数），只订当前可见标的，断线自动重连（指数退避）、心跳保活
- 顶部显示「实时」连接状态徽标；WS 未覆盖的数据自动回退 REST 值，永不空白
- ※ 2026-09 实测：币安已下线 `!ticker@arr` 全市场数组流，合约 WS 端点（fstream）对部分地区不推流——合约维度维持 REST 刷新（30s），现货全量实时

### 2. 行情界面渲染优化（微渲染）
- 拆分现货行 / 合约行 / 股票卡 / 雷达行 / 信号行独立 memo 组件，每个组件**只订阅自己那一个标的**的实时价
- 价格变化时只有对应那一行重渲，整表不再随 1s tick 全量重渲——千行行情滚动/筛选丝滑不卡

### 3. 订单状态跟踪卡片（交易所页）
- Agent 下单成功后**自动登记跟踪**：待成交 / 部分成交 / 已成交 / 已撤销状态流转一目了然
- 后台定时同步订单状态，卡片实时显示最新价与浮动盈亏（对比委托价）
- 可手动移除不再关注的订单；跟踪状态落本地数据库，重启应用不丢失

### 4. 止损止盈接近/触发提醒（应用内 + 系统通知）
- 对跟踪中的订单实时监控价格与止损/止盈位距离，**接近 0.5% 内提前预警**、触发即强提醒
- 应用内 Toast 弹窗 + 系统级通知双通道；同类提醒冷却抑制，不刷屏
- 订单状态变化（成交/撤销）同样推送提醒，不用盯盘也知道单子动向

### 5. 其它
- 打包依赖补齐 `websockets`（此前缺失会导致实时流在桌面版静默失效）

---

# BAZZ.AGENT v1.3.9

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.3.9 更新要点（币安技能自动更新）

### 1. baw CLI 自动更新
- 后端启动 **45 秒后自动检查** npm 上的 `@binance/agentic-wallet` 最新版，落后即自动升级（内置 runtime 原地更新，无需重装应用），此后**每 6 小时循环检查**
- 技能库页面新增**更新状态条**：显示当前 baw 版本 / 是否可更新 / 更新进度，支持「检查更新」与「一键更新」手动触发
- 升级自动保留 fetch 代理补丁（undici 同步随装，代理池流量不受影响）

### 2. Skills Hub 技能包自动更新
- 已安装的官方技能包（meme-rush、query-token-info、广场发帖等）启动后自动重装到 GitHub 最新版，也可在技能库页手动「一键更新」
- 更新进度实时显示（逐个技能计数），单个失败不影响其余技能

### 3. 修复：打包版技能安装/执行失败
- **npx 解析**：桌面版用户机器没有全局 node/npx，之前 `npx skills add` 直接失败 —— 现在**锚定内置 runtime** 的 npx.cmd / node.exe（dev 回退 PATH）
- **安装位置漂移**：打包态技能会被装到后端读不到的目录 —— 现在安装 cwd 锚定 `.agents` 所在位置，装完立即生效
- 数据类技能（node cli.mjs）执行同样锚定内置 node，用户机器零依赖

### 4. 其它
- 更新状态持久化 `workspace/.skill_update.json`（避免每次启动重复打 npm）
- 代理池启用时，技能更新与版本检查自动走代理

---

# BAZZ.AGENT v1.3.8

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.3.8 更新要点（代理池增强：内核内置 + Agent 钱包走代理）

### 1. mihomo 内核内置（不再单独下载）
- 安装包现已**自带 mihomo 内核**（CI 构建时打包进 `<安装根>/.system/kernel/`），开箱即用
- hysteria2/vmess/trojan/ss 等内核型节点首次使用不再需要联网下载内核
- 安装目录只读的兜底场景会自动把内置内核接化到工作区，在线下载通道保留作为最后兜底

### 2. 修复：开启代理池后 Agent 钱包二维码生成失败（「未返回 qrCodeId」）
- **原因**：baw CLI 用 Node 20 全局 fetch（内置 undici），它不读 HTTP(S)_PROXY 环境变量 ——
  代理池启用后环境变量注入对 baw 无效（仍直连），此前只有系统级全局/TUN 模式才能成功
- **修复**：代理池启用时自动给所有 Node 子进程挂载 fetch 代理补丁
  （`NODE_OPTIONS --require proxy-preload.cjs` → undici EnvHttpProxyAgent），
  baw 扫码登录 / 余额 / 转账 / Agent 钱包技能全部流量自动走代理池当前节点
- 切「直连」即恢复原样，无代理场景不受任何影响

---

# BAZZ.AGENT v1.3.7.2

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.3.7.2 更新要点（修复：桌面版升级后看不到新界面）

- **问题**：部分用户从旧版升级后，桌面版仍显示旧界面（如设置里没有代理池卡片），而网页版一切正常
- **原因**：Chromium 把旧版页面缓存在了应用数据目录，桌面版升级后加载的仍是缓存里的旧页面
- **修复**：
  - Electron 启动时自动清理 HTTP 缓存，强制加载最新界面
  - 后端对入口页面响应禁用缓存，此后每次升级立即生效，无需手动清理

---

# BAZZ.AGENT v1.3.7

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

## 🆕 v1.3.7 更新要点（代理池 · 仿 Ant Browser）

### 1. 代理池（设置页 · 可折叠卡片）
参考 Ant Browser 指纹浏览器的代理池体验，添加完整代理管理：
- **两类节点自动识别**：http/https/socks4/socks5 直连即用；hysteria2/vmess/trojan/ss/vless 等走可下载的 mihomo（Clash Meta）内核转发
- **一键下载内核**（~15MB）：GitHub 直连失败自动轮询 gh-proxy.com / ghfast.top / ghproxy.net 镜像，落 `<安装根>/.system/kernel/`
- **导入三种方式**：订阅 URL（自动识别 Clash YAML + base64）、批量粘贴（URI 列表、host:port）
- **真实测速**：直连型走真实 HTTPS 转发请求 Binance API；内核型走 mihomo provider healthcheck，失败 3 次自动标记 dead
- **切换即生效**：启用节点自动注入 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` 环境变量，全应用（含更新器）流量自动走代理；切「直连」即恢复
- **持久化**：导入列表 + 启用状态落 `workspace/proxies.json`，启动自动恢复；可删除节点、刷新订阅
- **内核转发链路实测**：selector 切换 → 混合端口 7899 → 真实 HTTP 200 ~400ms

### 2. 体验细节
- 代理池从顶层导航并入「设置」页（可折叠卡片，点 ▸ 展开即完整功能），导航栏保持干净
- workspace 文件列表加 proxies.json 用途说明（聊天页文件区）
- 修复 mihomo 订阅节点测速：新版不在 `/proxies/<name>/delay` 暴露（404），改用 provider healthcheck + 读取节点 history

---

# BAZZ.AGENT v1.3.6

**Binance Agent OS 专属 AI 交易桌面端（Agent OS Alpha Scout · Track A）**

本地优先的币安 AI 交易桌面前端：妖币/点火雷达 + 多模型 Agent 对话 + 带止损止盈的交易方案 + CEX 连接 + Agentic Wallet 链上操作 + 广场发文。行情扫描与 Agent 记忆**不需要任何 API Key**；真实下单/写盘一律先确认后执行。

## 🆕 v1.3.6 更新要点（安全加固 + 稳定性修复）

### 1. 本机接口加锁（安全加固）
此前本机 API 完全开放，浏览器里任何网页都能悄悄调用（读资产、改设置、触发钱包命令）。本版起：
- 桌面版每次启动生成**随机本机令牌**，只有应用自己的界面知道；
- 外部请求一律 401，CORS 同步收紧为本机来源；
- 开发模式行为不变，正常使用无任何感知。

### 2. 更新器双保险（安全加固）
- 更新包**只认本仓库 GitHub Releases 官方链接**，恶意网页诱导下载任意压缩包的路子被堵死；
- 安装时只接受本机更新缓存目录内的包，路径不受信直接中止。

### 3. 修复 8080 端口被占时的白屏
如果 8080 被其它程序占用，之前会拉不起后端导致主窗白屏。现在：检测到是本应用在跑就直接复用（双开），是别的程序占着就**自动换下一个端口**启动。

### 4. 数据层并发加固
数据库启用 WAL 模式 + 写操作串行化——多线程同时写（对话流、行情定时任务、钱包预热）不再可能出现锁冲突或数据交错。

### 5. 对话流不再"静默断死"
Agent 执行中途出错时，之前流会无声中断、回复丢失。现在会推送明确的错误提示到界面，且**已生成的部分回复照常保存**；中途关窗/切会话也不丢内容。

### 6. 行情接口提速
现货/合约行情接口去掉了内部的重复请求，30 秒轮询下币安请求量与响应延迟约**减半**。

### 7. 后端守护与日志
后端进程输出落盘到 `<安装根>\logs\backend-electron.log`（超 5MB 自动清理）；异常退出 2 秒后**自动重启**（连续 5 次放弃，防止死循环）。

### 8. 其它
- 交易所账户卡加载失败不再静默，会显示错误原因；
- 更新包预下载按版本管理——应用停留跨过新版本后，"立即更新"不会再装到旧包；
- 仓库补充 MIT LICENSE 文件，README 下载徽章不再锁死旧版本（始终指向最新 Release）。

## 🆕 v1.3.5 更新要点（文件管理增强 + 工作区只留用户数据）

### 1. 文件浏览器新增删除按钮
文件面板（对话页左侧「文件」标签）每一行右侧都有**删除按钮**：
- 点击后弹确认框，确认即删（文件/整个目录都支持）；
- 删除 `state.db` 会额外强提醒一次（里面存着全部会话、记忆与设置）；
- 文件查看弹窗底部也加了删除按钮，打不开的文件看完信息可以直接删；
- 后端严格限制只能删工作区内的文件，越界路径一律拒绝。

### 2. 文件用途一目了然
已知文件/目录下方会显示**用途说明小字**：`state.db`=会话/记忆/设置数据库、`attachments`=聊天附件、`generated`=Agent 生成的文件、`logs`=运行日志、`backups`=自动备份、`square_posts.json`=广场发文台账……哪些能删、哪些别动，一眼分清。

### 3. 工作区只留用户数据（系统文件外迁安装目录）
工作区根目录不再出现系统文件：
- `update-cache`（更新包缓存）→ 迁到 **`<安装根>\update-cache`**；
- `.migrated_v1~v4`（迁移标记）→ 收编到 **`<安装根>\.system\`**；
- 老版本升级后**首次启动自动搬迁**，旧文件不用手动删，工作区里只剩你的数据。

### 4. state.db 打开不再是一屏乱码
之前点开 `state.db` 会显示强行解码出的乱码。现在正确识别为二进制文件，显示「二进制文件，不可文本预览」+ 文件大小，并可直接删除。

### 5. 更新器加固
更新包缓存移到安装根后，更新脚本会在删除旧应用目录前先把 zip 挪到安全位置再解压，避免「下载好的更新包被自己删掉」的极端情况。

## 🆕 v1.3.4 更新要点（Release 介绍瘦身 + 工作区嵌套修复）

### 1. 更新介绍只显示当前版本
Release 介绍与应用内更新弹窗不再堆积全部历史版本说明——从本版起只显示**本次更新的内容**，一眼看清「这版改了什么」。

### 2. 修复「建文件夹又在工作区里套一层 workspace」
让 Agent 建文件夹/移文件时，之前偶尔会在工作区里**再套一层 `workspace/`** 文件夹（如 `workspace/workspace/妖币/…`）。根因是 Agent 的命令工具（mkdir/mv/cp/rm/ls 等）与写文件工具对 `workspace/` 前缀的解析规则不一致。现统一规则：
- `mkdir workspace/妖币` 与 `mkdir 妖币` 落点完全一致 → 都在工作区根建 `妖币/`；
- **历史嵌套自动整理**：启动时自动把旧数据里嵌套的 `workspace/` 内容上提回工作区根（有同名冲突时跳过不动，绝不覆盖）。

### 3. 沙箱越界探测收紧
`ls workspace/../..` 之类的越界探测现在会被直接拒绝；Agent 可见范围严格限定在工作区与程序目录内。

## 🆕 v1.3.3 更新要点（沙箱写文件修复 + 工作区落到安装目录）

### 1. Agent 沙箱写文件修复（核心）
此前打包版里 Agent 的沙箱把「工作区白名单」锚在了 PyInstaller 的 `_internal` 运行时目录——`pwd` 显示一串 `_internal` 深路径、白名单目录在磁盘上根本不存在、写任何绝对路径都被判「越界」，导致 Agent **完全无法落盘**。本版彻底修复：
- 沙箱工作区统一锚定 **`<应用安装目录>/workspace`**（如 `F:\1\BAZZ.AGENT\workspace`），`pwd` / `ls` / `cat` 看到的就是真实可见的目录；
- 相对路径（`workspace/...`、`.workbuddy/generated/...`）与进程 cwd 彻底解耦；Agent 回传的**绝对路径**（在白名单内）也接受；
- `.workbuddy/generated/` 现映射到工作区 `generated/` 子目录，写完的文件在安装目录下直接能找到。

### 2. 工作区落在应用安装位置
工作区从 `%APPDATA%\BAZZ.AGENT\workspace` 迁回**安装目录**：`state.db`、会话记录、钱包资料、Agent 产出文件、附件、日志全部集中在 `<安装根>\workspace`，数据跟着应用走、一眼可见。
- 安装目录只读（如受保护盘）时自动兜底回 `%APPDATA%`，永不裸崩；
- **旧数据自动回迁（v4 迁移）**：首启自动把 %APPDATA% 里的旧工作区整体搬回安装目录，并收编曾被误放到 `ScoutBackend\.scout.db` 的状态库，不丢任何数据；
- 自更新安全：工作区在应用目录内时，更新器自动走「**备份工作区 → 替换程序 → 还原工作区**」流程，升级不丢数据。

### 3. 跨盘符兼容加固
工作区与程序目录不在同一盘符（如程序在 E:、工作区在 C:）时，路径计算不再抛 `ValueError` 崩溃，读黑名单/技能例外判断全部兼容。

## 🆕 v1.3.2 更新要点（行情不止现货：加合约 + 股票化代币）

### 1. 行情三大维度一键切换（现货 / 合约 / 股票化代币）
行情页顶部新增维度切换器，随时在「现货 / 合约 / 股票化代币」之间切换，三种市场各自独立展示、统一 30s 自动刷新。

### 2. 合约行情（USDT 本位永续 · 全市场）
- 一次性拉取币安全市场 U 本位永续合约（fapi 24h ticker），按成交额降序，附带**资金费率**与全天高低价。
- 顶部四卡「合约大盘速览」：合约总数、上涨家数、下跌家数、费率拥挤（|资金费率|≥0.1%）。
- 内置 **全部 / 领涨 / 领跌 / 资金异动** 快速筛选 + 搜索框；每批 60 行「加载更多」。
- 每个合约行可一键「🔎 分析」或点击整行跳转下单（当前默认走现货交易通道）。

### 3. 股票化代币合约（TradFi · 美股 / 杠杆 ETF）
- 从合约快照中过滤出 **股票化代币 / 传统资产** 白名单（TSLA / NVDA / SOXL / INTW 等 30+ 个已上线永续），标的为美股 / 杠杆 ETF，价格跟随传统金融资产、24/7 交易、USDT 结算。
- 卡片式展示，标注**中文品种名 / 交易所原码**与**已知杠杆上限**（如 SOXL ≤25x），领涨 / 领跌快览一行拉出来看。

## 🆕 v1.3.1 更新要点

### 1. 富交易卡片（Agent 自动决策执行通道）
交易单卡片现在渲染**完整交易数据 + 单一执行通道徽标 + 单个「确认执行」按钮**：
- 卡片列出 **执行通道、标的与方向、现价、止损、止盈、数量、预计最大亏损**，及可用的资金费率、24h 涨跌——确认前一眼看清这笔单子。
- **执行通道由 Agent 自动判断**，不让你手选：已绑定币安交易所 API 密钥 → 走**交易所**（现货限价单）；否则走 **Agent 钱包**（baw 钱包兑换）。卡片右上角徽标直接标出本次要走哪条通道。

### 2. 双执行通道打通（不靠 MCP、不靠 OAuth 授权，靠密钥 + 技能）
- **交易所通道**：用你在「交易所页」界面填入的 API Key + Secret（HMAC 签名）直接下真实现货订单，**不再依赖 .env**，也不依赖 MCP/OAuth。密钥优先读界面绑定值，缺失才回退 .env。
- **Agent 钱包通道**：沿用现有 baw 钱包兑换链路（codes/send、非 MCP/OAuth）。
- Agent 在生成下单方案时就做好通道决策并写进 `signal.route`，确认执行时后端按该通道自动路由。

### 3. 行情界面大幅增强（不止妖币雷达）
新增一行三卡「行情综述」（并入现有 30s 自动刷新）：
- **市场宽度 / 突发**：全市场涨/跌/平家数、红绿占比条、上涨占比、平均 |涨跌|、±20%+ 极端动量标地数。
- **资金费率拥挤度**：全市场多头拥挤 / 空头拥挤靠前币列表（≥0.1% 标记拥挤），点击跳转交易。
- **24H 成交额热度榜**：成交额从高到低 TOP15（价格 / 涨跌 / 24h 成交额），hover 出现「🔎 分析」，点击整行跳转下单。

## 🆕 v1.2.22 更新要点（修复乱码 + 端口冲突）

1. **对话乱码修复**：命令执行（run_command）时，Windows 上程序（如 Python）的中文报错在控制台以 GBK 编码输出，直接被按 UTF-8 解码 → 变成乱码（如「ͨ │ҿ½…」）。现在智能解码：优先 UTF-8，失败自动回退 GBK，彻底解决 OS 中文报错在对话里变乱码。
2. **state.db 乱码修复**：`state.db` 是 SQLite 数据库（二进制），用文本方式打开必然是一堆乱码。现在 Agent 读取时自动识别二进制文件，明确提示「非文本、请用数据库工具查看」，不再吐乱码。这是数据库文件的正常形态，非损坏。
3. **端口冲突不再裸崩**：后端 8080 端口被其它进程占用（重复启动 / 另一个实例在跑）时，之前会报 `[Errno 10048]` 直接退出；现在自动向后探测空闲端口，并打印中文提示改用哪个端口，进程正常跑起来，不再崩。

## 🆕 v1.2.21 更新要点（交易单显示交易详情）

1. **确认下单卡片展示完整交易数据**：Agent 生成的交易单（propose_trade / 下单方案）在「确认执行」前，审批卡里现在会直接列出**标的与方向、现价、止损、止盈、数量、预计最大亏损**，以及可用的**资金费率、24h 涨跌**——不用再多看回复文本或在别处查，确认前一眼看清这笔单子到底要买/卖什么、什么价位进出、亏多少封顶，判断更踏实。

## 🆕 v1.2.20 更新要点（软件更新下载加速）

1. **并行分段下载（核心提速）**：下载更新包从「单连接一条道」改为 **4 条连接并行分段（HTTP Range）**，把整包按字节切成 4 段同时下载，最后拼接并校验完整。对 GitHub 这类单连接被限速的源提速非常明显，大包（几百 MB）下载时间大幅缩短；小包（<8MB，通常就是差分包）仍走单流，避免分段开销。
2. **断点续传**：下载中途断网/被掐断后，重试时从已下的位置继续，不再每次都从头下——网络不稳时省下大段重复下载时间。
3. **安全兜底**：服务器不支持断点分段时自动回退回原单流下载；任一分段失败也只回退到单流，绝不返回损坏包；拼接后逐对校验大小与结构才进入「替换应用」流程。

## 🆕 v1.2.19 更新要点（行情增强）

1. **智能异动信号降噪（准确性问题）**：信号扫描新增**流动性过滤**——只统计 24h 成交额 ≥ 200 万美元的交易对，把低流动性灰尘盘的「一根针假异动」从扫描池剔除，避免小盘庄股一冲就误报信号，信号质量更贴近真实可交易机会。
2. **资金费率缓存提速**：资金费率批量拉取加 30 秒缓存，行情每 30s 轮询时不再重复全量打 Binance 接口，数据更稳、接口压力更小。
3. **时效性增强**：
   - 妖币雷达自动重扫从 5 分钟缩短到 **3 分钟**，更快捕捉点火/起飞的变化；
   - 顶部「最后更新」从静态时间改为**实时「N 秒前 / N 分钟前」**，每秒刷新，一眼看清数据新鲜度。
4. **体验增强 · 一键 Agent 分析**：雷达、异动信号、全市场三张表的每个交易对都新增「🔎 分析」按钮，点击即切到对话页，让 Agent 结合行情页当前价位 / 90 日位置 / 成交量 / 资金费率等，给出**趋势方向、风险提示与入场计划**（含失效条件），并在回答里区分事实与推测。

## 🆕 v1.2.18 更新要点

1. **增量差分更新（Auto-Update 提速）**：Release 新增 `MANIFEST.json`（全量文件清单 + 逐文件 SHA256）与 `delta-<版本>.zip` 差分包。客户端检测到新版后，只下载**相对上一版发生变化的那几个文件**（通常几 MB 而非整包 ~300MB），再结合本地未变文件重建完整新包；边写边逐文件校验哈希，任一不符自动回退整包下载——更新又快又安全。
2. **后台预下载**：检测到新版本立即在后台静默拉取增量包，用户点「立即更新」时基本已就绪，秒级应用重启。打开设置页更新区即可看到下载进度或直接进入「重启并更新」。
3. **兼容兜底**：老版本跳过多个大版本 / 网络异常 / 差分包缺失时，自动走整包下载，功能永不失效。

## 🆕 v1.2.17 更新要点

1. **用户数据外置（数据安全重构）**：`workspace/`（state.db / 钱包资料 / 广场发文 / 附件 / 日志）从应用目录迁到系统用户数据目录 `%APPDATA%\BAZZ.AGENT\workspace`，与应用代码完全分离。自更新从"备份→替换→还原"变成**纯程序替换，用户数据永不触碰**。首次启动会自动把旧数据从应用目录整体搬入新位置，不会丢数据。
2. **更新包 SHA256 校验**：Release 新增 `SHA256SUMS` 资产；应用替换前先到官方校验和比对下载包哈希，不一致即刻中止（防止下载损坏或被篡改），校验和获取失败才放行并记录日志。
3. **发行物升级**：每次 Release 同时产出 `win32-x64-portable.zip` + `setup.exe` 安装包 + `SHA256SUMS`。

## ➕ v1.2.16 更新要点

1. **Agent 钱包扫码登录改为在浏览器打开配对页**：点击「扫码登录 Agent 钱包」后不再依赖应用内渲染二维码图片（此前在桌面端经常白屏/不显示二维码），而是调用 `shell.openExternal(urlForWeb)`，在系统默认浏览器里打开 Binance 配对页，用手机 Binance App 扫码确认。等待区显示「已在浏览器打开」+ 配对码，并继续轮询 `verify`，扫码后自动同步登录状态。
2. **`AgentSigninCard` 自检测内置 runtime**：组件内部自行拉取 `/api/wallet/runtime` 作为兜底，不再仅依赖父组件传参，避免后端 `/api/wallet` 只查系统 PATH 而误判「CLI 未安装」；Chat 弹层同样识别 `runtime.bundled`。
3. 后端 `wallet_client.py` 统一走 `wallet_runtime.baw_invocation()`，内置 Node + baw 优先于 PATH，状态/版本探测与命令转发保持一致。
4. 仓库清理：`.gitignore` 忽略本机构建产物（`runtime/`、`scripts_tmp/`、`frontend/scout-bundle/`），避免 96MB+ 构建目录误入库。

## 🆕 v1.2.15 更新要点（hotfix）

1. **修掉 v1.2.14 残留的 UI 矛盾**：v1.2.14 修好内置 Node + baw 后，钱包页"状态卡"已亮起 `内置 · baw v1.9.0`，但"扫码登录"区域却仍然显示 `⚠ 未检测到 Agentic Wallet (baw) CLI` + 一个点了没反应的「安装 Agentic Wallet」按钮。根因是 `AgentSigninCard` 内部对 `installed` 的判定只看后端 `/api/wallet` 返回的 `state.cli.installed`（后端走 `shutil.which("baw")` 查系统 PATH），没认 `runtime.bundled`——所以内置已就绪也会被错判成"未安装"。本版统一规则：**`runtime.bundled` 优先**，只要内置就绪就直接进扫码登录；点「安装」按钮会得到明确提示（`v1.2.11+ 已内置 Node 20 + baw CLI，无需安装。请直接点「扫码登录 Agent 钱包」`），不再静默失败。
2. **扫码登录区增加可见性提示**：内置场景下，扫码登录提示下方加一行绿字 `✓ 当前走 APP 内置 Node 20 + baw v1.9.0，无需任何安装`——和顶部状态卡形成呼应，再也不会让用户怀疑"为什么两个状态不一致"。
3. **附带修了一个隐藏的体感问题**：v1.2.11 起后端 `/api/wallet/install` 已返回 `deprecated:true`，但前端按钮 click 后这个 `detail` 字段会被静默吞掉，看起来像"按钮没反应"。本版让 `install()` 在已内置场景下直接给友好提示，不再走那个无效 API。

## 🔄 v1.2.14 更新要点（hotfix · 历史）

1. **修一个从 v1.2.2 就潜伏的「Agent 钱包显示未安装」真因**：之前你看到 `CLI 未安装 / npm 缺失 + 一键安装` 按钮——其实是 PyInstaller 启动后 `BAZZ_APP_DIR` 被错误指向 `_internal/`，导致内置 Node + baw 的查找路径错位、`isfile(node.exe)` 永远 False、UI 回退到 v1.2.10 老分支。本版双保险：
   - **launcher.py 改用 `sys.executable` 推导 exe 目录**（PyInstaller onedir 正确做法），从源头修对。
   - **workspace.RUNTIME_DIR 加 app_root 兜底**——即使 launcher 哪天又被改错，向上找含 `BAZZ.AGENT.exe` 或 `resources/` 的目录再下 `resources/runtime`，保证永远找得到内置 runtime。
2. **不丢你的数据**：老 launcher 把数据（state.db / 广场台账 / 附件）全写到了 `_internal/workspace/`，不在约定的 `ScoutBackend/workspace/`。首次启动会一次性把 `_internal/workspace/` 下的所有文件搬到新位置（写 `.migrated_v2` 标记防重跑），同时清空老目录。**升级完你的会话/记忆/广场台账一件不少。**
3. **诊断信息更透明**：去掉 WalletView 之前对 `/api/wallet/runtime` 请求的静默 catch（失败就吞错），现在失败原因直接显示在 UI 上。Agent 钱包面板在 `runtime` 未走内置时新增一段「runtime 诊断（v1.2.14+）」折叠卡，明示 `APP_DIR / RUNTIME_DIR / NODE_EXE / BAW pkg / mode` 的真实值——下次出问题自己就能看到哪条路径不对。
4. **顺手修了「更新 HTTP 403」的体感**：如果 `api.github.com` 触发速率限制（无鉴权 60 次/小时），更新面板已配好「打开下载页」浏览器兜底按钮（v1.2.13 就有），等一小时自动恢复；现在诊断卡也明示错误原因，不让人猜。

## 🔄 v1.2.13 更新要点（历史）

1. **更新弹窗不再反复打扰**：之前右下角更新卡会 5 分钟自动巡检 + 窗口切回焦点立即复查——"一直弹"。本版改为**每次启动只自动检查一次**：有新版本浮一张卡，点「稍后」后本会话不再出现；想再查就去设置页「软件更新」点「检查更新」（结果在面板内展示，不再弹浮窗）。已彻底移除定时轮询与焦点监听。
2. **恢复「应用内自动更新」——不用再去网页下载覆盖**：上一版（v1.2.11）因旧自动更新链路在部分网络环境下失败，被砍成"浏览器下载页"。本版重做整条链并修好两个致命 bug：
   - **修复替换路径算错一层**：旧脚本把 `app_root` 当父目录，删除目标 `app_root/BAZZ.AGENT-win32-x64` 实际不存在 → 替换逻辑空转。现在替换目标就是应用目录自身，新包解压到其父目录（替换后目录名恒为 `BAZZ.AGENT-win32-x64`）。
   - **更新不再误删你的数据**：你的会话/记忆/广场台账存在应用目录内的 `workspace/`。旧方案整目录删除会连数据一起清掉——新脚本先把 `workspace/` 原子挪到同级备份 → 替换 → 挪回新应用，任何一步失败都会把数据还原回去并中止。
   - 交互：设置页或浮窗内「下载更新」（进度条）→「重启并更新」→ **两步确认**（防手滑）→ 应用自动退出、整目录替换、自动重启，全程无需手动解压。
   - 下载带 3 次自动重试 + 落盘后校验 zip 顶层结构，包损坏会提示重新下载，不会解压到一半卡死。
3. **每一步失败都有浏览器兜底**：无论检查、下载还是应用环节出错，界面都会给出原因 + 「打开下载页」按钮——自动更新走不通时一键切到 GitHub release 页手动下载，不把人卡在半自动状态。

## 🔄 v1.2.12 更新要点（历史）

1. **修 v1.2.11 路径错位：内置 runtime/ 找不到的 bug**。v1.2.11 把 runtime 放到 `BAZZ.AGENT-win32-x64/resources/runtime/`（与 scout-bundle/ 同级），但 `workspace.RUNTIME_DIR` 算成了 `BAZZ_APP_DIR/resources/runtime`（= `resources/scout-bundle/ScoutBackend/resources/runtime`，不存在）→ `runtime_available()` 永远 False → Agent 钱包状态位仍显示红色「CLI 未安装 / npm 缺失」+「一键安装」按钮。**正确路径**是从 `BAZZ_APP_DIR` 跳两级到 `BAZZ.AGENT-win32-x64/` 再下 `runtime/`：`os.path.normpath(os.path.join(APP_DIR, "..", "..", "runtime"))`。下载 v1.2.12 后状态位才会真的显示「内置 · baw v1.9.0 / Node 20 LTS」绿色 pill。

## 🔄 v1.2.11 更新要点（历史）

1. **「自动下载 / 整目录替换 / 强制重启」整套作废**——之前的方案在多次中国代理环境下失败（GitHub API 403 / 下载中断 / PS 脚本杀进程 / 解压权限），用户也没看到任何更新提醒。本版彻底**砍掉所有下载/解压/重启逻辑**：`src/updater.py` 只剩 `check()`（取版本号 + release URL），`UpdatePanel` / `UpdateNotifier` 重写为极简版——检测到新版本时**只用系统默认浏览器打开 GitHub release 页**，用户在浏览器里下载、解压、覆盖即可。任何状态下都展示「打开下载页」按钮，没有静默失败。
2. **Agent 钱包「Node + npm + baw」全部内置**：之前状态位总显示红色「CLI 未安装 / npm 缺失」+ 「一键安装 `npm i -g`」按钮——中国用户常常没有 Node 18+ 环境或 npm 不在 PATH。本版构建期自动下 Node 20 LTS Windows x64 + `npm i @binance/agentic-wallet` 到 `runtime/`，整目录打进 APP（约 +100MB，zip 147 → 250MB）。**用户机器不再需要任何 Node 环境**，状态位直接显示「**内置 · baw vX.Y · 内置 · Node 20 LTS**」，「一键安装」按钮整段消失。

## 🔄 v1.2.10 更新要点（历史）

1. **「软件更新」区块置顶设置页首屏**：之前更新管理区排在设置页底部（深度思考与通道面板之间），长得和旁边几块管理面板太像、不容易被找到——多位反馈"桌面端里根本没有自动更新"。本版把**自动更新区块移到设置页最顶部**：打开「设置」第一眼就是「软件更新」卡片——已安装/最新版本一目了然、有新版本直接下载+重启更新，再也不会找不到。
2. **右下角悬浮提醒保持 5 分钟自动巡检**：发布新版本后，正在运行的旧版客户端会自动浮出更新卡（窗口切回焦点时立即复查），点击即达设置页更新区。

## 🔄 v1.2.9 更新要点（历史）

1. **启动动画完整播放保底（v1.2.8 修复回归）**：v1.2.8 在后端拉起较快的机器上会出现"动画没播完就切到主界面"——主窗首帧就绪立刻触发淡出交换。本版加入 **SPLASH_MIN_MS = 5.2 秒保底展示**，主窗再早就等够再切，金色液态铺屏 → 黑字品牌 → 紫色收尾全过程看得完整。

## 🔄 v1.2.8 更新要点（历史）

1. **修复启动动画未随包分发（v1.2.7 回归）**：打包脚本只把 `main.cjs`/`preload.cjs` 装进应用壳，漏了 `splash.html`。本版改为**整个 electron 目录整体打进包**——全新解压的便携版双击即可看到液态流体开场动画。
2. **更新提醒更可靠**：
   - 自动检查失败不再完全静默——右下角浮一条小红条提示「检查更新失败」并可重试（8 秒自动收起）；
   - 轮询间隔从 10 分钟缩短到 5 分钟；
   - **窗口重新获得焦点时立即复查**——从别的窗口切回应用即可发现刚发布的新版本。

## 🔄 v1.2.7 更新要点（历史）

1. **全新启动动画（液态流体转场）**：双击 exe 后先呈现约 5 秒品牌开场——暗底点阵打出 ALPHA SCOUT → 币安金液态从右下爆开铺满全屏（溅射金粒 + 冲击波圆环）→ 金屏刷出 BAZZ.AGENT → 深色液态从顶部盖回，定格白字品牌 + 金色发丝线 + 副标，与主界面同色系无缝衔接。
2. **开场视觉对齐应用深色金融风**：近黑 `#0a0b0d` 底 + 币安金点缀 + 细点阵网格，全片零外部资源、纯 CSS/SVG 实现。

## 🔄 v1.2.6 更新要点（历史）

1. **设置页新增「软件更新」管理区**：设置 → 深度思考下方即见完整更新入口——当前/最新版本一目了然，「检查更新」一键手动检测；检测到新版本可直接在设置页下载（带进度）、查看更新说明并「重启并更新」。
2. **右下角悬浮提醒保留**：有新版本时依然会在桌面右下角浮出提醒卡。

## 🔄 v1.2.5 更新要点（历史）

1. **自动更新（Auto-Update）**：GitHub 发布新版本后，旧版客户端自动收到提醒并可一键「重启并更新」——应用自动退出 → 原位整目录替换为新版 → 自动重启。
2. **交易所历史成交不再要求搜代币**：进入 Binance CEX 页，历史成交自动聚合展示账户所有非零币种的最近成交。
3. **内置更新通道直连 GitHub API**：更新检查绕过 Windows 系统代理干扰（避免代理把 API 打成 403）。

## 🚀 快速开始（Windows 便携版）

1. 下载并解压 `BAZZ.AGENT-v1.2.16-win32-x64-portable.zip`
2. 运行 `BAZZ.AGENT.exe` —— 先播放完整 5+ 秒启动动画，随后自动生成 `workspace/` 工作区，运行数据全部收口其中
3. 右上角配置 LLM（OpenAI 兼容端点，支持多模型 + 备份链），即可开始对话
4. 行情 / 妖币雷达 / 市场扫描开箱即用，无需任何 Key
5. 真实交易：设置 → 币安 CEX 填 API Key（应用内不落盘，可 sync 到 binance-cli profile）；或 Bots 面板扫码登录 Agentic Wallet（MPC 无密钥）——均需你在界面点确认才执行

## 🔒 安全设计

- 高危指令（全仓 / 清空账户等）硬拦截；下单 / 写盘默认二次确认，可信操作可勾选白名单
- 更新包仅从本仓库 GitHub Releases 下载，落盘后原位校验替换，不联网执行任何第三方脚本
- 所有运行数据仅存本机 `workspace/`，私钥 / 助记词永不上传

## 📖 更多文档

- 中文说明：`README.zh-CN.md` ｜ English：`README.md`
- 产品页：`docs/index.html`（可本地打开浏览）
