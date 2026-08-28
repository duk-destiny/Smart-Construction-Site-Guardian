"""UI 冒烟测试：确保 app 与 5 个页面模块可导入、渲染函数存在（不依赖 Streamlit 运行时）。"""

import importlib


def test_app_importable():
    import app
    assert hasattr(app, "main")


def test_pages_importable_and_have_render():
    """页面模块均可导入且含 render_* 函数（含实时监测与历史分析）。"""
    mods = {
        "ui.page_login": "render_login",
        "ui.page_upload": "render_upload",
        "ui.page_agents": "render_agents",
        "ui.page_lab": "render_lab",
        "ui.page_report": "render_report",
        "ui.page_admin": "render_admin",
        "ui.page_realtime": "render_realtime",
        "ui.page_history": "render_history",
    }
    for mod_name, fn in mods.items():
        mod = importlib.import_module(mod_name)
        assert hasattr(mod, fn), f"{mod_name} 缺少 {fn}"


def test_core_compliance_and_components():
    """三级合规组件与横幅函数可用。"""
    from core.compliance import evaluate
    from ui.components import compliance_banner, severity_summary
    comp = evaluate([{"cls": "no_helmet", "conf": 0.9, "bbox": [1, 1, 2, 2]}])
    assert comp["level"] == "critical"
    assert compliance_banner is not None
    assert severity_summary([]) == "检测目标 0 项"


def test_kb_admin_importable():
    from services.kb_admin import KbAdmin
    assert hasattr(KbAdmin, "import_pdf")
