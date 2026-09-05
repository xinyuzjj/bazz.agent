#!/usr/bin/env node
// binance-web3 数据技能通用启动器 —— 绕开官方 cli.mjs 的 Windows dispatch 缺陷
// （官方用 `import.meta.url === 'file://' + process.argv[1]` 判断直跑，Windows 反斜杠路径永远不匹配 → 空输出 exit 0）
//
// 用法: node skill_launcher.mjs <skill目录> <子命令> '<JSON参数>'
// 原理: import 技能 cli.mjs → 取其 COMMANDS 构建器 + call() → 打印 JSON
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import path from 'node:path';

const [, , skillDir, cmd, paramsStr] = process.argv;
if (!skillDir || !cmd) {
  console.error('用法: node skill_launcher.mjs <skill目录> <子命令> \'<JSON参数>\'');
  process.exit(2);
}

const cliPath = path.join(skillDir, 'scripts', 'cli.mjs');
const mod = await import(pathToFileURL(cliPath).href);

const builder = (mod.COMMANDS || mod.default?.COMMANDS)?.[cmd];
if (!builder) {
  console.error(`未知子命令: ${cmd}；可用: ${Object.keys(mod.COMMANDS || {}).join(', ')}`);
  process.exit(1);
}

let params = {};
if (paramsStr && paramsStr.trim()) {
  try { params = JSON.parse(paramsStr); }
  catch { console.error('JSON 参数解析失败'); process.exit(1); }
}

try {
  const spec = typeof builder === 'function' ? builder(params) : builder;
  const call = mod.call || mod.default?.call;
  if (!call) { console.error('cli.mjs 未导出 call()'); process.exit(1); }
  const data = await call(spec);
  // 兼容两种返回：直接 data，或 {code,data,...} 包装
  const out = (data && typeof data === 'object' && 'code' in data && 'data' in data) ? data : { data };
  console.log(JSON.stringify(out, null, 2));
} catch (e) {
  console.error(e?.message || String(e));
  if (e?.body) console.log(JSON.stringify(e.body, null, 2));
  process.exit(e?.exitCode || 1);
}
