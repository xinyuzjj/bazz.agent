// ============================================================
// proxy-preload.cjs — BAZZ.AGENT Node 子进程 fetch 代理补丁（v1.3.8）
//   背景：baw CLI 用 Node 20 全局 fetch（内置 undici），它不读
//   HTTP_PROXY/HTTPS_PROXY 环境变量 —— 代理池启用后 env 注入了对
//   baw 无效（仍直连），只有系统级全局模式（TUN）才拦得到。
//   本文件由 proxy_pool 在代理启用时通过 NODE_OPTIONS=--require 挂载：
//   存在代理 env 时把全局 dispatcher 换成 undici EnvHttpProxyAgent，
//   所有 fetch 流量自动走代理池。任何异常都静默，绝不影响 baw 本身。
//   依赖：runtime/node_modules/undici（prepare-runtime.js 安装）。
// ============================================================
try {
  const hasProxy = process.env.HTTPS_PROXY || process.env.https_proxy ||
    process.env.HTTP_PROXY || process.env.http_proxy ||
    process.env.ALL_PROXY || process.env.all_proxy;
  if (hasProxy) {
    const undici = require("undici");
    if (undici && typeof undici.setGlobalDispatcher === "function" && undici.EnvHttpProxyAgent) {
      // v1.5.30：本机地址必须绕过代理。技能（coin-report / market-data 等）靠 fetch
      // http://127.0.0.1:8080/8081 取本机后端数据，一旦被送进代理就一律报「本地后端不可达」，
      // 换多快的节点都救不回来（死代理探针实测确认：无 NO_PROXY → 502，有 → 200）。
      // NO_PROXY 在用户机器上可能缺失、也可能是空串，所以在这里强制做并集，
      // 不依赖上游 Python 侧是否设置正确。
      const LOCAL = ["127.0.0.1", "localhost", "::1"];
      const cur = process.env.NO_PROXY || process.env.no_proxy || "";
      const parts = cur.split(",").map((s) => s.trim()).filter(Boolean);
      const low = new Set(parts.map((s) => s.toLowerCase()));
      for (const h of LOCAL) { if (!low.has(h)) parts.push(h); }
      // v1.5.21：undici 默认 connect 超时 10s，慢节点/S3 域名握手直接 UND_ERR_CONNECT_TIMEOUT
      // （RUNE 发文实测踩坑）；放宽连接/头/体超时，让慢代理也能跑完上传
      undici.setGlobalDispatcher(new undici.EnvHttpProxyAgent({
        noProxy: parts.join(","),
        connect: { timeout: 30_000 },
        headersTimeout: 60_000,
        bodyTimeout: 120_000,
      }));
    }
  }
} catch {}
