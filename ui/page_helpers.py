"""页面公共工具：错误降级装饰器 + 自检行渲染。

- safe_page(label)：装饰各 render_xxx，捕获未预期异常并降级为 st.error，
  避免 traceback 直接甩给运维人员（对应改进项 5）。
- diag_row(label, ok, detail, key)：自检页单项结果行，带 data-testid，
  供 Playwright/自检脚本精确定位，不再靠全文正则（改进项 3）。
"""
from __future__ import annotations

import functools
import html as _html


def safe_page(label: str):
    """页面级错误降级：render 函数抛异常时显示一行可读原因，不冒泡成 stException。"""

    def _deco(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 页面层兜底，不中断运行
                import streamlit as st
                st.error(f"{label}加载异常：{exc}")
                st.caption("如反复出现请到「系统自检」页排查，或联系运维。")

        return _wrapper

    return _deco


def diag_row(label: str, ok: bool, detail: str, key: str) -> None:
    """自检页单项结果行：✅/❌ + 标签 + 细节，包裹 data-testid 便于定位。"""
    import streamlit as st

    flag = "✅" if ok else "❌"
    safe_label = _html.escape(label)
    safe_detail = _html.escape(detail)
    st.markdown(
        f'<div data-testid="diag-check-{key}">'
        f"{flag} <b>{safe_label}</b> ｜ {safe_detail}"
        "</div>",
        unsafe_allow_html=True,
    )