"""v1.6.2 妖币雷达「选币方法」修复测试 —— 零网络、隔离临时库。

背景（安装版真实战绩 22 单已关单：19 dump / 3 moon，胜率 13.6%，模拟 -1420.5U）：
根因是**触发层方向无关** —— 旧实现 11 条触发规则里 7 条用 abs() 或涨跌双向，
命中率最高的是「24h 振幅 ≥15%」（22 单里 19 单命中 = 86%）。
雷达实际是个**波动率探测器**：谁当天暴动得最厉害就登记谁，
而暴动之后的币正是均值回归概率最高的（dump 组登记时 24h 涨幅中位 18.1%、日振幅中位 27.5%）。

覆盖：
1. _trigger_hit 方向化：机会型必须顺向；振幅/量能不再单独构成机会
2. _radar_score 去绝对值 + 追高惩罚 + 振幅反向
3. _stage_of：12%~25% 不再算「点火」→ 新增 EXTENDED；SHORT_AMBUSH 必须已破位
4. 分组护栏：EXTENDED 只进 takeoff 组、不进 ignition 组（=不登记进跟踪表）
5. 同币冷却期：关单后 24h 内拒绝重登
6. 源码护栏：触发层不得再出现 abs()
7. 前端 i18n 双语 EXTENDED / SHORT_AMBUSH 键

运行：.venv/Scripts/python.exe tests/test_v162_radar_select.py
"""
import ast
import os
import sys
import tempfile
import time

# 必须在 import 任何 src 模块之前：workspace.py import 时读该 env 决定库位置
os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_test_v162_")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import scanner  # noqa: E402
import state    # noqa: E402

FAILS = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------- 1) 触发层方向化 ----------------

def test_trigger_directional():
    T = scanner._trigger_hit

    hit, rs = T({"amp24": 18.0, "chg24": 0.0}, {})
    check("触发：纯振幅 18%（旧实现必然命中）不再触发", hit is False, str(rs))

    hit, rs = T({"amp24": 30.0, "chg24": 0.0}, {})
    check("触发：振幅 30% 走风险通道（可取确认因子）", hit is True)
    check("触发：振幅 30% 不产生任何顺向依据",
          not any(r.startswith("24h +") for r in rs), str(rs))

    hit, rs = T({"chg24": 15.0, "amp24": 0.0}, {})
    check("触发：24h +15% 命中", hit is True)
    check("触发：24h +15% 记为顺向依据", "24h +15.0%" in rs, str(rs))

    # 旧实现 abs(chg24) >= 10 会让「跌 15%」与「涨 15%」命中同一条
    hit, rs = T({"chg24": -15.0, "amp24": 0.0}, {})
    check("触发：24h -15% 命中（风险侧）", hit is True)
    check("触发：24h -15% 记为向下、**不出现** 24h +",
          "24h -15.0%" in rs and not any(r.startswith("24h +") for r in rs), str(rs))

    hit, rs = T({"jump_L": -5.0, "chg24": 0.0}, {})
    check("触发：|L| 跳跃只认顺向（-5 记为向下）",
          any("跳跃检验" in r and "L=-5" in r for r in rs), str(rs))

    hit, rs = T({"speed5m": -1.2, "accel": True, "chg24": 0.0}, {})
    check("触发：向下加速记为向下（旧实现 abs 命中）",
          any(r.startswith("速度 -") for r in rs), str(rs))

    hit, rs = T({"rvol15": 3.0, "chg24": -2.0}, {})
    check("触发：放量但价在跌 → RVOL 不进顺向依据",
          not any(r.startswith("RVOL") for r in rs), str(rs))

    hit, rs = T({"rvol15": 3.0, "chg24": 2.0}, {})
    check("触发：放量且价在涨 → RVOL 进顺向依据",
          any(r.startswith("RVOL") for r in rs), str(rs))

    hit, rs = T({"chg24": 0.0}, {})
    check("触发：全零输入不命中", hit is False, str(rs))


# ---------------- 2) 评分方向化 ----------------

def test_score_directional():
    S = scanner._radar_score

    def sc(**d):
        base = {"chg24": 0.0, "amp24": 0.0, "rvol_d": 0.0}
        base.update(d)
        t = {"jump_L": 0.0, "speed5m": 0.0, "rvol15": 0.0, "flow": 0.0}
        return S(t, {}, base, False)

    check("评分：启动窗口（+8%）> 追高区（+20%）", sc(chg24=8) > sc(chg24=20),
          f"{sc(chg24=8)} vs {sc(chg24=20)}")
    check("评分：追高区（+13%）> 深度过热（+24%）", sc(chg24=13) > sc(chg24=24),
          f"{sc(chg24=13)} vs {sc(chg24=24)}")
    check("评分：上涨 > 同幅度下跌（旧实现 abs 下相等）", sc(chg24=8) > sc(chg24=-8),
          f"{sc(chg24=8)} vs {sc(chg24=-8)}")
    check("评分：振幅 45% < 振幅 15%（旧实现越大越高）", sc(amp24=45) < sc(amp24=15),
          f"{sc(amp24=45)} vs {sc(amp24=15)}")

    up = S({"jump_L": 5.0, "speed5m": 0.0, "rvol15": 0.0, "flow": 0.0}, {},
           {"chg24": 0.0, "amp24": 0.0, "rvol_d": 0.0}, False)
    dn = S({"jump_L": -5.0, "speed5m": 0.0, "rvol15": 0.0, "flow": 0.0}, {},
           {"chg24": 0.0, "amp24": 0.0, "rvol_d": 0.0}, False)
    check("评分：向上跳跃 > 向下跳跃（旧实现相等）", up > dn, f"{up} vs {dn}")


# ---------------- 3) 阶段判定 ----------------

def test_stage():
    base = {"chg24": 0.0, "chg1h": 0.0, "chg3d": 0.0, "chg30d": 0.0, "pos": 0.5,
            "rvol_d": 1.0, "amp24": 6.0}

    d = dict(base, chg24=18.0, oi_pulse15=8.0, chg1h=1.0)
    check("stage：24h +18% + OI 脉冲 → EXTENDED（旧实现误判 IGNITION）",
          scanner._stage_of(d)[0] == "EXTENDED", str(scanner._stage_of(d)[:2]))
    d = dict(base, chg24=12.0, oi_pulse15=8.0)
    check("stage：12% 边界仍算 IGNITION（追高线是 >12）",
          scanner._stage_of(d)[0] == "IGNITION")

    d = dict(base, breakout20=True, rvol_d=2.5, chg24=2.0)
    check("stage 回归：破位 + 放量 + 涨 2% → IGNITION", scanner._stage_of(d)[0] == "IGNITION")
    d = dict(base, chg24=30.0)
    check("stage 回归：+30% → VERTICAL", scanner._stage_of(d)[0] == "VERTICAL")
    d = dict(base, chg24=12.0, oi_chg24=-8.0)
    check("stage 回归：涨 12% + OI -8% → DISTRIBUTION", scanner._stage_of(d)[0] == "DISTRIBUTION")
    d = dict(base, pos=0.40, chg30d=5.0, chg3d=0.0, rvol_d=2.0)
    check("stage 回归：低位放量价平 → ACCUMULATION", scanner._stage_of(d)[0] == "ACCUMULATION")

    hot = {"chg24": 0.0, "chg1h": 0.5, "chg3d": 0.0, "chg30d": 40.0, "pos": 0.9,
           "funding": 0.004, "funding_peak": 0.004, "taker_ratio": 0.9,
           "rvol_d": 1.0, "amp24": 6.0}
    check("stage：高位滞涨但**未破位** → 不再成立 SHORT_AMBUSH",
          scanner._stage_of(hot)[0] != "SHORT_AMBUSH", str(scanner._stage_of(hot)[:2]))
    check("stage：高位滞涨 + 1h -3%（已破位）→ SHORT_AMBUSH",
          scanner._stage_of(dict(hot, chg1h=-3.0))[0] == "SHORT_AMBUSH")


# ---------------- 4) 分组护栏 ----------------

def test_group_guard():
    src = open(os.path.join(ROOT, "src", "scanner.py"), encoding="utf-8").read()

    line_tk = next((l for l in src.splitlines() if l.strip().startswith("rows_tk = [")), "")
    line_ign = next((l for l in src.splitlines() if l.strip().startswith("rows_ign = [")), "")
    check("分组：EXTENDED 进 takeoff 组（保持可见）", "EXTENDED" in line_tk, line_tk[:90])
    check("分组：EXTENDED **不**进 ignition 组（=不登记跟踪）",
          "EXTENDED" not in line_ign, line_ign[:90])
    # v1.6.3 变更：SHORT_AMBUSH 已移出 ignition 组（真实战绩 3/3 做空全亏，用户定位「绝不做空」）
    # → 本断言随之从「ignition 含三个可下注阶段」改为「含两个」；SHORT_AMBUSH 改由
    #   test_v163_manip_guard.test_short_ambush_not_tradeable 覆盖。
    check("分组：ignition 组只含两个可下注阶段（SHORT_AMBUSH 已于 v1.6.3 移出）",
          all(k in line_ign for k in ("ACCUMULATION", "IGNITION"))
          and "SHORT_AMBUSH" not in line_ign, line_ign[:90])


# ---------------- 5) 源码护栏：触发层不得再出现 abs() ----------------

def test_source_guard():
    path = os.path.join(ROOT, "src", "scanner.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)

    def find(name):
        return next((n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == name), None)

    def abs_args(fn):
        """fn 内所有 abs() 实参的源码片段（注释/文档串不会骗到它）。"""
        out = []
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "abs" and n.args):
                out.append(ast.unparse(n.args[0]))
        return out

    fn = find("_trigger_hit")
    check("AST：找得到 _trigger_hit", fn is not None)
    if fn is not None:
        check("AST：_trigger_hit 内完全不调用 abs()（方向无关的根因）",
              abs_args(fn) == [], str(abs_args(fn)))

    sc = find("_radar_score")
    check("AST：找得到 _radar_score", sc is not None)
    if sc is not None:
        args = abs_args(sc)
        # 只禁「动量类」实参：涨跌幅/跳跃/速度/振幅。funding 用 abs 是合理的（费率本就看幅度）。
        banned = [a for a in args
                  if any(k in a for k in ('"chg24"', '"jump_L"', '"speed5m"', '"amp24"'))]
        check("AST：_radar_score 的动量项不再用 abs()（只认顺向）", banned == [], str(banned))

    check("源码：跳跃项改顺向 max(0.0, ...)", 'max(0.0, t.get("jump_L"' in src)
    check("源码：候选池不再按 |24h 涨跌| 排序",
          'key=lambda s: abs(feats[s]["d"]["chg24"])' not in src)
    check("源码：候选池改按启动窗口优先级排序", "_cand_rank" in src)


# ---------------- 6) 同币冷却期 ----------------

def test_reentry_cooldown():
    sym = "CDTEST%d" % (int(time.time()) % 100000)

    tid = state.radar_track_add(sym, "IGNITION", 1.0)
    check("冷却期：首次登记成功返回 id", bool(tid), repr(tid))

    check("冷却期：pending 中重复登记仍幂等返回同 id",
          state.radar_track_add(sym, "IGNITION", 1.0) == tid)

    check("冷却期：关单成功", state.radar_track_close(tid, "dump", 0.9) is True)

    again = state.radar_track_add(sym, "IGNITION", 1.0)
    check("冷却期：关单后 24h 内拒绝重登（返回空串）", again == "", repr(again))
    pending = [r for r in state.radar_tracks_list("pending") if r["symbol"] == sym]
    check("冷却期：拒绝后确实没有新增 pending 记录", len(pending) == 0)

    # 冷却期外可重登：把 closed_at 往前推 25h
    _shift_closed_at(sym, 25 * 3600)
    again2 = state.radar_track_add(sym, "IGNITION", 1.0)
    check("冷却期：超过 24h 后可以重新登记", bool(again2) and again2 != "")


def _shift_closed_at(sym: str, seconds: float) -> None:
    conn = state._conn_get()
    row = conn.execute("SELECT id FROM radar_tracks WHERE symbol=? AND status='closed' "
                       "ORDER BY closed_at DESC LIMIT 1", (sym,)).fetchone()
    if not row:
        return
    conn.execute("UPDATE radar_tracks SET closed_at=closed_at-? WHERE id=?",
                 (float(seconds), row["id"]))
    conn.commit()


# ---------------- 7) 前端 i18n ----------------

def test_frontend():
    loc = open(os.path.join(ROOT, "frontend", "src", "i18n", "locales.ts"), encoding="utf-8").read()
    check("i18n：markets.stage.EXTENDED 中英双语都有",
          loc.count('"markets.stage.EXTENDED"') == 2, str(loc.count('"markets.stage.EXTENDED"')))
    check("i18n：markets.stage.SHORT_AMBUSH 补齐双语（英文此前缺失）",
          loc.count('"markets.stage.SHORT_AMBUSH"') == 2,
          str(loc.count('"markets.stage.SHORT_AMBUSH"')))

    rows = open(os.path.join(ROOT, "frontend", "src", "components", "MarketRows.tsx"),
                encoding="utf-8").read()
    check("前端：STAGE_META 有 EXTENDED 样式", "EXTENDED:" in rows)


if __name__ == "__main__":
    print("== v1.6.2 妖币选币方法修复单测 ==")
    test_trigger_directional()
    test_score_directional()
    test_stage()
    test_group_guard()
    test_source_guard()
    test_reentry_cooldown()
    test_frontend()
    if FAILS:
        print(f"\n❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print("   -", f)
        sys.exit(1)
    print("\n✅ 全部通过")
