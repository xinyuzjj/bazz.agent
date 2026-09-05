/* scout-w3-bridge — Binance Web3 Wallet API 官方连接器桥
 *
 * 复用官方 @binance-web3/wallet（内部完成 X-OC-* 签名、/build 前缀、
 * query 序列化），后端 Python 用 node 调本脚本，避免自行重造签名细节。
 *
 * 用法： node bridge.mjs '<json>'
 *   {"op":"balance","apiKey":"BX-…","apiSecret":"…",
 *    "address":"0x…","chains":["56"],"page":1,"pageSize":20}
 *   {"op":"verify", ...同字段...}
 * 输出：一行 JSON {"ok":true,"data":…} 或 {"ok":false,"code":"…","message":"…"}
 */
import { Web3Wallet } from "@binance-web3/wallet";

const arg = process.argv[2] || "{}";
let req;
try {
  req = JSON.parse(arg);
} catch {
  console.log(JSON.stringify({ ok: false, code: "bad_json", message: "参数不是合法 JSON" }));
  process.exit(1);
}

const op = req.op || "";
const apiKey = (req.apiKey || "").trim();
const apiSecret = (req.apiSecret || "").trim();
if (!op || !apiKey || !apiSecret) {
  console.log(JSON.stringify({ ok: false, code: "missing", message: "op/apiKey/apiSecret 均必填" }));
  process.exit(1);
}

function ok(data, raw) {
  console.log(JSON.stringify({ ok: true, data, raw: raw === undefined ? undefined : String(raw).slice(0, 60000) }));
}
function fail(code, message, extra) {
  console.log(JSON.stringify({ ok: false, code: code || "error", message: message || "未知错误", ...(extra || {}) }));
}

const client = new Web3Wallet({
  configurationRestAPI: { apiKey, apiSecret, timeout: 25000 },
});

async function callBalance() {
  const address = (req.address || "").trim();
  if (!address) { fail("no_address", "需要 address 才能查余额"); return; }
  const chains = Array.isArray(req.chains) ? req.chains : [String(req.chains || "56")];
  const page = req.page || 1;
  const pageSize = req.pageSize || 50;
  // 签名顺序与官方客户端一致；recvWindow/nonce 传 undefined 即不附加
  const resp = await client.restAPI.getAllTokenBalancesByAddress(
    undefined, undefined, address, chains, false, page, pageSize,
  );
  // 官方封装：resp.data 可能为函数（解包 OC envelope）或对象
  if (resp && typeof resp.data === "function") {
    try { return await resp.data(); } catch { /* fallthrough */ }
  }
  return resp && typeof resp === "object" ? resp : { raw: resp };
}

(async () => {
  try {
    const data = await callBalance();
    ok(data);
  } catch (e) {
    // axios / 网关错误：尽力提取 OC 错误码与消息
    const ed = e && e.response && e.response.data;
    const code = ed && (ed.code ?? ed.code_);       // OC: code 字段（0 成功 / 非 0 错误）
    const msg = ed && (ed.msg || ed.message || ed.messageDetail);
    if (code !== undefined && code !== null) {
      fail(String(code), msg || `网关错误 code=${code}`, { detail: msg || undefined, gateway: true });
      return;
    }
    fail((e && e.code) || "network", (e && e.message) || String(e), { detail: String(e) });
  }
})();
