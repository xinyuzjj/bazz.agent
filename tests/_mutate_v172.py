"""v1.7.2 护栏变异验证：把每条新护栏对应的实现改坏，确认它真的会红。

⚠️ 全程在**副本**里做（%TEMP%/v172mut），绝不碰工作区源码。
⚠️ 每个锚点必须**唯一**命中（count == 1）；命中次数 ≠ 1 会打印「跳过」并计入失败 ——
   那说明锚点写得不够精确，或者源码演进后锚点失效了（同一个陷阱：护栏自己也会腐烂）。

⚠️ 一条变异可以包含**多处编辑**（`(rel, old, new)` 列表）：有些退化必须同时改两处
   （例如「把派发挪到循环之后」= 删掉前面那处 + 在后面补一处），单点替换做不到。

本轮的变异重点全在**「静默」这个失败形状**上 —— 那些不报错、不飘红、数字只是悄悄变少
或悄悄变多的退化。它们才是真正需要护栏的：
  · 单位少乘/多乘 100（两种都试，因为两个源的单位**相反**）
  · 限流被读成「没数据」（只把限流识别拆掉、只把记账拆掉，两种都试）
  · 退避形同虚设 / 节流上限比间隔还小（调大间隔成了空操作）
  · 硬编码端口回归（端口动态分配，硬编码会指向一个空端口甚至别的进程）
  · 「整条链路不通」被说成「这些币没有链上数据」
  · 位置类指纹混进结构性交叉（两个不同范畴的东西比大小）
  · 链条证据去改分级（无样本改判据）
  · 前端用动态 i18n 键（绕过「键必须两本都有」的静态扫描）

运行：.venv/Scripts/python.exe tests/_mutate_v172.py
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MUT = os.path.join(os.environ.get("TEMP") or "/tmp", "v172mut")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

ONCHAIN = "src/onchain.py"
SCANNER = "src/scanner.py"
MONSTER = "src/square_monster.py"
ROWS = "frontend/src/components/MarketRows.tsx"
VIEW = "frontend/src/views/MarketsView.tsx"
LOC = "frontend/src/i18n/locales.ts"
MOCK = "frontend/src/preview/mock.ts"

# 每条：(说明, [(文件, 旧, 新), …])
MUTATIONS = [
    # ── 单位归一（坑 1）：两个源的方向相反，所以两种错法都要试 ──
    ("GoPlus 的 percent 不再 ×100（小数比例被当百分数，静默差 100 倍）",
     [(ONCHAIN, "return None if x is None else round(x * 100.0, 4)", "return x")]),
    ("RugCheck 的 pct 被多乘 100（本来就是百分数，静默差 100 倍）",
     [(ONCHAIN, '_f(h.get("pct")), False)', '_pct(h.get("pct")), False)')]),

    # ── 先剔销毁地址 / LP 池（坑 2）──
    ("销毁地址不再剔除（CAKE 会被读成「单一持有人控盘」，结论反过来）",
     [(ONCHAIN, "    if a in _BURN_ADDRS:\n        return True", "    if False:\n        return True")]),
    ("全部被剔除时返回 0 而不是 None（0 会被读成「极其分散」）",
     [(ONCHAIN, "        return None, None, raw_top1, excluded",
                "        return 0.0, 0.0, raw_top1, excluded")]),

    # ── 限流识别（本轮新修的核心）──
    ("限流信号不再识别（HTTP 200 + 没有 result 被读成「这个币没数据」）",
     [(ONCHAIN, '    if str(code) in ("4029", "429") or "too many requests" in msg.lower():',
                "    if False:")]),
    ("识别了限流但**不记账**（计数与退避都不置 → 界面上依然静默）",
     [(ONCHAIN,
       '        # 走到下面那句就会被读成「这个币没有链上数据」。\n'
       '        _note_throttle("goplus")\n'
       '        return None',
       '        # 走到下面那句就会被读成「这个币没有链上数据」。\n'
       '        return None')]),
    ("退避期间照旧发请求（退避等于没做）",
     [(ONCHAIN, '    if kind == "goplus":\n        return time.time() >= _goplus_backoff_until',
                '    if kind == "goplus":\n        return True')]),
    ("节流休眠上限退回写死的 2.0（间隔调到 5.5s 就成了空操作）",
     [(ONCHAIN, "        time.sleep(min(wait, _MAX_THROTTLE_SLEEP))",
                "        time.sleep(min(wait, 2.0))")]),
    ("GoPlus 间隔退回 0.12s（实测第 11 次即限流）",
     [(ONCHAIN, "GOPLUS_MIN_GAP = 5.5", "GOPLUS_MIN_GAP = 0.12")]),

    # ── 路由：动态端口 ──
    ("混合端口退回硬编码 7899（端口被占用时指向空端口，整层静默全空）",
     [(ONCHAIN,
       '        p = int(proxy_kernel.mixed_port() or 0)\n'
       '        return f"http://127.0.0.1:{p}" if p else ""',
       '        return "http://127.0.0.1:7899"')]),

    # ── 缓存损坏不得拖垮链路 ──
    ("损坏缓存不再吞掉（外部数据坏掉会连坐雷达）",
     [(ONCHAIN, "    except Exception:\n        pass\n\n\ndef _save_cache() -> None:",
                "    except Exception:\n        raise\n\n\ndef _save_cache() -> None:")]),

    # ── 交叉判定：阈值与中间档 ──
    ("判据阈值改成 50（不再是实测分布定的 60）",
     [(ONCHAIN, "OC_TOP10_HIGH = 60.0", "OC_TOP10_HIGH = 50.0")]),
    ("中间档硬判成「链上佐证」（不判中间 = 不编故事）",
     [(ONCHAIN, '    return {"code": "neutral", "state": "中性", "top1_pct": t1, "top10_pct": t10,',
                '    return {"code": "confirm", "state": "中性", "top1_pct": t1, "top10_pct": t10,')]),

    # ── 只有结构性指纹参与交叉 ──
    ("位置类指纹（空头付钱）混进结构性名单（两个范畴比大小）",
     [(SCANNER, '_MANIP_STRUCT = ("无现货", "合约独大", "换手畸高")',
                '_MANIP_STRUCT = ("无现货", "合约独大", "换手畸高", "空头付钱")')]),

    # ── 接线与旁路纪律 ──
    ("链上派发没接进扫描",
     [(SCANNER, "    _onchain_dispatch(pool_syms)\n"
                "    # 只读缓存（零网络）。**必须在逐币循环之前读一次**而不是每行读一次：",
                "    # 只读缓存（零网络）。**必须在逐币循环之前读一次**而不是每行读一次：")]),
    ("派发被挪到循环**之后**（这一轮末尾永远填不上读数）",
     [(SCANNER, "    _onchain_dispatch(pool_syms)\n"
                "    # 只读缓存（零网络）。**必须在逐币循环之前读一次**而不是每行读一次：",
                "    # 只读缓存（零网络）。**必须在逐币循环之前读一次**而不是每行读一次："),
      (SCANNER, "    _ts_persist(_ts_rows)",
                "    _onchain_dispatch(pool_syms)\n    _ts_persist(_ts_rows)")]),
    ("payload 不下发链上体检快照（「通没通」重新变成不可见）",
     [(SCANNER, '        "onchain": onchain_health(),', '        "onchain": None,')]),
    ("体检骨架缺字段（前端会静默少显示一块）",
     [(SCANNER,
       '             "goplus_backoff_sec": 0, "rug_backoff_sec": 0, "refreshing": False,',
       '             "goplus_backoff_sec": 0, "refreshing": False,')]),
    ("「整条链路不通」被说成「这个币没有链上数据」（替数据源背锅）",
     [(SCANNER, '    if not measured:\n        cell["state"] = "source_down"',
                '    if False:\n        cell["state"] = "source_down"')]),
    ("旁路格不再吞异常（脏输入会打断雷达主路径）",
     [(SCANNER,
       '    try:\n'
       '        cell = onchain.row_cell(reading, [f for f in (manip_flags or [])\n'
       '                                         if f in _MANIP_STRUCT])\n'
       '    except Exception:\n'
       '        return {}',
       '    try:\n'
       '        cell = onchain.row_cell(reading, [f for f in (manip_flags or [])\n'
       '                                         if f in _MANIP_STRUCT])\n'
       '    except Exception:\n'
       '        raise')]),

    # ── 妖币控盘轴：只加证据 ──
    ("链上证据去改控盘度**分级**（新数据无样本改判据）",
     [(MONSTER, '    chain = row.get("onchain") if isinstance(row.get("onchain"), dict) else {}',
                '    chain = row.get("onchain") if isinstance(row.get("onchain"), dict) else {}\n'
                '    if chain.get("state") == "warn":\n'
                '        grade, color = "链上预警", (246, 70, 93)')]),

    # ── 前端契约 ──
    ("前端改用动态 i18n 键（绕过「用到的键必须两本字典都有」的静态扫描）",
     [(ROWS,
       '    case "warn": return t("markets.onchain.warn");\n'
       '    case "confirm": return t("markets.onchain.confirm");\n'
       '    case "refute": return t("markets.onchain.refute");\n'
       '    case "clean": return t("markets.onchain.clean");\n'
       '    case "neutral": return t("markets.onchain.neutral");\n'
       '    case "source_down": return t("markets.onchain.sourceDown");\n'
       '    default: return t("markets.onchain.unknown");',
       '    default: return t(`markets.onchain.${state}`);')]),
    ("前端直接渲染后端下发的中文 label（英文界面串中文）",
     [(ROWS, "              {ocLabelText}", "              {ocCell.label}")]),
    ("体检「未通」档不再红标（失效状态看起来像正常）",
     [(VIEW,
       '                <span className="pill pill-red text-[10.5px]" title={t("markets.onchainDownTip", {',
       '                <span className="pill pill-dim text-[10.5px]" title={t("markets.onchainDownTip", {')]),
    ("i18n 只补中文（en 少一个键 → 英文界面显示裸 key）",
     [(LOC, '  "markets.onchainDispNo": "not dispatched",\n', '')]),
    ("mock 不再演示 confirm 档（预览里看不到那一档）",
     [(MOCK, 'state: "confirm",', 'state: "clean",')]),
    ("链上 pill 的守卫变量退回不存在的 `oc`（渲染期 ReferenceError，只有 tsc 抓得到）",
     [(ROWS, "{ocCell && (", "{oc && (")]),
]


def prepare():
    if os.path.isdir(MUT):
        shutil.rmtree(MUT)
    os.makedirs(MUT)
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(MUT, "src"))
    shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(MUT, "tests"))
    os.makedirs(os.path.join(MUT, "frontend"))
    shutil.copytree(os.path.join(ROOT, "frontend", "src"), os.path.join(MUT, "frontend", "src"))


def run():
    r = subprocess.run([PY, os.path.join(MUT, "tests", "test_v172_onchain.py")],
                       capture_output=True, text=True, cwd=MUT, timeout=600)
    out = (r.stdout or "") + (r.stderr or "")
    fails = [ln.strip()[2:] for ln in out.splitlines() if ln.strip().startswith("- ")]
    return r.returncode, out, fails


def apply_edits(edits):
    """先把所有锚点验一遍（全部 count==1 才动手），再逐个替换。"""
    for rel, old, _new in edits:
        s = open(os.path.join(MUT, rel), encoding="utf-8", newline="").read()
        if s.count(old) != 1:
            return False, f"{rel}: 锚点命中 {s.count(old)} 次"
    for rel, old, new in edits:
        p = os.path.join(MUT, rel)
        s = open(p, encoding="utf-8", newline="").read()
        open(p, "w", encoding="utf-8", newline="").write(s.replace(old, new, 1))
    return True, ""


def main():
    prepare()
    rc, out, _ = run()
    baseline_ok = rc == 0 and "全部通过" in out
    print(f"基线（未变异）: rc={rc} 全部通过={'全部通过' in out}")
    if not baseline_ok:
        print("⚠️ 基线就不通过，变异验证无意义")
        print(out[-3000:])
        return 2
    bad = []
    for i, (name, edits) in enumerate(MUTATIONS, 1):
        prepare()
        ok, why = apply_edits(edits)
        if not ok:
            print(f"[{i:2d}] 跳过（{why}）：{name}")
            bad.append(name)
            continue
        rc, out, fails = run()
        caught = rc != 0 or "全部通过" not in out
        print(f"[{i:2d}] {'✅ 转红' if caught else '❌ 没红'}  {name}")
        if caught and fails:
            print(f"       触发：{fails[0][:100]}")
        if not caught:
            bad.append(name)
    print("\n" + "=" * 62)
    print(f"有效 {len(MUTATIONS) - len(bad)} / {len(MUTATIONS)}")
    if bad:
        print("未生效的变异：")
        for b in bad:
            print("  -", b)
    shutil.rmtree(MUT, ignore_errors=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
