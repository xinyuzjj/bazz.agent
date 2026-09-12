"""v1.5.43 回归测试：模型选择三处改造（用户反馈）。

① 对话框模型下拉只显示「大模型 LLM 配置里选择了的」模型（主模型 + 备用 fallback + aux 槽位），
   不再把「拉取模型」拉回的全量目录（几百个）塞进下拉；
② 设置页主模型改下拉选择（原 datalist 几百个模型基本选不了）；
③ 备用模型改「下拉添加 + 已选 chip」，不再铺成一整墙 chip。

离线、纯源码扫描，不导入应用、不联网。
运行：python tests/test_v1543_model_pickers.py  或  pytest tests/test_v1543_model_pickers.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_chat_dropdown_only_settings_models():
    """ChatView 的 loadAvailableModels 不得再拉全量目录 —— 只合并设置里选了的模型。"""
    src = (ROOT / "frontend" / "src" / "views" / "ChatView.tsx").read_text(encoding="utf-8-sig")
    fn_start = src.index("const loadAvailableModels")
    fn_end = src.index("loadAvailableModels();", fn_start)
    fn = src[fn_start:fn_end]
    assert "llmModels" not in fn, "对话框模型下拉仍在调用 api.llmModels 拉全量目录（v1.5.43 回归）"
    # 必须合并的三类来源：主模型、备用 fallback、aux 槽位
    assert "backup_models" in fn, "对话框模型下拉未合并备用 fallback 模型"
    assert "aux" in fn, "对话框模型下拉未合并 aux 槽位模型"


def test_settings_main_model_is_dropdown():
    """设置页主模型必须是 <select> 下拉；禁止再用 datalist（几百个模型选不了）。"""
    src = (ROOT / "frontend" / "src" / "views" / "SettingsView.tsx").read_text(encoding="utf-8-sig")
    assert "datalist" not in src, "SettingsView 仍在用 datalist 作模型选择（v1.5.43 回归）"
    assert 'list="llm-model-list"' not in src, "aux 槽位仍引用已删除的 llm-model-list"
    # 主模型 select：取 t("settings.mainModel") 标签后紧跟的应是 select
    anchor = src.index('t("settings.mainModel")')
    seg = src[anchor:anchor + 400]
    assert "<select" in seg, "主模型未改成 <select> 下拉"


def test_settings_backup_models_dropdown_add():
    """备用模型必须是「下拉添加 + 已选 chip」：存在添加用 <select>，且不再把 modelList 全量渲染成按钮墙。"""
    src = (ROOT / "frontend" / "src" / "views" / "SettingsView.tsx").read_text(encoding="utf-8-sig")
    anchor = src.index('t("settings.backupFallback")')
    seg = src[anchor:src.index("aux 任务细分模型槽", anchor)]
    assert "backupAddPh" in seg, "备用模型缺「下拉添加」选择器"
    assert ".map((m: string)" in seg and "backup_models ?? []).filter" in seg, "已选备用 chip 缺失或不可移除"
    # 旧的「目录全量铺开」按钮墙：把 modelList（或 presets 兜底）直接 map 成按钮
    assert "modelList : (prov?.presets ?? [])).map" not in seg, "备用模型仍在把全量目录铺成按钮墙（v1.5.43 回归）"


def test_i18n_keys_bilingual():
    """新 i18n 键中英双语齐全，旧键 backupHint 已清理。"""
    src = (ROOT / "frontend" / "src" / "i18n" / "locales.ts").read_text(encoding="utf-8-sig")
    for k in ("settings.backupAddPh", "settings.backupRemoveTip"):
        assert src.count(f'"{k}"') == 2, f"i18n {k} 需 zh/en 双语齐全"
    assert "backupHint" not in src, "废弃 i18n 键 settings.backupHint 未清理"


# ---------------- runner ----------------

def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            fails += 1
            import traceback
            print(f"  FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
