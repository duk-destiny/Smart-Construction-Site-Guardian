"""通用 UI 组件（D1/B1 复用）：三级合规状态横幅，统一上传态与实时态展示。"""

from __future__ import annotations

import streamlit as st


def compliance_banner(comp: dict, risk_level: str | None = None,
                      subtitle: str = "") -> None:
    """渲染三级合规状态横幅（红=不合规 / 黄=警告 / 绿=合规）。

    Args:
        comp: core.compliance.evaluate 的返回（含 status/level/color）。
        risk_level: 可选的风险等级（低/一般/较大/重大），与三级合规并列展示。
        subtitle: 可选附加文案（如检测目标数、耗时）。
    """
    parts = [f"合规状态：{comp.get('status', '—')}"]
    if risk_level:
        parts.append(f"风险等级：{risk_level}")
    if subtitle:
        parts.append(subtitle)
    text = " ｜ ".join(parts)
    st.markdown(
        f"""<div style="background:{comp.get('color', '#43a047')};color:#fff;
        padding:10px 16px;border-radius:8px;font-size:18px;font-weight:700;
        margin-bottom:8px;">{text}</div>""",
        unsafe_allow_html=True)


def severity_summary(detections: list[dict]) -> str:
    """将检测项压缩为一句话摘要，供横幅副标题使用。"""
    if not detections:
        return "检测目标 0 项"
    return f"检测目标 {len(detections)} 项"
