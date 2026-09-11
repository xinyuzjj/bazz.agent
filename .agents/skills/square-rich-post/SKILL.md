---
name: square-rich-post
description: |
  广场富媒体发文：一句话生成并发布「全维度行情拆解」文章到币安广场。
  自动取 90d K线/费率/OI/多空比/恐惧贪婪 → Pillow 画深色封面图（K线+成交量）与 24h 分时图
  → 组稿（$cashtag + #hashtag，事实与推测分栏）→ 调 square-post 发布。
  触发词：富媒体发文、图文帖、行情快报、深度行情文、square rich、发文带图。
metadata:
  author: bazz-agent
  version: "1.0"
---

# Square Rich Post（广场富媒体发文模板）

## 命令

| 命令 | 说明 |
|---|---|
| `rich <SYMBOL> [market=futures]` | 只合成不出：出封面图 + 24h 图 + 深度稿，返回文件路径与摘要 |
| `rich <SYMBOL> [market] --publish` | 合成后直接发布（文章 + 封面图，带 $cashtag/#hashtag） |

示例：`run_skill square-rich-post "RAYUSDT --publish"` / `run_skill square-rich-post "BTCUSDT spot"`（纯预览）

## 产物（workspace/square_rich/<SYM>_<ts>/）

- `cover.png` — 1280x720 封面：90 日 K线 + 成交量 + 90d 高低标注 + 底部信息条（费率/OI/多空比/情绪）
- `chart_24h.png` — 24h 分时图（备用，可手动发图文帖）
- `title.txt` / `article.txt` — 组稿（正文含 `$BASE` cashtag 与 `#话题` 标签，币安服务端解析）
- `meta.json` — 统计与产物清单

## 发布形态

- 默认：**长文章 + 封面图**（contentType=2，走 square-post `image --title --cover --text-file`）
- 台账：经 agent/skills_client 发布会自动记入 Square 台账页（skill=square-rich-post）

## Agent 约定

1. `--publish` 前无需再确认密钥（square-post 自动读取）。
2. 文中「判断 · 推测」为规则化生成，若你（agent）有更好判断可改稿后再走
   `node cli.mjs <SYMBOL> --publish --reuse <目录>` 用旧目录重发，或直接用 square-post
   `text --text-file <改稿> --title <标题>` 发布。
3. 数据缺失项自动省略，绝不编造；推测必须与事实分栏。
4. 每日 100 帖上限（square-post 官方配额）。
