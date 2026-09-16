# Contributing to BAZZ.AGENT

感谢你有兴趣让 BAZZ.AGENT 变得更好。本项目是一份面向币安交易场景的 Electron + FastAPI + React
桌面应用，结构上有几个独有的约束 —— 提交前请先读一遍。

## 1. 工程约束（先读，再动手）

- **真实工程目录 = `E:\hermes_app\binance-agent-os-scout`（main 主工作树）**。链接工作树
  `C:\Users\Administrator\WorkBuddy\Worktrees\binance-agent-os-scout\main-a919d7e7` 是
  `workbuddy/main-a919d7e7` 分支的 WorkBuddy 工作副本 —— **只作只读视图，不要在那里跑
  `git stash / reset / merge / update-ref`**，否则 worktree 元数据会丢（已经在两次发版里踩过）。
- **仓库换行符并不统一**：实测 LF-only / CRLF-all / MIXED 三种并存。
  编辑时**保持原文件风格**，提交前用 `git diff --numstat` 自检 —— 若增删数 ≈ 文件总行数，
  说明换行符被整体改写，必须回退重做。
- **不要把改动直接落到用户已安装的目录**（如 `F:\1\BAZZ.AGENT`）。Python 代码编译进
  `ScoutBackend.exe`，源码改动对已安装版本零作用；只有 `.agents/skills/**/cli.mjs` 是明文。
  任何用户能感知的修复都必须重新发版。

## 2. 开发环境

| 项 | 版本/位置 |
|---|---|
| Python | 3.11.15（`E:\hermes_app\binance-agent-os-scout\.venv`） |
| Node | 20.20+（Vite 5 / Electron 28 要求） |
| Vite 代理端口 | 5173 → `127.0.0.1:8080`（`BAZZ_DEV_PORT` 可改） |
| 本机后端 | `python desktop_app.py`（鉴权用 `BAZZ_AUTH_TOKEN`，工作区用 `BAZZ_WORKSPACE`） |

起本地后端做端到端时，**先把工作区隔离到临时目录**：

```bash
export BAZZ_WORKSPACE=$(mktemp -d) BAZZ_AUTH_TOKEN=demo-token BAZZ_PORT=18099
python desktop_app.py
```

## 3. 提交前自检

```bash
# 1) 离线回归测试（AST/桩隔离；不联网、不下单）
cd E:/hermes_app/binance-agent-os-scout
for t in test_v150_market test_v151_radar_track test_v1528_fixes \
         test_v1529_hardening test_v1530_local_backend test_v1531_prompt_fixes \
         test_v1532_proxy_kernel_state test_v1534_image_preview \
         test_v1535_square_delete; do
  printf "%-32s " "$t"; .venv/Scripts/python.exe tests/$t.py 2>&1 | tail -1
done

# 2) 静态检查
.venv/Scripts/python.exe -m py_compile desktop_app.py src/xxx.py
node --check electron/main.cjs electron/preload.cjs
cd frontend && npx tsc --noEmit && npm run build && cd ..
```

新增测试套件后**必须**回来更新 `tests/README.md` 与项目记忆里的测试清单 —— 漏跑等于没护栏。

## 4. 改动类型与测试要求

| 改动类型 | 必须做的验证 |
|---|---|
| 后端端点 | 新增/修改至少一条 `tests/test_*.py`；端到端优先用 `TestClient` |
| 前端组件 | `tsc --noEmit` + `npm run build` |
| CSS/视觉 | 截图对比浅色 + 深色 + 一档窄窗口 |
| 文档 | 直接提 PR，无须运行测试 |
| 技能描述 | 必须同时核对 CLI 的 `CMDS`/`  命令集`；脱字符当命令会被模型当真 |

**警惕「能力存在但没接线」**：后端端点 + `api.ts` 封装都有，但 UI 类型声明了字段却从不赋值，
渲染分支永远走不到 —— 静态检查全绿而功能不存在。排查口诀：

```bash
grep -rn "<能力名>" frontend/src --include='*.ts*' | grep -v 'api.ts'
```

如果只剩定义处、没有调用处，就是没接线。

## 5. 提 PR

- 分支命名：`feat/<scope>`、`fix/<scope>`、`chore/<scope>`（不要在 main 上直接提交）
- commit 信息：中文一句话标题 + 必要时正文列根因与修复点
- 一个 PR 只做一件事；包含无关改动会让 review 变难、合并冲突变多
- 如果改了 `package.json` 的 `version` 或 `RELEASE_NOTES.md`，**确认你不是顺手发版**。
  发版流程见 `PROJECT_STATUS.md §六、验证命令速查` 之后的发版章节；只有用户明确要求时才走完整发版。

## 6. 代码风格

- 缩进：Python 4 空格 / TS 2 空格
- 字符串：仓库内只用 ASCII straight quotes（`'…'`、`"…"`）；文档/正文允许 locale-appropriate 弯引号
- 注释：中文优先，技术名词保留英文
- 不要引入新依赖而不在 PR 说明里写明原因（特别是体积大的 npm 包）

## 7. 行为不可回退的清单

下面是已经多次踩过坑、属于"语义护栏"的设计决策。改之前请理解它解决过什么问题：

- 更新退出走专用 `bazz:quit-for-update`，**不进**"用户点 X"的询问链路（否则 PS 脚本等 180 秒超时失败）
- 广场失败卡片单条删除 + 批量清空走二次确认；**只删本地台账**，不调币安 API
- 图片预览走 `workspaceRawBlob` → `objectURL`；不要走含 NUL 字节会被拒绝的文本通道
- mihomo 跨进程状态从 `state.json`/`config.yaml` 恢复并探活；启动中端口不能被恢复逻辑清零
- 本机后端 401 = 后端活着 = 鉴权问题，不是网络问题
- 子进程 `text=True` 必须配 `encoding='utf-8', errors='replace'`，否则 zh-CN Windows 上 GBK 严格解码会静默崩
- 技能 description 里宣传的名词必须能映射到 CLI 的真实子命令