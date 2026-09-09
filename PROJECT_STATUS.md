# BAZZ.AGENT 项目状态交接（2026-09-09 会话收尾）

> 用途：开新任务/新会话前快速恢复上下文。读完即可继续开发，无需翻旧对话。
> 项目路径：`e:\hermes_app\binance-agent-os-scout` ｜ 仓库：`github.com/xinyuzjj/bazz.agent`

---

## 一、项目概览

- **形态**：Windows 桌面端（Electron 壳）+ FastAPI Python 后端 + TS/Vite 前端
- **定位**：币安 AI 交易终端 —— Agent 对话、行情（现货/合约/股票化代币）、交易方案卡、CEX 连接、Agentic Wallet、广场发文、Skills Hub
- **当前版本**：v1.3.9（已发布 Latest，4 资产齐全）
- **工作区**：安装目录 `<安装根>/workspace`（state.db / proxies.json / .skill_update.json / 附件 / 日志）

## 二、今天发布的三个版本

| 版本 | 核心内容 |
|---|---|
| **v1.3.7.2** | 修桌面版升级看不到新界面：Chromium 缓存旧 index.html → Electron 启动 `session.clearCache()` + 后端入口页 `Cache-Control: no-store` |
| **v1.3.8** | ① mihomo 内核内置进安装包（CI 下载 → `<安装根>/.system/kernel/`，免单独下载，只读安装目录自动接化）；② 修代理池开启后 Agent 钱包「未返回 qrCodeId」 |
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

**P1（下一版建议）**
1. 行情 WebSocket 实时流（替换 30s 轮询，币安免费 WS）
2. 行情界面渲染优化（时间戳每秒重渲全表 → 拆组件 + memo）
3. 订单状态跟踪卡片（下单后待成交/已成交/撤销 + 提醒）
4. 止损止盈接近触发提醒（应用内 + 系统通知）

**P2**
5. 启动加速（splash 保底 5.2s 可压到就绪即切换）
6. 前端空 catch 补错误提示（SettingsView L52-69 等）
7. 广场发文草稿箱/定时发布
8. Agent 记忆导出 Markdown 报告

**P3**
9. 多会话并行 Agent 任务
10. 行情自选列表置顶

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
