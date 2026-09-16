# Pull Request

感谢你的贡献。请填写以下内容，便于 review 与发版。

## 改动摘要

<!-- 一两句话说明本次改动的目的 -->

## 改动内容

- [ ] 新增 / 修改文件列表（按模块）
- [ ] 新增 / 修改测试套件
- [ ] 更新文档 / 项目记忆

## 关联 Issue

<!-- `Closes #123` 或 `Refs #123` -->

## 自检清单

- [ ] `git diff --numstat` 无整文件换行改写（无 ±文件总行数 量级）
- [ ] `npx tsc --noEmit` 通过（涉及前端）
- [ ] `node --check electron/*.cjs` 通过（涉及 Electron）
- [ ] `.venv/Scripts/python.exe -m py_compile desktop_app.py src/<改动文件>` 通过（涉及后端）
- [ ] 离线回归套件全部通过（9 个套件，见 `tests/`）
- [ ] 新护栏已验"能抓住旧行为"：临时回退 → 测试 FAIL → 恢复 → 测试 PASS
- [ ] **未**修改 package.json 的 `version`，**未**追加 RELEASE_NOTES.md
- [ ] **未**改动用户已安装的安装包目录（如 `F:\1\BAZZ.AGENT`）
- [ ] **未**触碰 `.workbuddy-ai/` 或 `workspace/` 下的真实数据

## 截图 / 录屏

<!-- 若涉及 UI / 视觉改动，请附浅色 + 深色 + 窄窗口截图 -->

## 风险与回退

<!-- 是否影响真实下单、钱包签名、密钥、跨进程状态、版本兼容？回退方案是什么？ -->

## 其他

<!-- 任何 review 时希望特别注意的事项 -->