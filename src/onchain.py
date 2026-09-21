"""链上筹码分布（v1.7.2）—— 零 Key、免费源的「谁拿着这些币」。

为什么另起模块，而不是塞进 `scanner.py`（已 3185 行）：
  · 数据源完全不同（CoinGecko / GoPlus / RugCheck，都不是币安）；
  · 路由策略不同（CoinGecko 必须走代理，GoPlus / RugCheck 直连可达）；
  · 依赖是**三层链式**（symbol → cg_id → 合约地址 → 持有人），每层 TTL 不同；
  · 失败语义是**旁路**：任何失败都不得影响雷达主流程（与 `ts_series` 同级纪律）。

────────────────────────────────────────────────────────────────────────────
为什么原报告说「链上筹码需要第三方 API key 与配额，本项目约束下无法落地」是错的
────────────────────────────────────────────────────────────────────────────
2026-09-17 实测（本机，零 Key）：

  · GoPlus `api.gopluslabs.io/api/v1/token_security/{chainId}?contract_addresses=`
      → HTTP 200，**直连可达**（0.34~0.79s），无需 Key、无需代理。
      实测链 ID：1 / 56 / 137 / 8453 / 42161 / 10 全部命中。
  · GoPlus `api.gopluslabs.io/api/v1/solana/token_security?contract_addresses=`
      → HTTP 200，直连可达（0.34s）。
  · RugCheck `api.rugcheck.xyz/v1/tokens/{mint}/report`
      → HTTP 200，直连可达（1.13s），且比 GoPlus 更富：20 名持仓 + `risks[]` + `score`。

三者都不需要 Key。原判断错在把「有免费源」与「有付费 API」混为一谈。

────────────────────────────────────────────────────────────────────────────
五个必须写进代码的坑（都是实测踩出来的，不是推测）
────────────────────────────────────────────────────────────────────────────
【坑 1 · 单位相反】两个源的占比字段单位**相反**，合并时必然静默差 100 倍：
    · GoPlus（EVM 与 Solana 一致）`holders[].percent` 是**小数比例** → 必须 ×100。
      实测 CAKE top1 `percent=0.9367`，而 `balance/total_supply = 93.67%`；
      实测 TRUMP `percent=0.7269`，RugCheck 同币给 `pct=72.6864` —— 两源互相印证。
    · RugCheck `topHolders[].pct` **已经是百分数** → 不得再 ×100。
      实测 BONK `pct=8.8307`，`uiAmount/supply = 8.83%`（同样互相印证）。
    结论：**单位在各自解析分支里就地归一**，绝不把两个源的原始值混进同一个变量。

【坑 2 · 第一名可能是销毁地址 / LP 池，而不是庄家】
    实测 CAKE(BSC) top1 = `0x000000000000000000000000000000000000dead`，占 93.67%
    —— 那是**销毁地址**（GoPlus 同时给 `is_locked=1`）。若把它当「单一持有人控盘」，
    结论会完全反过来：销毁 = 供给减少（利好），控盘 = 拉高出货（利空）。
    实测 TRUMP(Solana) top1 = 72.69%，RugCheck `knownAccounts` 里**不是** AMM 池，
    `risks` 同时给出 `Single holder ownership` —— 这个才是真的高集中度。
    所以：**先剔除** 销毁地址 / `is_locked` / `knownAccounts.type == "AMM"`，再算占比；
    同时把剔除前的原始值一并保留（`top1_raw_pct`），让「剔了什么」可审计。

【坑 3 · CoinGecko 免费额度极紧，且 429 的响应体不是数组】
    实测连续打 12 次 `/coins/{id}`：第 1 次起就是 HTTP 429（187B），
    body 是 `{"status": {"error_code": 429, ...}}` —— **dict 不是 list**。
    按 `for x in r.json()` 消费会直接抛 TypeError，或更糟：把 dict 当空结果。
    更隐蔽的是：`/coins/markets` 正常返回是 **list**，被限流时变成 **dict**，
    于是「限流」会被读成「这个 symbol 没有 id」→ 静默丢掉整批币。
    所以本模块对 CoinGecko 采取：**请求量压到每 30 分钟 2 次**
    （`/coins/markets` 批量消歧 + `/coins/list` 全量平台表，均长 TTL + 落盘），
    并显式识别 429 → 静默 15 分钟（`_cg_backoff_until`），**不重试、不计入失败统计**。

【坑 4 · 币安符号带乘数前缀，剥不掉就整类 memecoin 全丢】
    实测（2026-09）722 个 USDT 永续里有 **13 个**带乘数前缀：
        `1000SHIBUSDT` `1000XECUSDT` `1000LUNCUSDT` `1000PEPEUSDT` `1000FLOKIUSDT`
        `1000BONKUSDT` `1000SATSUSDT` `1000RATSUSDT` `1MBABYDOGEUSDT` `1000CATUSDT`
        `1000000MOGUSDT` `1000CHEEMSUSDT` `1000000BOBUSDT`
    而**现货**对同一批币是 `BONKUSDT` / `PEPEUSDT`（无前缀）。雷达池是**现货 ∪ 合约**，
    于是同一个币会以两个 symbol 各出一行（`BONKUSDT` 与 `1000BONKUSDT`）。
    若不剥前缀：`1000BONK` 查不到 CoinGecko 的 `bonk` → BONK/PEPE/SHIB/FLOKI/MOG/
    CHEEMS/BOB/SATS/RATS/LUNC/XEC/CAT/BABYDOGE **13 个币静默变成「未测到」**，
    而它们恰好是雷达最该看的 memecoin 类。
    所以：① 剥前缀 + 剥计价后缀得到 base；② **以 base 为缓存键**，让现货行与合约行
    共用同一次读数；③ 实测 493 个现货符号剥完**零撞车**（`1INCHUSDT` 不会被误剥）。

【坑 5 · 消歧必须按市值排名，不能按字母序】
    实测 `/coins/list` 按字母序取首个 id 的错法：
        BTC → `batcat` ／ ZEC → `binance-peg-zcash-token` ／ ETH → `anubis-bridged-eth-anubis`
    正解是 `/coins/markets?symbols=…&order=market_cap_desc`：**一次请求 0.7s** 拿到
    多个 symbol 的正确 id（实测 15/15 全对）。所以本模块**只用** markets 消歧。
    并且最后还有一道**合约→symbol 回环校验**（`_sym_ok`）：GoPlus/RugCheck 回传的
    symbol 必须与 base 一致，否则按未测到处理 —— 这是防「消歧选错币」的最后闸门。

【坑 6 · 选链不能按「优先级表」，必须用 CoinGecko 的**规范链**】
    第一版按 `CHAIN_PRIORITY`（solana 优先）选链，实测直接读错币：
        CAKE      → 读到 Solana 上同名合约（6,376 持有人），真实是 BSC（1,911,023）
        BABYDOGE  → 读到 Solana 同名合约（top1 99.38%），真实是 BSC
        ENA / CAT → 同样读到 Solana 上的同名部署
    根因：CoinGecko 的 `platforms` 里**包含各链的桥接/包装合约**，而「优先级表」
    不知道哪条是主链。更糟的是**回环校验拦不住** —— 桥接合约的 symbol 与真币一致
    （`$WIF`、`CAKE`、`BabyDoge`…），所以读出来的数字看起来完全正常。
    正解：**`platforms` 的首键就是规范链**（CoinGecko 把规范链排在首位）。
    实测 6/6 与 `/coins/{id}` 的 `asset_platform_id` 吻合，且顺序把
    `pancakeswap-token` 排在 `binance-smart-chain`、`ethena` 排在 `ethereum`
    —— 与真实主链一致。这条假设**承重**，所以每次刷新最多花 1 次 `/coins/{id}`
    复核一个多链币，不一致时计入 `canon_mismatch` 并在体检视图暴露。
    规范链**不在支持表**时（bittensor→`robinhood`、hyperliquid→`hyperliquid`）
    → 一律**未测到**，绝不退到别的链去读。

【坑 7 · 链上 symbol 可能带 `$` 前缀，回环校验会误杀】
    实测 WIF 的链上 symbol 是 **`$WIF`**（RugCheck `tokenMeta.symbol` 与 GoPlus
    `metadata.symbol` 都是），而币安/CoinGecko 用的是 `WIF`。若严格相等比较，
    WIF 这类币会被整批误判成「消歧选错币」而丢弃 —— 一个**静默的漏报**。
    所以 `_sym_ok` 先剥 `$` 与空白再比（只剥这两类装饰，不放宽到模糊匹配）。

────────────────────────────────────────────────────────────────────────────
接口
────────────────────────────────────────────────────────────────────────────
    get_onchain_holdings(symbols) -> (dict, bool)   # 只读缓存，**绝不阻塞**
    refresh_async(symbols)                          # 后台线程取数（单一在途 + 节流）
    onchain_view()                                  # 体检视图（进 payload，供界面自证）
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

import requests

# ── 三个数据源基址 ──────────────────────────────────────────────────────────
CG_BASE = "https://api.coingecko.com/api/v3"
GOPLUS_BASE = "https://api.gopluslabs.io/api/v1"
RUGCHECK_BASE = "https://api.rugcheck.xyz/v1"

# ── 缓存 TTL ────────────────────────────────────────────────────────────────
# base → cg_id：市值排名会漂（实测 WIF 从 182 漂到 184），但半小时内无妨。
# 一次请求批量覆盖全部候选（`symbols=a,b,c`），所以短 TTL 也不贵。
CG_MARKETS_TTL = 1800.0     # 30 分钟
# cg_id → 全链合约地址：取自 `/coins/list?include_platform=true`
# （实测 21252 条 / 3.75MB / 2.18s）。「某币新上一条链」是公告级事件，给 6 小时；
# 且**落盘复用**，重启不必重花配额。
CG_LIST_TTL = 21600.0       # 6 小时
# cg_id → asset_platform_id（**规范链**）：只在「该币有多条链」时才需要，见 `_pick_chain`。
CG_CANON_TTL = 21600.0      # 6 小时
# 持有人集中度：持有人结构是慢变量，15 分钟足够。
HOLD_TTL = 900.0            # 15 分钟

# ── 节流与预算 ──────────────────────────────────────────────────────────────
CG_MIN_GAP = 0.8            # CoinGecko 两次请求最小间隔（实测连续打即 429，留足余量）
CG_BACKOFF = 900.0          # 撞 429 后静默 15 分钟
CG_CALL_BUDGET = 6          # 单次后台刷新允许消耗的 CoinGecko 调用数（超出留到下轮）
# GoPlus 免费层：**这是本轮实测出来的核心配置**。
#   · 间隔 0.12s 连打 → 第 11 次返回 `{"code":4029,"message":"too many requests"}`（HTTP 仍是 200）
#   · 被限流后：距首次 30s 仍限流、**45s 恢复**
#   · **间隔 5.5s（≈8 次/45s）连打 14 次全部成功（82s，零限流）**
# 结论：它按「滚动窗口内次数」限流，**匀速放慢可以从根上不触发**，比被限流后再退避好得多 ——
# 退避的代价是那一轮后半段的币被读成「未测到」，而匀速的代价只是后台线程多跑一分多钟。
GOPLUS_MIN_GAP = 5.5
GOPLUS_BACKOFF = 45.0       # 万一还是撞上了：实测恢复点 45s
RUGCHECK_BACKOFF = 30.0     # RugCheck 未实测到限流（一次请求拿全 20 名持有人，请求量最小）
# `_throttle()` 的单次休眠上限。⚠️ 原先硬编码 2.0s —— 那意味着**任何大于 2s 的间隔都形同虚设**
# （写成 5.5s 也只会睡 2s，然后照样触发限流）。要调间隔就必须同时看这个上限。
_MAX_THROTTLE_SLEEP = 6.0
# 单轮最多查几个币（与确认层的 24 币封顶同量级）。**注意这是个"上限"不是"配额"**：
# GoPlus 大约 10 次就限流，所以纯 EVM 的一轮通常只填得进 ~10 个，其余留到下一轮
# （`refresh_async` 的 todo 会把「没有读数」的币重新排进来）。这是**有意的**：
# 与其硬打被限流后把 10 个币读成「无链上数据」，不如慢三轮填满，且每一轮都如实记账。
MAX_SYMBOLS_PER_REFRESH = 24
RUGCHECK_MIN_GAP = 0.30
REFRESH_MIN_GAP = 45.0      # 两次后台刷新之间的最小间隔（防用户连点「强制重扫」打爆外部源）
HTTP_TIMEOUT = 10.0

# ── 交叉验证判据（链下代理 × 链上实证）─────────────────────────────────────────
# ⚠️ 这两条线是**展示层判据，不是交易判据** —— 它们不参与 `_radar_score`、不参与
# 妖币三轴的 `grade`，只决定「链上佐证这一格显示什么话」。理由：v1.7.2 是本轴第一次
# 拿到真实读数，任何把它接进评分/分级的做法都是**无样本改判据**。
#
# 取值依据（2026-09-17 实测 14 个真实读数，`top10_pct` 中位 **45.5%**）：
#   4.14 / 23.22 / 24.61 / 26.98 / 36.67 / 38.62 / 45.38 / 45.68
#   52.99 / 59.22 / 60.47 / 62.82 / 87.70 / 95.83
# 取 60 / 25 两条线 → 高 4 个、低 3 个、中间 7 个。**中间那一档刻意不判**：
# 把 45% 的币硬判成「集中」或「分散」都是编故事，而报警必须稀有才有意义。
OC_TOP10_HIGH = 60.0        # top10 ≥ 60% → 链上判「高度集中」
OC_TOP10_LOW = 25.0         # top10 < 25% → 链上判「相对分散」

# ── 符号归一 ────────────────────────────────────────────────────────────────
# 计价后缀：币安 USDT 永续/现货为主，其余为稳健性冗余（本项目目前只消费 USDT 对）。
_QUOTES = ("USDT", "USDC", "FDUSD", "TUSD", "BUSD", "BTC", "ETH", "BNB")
# 乘数前缀：**必须长到短匹配**（`1000000MOG` 不能先被 `1000` 吃掉 → 剩下 `000MOG`）。
# 见模块头「坑 4」的实测清单。
_MULTS = ("1000000", "100000", "10000", "1000", "1M")
_BASE_RE = re.compile(r"^(" + "|".join(_MULTS) + r")?(.+)$")


def base_symbol(sym: str):
    """币安 symbol → `(base, mult)`。例：

        `BONKUSDT`        → `("BONK", 1)`
        `1000BONKUSDT`    → `("BONK", 1000)`
        `1MBABYDOGEUSDT`  → `("BABYDOGE", 1000000)`
        `1INCHUSDT`       → `("1INCH", 1)`      ← 不得把 `1INCH` 的 `1` 当乘数
        `RAVEUSDT`        → `("RAVE", 1)`

    `mult` 只用于**展示口径**（`total_supply` 的换算）；**占比类字段与乘数无关**
    （分子分母同乘），所以集中度读数是乘数不变的。
    """
    s = str(sym or "").strip().upper()
    for q in _QUOTES:
        if s.endswith(q) and len(s) > len(q):
            s = s[:-len(q)]
            break
    m = _BASE_RE.match(s)
    if not m:
        return s, 1
    pre, rest = m.group(1) or "", m.group(2) or s
    if not pre:
        return rest, 1
    # `1M` 是唯一非纯数字前缀
    mult = 1000000 if pre == "1M" else int(pre)
    # 防误剥：剥完必须还剩至少 1 个字符（`1000` 本身不是币名，但保守起见仍校验）。
    return (rest or s), (mult if rest else 1)


# ── 链支持表 ────────────────────────────────────────────────────────────────
# 只列**实测过或 GoPlus 文档明确支持**的链。不在此表的链一律 `unsupported_chain`（未测到），
# 而不是猜一个 chainId —— 猜错会拿到空 result（退化成未测到，不致命），但更糟的是
# 万一地址在别的链上存在，就会读成「另一个币的筹码」。宁可不测。
#
# 实测已验证：1 / 56 / 137 / 8453 / 42161 / 10。
GOPLUS_CHAIN_ID = {
    "ethereum": "1",
    "binance-smart-chain": "56",
    "polygon-pos": "137",
    "base": "8453",
    "arbitrum-one": "42161",
    "optimistic-ethereum": "10",
    "avalanche": "43114",
    "cronos": "25",
    "fantom": "250",
    "xdai": "100",
    "zksync": "324",
    "linea": "59144",
    "mantle": "5000",
    "celo": "42220",
    "klay-token": "8217",
    "harmony-shard-0": "1666600000",
    "pulsechain": "369",
}

# 选链**不用**优先级表 —— 见模块头「坑 6」：按优先级（solana 优先）挑会把
# CAKE / BABYDOGE / ENA / CAT 读成 Solana 上的同名桥接合约。规范链取自
# CoinGecko `platforms` 的**首键**。这里只保留「哪些链**能读**」这一张表（GOPLUS_CHAIN_ID）。
#
# 保留一条历史注记，避免以后有人再引入优先级表：CoinGecko 的平台表里还有一批
# 桥接/新链键（`robinhood` / `unichain` / `abstract` / `hyperevm` / `neon-evm` /
# `ink` / `x-layer` / `morph-l2`）。实测 `robinhood` 链上的冒名币带着 7.6e8 的
# 假流动性 —— 任何「按流动性/按优先级挑链」的策略都会踩进去。

# 销毁 / 黑洞地址（EVM 常见写法）。这些地址持仓 = 供给被销毁，与「控盘」相反。
_BURN_ADDRS = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000001",
    "0x0000000000000000000000000000000000000002",
    "0xffffffffffffffffffffffffffffffffffffffff",
}
# RugCheck `knownAccounts[addr].type` 里属于「非自然人持仓」的类型。
_POOL_TYPES = {"AMM", "POOL", "VAULT", "LOCK", "LOCKER", "FARM", "STAKING", "BRIDGE", "CEX"}
_POOL_NAME_HINTS = ("pool", "amm", "locker", "vault", "bridge", "lp ")

# ── 会话与路由 ──────────────────────────────────────────────────────────────
# `trust_env=False` + 显式 `proxies=`：完全掌控路由。不能靠 env，因为沙箱会把
# `HTTP(S)_PROXY` 指向一个对本项目无用的端口（实测 45746 对 CoinGecko 直接超时），
# 而真机上 env 又是 proxy_pool 注入的用户代理 —— 两者行为必须由本模块显式决定。
_session = requests.Session()
_session.trust_env = False

_DIRECT = {"http": None, "https": None}
_ROUTE_OK: dict = {}                       # kind → 已验证可用的路由名
_ROUTE_LOCK = threading.Lock()


class _RateLimited(Exception):
    """429。必须与普通网络错误分开：它意味着「再来会更糟」，要静默而不是重试。"""


def _env_proxy() -> str:
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return ""


def _kernel_proxy() -> str:
    """proxy_pool 里当前激活的代理（真机上就是用户的节点）。失败返回空串。"""
    try:
        import proxy_pool
        return (proxy_pool.active_url() or "").strip()
    except Exception:
        return ""


def _kernel_mixed() -> str:
    """proxy_kernel 本地混合端口的 URL；未运行返回空串。

    ⚠️ 端口**不能硬编码**。此前写死 `http://127.0.0.1:7899`，而 `proxy_kernel` 用的是
    `_free_port(7899)` —— **7899 被占用时会顺延到 7900/7901…**，此时这个常量指向的是一个
    空端口（甚至可能是别的进程的端口），于是 CoinGecko 这一层恒失败，而失败是**静默**的：
    整条链路的终态是「所有币都未测到」，看起来跟「这些币本来就没链上数据」一模一样。
    权威来源是 `proxy_kernel.mixed_port()`（它内部还会 `_recover()` 读 state.json）。
    """
    try:
        import proxy_kernel
        p = int(proxy_kernel.mixed_port() or 0)
        return f"http://127.0.0.1:{p}" if p else ""
    except Exception:
        return ""


def _routes(kind: str) -> list:
    """按 kind 给出候选路由（有序）。

    `cg`（CoinGecko）：实测**直连超时**，必须先走代理 → 顺序为 env → 代理池节点 → 内核混合端口 → 直连。
    `direct`（GoPlus / RugCheck）：实测**直连可达** → 直连优先，代理仅作兜底。

    ⚠️ env 排在第一位是有前提的：`proxy_pool.apply_env()` 会把**当前可用节点**注入
    `HTTP(S)_PROXY`。但它不保证 env 里的代理一定能到 CoinGecko —— 实测宿主/其他工具留下的
    代理会占住这个变量，此时前几次尝试全部超时。所以每次成功都会记进 `_ROUTE_OK`，
    后续直接命中；发现失效会清掉重找。**失败迹象由 `onchain_view()["last_error"]` 暴露。**
    """
    proxied = []
    for u in (_env_proxy(), _kernel_proxy(), _kernel_mixed()):
        if u and u not in proxied:
            proxied.append(u)
    if kind == "cg":
        return [{"http": u, "https": u} for u in proxied] + [_DIRECT]
    return [_DIRECT] + [{"http": u, "https": u} for u in proxied]


def _route_name(px) -> str:
    return "direct" if px is _DIRECT else str(px.get("https"))


def _http_json(url: str, params: dict, kind: str, timeout: float = HTTP_TIMEOUT):
    """按候选路由取 JSON。返回 `(json, route_name)`；全失败返回 `(None, "")`。

    已验证成功的路由会记住，后续直接复用；该路由失效时清掉并重新发现。
    """
    global _last_error
    with _ROUTE_LOCK:
        cached = _ROUTE_OK.get(kind)
    cands = _routes(kind)
    if cached:
        cands = ([p for p in cands if _route_name(p) == cached]
                 + [p for p in cands if _route_name(p) != cached])
    for px in cands:
        try:
            r = _session.get(url, params=params, timeout=timeout, proxies=px)
            if r.status_code == 429:
                raise _RateLimited()
            r.raise_for_status()
            j = r.json()
            with _ROUTE_LOCK:
                _ROUTE_OK[kind] = _route_name(px)
            return j, _route_name(px)
        except _RateLimited:
            raise
        except Exception as e:                     # 路由不通 / 超时 / JSON 坏
            _last_error = f"{kind}:{type(e).__name__}"
            with _ROUTE_LOCK:
                if _ROUTE_OK.get(kind) == _route_name(px):
                    _ROUTE_OK.pop(kind, None)
    return None, ""


_last_error = ""

# ── 缓存 ────────────────────────────────────────────────────────────────────
# ⚠️ `symbols` / `holdings` 的键是 **base 符号**（`BONK`，不是 `1000BONKUSDT`）：
# 现货行与合约行因此共用同一次读数（见模块头「坑 4」）。
_cache = {
    "symbols": {},          # base → {"id": cg_id, "ts": float}
    "symbols_ts": 0.0,      # 上一次「成功拉到一批」的时刻（用于 TTL 与补漏节流）
    "platforms": {},        # cg_id → {chain: contract}
    "platforms_ts": 0.0,
    "canon": {},            # cg_id → {"asset_platform_id": str, "ts": float}
    "holdings": {},         # base → 读数 dict（含 "ts"）
}
_measured = False              # 是否**至少成功读到过一次**（与 ts_series 的 measured 同义）
_last_ok_at = 0.0
_cg_backoff_until = 0.0
_goplus_backoff_until = 0.0
_rug_backoff_until = 0.0
_cg_last_call = 0.0
_goplus_last_call = 0.0
_rug_last_call = 0.0
_lock = threading.Lock()
_refreshing = False
_last_refresh_at = 0.0
_stats = {"refreshes": 0, "symbols_ok": 0, "symbols_fail": 0, "cg_429": 0,
          # v1.7.2：逐源限流计数。此前只有 `cg_429` —— GoPlus 被限流时**零痕迹**，
          # 而它 10 次就限，是三个源里最容易触发的一个。
          "goplus_throttled": 0, "rug_throttled": 0,
          "canon_mismatch": 0, "last_route": ""}
_loaded = False


def _cache_path() -> str:
    try:
        import workspace
        return os.path.join(workspace.WORKSPACE, "onchain_cache.json")
    except Exception:
        return ""


def _load_cache() -> None:
    """进程内首次使用时从 workspace 载入（跨重启复用，避免重花 CoinGecko 配额）。"""
    global _loaded, _measured, _last_ok_at
    if _loaded:
        return
    _loaded = True
    p = _cache_path()
    if not p or not os.path.exists(p):
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return
        for k in ("symbols", "platforms", "canon", "holdings"):
            v = d.get(k)
            if isinstance(v, dict):
                _cache[k] = v
        _cache["symbols_ts"] = float(d.get("symbols_ts") or 0)
        _cache["platforms_ts"] = float(d.get("platforms_ts") or 0)
        _last_ok_at = float(d.get("last_ok_at") or 0)
        # ⚠️ 落盘里是**上一轮成功**的读数，但 `measured` 必须表达「缓存里的数是不是实测值」。
        # 有读数 ⇒ 它确实来自一次成功读取 ⇒ True；空 ⇒ False（不得把「有文件」当「测到了」）。
        _measured = bool(_cache["holdings"])
    except Exception:
        pass


def _save_cache() -> None:
    p = _cache_path()
    if not p:
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        # newline="" + 显式 utf-8：仓库里 LF/CRLF 混存，写文本必须锁住换行与编码
        # （见项目约定：Python 写文本须 newline='' 或 write_bytes）。
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            json.dump({"symbols": _cache["symbols"], "symbols_ts": _cache["symbols_ts"],
                       "platforms": _cache["platforms"], "platforms_ts": _cache["platforms_ts"],
                       "canon": _cache["canon"], "holdings": _cache["holdings"],
                       "last_ok_at": _last_ok_at}, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


# ── 限速 ────────────────────────────────────────────────────────────────────
def _throttle(kind: str) -> None:
    """源级最小间隔。CoinGecko 免费额度实测「连打即 429」，间隔必须留足。"""
    global _cg_last_call, _goplus_last_call, _rug_last_call
    if kind == "cg":
        gap, attr = CG_MIN_GAP, "_cg_last_call"
    elif kind == "goplus":
        gap, attr = GOPLUS_MIN_GAP, "_goplus_last_call"
    else:
        gap, attr = RUGCHECK_MIN_GAP, "_rug_last_call"
    wait = gap - (time.time() - globals()[attr])
    if wait > 0:
        # 上限从写死的 2.0s 提到 `_MAX_THROTTLE_SLEEP`：见该常量的注释 ——
        # 间隔一旦调到 2s 以上，旧的 2.0s 上限会把节流悄悄变成空操作。
        time.sleep(min(wait, _MAX_THROTTLE_SLEEP))
    globals()[attr] = time.time()


def cg_available() -> bool:
    """CoinGecko 是否处于可调用状态（未在 429 静默期内）。"""
    return time.time() >= _cg_backoff_until


def _throttle_signal(j) -> str:
    """识别「HTTP 200 但语义上是限流」的响应。命中返回 `message`，否则返回空串。

    GoPlus 免费层实测（2026-09-17，本机）：
      · 连续 10 次后，第 11 次返回 **HTTP 200** + `{"code": 4029, "message": "too many requests"}`,
        **响应体里根本没有 `result` 键**；
      · 被限流期间**之前能读到的币也一起失败** —— 所以那不是「这个币没数据」；
      · 恢复点：距首次限流 **30s 仍限流、45s 恢复**；恢复后又是约 10 次。

    ⚠️ 为什么必须单独识别：原有解析是「`result` 不是非空 dict → return None」，而限流响应
    **恰好也没有 result**。于是限流被读成「这个币没有链上数据」—— 两者在 UI 上都是「未测到」，
    **用户区分不了、日志里也什么都不留**。这正是本项目最忌讳的失败形状：
    不报错、不飘红，只是数字静默地少了几个。
    """
    if not isinstance(j, dict):
        return ""
    code = j.get("code")
    msg = str(j.get("message") or "")
    # 4029 = GoPlus 的限流码。同时兼容别家可能用的 429 / 文本形式。
    if str(code) in ("4029", "429") or "too many requests" in msg.lower():
        return msg or f"code={code}"
    if j.get("error") and not j.get("result"):
        return str(j.get("error"))
    return ""


def _src_available(kind: str) -> bool:
    """该数据源是否不在退避期内。`_read_*` 在发请求前必须先问这一句。"""
    if kind == "goplus":
        return time.time() >= _goplus_backoff_until
    if kind == "rug":
        return time.time() >= _rug_backoff_until
    return True


def _note_throttle(kind: str) -> None:
    """记录一次限流：开退避窗口 + 计数（**所有源都计数**，不只 CoinGecko）。

    此前只有 `cg_429` 一个计数器，GoPlus / RugCheck 被限流时**没有任何痕迹** ——
    而它们恰好是最容易触发限流的两家（GoPlus 10 次就限）。
    """
    global _goplus_backoff_until, _rug_backoff_until
    now = time.time()
    if kind == "goplus":
        _goplus_backoff_until = now + GOPLUS_BACKOFF
        _stats["goplus_throttled"] = int(_stats.get("goplus_throttled") or 0) + 1
    elif kind == "rug":
        _rug_backoff_until = now + RUGCHECK_BACKOFF
        _stats["rug_throttled"] = int(_stats.get("rug_throttled") or 0) + 1
    else:
        _stats["cg_429"] = int(_stats.get("cg_429") or 0) + 1


def _cg_get(url: str, params: dict, budget: list):
    """带预算 + 429 静默的 CoinGecko 取数。返回 json 或 None。

    `budget` 是单元素列表（可变），用于跨调用累计本轮已消耗的调用数。
    """
    global _cg_backoff_until
    if not cg_available() or budget[0] <= 0:
        return None
    budget[0] -= 1
    _throttle("cg")
    try:
        j, route = _http_json(url, params, "cg")
        if route:
            _stats["last_route"] = "cg:" + route
        if _throttle_signal(j):
            _cg_backoff_until = time.time() + CG_BACKOFF
            _note_throttle("cg")
            return None
        return j
    except _RateLimited:
        _cg_backoff_until = time.time() + CG_BACKOFF
        _note_throttle("cg")
        return None
    except Exception:
        return None


# ── L1：base → CoinGecko id（按市值排名消歧）────────────────────────────────
def _refresh_symbols(bases: list, budget: list) -> None:
    """批量消歧（见模块头「坑 5」）。"""
    if not bases:
        return
    now = time.time()
    missing = [b for b in bases if b not in _cache["symbols"]]
    fresh = _cache["symbols_ts"] and now - _cache["symbols_ts"] < CG_MARKETS_TTL
    # TTL 未到且没有缺口 → 不请求。有缺口时也**最多 5 分钟补一次**，
    # 否则一个 CoinGecko 压根没有的 symbol 会让每轮刷新都白发一次请求。
    if fresh and not (missing and now - _cache["symbols_ts"] > 300):
        return
    j = _cg_get(f"{CG_BASE}/coins/markets",
                {"vs_currency": "usd", "symbols": ",".join(bases[:200]),
                 "order": "market_cap_desc", "per_page": 250, "page": 1}, budget)
    # ⚠️ 正常是 list；被限流时是 dict（`{"status": {...}}`）。必须显式判型，
    # 否则 dict 会被当成「没有结果」→ 整批币静默丢掉（见模块头「坑 3」）。
    if not isinstance(j, list) or not j:
        return
    # 列表已按市值降序 → 首次出现即该 symbol 的最优 id。
    out: dict = {}
    for it in j:
        if not isinstance(it, dict):
            continue
        s = str(it.get("symbol") or "").upper()
        cid = str(it.get("id") or "")
        if s and cid and s not in out:
            out[s] = {"id": cid, "ts": now}
    if out:
        _cache["symbols"].update(out)
        _cache["symbols_ts"] = now


# ── L2：cg_id → 全链合约地址 ────────────────────────────────────────────────
def _refresh_platforms(budget: list) -> None:
    """一次请求拿全量 id→平台表（实测 21252 条 / 3.75MB / 2.18s）。

    为什么不用「逐币 `/coins/{id}`」：那对 24 个候选就是 24 次 CoinGecko 调用，
    在免费额度下必然 429（实测）。而 `/coins/list?include_platform=true` 一次到位，
    且长 TTL + 落盘，把 CoinGecko 的长期负载压到「每 6 小时 1 次」。
    """
    now = time.time()
    if _cache["platforms_ts"] and now - _cache["platforms_ts"] < CG_LIST_TTL:
        return
    j = _cg_get(f"{CG_BASE}/coins/list", {"include_platform": "true"}, budget)
    if not isinstance(j, list) or not j:
        return
    out: dict = {}
    for it in j:
        if not isinstance(it, dict):
            continue
        cid = str(it.get("id") or "")
        pl = it.get("platforms")
        if cid and isinstance(pl, dict):
            out[cid] = {str(k): str(v) for k, v in pl.items() if v}
    if out:
        _cache["platforms"] = out
        _cache["platforms_ts"] = now


def _refresh_canon(cg_id: str, budget: list) -> str:
    """取该币的**规范链**（`asset_platform_id`）。只在多链币上调用，且有预算闸门。

    为什么非要它：多链币若只按优先级选链，会把**桥接合约**当成主链筹码。
    实测 TAO 的平台表只有 `robinhood` + `base`（主链 substrate 不在表里）、
    HYPE 只有 `hyperliquid`（不在支持表里）—— 这类币「未测到」才是正确答案，
    而乱选一条链读出来的数字**看起来完全正常**，属于最危险的那种错。
    """
    now = time.time()
    e = _cache["canon"].get(cg_id)
    if isinstance(e, dict) and e.get("ts") and now - float(e["ts"]) < CG_CANON_TTL:
        return str(e.get("asset_platform_id") or "")
    if budget[0] <= 0 or not cg_available():
        return ""
    j = _cg_get(f"{CG_BASE}/coins/{cg_id}",
                {"localization": "false", "tickers": "false", "market_data": "false",
                 "community_data": "false", "developer_data": "false"}, budget)
    if not isinstance(j, dict) or not j:
        return ""
    apid = str(j.get("asset_platform_id") or "")
    _cache["canon"][cg_id] = {"asset_platform_id": apid, "ts": now}
    return apid


def _pick_chain(cg_id: str, platforms: dict, budget: list, verify: list):
    """选链。返回 `(chain, contract, canon_verified)`；无法确定返回 `(None, None, None)`。

    **规范链 = `platforms` 的首键**（见模块头「坑 6」）。为什么不按优先级表挑：
    实测按「solana 优先」会把 CAKE / BABYDOGE / ENA / CAT 全部读成 Solana 上的
    同名桥接合约 —— 而桥接合约的 symbol 与真币一致，回环校验拦不住，
    读出来的集中度数字**看起来完全正常**。这类错比「未测到」危险得多。

    `canon_verified`：True = `/coins/{id}` 确认了首键；False = 两者不一致（已采用
    权威值）；None = 本轮未复核（多链币每轮最多复核 1 个，省 CoinGecko 配额）。
    """
    order = list(platforms.keys())
    if not order:
        return None, None, None
    primary = order[0]
    if not (primary == "solana" or primary in GOPLUS_CHAIN_ID):
        # 规范链不在支持表（实测 bittensor→robinhood、hyperliquid→hyperliquid）→
        # **未测到**。绝不退到别的链去读：那正是读到同名桥接合约的路径。
        return None, None, None
    verified = None
    if len(order) > 1 and verify[0] > 0:
        verify[0] -= 1
        canon = _refresh_canon(cg_id, budget)
        if canon:
            verified = (canon == primary)
            if not verified:
                _stats["canon_mismatch"] = int(_stats.get("canon_mismatch") or 0) + 1
                if canon == "solana" or canon in GOPLUS_CHAIN_ID:
                    # 权威值可用 → 用它（并把「首键假设失效」这件事计数暴露出去）
                    return canon, platforms.get(canon), False
    return primary, platforms.get(primary), verified


# ── 解析工具 ────────────────────────────────────────────────────────────────
def _f(v):
    """转 float；**非数值 / 空串 / NaN / inf → None**（未测到），绝不写 0。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def _pct(v):
    """GoPlus 的 `percent` 是**小数比例** → ×100。见模块头「坑 1」。"""
    x = _f(v)
    return None if x is None else round(x * 100.0, 4)


def _bool01(v):
    """GoPlus 的布尔字段是 "0"/"1" 字符串。**缺失 → None**（未测到），不是 False。"""
    if v is None or v == "":
        return None
    s = str(v).strip().lower()
    if s in ("1", "true", "yes"):
        return True
    if s in ("0", "false", "no"):
        return False
    return None


def _sym_ok(base: str, got) -> bool:
    """合约 → symbol **回环校验**。这是防「消歧选错币」的一道闸门。

    实测两处必须容错（见模块头「坑 7」）：
      · GoPlus 对 CAKE 回传 `"Cake"`（首字母大写）→ 必须大小写不敏感；
      · WIF 的链上 symbol 是 `"$WIF"`（带 `$` 前缀）→ 必须先剥 `$` 与空白。
    只剥这两类装饰，**不放宽到模糊匹配** —— 否则这道闸门就没意义了。
    """
    g = str(got or "").strip().lstrip("$").strip().upper()
    return bool(g) and g == str(base or "").strip().upper()


def _is_pool(addr: str, known: dict) -> bool:
    a = str(addr or "").lower()
    if not a:
        return False
    if a in _BURN_ADDRS:
        return True
    rec = known.get(addr) or known.get(a) or {}
    if isinstance(rec, dict):
        if str(rec.get("type") or "").upper() in _POOL_TYPES:
            return True
        n = str(rec.get("name") or "").lower()
        if any(k in n for k in _POOL_NAME_HINTS):
            return True
    return False


def _concentration(rows: list, known: dict = None):
    """从 `[(addr, pct, locked), …]` 算集中度。返回 `(top1, top10, raw_top1, excluded_n)`。

    先剔除销毁地址 / 锁定 / AMM 池，再取最大与前十合计；`raw_top1` 保留剔除前的值，
    让「剔了什么」可审计（见模块头「坑 2」）。
    占比单位：调用方必须**已经归一成百分数**。
    """
    known = known or {}
    kept, excluded = [], 0
    for addr, pct, locked in rows:
        if pct is None:
            continue
        if locked or _is_pool(addr, known):
            excluded += 1
            continue
        kept.append(pct)
    raw_top1 = max((r[1] for r in rows if r[1] is not None), default=None)
    if not kept:
        # 全部被剔除（例如一个只被销毁地址持有的币）→ 集中度**未测到**，
        # 而不是 0 —— 0 会被读成「极其分散」，与事实相反。
        return None, None, raw_top1, excluded
    top1 = round(max(kept), 4)
    top10 = round(sum(sorted(kept, reverse=True)[:10]), 4)
    return top1, top10, raw_top1, excluded


# ── L3a：EVM（GoPlus）───────────────────────────────────────────────────────
def _read_evm(base: str, chain: str, addr: str):
    cid = GOPLUS_CHAIN_ID.get(chain)
    if not cid or not addr:
        return None
    if not _src_available("goplus"):
        return None                     # 退避期内不发请求：省时间、也不再加剧限流
    _throttle("goplus")
    try:
        j, _ = _http_json(f"{GOPLUS_BASE}/token_security/{cid}",
                          {"contract_addresses": addr}, "direct")
    except _RateLimited:
        _note_throttle("goplus")
        return None
    except Exception:
        return None
    if not isinstance(j, dict):
        return None
    if _throttle_signal(j):
        # ⚠️ 必须在解析 result **之前**判定 —— 限流响应同样没有 result，
        # 走到下面那句就会被读成「这个币没有链上数据」。
        _note_throttle("goplus")
        return None
    res = j.get("result")
    if not isinstance(res, dict) or not res:
        return None
    d = res.get(addr) or res.get(addr.lower())
    if d is None:
        for k, v in res.items():
            if str(k).lower() == str(addr).lower():
                d = v
                break
    if not isinstance(d, dict):
        return None
    # 回环校验：消歧选错币时，这里就是那道闸门。
    if not _sym_ok(base, d.get("token_symbol")):
        return None
    holders = d.get("holders") if isinstance(d.get("holders"), list) else []
    rows = [(str(h.get("address") or ""), _pct(h.get("percent")),
             bool(_bool01(h.get("is_locked")))) for h in holders if isinstance(h, dict)]
    top1, top10, raw1, exc = _concentration(rows)
    return {
        "source": "goplus",
        "chain": chain,
        "contract": addr,
        "holder_count": _f(d.get("holder_count")),
        "top1_pct": top1,
        "top10_pct": top10,
        "top1_raw_pct": raw1,
        "holders_excluded": exc,
        "holders_returned": len(rows),
        "creator_pct": _pct(d.get("creator_percent")),
        "owner_pct": _pct(d.get("owner_percent")),
        # 貔貅 / 税率：**缺失一律 None**（实测 CAKE 的 buy_tax/sell_tax 就是 None）。
        # 写成 False 会被读成「已确认不是貔貅」，那是相反的结论。
        "is_honeypot": _bool01(d.get("is_honeypot")),
        "buy_tax_pct": _pct(d.get("buy_tax")),
        "sell_tax_pct": _pct(d.get("sell_tax")),
        "is_mintable": _bool01(d.get("is_mintable")),
        "transfer_pausable": _bool01(d.get("transfer_pausable")),
        "is_open_source": _bool01(d.get("is_open_source")),
        "risks": None,
        "score": None,
        "total_supply": _f(d.get("total_supply")),
    }


# ── L3b：Solana（RugCheck 主 / GoPlus Solana 备）────────────────────────────
def _read_solana_rugcheck(base: str, addr: str):
    if not _src_available("rug"):
        return None
    _throttle("rug")
    try:
        j, _ = _http_json(f"{RUGCHECK_BASE}/tokens/{addr}/report", {}, "direct", timeout=25.0)
    except _RateLimited:
        _note_throttle("rug")
        return None
    except Exception:
        return None
    if not isinstance(j, dict) or not j:
        return None
    if _throttle_signal(j):
        _note_throttle("rug")
        return None
    meta = j.get("tokenMeta") if isinstance(j.get("tokenMeta"), dict) else {}
    if not _sym_ok(base, meta.get("symbol")):
        return None
    known = j.get("knownAccounts") if isinstance(j.get("knownAccounts"), dict) else {}
    th = j.get("topHolders") if isinstance(j.get("topHolders"), list) else []
    # ⚠️ RugCheck 的 `pct` **已经是百分数** → 不得再 ×100（见模块头「坑 1」）。
    rows = [(str(h.get("owner") or h.get("address") or ""), _f(h.get("pct")), False)
            for h in th if isinstance(h, dict)]
    top1, top10, raw1, exc = _concentration(rows, known)
    risks = j.get("risks")
    risk_names = ([str(x.get("name")) for x in risks if isinstance(x, dict) and x.get("name")]
                  if isinstance(risks, list) else None)
    token = j.get("token") if isinstance(j.get("token"), dict) else {}
    supply = _f(token.get("supply"))
    dec = _f(token.get("decimals"))
    ui_supply = (supply / (10 ** int(dec))) if (supply and dec is not None) else None
    cb = _f(j.get("creatorBalance"))
    return {
        "source": "rugcheck",
        "chain": "solana",
        "contract": addr,
        "holder_count": _f(j.get("totalHolders")),
        "top1_pct": top1,
        "top10_pct": top10,
        "top1_raw_pct": raw1,
        "holders_excluded": exc,
        "holders_returned": len(rows),
        "creator_pct": (round(cb / ui_supply * 100.0, 4)
                        if (cb is not None and ui_supply) else None),
        "owner_pct": None,
        "is_honeypot": None,                       # Solana 无此概念
        "buy_tax_pct": None,
        "sell_tax_pct": None,
        "is_mintable": (bool(token.get("mintAuthority")) if "mintAuthority" in token else None),
        "transfer_pausable": None,
        "is_open_source": None,
        "risks": risk_names,
        "score": _f(j.get("score")),
        "total_supply": supply,
    }


def _read_solana_goplus(base: str, addr: str):
    if not _src_available("goplus"):
        return None
    _throttle("goplus")
    try:
        j, _ = _http_json(f"{GOPLUS_BASE}/solana/token_security",
                          {"contract_addresses": addr}, "direct")
    except _RateLimited:
        _note_throttle("goplus")
        return None
    except Exception:
        return None
    if not isinstance(j, dict):
        return None
    if _throttle_signal(j):
        _note_throttle("goplus")
        return None
    res = j.get("result")
    if not isinstance(res, dict) or not res:
        return None
    d = None
    for k, v in res.items():
        if str(k).lower() == str(addr).lower():
            d = v
            break
    if not isinstance(d, dict):
        d = list(res.values())[0] if res else None
    if not isinstance(d, dict):
        return None
    meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
    # GoPlus Solana 不回传顶层 token_symbol，symbol 在 `metadata.symbol` 里。
    if not _sym_ok(base, meta.get("symbol")):
        return None
    holders = d.get("holders") if isinstance(d.get("holders"), list) else []
    rows = [(str(h.get("account") or ""), _pct(h.get("percent")),
             bool(_bool01(h.get("is_locked")))) for h in holders if isinstance(h, dict)]
    top1, top10, raw1, exc = _concentration(rows)
    mintable = d.get("mintable")
    is_mintable = (None if not isinstance(mintable, dict)
                   else _bool01(mintable.get("status")))
    return {
        "source": "goplus-solana",
        "chain": "solana",
        "contract": addr,
        "holder_count": _f(d.get("holder_count")),
        "top1_pct": top1,
        "top10_pct": top10,
        "top1_raw_pct": raw1,
        "holders_excluded": exc,
        "holders_returned": len(rows),
        "creator_pct": None,
        "owner_pct": None,
        "is_honeypot": None,
        "buy_tax_pct": None,
        "sell_tax_pct": None,
        "is_mintable": is_mintable,
        "transfer_pausable": None,
        "is_open_source": None,
        "risks": None,
        "score": None,
        "total_supply": _f(d.get("total_supply")),
    }


def _read_one(base: str, chain: str, addr: str):
    """按链分派。Solana 先 RugCheck（20 名持仓 + risks）再 GoPlus 兜底；EVM 走 GoPlus。"""
    if chain == "solana":
        return _read_solana_rugcheck(base, addr) or _read_solana_goplus(base, addr)
    return _read_evm(base, chain, addr)


# ── 对外：后台刷新 ──────────────────────────────────────────────────────────
def _do_refresh(bases: list) -> None:
    global _refreshing, _last_refresh_at, _measured, _last_ok_at
    try:
        _load_cache()          # 放后台线程里做：`workspace` 首次 import 有一次性迁移副作用，
                               # 不能让它落在 `refresh_async` 的调用方（扫描热路径）上。
        budget = [CG_CALL_BUDGET]
        verify = [1]           # 多链币的规范链复核配额（见 `_pick_chain`）
        _refresh_symbols(bases, budget)
        _refresh_platforms(budget)
        for base in bases:
            e = _cache["symbols"].get(base)
            if not e:
                _stats["symbols_fail"] += 1
                continue
            cg_id = e["id"]
            platforms = _cache["platforms"].get(cg_id)
            if not isinstance(platforms, dict) or not platforms:
                _stats["symbols_fail"] += 1
                continue
            chain, addr, verified = _pick_chain(cg_id, platforms, budget, verify)
            if not chain or not addr:
                _stats["symbols_fail"] += 1
                continue
            try:
                r = _read_one(base, chain, addr)
            except Exception:
                r = None
            if not r:
                _stats["symbols_fail"] += 1
                continue
            r["ts"] = time.time()
            r["base"] = base
            r["cg_id"] = cg_id
            r["multi_chain"] = len(platforms) > 1
            r["platforms_n"] = len(platforms)
            # 规范链是否被 `/coins/{id}` 复核过（True/False/None）。None 不是失败 ——
            # 多数币只有一条链、压根不需要复核；多链币每轮最多复核 1 个。
            r["canon_verified"] = verified
            with _lock:
                _cache["holdings"][base] = r
            _stats["symbols_ok"] += 1
        if _stats["symbols_ok"]:
            _measured = True
            _last_ok_at = time.time()
        _stats["refreshes"] += 1
        _save_cache()
    except Exception:
        pass
    finally:
        with _lock:
            _refreshing = False
        _last_refresh_at = time.time()


def refresh_async(symbols) -> bool:
    """请求后台刷新（**立即返回**，绝不阻塞调用方）。返回是否真的派发了刷新。

    为什么必须异步：`get_radar_v2` 跑在 UI 请求路径上，而冷启动时本模块要发
    「1 次 markets + 1 次 list + 最多 24 次 L3」，串行会往扫描延迟里加十几秒 ——
    v1.7.1 刚做过延迟治理（C3），不能在这里倒退回去。
    筹码分布是**慢变量**（15 分钟 TTL），晚一轮填上完全无损；
    而第一轮如实留空（未测到），比「阻塞 15 秒换一个立刻可用的数」更符合本项目纪律。
    """
    global _refreshing, _last_refresh_at
    now = time.time()
    if now - _last_refresh_at < REFRESH_MIN_GAP:
        return False
    bases = []
    for s in (symbols or []):
        b, _m = base_symbol(s)
        if b and b not in bases:
            bases.append(b)
    if not bases:
        return False
    # 只取「没有读数 / 读数已过期」的币，且封顶 —— 不对全池无条件打请求。
    todo = [b for b in bases
            if not (_cache["holdings"].get(b) or {}).get("ts")
            or now - float(_cache["holdings"][b].get("ts") or 0) > HOLD_TTL]
    if not todo:
        return False
    todo = todo[:MAX_SYMBOLS_PER_REFRESH]
    with _lock:
        if _refreshing:
            return False
        _refreshing = True
    try:
        threading.Thread(target=_do_refresh, args=(todo,), name="onchain-refresh",
                         daemon=True).start()
        return True
    except Exception:
        with _lock:
            _refreshing = False
        return False


# ── 对外：只读缓存 ──────────────────────────────────────────────────────────
def get_onchain_holdings(symbols=None) -> tuple:
    """读缓存。返回 `(dict, measured)`。**非阻塞、零网络**。

    `measured` 语义与 `scanner.get_funding_intervals` 完全一致：
      True  = 至少成功从外部源读到过一次（缓存里的读数是实测值）；
      False = 从未成功过（429 / 路由不通 / 全部校验失败）→ 调用方必须把「拿不到」
              当成**未测到**，**不得**当成「这个币没有筹码数据」或「筹码很分散」。

    ⚠️ 逐币语义：返回的 dict 里**没有**某个 symbol = 那个币未测到；有但字段是 None =
    该字段未测到。两者都不能写成 0。
    ⚠️ 入参是**币安 symbol**（`1000BONKUSDT` 也行），返回的键与入参**逐字对应**，
    方便调用方按行查找；内部按 base 归一，所以 `BONKUSDT` 与 `1000BONKUSDT`
    拿到的是**同一次读数**。
    """
    _load_cache()
    with _lock:
        data = dict(_cache["holdings"])
    if symbols is None:
        return data, bool(_measured)
    out = {}
    for s in symbols:
        raw = str(s or "").strip().upper()
        if not raw:
            continue
        b, _m = base_symbol(raw)
        if b in data:
            out[raw] = data[b]
    return out, bool(_measured)


# ── 对外：交叉判定（链下代理 × 链上实证）──────────────────────────────────────
def cross_verdict(reading, struct_flags) -> dict:
    """把「链下控盘代理」与「链上筹码实证」对撞。**纯函数、零 IO**。

    为什么这两者要交叉：`scanner._manip_flags` 那五个指纹**全是链下代理** ——
    「无现货 / 合约独大 / 换手畸高 / 空头付钱 / 拉升无爆仓」，说的是**盘面长什么样**，
    本质都是在**猜**「有人在控盘」。链上筹码回答的是另一个问题：**币到底在谁手里**。
    两者一致时是佐证，不一致时才是真正的信息 —— 尤其下面这两种：

      · **链上预警**（代理没命中、链上却很集中）：代理指标**结构性看不见**的那类控盘。
        这才是「打假器」最该报的一格。
      · **链上反驳**（代理命中了、链上却很分散）：代理**误报**。实测最有说服力的例子是
        **CAKE(BSC)** —— top1 是销毁地址 `0x…dead` 占 **92.74%**，若不先剔除销毁地址就直接算，
        它会被读成「单一持有人控盘」这个最极端的结论；剔除后 CAKE 以 **4.14%** 成为整批
        14 个读数里**最分散**的一个。**结论完全反过来**，而两个数字看起来都很正常。

    ⚠️ `struct_flags` 只应传**结构性**指纹（无现货 / 合约独大 / 换手畸高）。
    不要把「空头付钱 / 拉升无爆仓」传进来 —— 那两个说的是**对手盘在挨打**，
    与「筹码集中在谁手里」不是一个范畴，混进来会做出一堆无意义的「矛盾」。
    调用方各自持有一份结构性名单，本函数**不认识任何指纹的名字**，只看它空不空。

    ⚠️ **中间那一档刻意不判**（`neutral`）。top10 落在 25%~60% 之间的币占实测样本的一半，
    把它们硬判成「集中」或「分散」都是编故事；而报警必须稀有才有意义。
    """
    if not isinstance(reading, dict):
        return {
            "code": "unknown", "state": "未测到",
            "text": "链上筹码未测到（无合约地址 / 主链不在支持表 / 数据源被限流，三种都表现为这一格）",
            "top1_pct": None, "top10_pct": None, "proxy_hit": bool(struct_flags),
        }
    t10 = reading.get("top10_pct")
    t1 = reading.get("top1_pct")
    hit = bool(struct_flags)
    if t10 is None:
        return {
            "code": "unknown", "state": "未测到",
            "text": "链上读到了合约，但前十持仓占比没测出来（持仓行全部被剔除或字段缺失）",
            "top1_pct": t1, "top10_pct": None, "proxy_hit": hit,
        }
    t10 = float(t10)
    # 措辞里带上 top1 与「剔了几行」，让「剔了什么」可审计（见模块头「坑 2」）
    exc = reading.get("holders_excluded")
    keep_note = f"；已剔除 {exc} 个销毁地址/LP 池" if exc else ""
    t1txt = f"第一 {t1:.1f}%、" if t1 is not None else ""
    detail = f"前十持仓 {t10:.1f}%（{t1txt}来源 {reading.get('source')}/{reading.get('chain')}{keep_note}）"

    if t10 >= OC_TOP10_HIGH:
        if hit:
            return {"code": "confirm", "state": "链上佐证", "top1_pct": t1, "top10_pct": t10,
                    "proxy_hit": True,
                    "text": f"链下盘面已见控盘指纹，链上也确认高度集中 —— {detail}。两边同向，"
                            "控盘这件事基本可以当真；含义是**离场要果断**，不是「不能碰」"}
        return {"code": "warn", "state": "链上预警", "top1_pct": t1, "top10_pct": t10,
                "proxy_hit": False,
                "text": f"盘面看不到控盘指纹，但链上高度集中 —— {detail}。"
                        "这是代理指标**结构性看不见**的那类控盘（不用现货/不靠换手也能攥住筹码）"}
    if t10 < OC_TOP10_LOW:
        if hit:
            return {"code": "refute", "state": "链上反驳", "top1_pct": t1, "top10_pct": t10,
                    "proxy_hit": True,
                    "text": f"盘面命中控盘指纹，但链上筹码其实很分散 —— {detail}。"
                            "代理可能误报（销毁地址/LP 池被当成庄家是实测踩过的坑），"
                            "也可能是控盘发生在合约端而非现货持币端"}
        return {"code": "clean", "state": "无异常", "top1_pct": t1, "top10_pct": t10,
                "proxy_hit": False,
                "text": f"盘面与链上都没有控盘迹象 —— {detail}"}
    return {"code": "neutral", "state": "中性", "top1_pct": t1, "top10_pct": t10,
            "proxy_hit": hit,
            "text": f"链上集中度处在中间段（{OC_TOP10_LOW:.0f}%~{OC_TOP10_HIGH:.0f}% 之间不判）—— {detail}"}


def row_cell(reading, struct_flags) -> dict:
    """雷达行 / 妖币面板用的一格：原始读数压成展示所需最小集 + 交叉判决。

    只带下游**真的会用到**的字段。`risks`（RugCheck 的风险名列表，实测 TRUMP 会给出
    `Single holder ownership`）保留 —— 它是外部源自己的判断，能当交叉验证的第二票。
    """
    v = cross_verdict(reading, struct_flags)
    out = {
        "state": v["code"], "label": v["state"], "text": v["text"],
        "top1_pct": v["top1_pct"], "top10_pct": v["top10_pct"],
        "proxy_hit": v["proxy_hit"],
    }
    if isinstance(reading, dict):
        out["source"] = reading.get("source")
        out["chain"] = reading.get("chain")
        out["top1_raw_pct"] = reading.get("top1_raw_pct")
        out["holders_excluded"] = reading.get("holders_excluded")
        out["holder_count"] = reading.get("holder_count")
        out["risks"] = reading.get("risks")
        out["is_honeypot"] = reading.get("is_honeypot")
        out["sell_tax_pct"] = reading.get("sell_tax_pct")
        out["score"] = reading.get("score")
        out["ts"] = int(reading.get("ts") or 0)
    return out


def onchain_view() -> dict:
    """体检视图（进 payload）：让「到底通没通、覆盖了几个币」在界面上可见。

    与 `scanner.ts_store_view` 同源动机：只写不看的旁路数据，出问题时没人会发现。
    """
    _load_cache()
    with _lock:
        n = len(_cache["holdings"])
    return {
        "measured": bool(_measured),
        "symbols_cached": n,
        "platforms_cached": len(_cache["platforms"]),
        "last_ok_at": int(_last_ok_at) if _last_ok_at else 0,
        "cg_available": bool(cg_available()),
        "cg_backoff_sec": max(0, int(_cg_backoff_until - time.time())),
        # v1.7.2：逐源可用性 + 退避剩余秒数。没有这三个字段时，「GoPlus 被限流」与
        # 「这些币本来就没有链上数据」在界面上完全一样 —— 前者是可恢复的临时状态，
        # 后者是事实，把两者混为一谈正是本轮要修的东西。
        "goplus_available": bool(_src_available("goplus")),
        "goplus_backoff_sec": max(0, int(_goplus_backoff_until - time.time())),
        "rug_backoff_sec": max(0, int(_rug_backoff_until - time.time())),
        "refreshing": bool(_refreshing),
        "route": _stats.get("last_route") or "",
        "stats": dict(_stats),
        "last_error": _last_error,
    }


def reset_for_test() -> None:
    """测试用：清空全部内存态与落盘缓存。**不要**在运行时调用。"""
    global _measured, _last_ok_at, _cg_backoff_until, _refreshing, _last_refresh_at
    global _goplus_backoff_until, _rug_backoff_until
    global _last_error, _loaded, _cg_last_call, _goplus_last_call, _rug_last_call
    with _lock:
        _cache["symbols"] = {}
        _cache["platforms"] = {}
        _cache["canon"] = {}
        _cache["holdings"] = {}
        _cache["symbols_ts"] = 0.0
        _cache["platforms_ts"] = 0.0
    _measured = False
    _last_ok_at = 0.0
    _cg_backoff_until = 0.0
    _goplus_backoff_until = 0.0
    _rug_backoff_until = 0.0
    _cg_last_call = _goplus_last_call = _rug_last_call = 0.0
    _refreshing = False
    _last_refresh_at = 0.0
    _last_error = ""
    _loaded = False
    _stats.update({"refreshes": 0, "symbols_ok": 0, "symbols_fail": 0, "cg_429": 0,
                   "goplus_throttled": 0, "rug_throttled": 0,
                   "canon_mismatch": 0, "last_route": ""})
    with _ROUTE_LOCK:
        _ROUTE_OK.clear()
