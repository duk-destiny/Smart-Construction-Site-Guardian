"""管理端控制台门面（Phase 0）：page_admin 的全部数据/写操作入口。

连接托管、PDF 消毒+魔数校验、评测文件解析等从 UI 层下沉至此；
权限校验仍由各业务 Service 承担（本模块不重复造轮子）。
"""
from __future__ import annotations

import json
import os

from core.config import shared_config
from core.evidence import sanitize_filename
from core.logging import get_logger
from core.paths import data_path
from services.db import scoped

log = get_logger(__name__)


# ---------- 知识库 ----------

def import_pdf(user_id: str | None, uploaded) -> dict:
    """规范 PDF 导入：消毒文件名 → 魔数/大小校验 → 解析入库 → 审计。"""
    from core.upload_guard import check_upload
    from services.kb_admin import KbAdmin
    from services.task_entry import upload_limits
    data = uploaded.getvalue()
    ok, err = check_upload(data, uploaded.name, upload_limits())
    if not ok:
        return {"ok": False, "error": err}
    os.makedirs(data_path("kb"), exist_ok=True)
    path = os.path.join(data_path("kb"),
                        sanitize_filename(uploaded.name, fallback="spec.pdf"))
    with open(path, "wb") as f:
        f.write(data)
    with scoped() as conn:
        res = KbAdmin(conn).import_pdf(path, user_id or "admin")
        if res.get("ok"):
            from services.audit_service import AuditService
            AuditService(conn).append(
                user_id, "import_pdf",
                {"filename": os.path.basename(path), "chunks": res["chunks"]})
    return res


def kb_docs() -> list[dict]:
    """知识库已导入文档列表（API 用）。"""
    from services.kb_admin import KbAdmin
    with scoped() as conn:
        return [dict(r) for r in KbAdmin(conn).list_docs()]


# ---------- 台账 / 审计 / 反馈 ----------

def hazard_summary_rows(limit: int = 100) -> list[dict]:
    with scoped() as conn:
        rows = conn.execute(
            "SELECT task_id, risk_level, hazard_desc, created_at "
            "FROM v_task_summary WHERE hazard_desc IS NOT NULL "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def audit_rows(limit: int = 200) -> list[dict]:
    with scoped() as conn:
        rows = conn.execute(
            "SELECT user_id, action, detail_json, created_at FROM audit_logs "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def audit_csv(start: str | None = None, end: str | None = None) -> tuple[str, int]:
    from services.audit_service import AuditService
    with scoped() as conn:
        return AuditService(conn).export_csv(start=start, end=end)


def feedback_samples(limit: int = 500) -> list[dict]:
    from services.task_service import TaskService
    from core.paths import resolve
    with scoped() as conn:
        rows = [dict(r) for r in TaskService(conn).list_feedback_samples(limit)]
    # 展示用绝对路径（DB 存相对项目根）；磁盘缺失时原样带回由 UI 判空
    for r in rows:
        if r.get("image_path"):
            r["image_abs"] = resolve(r["image_path"])
    return rows


def review_feedback(feedback_id: str, status: str, user_id: str | None) -> None:
    from services.task_service import TaskService
    with scoped() as conn:
        TaskService(conn).review_feedback_sample(feedback_id, status,
                                                 user_id=user_id)


def update_feedback_corrections(feedback_id: str, corrections: list[dict],
                                user_id: str | None) -> None:
    from services.task_service import TaskService
    with scoped() as conn:
        TaskService(conn).update_feedback_corrections(
            feedback_id, corrections, user_id=user_id)


def feedback_csv_text() -> str:
    from services.task_service import TaskService
    with scoped() as conn:
        return TaskService(conn).feedback_csv()


# ---------- 告警 ----------

def alarm_events(limit: int = 500) -> list[dict]:
    from services.task_service import TaskService
    from core.paths import resolve
    with scoped() as conn:
        rows = [dict(r) for r in TaskService(conn).list_alarm_events(limit)]
    for r in rows:
        if r.get("image_path"):
            r["image_abs"] = resolve(r["image_path"])
    return rows


def update_alarm_event(alarm_id: str, status: str, user_id: str | None) -> None:
    from services.task_service import TaskService
    with scoped() as conn:
        TaskService(conn).update_alarm_event(alarm_id, status, user_id=user_id)


def alarm_detail(alarm_id: str) -> dict | None:
    """单条告警详情（API 用）；不存在返回 None。"""
    from services.task_service import TaskService
    from core.paths import resolve
    with scoped() as conn:
        row = TaskService(conn).alarms.get_by_id(alarm_id)
    if row is None:
        return None
    d = dict(row)
    if d.get("image_path"):
        d["image_abs"] = resolve(d["image_path"])
    return d


def convert_alarm_to_order(alarm_id: str, user_id: str | None) -> str:
    from services.dispatch_service import DispatchService
    with scoped() as conn:
        return DispatchService(conn).convert_alarm_to_order(alarm_id, user_id)


def notification_logs(limit: int = 200) -> list[dict]:
    from services.task_service import TaskService
    with scoped() as conn:
        return [dict(r) for r in TaskService(conn).list_notification_logs(limit)]


# ---------- 数据清理 ----------

def clear_all_data(user_id: str | None, confirmation: str) -> dict:
    from services.task_service import TaskService
    with scoped() as conn:
        return TaskService(conn).clear_all_data(user_id, confirmation)


# ---------- 周报 ----------

def weekly_report(start: str, end: str, user_id: str | None) -> dict:
    """生成周报；file_path 解析为绝对路径供 UI 直接下载。"""
    from services.report_service import WeeklyReportService
    from core.paths import resolve
    with scoped() as conn:
        res = WeeklyReportService(conn).generate(start, end, user_id=user_id)
    if res.get("data", {}).get("file_path"):
        res["data"]["file_path"] = resolve(res["data"]["file_path"])
    return res


# ---------- 模型注册/切换 ----------

def model_families() -> list[dict]:
    from services.model_service import ModelService
    with scoped() as conn:
        return [dict(m) for m in ModelService(conn).list_models()]


def active_model(name: str) -> dict | None:
    from services.model_service import ModelService
    with scoped() as conn:
        m = ModelService(conn).active_model(name)
    return dict(m) if m is not None else None


def switch_model(name: str, model_id: str, user_id: str | None) -> None:
    from services.model_service import ModelService
    with scoped() as conn:
        ModelService(conn).switch(name, model_id)
        from services.audit_service import AuditService
        AuditService(conn).append(user_id, "switch_model",
                                  {"name": name, "model_id": model_id})


def register_model(name: str, version: str, path: str, data_yaml: str | None,
                   imgsz: int, mAP50: float | None, mAP50_95: float | None,
                   notes: str | None, user_id: str | None) -> None:
    """复训产物注册（active=0 不顶替）+ auto_register_model 审计。"""
    from services.model_service import ModelService
    with scoped() as conn:
        ms = ModelService(conn)
        ms.register(name=name, version=version, path=path, data_yaml=data_yaml,
                    imgsz=imgsz, mAP50=mAP50, mAP50_95=mAP50_95,
                    notes=notes, active=False)
        from services.audit_service import AuditService
        AuditService(conn).append(user_id, "auto_register_model",
                                  {"name": name, "version": version,
                                   "path": path})


# ---------- 用户治理 ----------

def list_users() -> list[dict]:
    from dao.models import UserDAO
    with scoped() as conn:
        return [dict(u) for u in UserDAO(conn).list_all()]


def create_user(actor_user_id: str | None, username: str, password: str,
                role: str, must_change_password: bool = True) -> dict:
    from services.auth_service import AuthService
    with scoped() as conn:
        return AuthService(conn).create_user(actor_user_id, username, password,
                                             role,
                                             must_change_password=must_change_password)


def admin_reset_password(actor_user_id: str | None, target_user_id: str,
                         new_password: str) -> dict:
    from services.auth_service import AuthService
    with scoped() as conn:
        return AuthService(conn).admin_reset_password(
            actor_user_id, target_user_id, new_password)


def set_user_disabled(actor_user_id: str | None, target_user_id: str,
                      disabled: bool) -> dict:
    from services.auth_service import AuthService
    with scoped() as conn:
        return AuthService(conn).set_user_disabled(actor_user_id,
                                                   target_user_id, disabled)


# ---------- 模型评估摘要（文件解析下沉）----------

def eval_summary_rows() -> list[dict]:
    """解析 data/eval/model_eval.json 为展示行；缺失/损坏返回空表。"""
    eval_path = data_path("eval", "model_eval.json")
    if not os.path.exists(eval_path):
        return []
    try:
        with open(eval_path, encoding="utf-8") as f:
            eval_data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning(f"评测文件解析失败: {exc}")
        return [{"error": "评测文件解析失败"}]
    rows: list[dict] = []
    for model_name, model_data in (eval_data.get("models") or {}).items():
        if model_data and isinstance(model_data.get("results"), list):
            versioned = {"?": model_data}
        else:
            versioned = model_data or {}
        for ver, ver_data in versioned.items():
            results = (ver_data.get("results") or []) if isinstance(ver_data, dict) else []
            for result in results:
                for cls in result.get("classes") or []:
                    rows.append({
                        "场景": model_name,
                        "版本": ver,
                        "口径": ("线上一致" if result.get("role") == "configured"
                                 else "扫描参考"),
                        "置信度阈值": result.get("conf_threshold"),
                        "类别": cls.get("label") or cls.get("class"),
                        "TP": cls.get("tp", 0),
                        "FP": cls.get("fp", 0),
                        "FN": cls.get("fn", 0),
                        "Precision": round(cls.get("precision", 0.0), 3),
                        "Recall": round(cls.get("recall", 0.0), 3),
                        "F1": round(cls.get("f1", 0.0), 3),
                    })
    return rows


def notify_demo_mode_default() -> bool:
    return bool((shared_config().get("notify") or {}).get("demo_mode", False))


def notify_status() -> dict:
    """推送通道状态（不回显 webhook URL，防密钥经 API 泄露）。"""
    conf = shared_config().get("notify") or {}
    return {
        "enabled": bool(conf.get("enabled", False)),
        "demo_mode": bool(conf.get("demo_mode", False)),
        "channel": conf.get("channel", "generic"),
        "webhook_configured": bool(str(conf.get("webhook_url") or "").strip()),
    }


def notify_test_push() -> dict:
    """管理端测试推送（demo_mode 时捕获到 mock_capture.jsonl）。"""
    from services.notify_service import NotificationService
    return NotificationService().test_push()


def mock_capture_tail(n: int = 10) -> list[dict]:
    """演示模式捕获的最近 n 条推送 payload（无文件返回空表）。"""
    cap_path = data_path("mock_capture.jsonl")
    if not os.path.exists(cap_path):
        return []
    out: list[dict] = []
    try:
        with open(cap_path, "r", encoding="utf-8") as f:
            lines = f.read().strip().splitlines()[-n:]
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"raw": line})
    except OSError as exc:
        log.warning(f"mock_capture 读取失败: {exc}")
    return out
