"""三级合规判定（B1）：将检测结果按类别严重度归并为 合规/警告/不合规。

数据驱动：严重度映射以"项目隐患键"为键，可随白名单/场景需求扩展（C2）。
- critical（不合规/红）：直接危及人身或引发火灾的高危项；
- warning（警告/黄）：需关注但非即时高危项；
- safe（合规/绿）：正向安全信号（已佩戴安全帽/反光衣、人员在场）。

整体状态取所有检测项中的最高严重度；无任何检测项视为合规（无违规目标）。
"""
from __future__ import annotations

from core.yolo_adapter import COCO_CN
from core.yolo_engine import WHITELIST_CN

# 类别 → 严重度默认映射（critical=不合规, warning=警告, safe=合规）
# 可被 config.yaml 的 compliance.severity（分类→级别）数据驱动覆盖（C2）。
_SEVERITY_DEFAULT: dict[str, str] = {
    # 高危·不合规（红）
    "spark": "critical", "smoke": "critical", "no_helmet": "critical",
    "face_shield": "critical", "extinguisher": "critical",
    "load_object_tilted": "critical",
    # 需关注·警告（黄）
    "flammable": "warning", "no_vest": "warning", "load_object": "warning",
    # 安全·合规（绿）
    "helmet": "safe", "vest": "safe", "person": "safe",
}

# 运行时合并后的严重度表（_build_severity 在首次 evaluate 时惰性填充）
SEVERITY: dict[str, str] = dict(_SEVERITY_DEFAULT)


def _build_severity() -> None:
    """合并 config.yaml 中 compliance.severity 覆盖项（数据驱动，C2）。"""
    try:
        from core.config import ConfigLoader
        override = ConfigLoader().get("compliance.severity") or {}
        if isinstance(override, dict):
            for k, v in override.items():
                if v in ("critical", "warning", "safe"):
                    SEVERITY[k] = v
    except Exception:  # noqa: BLE001 配置缺失则使用默认
        pass

# 严重度 → 三级合规文案
LEVEL_LABEL = {"critical": "不合规", "warning": "警告", "safe": "合规"}
# 三级合规 → 展示顺序（数值越大越严重），用于取最高
LEVEL_RANK = {"safe": 0, "warning": 1, "critical": 2}
# 三级合规 → 主题色（B1 红/黄/绿）
LEVEL_COLOR = {"critical": "#e53935", "warning": "#fbc02d", "safe": "#43a047"}


def _label(cls: str) -> str:
    return WHITELIST_CN.get(cls, COCO_CN.get(cls, cls))


def evaluate(detections: list[dict]) -> dict:
    """对一帧检测结果做三级合规研判。

    Args:
        detections: [{"cls","conf","bbox":[cx,cy,w,h]}, ...]
    Returns:
        {
          "status": "合规"|"警告"|"不合规",
          "level":  "safe"|"warning"|"critical",
          "color":  "#...",
          "violations": [{"cls","conf","label","severity","bbox"}],  # 非 safe 项
          "safe":      [{"cls","conf","label","bbox"}],             # 安全项
          "reasons":   [str, ...],   # 给处置/提示用的人类可读说明
        }
    """
    _build_severity()
    if not detections:
        return {
            "status": "合规", "level": "safe", "color": LEVEL_COLOR["safe"],
            "violations": [], "safe": [],
            "reasons": ["未检出违规目标，现场状况良好"],
        }

    violations: list[dict] = []
    safe_items: list[dict] = []
    top_level = "safe"
    for d in detections:
        cls = d.get("cls")
        sev = SEVERITY.get(cls, "warning")  # 白名单外未知类按警告保守处理
        item = {
            "cls": cls, "conf": d.get("conf", 0.0),
            "label": _label(cls), "severity": sev,
            "bbox": d.get("bbox", [0, 0, 0, 0]),
        }
        if sev == "safe":
            safe_items.append(item)
        else:
            violations.append(item)
        if LEVEL_RANK[sev] > LEVEL_RANK[top_level]:
            top_level = sev

    # 生成原因说明
    reasons: list[str] = []
    for v in violations:
        if v["severity"] == "critical":
            reasons.append(f"【不合规】{v['label']}：存在即时高危风险，须立即处置")
        else:
            reasons.append(f"【警告】{v['label']}：需现场关注并尽快整改")
    for s in safe_items:
        reasons.append(f"【合规】{s['label']}")

    return {
        "status": LEVEL_LABEL[top_level],
        "level": top_level,
        "color": LEVEL_COLOR[top_level],
        "violations": violations,
        "safe": safe_items,
        "reasons": reasons,
    }
