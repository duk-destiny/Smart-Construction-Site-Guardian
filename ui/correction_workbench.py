"""可视化逐目标纠偏工作台组件：原图 + 检测框 + 修正框 + 逐目标控件。"""
from __future__ import annotations

import os

import cv2
import streamlit as st

# 白名单（情况1·纯展示常量）：仅作纠偏控件的下拉选项与框上标签，
# 不执行合规判定、不做业务计算。
from core.yolo_engine import WHITELIST, WHITELIST_CN


def _annotate(frame, detections: list[dict], corrections: list[dict]):
    """在 BGR 帧上绘制原始框和人工修正结果。"""
    out = frame.copy()
    for i, det in enumerate(detections):
        fix = corrections[i] if i < len(corrections) else {}
        box = det.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            continue
        cx, cy, w, h = (float(v) for v in box)
        x1, y1 = int(cx - w / 2), int(cy - h / 2)
        x2, y2 = int(cx + w / 2), int(cy + h / 2)
        if fix.get("is_fp"):
            color = (60, 60, 60)
            label = "误报"
        else:
            cls = fix.get("corrected_cls") or det.get("cls")
            color = (33, 150, 243)
            label = WHITELIST_CN.get(cls, cls)
            if fix.get("corrected_cls"):
                color = (67, 160, 71)
                label = f"{label}（人工修正）"
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        cv2.putText(out, label, (x1, max(y1 - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    return out


def render_target_corrections(
    image_path: str | None,
    detections: list[dict],
    corrections: list[dict],
    key_prefix: str,
) -> list[dict]:
    """渲染一张原图与逐目标纠偏控件，返回更新后的 corrections。"""
    frame = None
    if image_path and os.path.exists(image_path):
        frame = cv2.imread(image_path)
    if frame is not None:
        st.image(cv2.cvtColor(
            _annotate(frame, detections, corrections), cv2.COLOR_BGR2RGB),
            caption="蓝色=原始检测；绿色=人工修正；灰色=误报",
            use_container_width=True)
    else:
        st.caption("未找到原图，仅显示文本纠偏控件")

    updated: list[dict] = []
    for i, det in enumerate(detections):
        fix = corrections[i] if i < len(corrections) else {}
        st.caption(
            f"目标 {i + 1}：{det.get('violation_desc') or det.get('cls')} ｜ "
            f"conf {det.get('conf', 0):.2f} ｜ bbox {det.get('bbox')}"
        )
        c1, c2 = st.columns([1, 2])
        with c1:
            is_fp = st.checkbox(
                "误报", value=bool(fix.get("is_fp")),
                key=f"{key_prefix}_fp_{i}")
        with c2:
            corrected = st.selectbox(
                "修正类别", ["保持"] + WHITELIST,
                index=0 if not fix.get("corrected_cls") else WHITELIST.index(
                    fix["corrected_cls"]) + 1,
                key=f"{key_prefix}_cls_{i}")
        updated.append({
            "cls": det.get("cls"),
            "conf": det.get("conf"),
            "bbox": det.get("bbox"),
            "is_fp": is_fp,
            "corrected_cls": None if corrected == "保持" else corrected,
        })
    return updated
