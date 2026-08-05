"""页面5：管理端页（page_admin，仅 admin）。"""
from __future__ import annotations

import json
import os

import streamlit as st
from ui.page_helpers import safe_page

from dao.db import get_conn, init_db
from services.audit_service import AuditService
from services.kb_admin import KbAdmin
from services.model_service import ModelService
from services.permission_service import PermissionError
from services.notify_service import CHANNEL_LABEL, NotificationService
from services.task_service import TaskService
from services.training_service import TrainingService
from ui.correction_workbench import render_target_corrections


@safe_page("管理端")
def render_admin() -> None:
    st.title("🛠 管理端（仅管理员）")
    if st.session_state.get("role") != "admin":
        st.error("无权限访问管理端")
        return

    st.subheader("导入规范 PDF")
    pdf = st.file_uploader("选择规范 PDF", type=["pdf"])
    if pdf and st.button("解析入库"):
        os.makedirs("data/kb", exist_ok=True)
        path = os.path.join("data/kb", pdf.name)
        with open(path, "wb") as f:
            f.write(pdf.getbuffer())
        conn = get_conn()
        init_db(conn)
        res = KbAdmin(conn).import_pdf(path, st.session_state.get("user_id", "admin"))
        if res.get("ok"):
            st.success(f"入库成功，切分 {res['chunks']} 块")
            AuditService(conn).append(st.session_state.get("user_id"), "import_pdf",
                                     {"filename": pdf.name, "chunks": res["chunks"]})
        else:
            st.error(res.get("error", "导入失败"))

    st.divider()
    st.subheader("全量隐患记录")
    conn = get_conn()
    init_db(conn)
    rows = conn.execute(
        "SELECT task_id, risk_level, hazard_desc, created_at FROM v_task_summary "
        "WHERE hazard_desc IS NOT NULL ORDER BY created_at DESC LIMIT 100").fetchall()
    if rows:
        st.dataframe([dict(r) for r in rows])
    else:
        st.caption("暂无记录")

    st.divider()
    st.subheader("操作审计日志")
    logs = conn.execute(
        "SELECT user_id, action, detail_json, created_at FROM audit_logs "
        "ORDER BY created_at DESC LIMIT 200").fetchall()
    if logs:
        st.dataframe([dict(r) for r in logs])
    else:
        st.caption("暂无日志")

    st.divider()
    st.subheader("人工纠偏反馈样本")
    ts = TaskService(conn)
    samples = ts.list_feedback_samples()
    st.metric("纠偏样本数", len(samples))
    if samples:
        st.dataframe([{
            "时间": s["created_at"],
            "任务": s["task_id"],
            "自动风险": s["auto_risk_level"],
            "改判风险": s["corrected_risk_level"],
            "原因": s["reason"],
            "类型": s["feedback_type"],
            "状态": s["status"],
        } for s in samples])
        for sample in samples[:50]:
            st.caption(
                f"{sample['created_at']} ｜ {sample['task_id']} ｜ "
                f"{sample['auto_risk_level'] or '—'} → "
                f"{sample['corrected_risk_level']} ｜ {sample['reason']}"
            )
            status = st.selectbox(
                "审核状态", ["pending", "confirmed", "rejected"],
                index=["pending", "confirmed", "rejected"].index(sample["status"]),
                key=f"feedback_status_{sample['id']}")
            if st.button("提交审核", key=f"feedback_btn_{sample['id']}"):
                ts.review_feedback_sample(
                    sample["id"], status,
                    user_id=st.session_state.get("user_id"))
                st.success("反馈样本审核已更新")
            with st.expander(f"可视化复核：{sample['id']}", key=f"fb_workbench_{sample['id']}"):
                try:
                    detections = json.loads(sample["detection_json"] or "[]")
                except ValueError:
                    detections = []
                try:
                    corrections = json.loads(sample["corrected_labels_json"] or "[]")
                except ValueError:
                    corrections = []
                updated = render_target_corrections(
                    sample["image_path"], detections, corrections,
                    f"admin_fb_{sample['id']}")
                if st.button("保存逐目标修正", key=f"fb_save_{sample['id']}"):
                    ts.update_feedback_corrections(
                        sample["id"], updated,
                        user_id=st.session_state.get("user_id"))
                    st.success("逐目标修正已保存")
        st.download_button(
            "导出纠偏样本 CSV",
            ts.feedback_csv(),
            file_name="feedback_samples.csv",
            mime="text/csv",
        )
    else:
        st.caption("暂无人工改判记录")

    st.divider()
    st.subheader("告警生命周期")
    alarms = ts.list_alarm_events()
    if alarms:
        for alarm in alarms:
            st.caption(
                f"{alarm['created_at']} ｜ {alarm['cls'] or '—'} ｜ "
                f"{alarm['scene_id'] or '—'} ｜ conf {alarm['conf'] or '—'} ｜ "
                f"来源 {alarm['source'] or 'camera'}"
            )
            img_path = alarm["image_path"]
            if img_path and os.path.exists(img_path):
                st.image(img_path,
                         caption=f"告警证据截图：{img_path}",
                         use_column_width=True)
            status = st.selectbox(
                "状态", ["new", "confirmed", "false_alarm", "resolved"],
                index=["new", "confirmed", "false_alarm", "resolved"].index(alarm["status"]),
                key=f"alarm_status_{alarm['id']}")
            if st.button("更新状态", key=f"alarm_btn_{alarm['id']}"):
                ts.update_alarm_event(
                    alarm["id"], status,
                    user_id=st.session_state.get("user_id"))
                st.success("告警状态已更新")
    else:
        st.caption("暂无告警事件")

    st.divider()
    st.subheader("外部推送")
    from core.config import ConfigLoader
    _cfg_demo = bool((ConfigLoader().get("notify") or {}).get("demo_mode", False))
    n_demo = st.toggle("演示模式（无需 webhook）", value=st.session_state.get("notify_demo", _cfg_demo), key="notify_demo_toggle")
    st.session_state["notify_demo"] = n_demo
    notify_svc = NotificationService(demo_mode=n_demo)
    n_enabled = notify_svc.enabled()
    n_channel = notify_svc.channel()
    n_url = notify_svc.webhook_url()
    st.caption(
        f"通道：{CHANNEL_LABEL.get(n_channel, n_channel)}{('（模拟）' if n_demo else '')} ｜ "
        f"启用：{'✅ 是' if n_enabled else '❌ 否'} ｜ "
        f"webhook：{n_url or '未配置'}"
    )
    if not n_demo and (not n_enabled or not n_url):
        st.info("外部推送未启用：请在 config/config.yaml 配置 notify.enabled=true 与 notify.webhook_url，或开启演示模式。")
    if st.button("发送测试推送", key="notify_test"):
        res = notify_svc.test_push()
        if res.get("ok"):
            st.success(f"测试推送成功（{res.get('status')}{'·模拟' if n_demo else ''}）")
        else:
            st.warning(f"测试推送未成功：{res.get('status')} ｜ {res.get('error')}")
    with st.expander("模拟捕获的推送 payload（最近 10 条）"):
        import json as _json, os as _os
        _cap_path = _os.path.join("data", "mock_capture.jsonl")
        if _os.path.exists(_cap_path):
            with open(_cap_path, "r", encoding="utf-8") as _f:
                _rows = _f.read().strip().splitlines()[-10:]
            for _l in reversed(_rows):
                try:
                    st.json(_json.loads(_l))
                except Exception:
                    st.code(_l)
        else:
            st.caption("暂无捕获记录（演示模式下点「发送测试推送」会产生）")
    logs = ts.list_notification_logs()
    st.caption(f"推送留痕（最近 {len(logs)} 条）")
    if logs:
        st.dataframe([{
            "时间": r["created_at"],
            "告警": r["alarm_id"],
            "通道": r["channel"],
            "状态": r["status"],
            "错误": r["error"] or "",
        } for r in logs])
    else:
        st.caption("暂无推送记录")

    st.divider()
    st.subheader("模型评估摘要")
    eval_path = os.path.join("data", "eval", "model_eval.json")
    if os.path.exists(eval_path):
        try:
            with open(eval_path, encoding="utf-8") as f:
                eval_data = json.load(f)
            eval_rows = []
            for model_name, model_data in (eval_data.get("models") or {}).items():
                for result in model_data.get("results") or []:
                    threshold = result.get("conf_threshold")
                    for cls in result.get("classes") or []:
                        eval_rows.append({
                            "模型": model_name,
                            "置信度阈值": threshold,
                            "类别": cls.get("label") or cls.get("class"),
                            "TP": cls.get("tp", 0),
                            "FP": cls.get("fp", 0),
                            "FN": cls.get("fn", 0),
                            "Precision": round(cls.get("precision", 0.0), 3),
                            "Recall": round(cls.get("recall", 0.0), 3),
                            "F1": round(cls.get("f1", 0.0), 3),
                        })
            if eval_rows:
                st.dataframe(eval_rows)
            else:
                st.caption("评测文件无可用结果")
        except (json.JSONDecodeError, OSError):
            st.error("评测文件解析失败")
    else:
        st.caption("暂无评测文件，可运行 scripts/evaluate_models.py 生成")

    st.divider()
    st.subheader("模型版本与回滚")
    ms = ModelService(conn)
    models = ms.list_models()
    if models:
        for m in models:
            active_tag = " ｜ ✅ 当前" if m["active"] else ""
            st.caption(
                f"{m['name']} {m['version']}{active_tag} ｜ "
                f"mAP50 {m['mAP50'] or '—'} ｜ mAP50-95 {m['mAP50_95'] or '—'} ｜ "
                f"{m['path']}"
            )
            if not m["active"] and st.button(
                "切换为当前版本", key=f"switch_model_{m['id']}"):
                ms.switch(m["name"], m["id"])
                AuditService(conn).append(
                    st.session_state.get("user_id"), "switch_model",
                    {"name": m["name"], "version": m["version"], "model_id": m["id"]})
                st.success(f"已切换 {m['name']} 到 {m['version']}")
                st.rerun()
    else:
        st.caption("暂无模型注册记录")

    st.divider()
    st.subheader("复训与模型替换")
    train_svc = TrainingService()
    task = train_svc.status()
    st.caption(f"任务状态：{task.get('phase', 'idle')} ｜ PID {task.get('pid') or '—'}")
    if st.button("刷新任务状态", key="refresh_train_status"):
        st.rerun()

    task_pid = task.get("pid")
    if task.get("phase") in ("preparing", "running") or (
        task_pid and train_svc.alive(task_pid)
    ):
        save_best = st.checkbox(
            "早停后保存当前最佳", value=True, key="early_stop_save")
        if st.button("早停训练并保存", key="btn_early_stop"):
            ok, msg = train_svc.stop()
            if ok and save_best:
                command = task.get("command", "")
                name = "ppe" if "--only ppe" in command else "fire"
                version = task.get("version", "v3")
                run_name = "ppe_s" if name == "ppe" else "fire_s"
                run_dir = os.path.join(
                    "data", "runs_combined", f"{run_name}_ft_{version}")
                ok2, msg2 = train_svc.export_best(name, version, run_dir)
                if ok2:
                    st.success("已早停并导出当前最佳")
                else:
                    st.error(f"导出失败: {msg2}")
            else:
                st.success(msg)
            st.rerun()

    if st.button("生成合并训练集", key="btn_prepare_train"):
        ok, msg = train_svc.start_prepare()
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)

    with st.form("retrain_form"):
        version = st.text_input("新版本号", value="v3", key="retrain_version")
        only = st.selectbox("训练模型", ["全部", "ppe", "fire"], key="retrain_only")
        from_best = st.checkbox("从现有 best.pt 续训", value=True,
                                key="retrain_from_best")
        epochs = st.number_input("epochs", min_value=1, max_value=200,
                                 value=60, step=5, key="retrain_epochs")
        submitted = st.form_submit_button("开始复训")
    if submitted:
        ok, msg = train_svc.start_train(
            from_best=from_best,
            version=version.strip() or "v3",
            only=None if only == "全部" else only,
            epochs=int(epochs))
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)

    log_text = train_svc.tail_log(4000)
    if log_text:
        with st.expander("训练日志（最近 4000 字符）"):
            st.code(log_text)

    result = train_svc.latest_result()
    if result and result.get("ok"):
        st.success("新模型已自动注册，请对比后确认切换")
        for name, r in (result.get("results") or {}).items():
            old = ms.active_model(name)
            existing = [
                m for m in ms.list_models()
                if m["name"] == name and m["version"] == r.get("version")
            ]
            if not existing:
                data_yaml = os.path.join("data", "combined", name, "data.yaml")
                ms.register(
                    name=name, version=r.get("version"), path=r.get("path"),
                    data_yaml=data_yaml if os.path.exists(data_yaml) else None,
                    imgsz=640, mAP50=r.get("mAP50"), mAP50_95=r.get("mAP50_95"),
                    notes="管理端复训自动注册", active=False)
                AuditService(conn).append(
                    st.session_state.get("user_id"), "auto_register_model",
                    {"name": name, "version": r.get("version"),
                     "path": r.get("path")})
                existing = [
                    m for m in ms.list_models()
                    if m["name"] == name and m["version"] == r.get("version")
                ]
            old_map = old["mAP50"] if old else None
            new_map = r.get("mAP50")
            st.caption(
                f"{name}：旧 mAP50 {old_map or '—'} → 新 mAP50 {new_map or '—'}"
            )
            if existing and not existing[0]["active"]:
                if st.button(
                    f"确认切换 {name} 到 {r.get('version')}",
                    key=f"apply_retrain_{name}_{r.get('version')}"):
                    ms.switch(name, existing[0]["id"])
                    AuditService(conn).append(
                        st.session_state.get("user_id"), "switch_model",
                        {"name": name, "version": r.get("version"),
                         "model_id": existing[0]["id"]})
                    st.success(f"已切换 {name}")
                    st.rerun()
            elif existing and existing[0]["active"]:
                st.caption(f"{name}：已是当前版本")

    st.divider()
    st.subheader("清空全部监测数据")
    st.warning(
        "该操作会删除任务、检测记录、工单、反馈样本和告警事件；"
        "账号、审计日志、知识库与模型注册会保留。"
    )
    confirm_text = st.text_input("请输入 RESET 确认清空", key="clear_data_confirm")
    if st.button(
        "下一步：启用清空确认",
        disabled=confirm_text.strip() != "RESET",
        key="clear_data_arm",
    ):
        st.session_state["_clear_armed"] = True
        st.rerun()
    if st.session_state.get("_clear_armed"):
        st.warning("确认清空不可撤销，请再次点击执行")
        if st.button(
            "确认清空全部监测数据",
            type="primary",
            disabled=confirm_text.strip() != "RESET",
            key="clear_data_btn",
        ):
            try:
                result = ts.clear_all_data(
                    st.session_state.get("user_id"), confirm_text.strip())
                deleted = sum(result["deleted"].values())
                st.session_state.pop("_clear_armed", None)
                st.success(f"已清空 {deleted} 条监测/任务记录")
                st.rerun()
            except PermissionError as exc:
                st.error(str(exc))
            except ValueError as exc:
                st.error(str(exc))
