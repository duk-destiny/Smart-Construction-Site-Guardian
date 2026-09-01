"""页面5：管理端页（page_admin，仅 admin）。

Phase 0：全部数据/写操作经 services.admin_console 与 services.order_service，
本页零 get_conn/DAO/core；连接生命周期由服务门面自持。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import streamlit as st
from ui.page_helpers import safe_page

from services import admin_console, order_service
from services.dispatch_service import _now_str
from services.notify_service import CHANNEL_LABEL, NotificationService
from services.permission_service import AuthorizationError
from services.training_service import TrainingService
from ui.correction_workbench import render_target_corrections


def _len_cn(lst) -> str:
    """中文友好的计数显示。"""
    return str(len(lst))


def _reload_running_engines() -> None:
    # 模型切换后热加载：让实时页(@st.cache_resource)与后台监控的引擎重建引擎
    # 列表，复用 _SESSIONS 会话缓存，无需重启进程即用上新 active 模型。
    try:
        from services import realtime_entry
        realtime_entry.get_engine().reload()
    except Exception:
        pass
    try:
        from services.monitor_service import get_monitor
        mon = get_monitor()
        if mon is not None and getattr(mon, "engine", None) is not None:
            mon.engine.reload()
    except Exception:
        pass


@safe_page("管理端")
def render_admin() -> None:
    st.title("🛠 管理端（仅管理员）")
    if st.session_state.get("role") != "admin":
        st.error("无权限访问管理端")
        return

    uid = st.session_state.get("user_id")

    # ── 导入规范 PDF ──
    st.subheader("导入规范 PDF")
    pdf = st.file_uploader("选择规范 PDF", type=["pdf"])
    if pdf and st.button("解析入库"):
        res = admin_console.import_pdf(uid, pdf)
        if res.get("ok"):
            st.success(f"入库成功，切分 {res['chunks']} 块")
        else:
            st.error(res.get("error", "导入失败"))

    # ── 用户管理（v0.8）──
    st.divider()
    st.subheader("用户管理（v0.8）")
    _users = admin_console.list_users()
    if _users:
        st.dataframe([{
            "用户名": u["username"], "角色": u["role"],
            "初始密码未改": "⚠️ 是" if u["must_change_password"] else "否",
            "状态": "🚫 已停用" if u["disabled"] else "✅ 正常",
            "创建时间": (u["created_at"] or "")[:19],
        } for u in _users], use_container_width=True)
    else:
        st.caption("暂无用户")

    with st.expander("➕ 新建用户"):
        with st.form("user_create_form"):
            _nu_name = st.text_input("用户名（2-32 字符）")
            _nu_pwd = st.text_input("初始密码（至少 8 位，首登建议强制修改）",
                                    type="password")
            _nu_role = st.selectbox("角色",
                                    ["responsible", "safety", "admin"],
                                    format_func=lambda r: {
                                        "responsible": "responsible · 整改责任人",
                                        "safety": "safety · 安全员",
                                        "admin": "admin · 管理员"}.get(r, r))
            _nu_must = st.checkbox("标记初始密码未改（登录后提醒/强制修改）",
                                   value=True)
            if st.form_submit_button("创建用户", use_container_width=True):
                _res = admin_console.create_user(uid, _nu_name, _nu_pwd,
                                                 _nu_role,
                                                 must_change_password=_nu_must)
                if _res.get("ok"):
                    st.success(f"用户 {_nu_name.strip()} 创建成功")
                    st.rerun()
                else:
                    st.error(_res.get("error", "创建失败"))

    _uc1, _uc2 = st.columns(2)
    with _uc1:
        with st.expander("🔑 重置密码"):
            _tgt_name = st.selectbox("选择用户",
                                     [u["username"] for u in _users] or ["—"],
                                     key="user_reset_pick")
            _new_pwd = st.text_input("新密码（至少 8 位）", type="password",
                                     key="user_reset_pwd")
            if st.button("重置并要求对方下次登录改密", key="user_reset_btn"):
                _row = next((u for u in _users
                             if u["username"] == _tgt_name), None)
                if _row is None:
                    st.error("未找到该用户")
                else:
                    _res = admin_console.admin_reset_password(
                        uid, _row["id"], _new_pwd)
                    if _res.get("ok"):
                        st.success(f"已重置 {_tgt_name} 的密码")
                    else:
                        st.error(_res.get("error", "重置失败"))
    with _uc2:
        with st.expander("🚫 停用 / ✅ 启用"):
            _tgt2_name = st.selectbox("选择用户",
                                      [u["username"] for u in _users] or ["—"],
                                      key="user_dis_pick")
            _row2 = next((u for u in _users
                          if u["username"] == _tgt2_name), None)
            _is_dis = bool(_row2 and _row2["disabled"])
            if st.button("🚫 停用该账号" if not _is_dis else "✅ 启用该账号",
                         key="user_dis_btn"):
                if _row2 is None:
                    st.error("未找到该用户")
                else:
                    _res = admin_console.set_user_disabled(uid, _row2["id"],
                                                           not _is_dis)
                    if _res.get("ok"):
                        st.success("已更新账号状态")
                        st.rerun()
                    else:
                        st.error(_res.get("error", "操作失败"))

    # ── 全量隐患记录 ──
    st.divider()
    st.subheader("全量隐患记录")
    rows = admin_console.hazard_summary_rows()
    if rows:
        st.dataframe(rows)
    else:
        st.caption("暂无记录")

    # ── 操作审计日志 ──
    st.divider()
    st.subheader("操作审计日志")
    logs = admin_console.audit_rows()
    if logs:
        st.dataframe(logs)
    else:
        st.caption("暂无日志")
    with st.expander("⬇️ 导出审计流水 CSV（归档快照，库内仍仅追加不可删改）"):
        _ac1, _ac2 = st.columns(2)
        _a_start = _ac1.date_input("起始日期（留空=全量）", value=None,
                                   key="audit_exp_start")
        _a_end = _ac2.date_input("结束日期（含当日，留空=全量）", value=None,
                                 key="audit_exp_end")
        if st.button("生成审计 CSV", key="audit_exp_btn",
                     use_container_width=True):
            _csv_text, _n = admin_console.audit_csv(
                start=_a_start.isoformat() if _a_start else None,
                end=_a_end.isoformat() if _a_end else None)
            if _n == 0:
                st.caption("该区间无审计记录")
            else:
                st.download_button(
                    f"下载审计 CSV（{_n} 行）", _csv_text,
                    file_name=(f"audit_{_a_start or 'all'}_"
                               f"{_a_end or 'all'}.csv"),
                    mime="text/csv", key="audit_exp_dl")
    st.caption("生产留存：cron 挂 scripts/audit_maintenance.py 定期归档"
               "（--retention-days N，加 --delete 才删档，删前自动留 purge 凭证）。")

    # ── 人工纠偏反馈样本 ──
    st.divider()
    st.subheader("人工纠偏反馈样本")
    samples = admin_console.feedback_samples()
    st.metric("纠偏样本数", len(samples))
    if samples:
        _fb_filter = st.selectbox(
            "筛选审核状态", ["全部", "pending", "confirmed", "rejected"],
            key="fb_status_filter")
        _filtered = ([s for s in samples if s["status"] == _fb_filter]
                     if _fb_filter != "全部" else samples)
        st.caption(f"当前筛选：{_fb_filter}（{_len_cn(_filtered)} 条）")
        st.dataframe([{
            "时间": s["created_at"],
            "任务": s["task_id"],
            "自动风险": s["auto_risk_level"],
            "改判风险": s["corrected_risk_level"],
            "原因": s["reason"],
            "类型": s["feedback_type"],
            "状态": s["status"],
        } for s in _filtered], use_container_width=True)
        st.download_button(
            "导出纠偏样本 CSV（全部）",
            admin_console.feedback_csv_text(),
            file_name="feedback_samples.csv",
            mime="text/csv",
        )
        if _filtered:
            st.markdown(f"**逐条审核（{_len_cn(_filtered)} 条）**")
            for sample in _filtered[:30]:
                _label = (f"{sample['created_at'][:16]} ｜ "
                          f"{sample['auto_risk_level'] or '—'} → "
                          f"{sample['corrected_risk_level']} ｜ {sample['reason']}")
                with st.expander(f"[{sample['status']}] {_label}", key=f"fb_row_{sample['id']}"):
                    _col_a, _col_b = st.columns([2, 1])
                    with _col_b:
                        status = st.selectbox(
                            "审核状态", ["pending", "confirmed", "rejected"],
                            index=["pending", "confirmed", "rejected"].index(sample["status"]),
                            key=f"feedback_status_{sample['id']}")
                        if st.button("提交审核", key=f"feedback_btn_{sample['id']}"):
                            admin_console.review_feedback(sample["id"], status, uid)
                            st.success("反馈样本审核已更新")
                            st.rerun()
                    with _col_a:
                        try:
                            detections = json.loads(sample["detection_json"] or "[]")
                        except ValueError:
                            detections = []
                        try:
                            corrections = json.loads(sample["corrected_labels_json"] or "[]")
                        except ValueError:
                            corrections = []
                        updated = render_target_corrections(
                            sample.get("image_abs") or sample["image_path"],
                            detections, corrections, f"admin_fb_{sample['id']}")
                        if st.button("保存逐目标修正", key=f"fb_save_{sample['id']}"):
                            admin_console.update_feedback_corrections(
                                sample["id"], updated, uid)
                            st.success("逐目标修正已保存")
        else:
            st.caption("该状态下暂无样本")
    else:
        st.caption("暂无人工改判记录")

    # ── 告警生命周期 ──
    st.divider()
    st.subheader("告警生命周期")
    alarms = admin_console.alarm_events()
    if alarms:
        _c_filter, _c_img = st.columns([1, 1])
        with _c_filter:
            _alarm_filter = st.selectbox(
                "筛选告警状态", ["全部", "new", "confirmed", "false_alarm", "resolved"],
                key="alarm_status_filter")
        with _c_img:
            _alarm_img_only = st.toggle("仅看有截图", value=False, key="alarm_img_only")
        _alarm_view = [a for a in alarms
                       if (_alarm_filter == "全部" or a["status"] == _alarm_filter)
                       and (not _alarm_img_only
                            or (a.get("image_abs")
                                and os.path.exists(a["image_abs"])))]
        st.caption(f"当前筛选：{_alarm_filter}{' · 仅截图' if _alarm_img_only else ''}"
                   f"（{_len_cn(_alarm_view)} 条 / 共 {_len_cn(alarms)} 条）")
        if _alarm_view:
            for alarm in _alarm_view[:50]:
                _tag = {"new": "🆕", "confirmed": "✅",
                        "false_alarm": "❌", "resolved": "✔️"}.get(alarm["status"], "•")
                _has_img = alarm.get("image_abs") and os.path.exists(alarm["image_abs"])
                _img_tag = "📷" if _has_img else "—"
                _label = (f"{_tag} [{alarm['status']}] "
                          f"{alarm['created_at'][:16]} ｜ {alarm['cls'] or '—'} ｜ "
                          f"conf {alarm['conf'] or '—'} ｜ {_img_tag}")
                with st.expander(_label, key=f"alarm_row_{alarm['id']}"):
                    _col_info, _col_act = st.columns([3, 1])
                    with _col_info:
                        st.caption(
                            f"场景：{alarm['scene_id'] or '—'} ｜ "
                            f"来源：{alarm['source'] or 'camera'}")
                        _clause = alarm.get("clause")
                        if _clause:
                            st.caption(f"违反规范：{_clause}")
                        if _has_img:
                            st.image(alarm["image_abs"],
                                     caption="告警证据截图",
                                     use_container_width=True)
                        else:
                            st.caption("（无证据截图——自检/无帧告警）")
                    with _col_act:
                        status = st.selectbox(
                            "状态", ["new", "confirmed", "false_alarm", "resolved"],
                            index=["new", "confirmed", "false_alarm", "resolved"].index(alarm["status"]),
                            key=f"alarm_status_{alarm['id']}")
                        if st.button("更新状态", key=f"alarm_btn_{alarm['id']}"):
                            admin_console.update_alarm_event(alarm["id"], status, uid)
                            st.success("告警状态已更新")
                            st.rerun()
                        if alarm["status"] in ("new", "confirmed"):
                            if st.button("📮 转为整改工单",
                                         key=f"alarm_to_wo_{alarm['id']}",
                                         use_container_width=True):
                                try:
                                    _oid = admin_console.convert_alarm_to_order(
                                        alarm["id"], uid)
                                    st.success(
                                        f"已生成工单 {_oid}，"
                                        "请到「工单/改判/导出」页派发")
                                except (AuthorizationError, ValueError) as e:
                                    st.error(str(e))
            if len(_alarm_view) > 50:
                st.caption(f"仅显示前 50 条，共 {_len_cn(_alarm_view)} 条，请用筛选缩小范围")
        else:
            st.caption("该筛选条件下暂无告警")
    else:
        st.caption("暂无告警事件")

    # ── 工单验收队列 ──
    st.divider()
    st.subheader("工单验收队列")
    _pending = order_service.pending_review_orders()
    if not _pending:
        st.caption("暂无待验收工单（责任人提交整改后出现在这里）")
    for _idx, _o in enumerate(_pending):
        _desc = (_o["hazard_desc"] or "")[:30]
        with st.expander(f"[待验收] {_o['risk_level']} ｜ {_desc}", key=f"wo_{_o['id']}", expanded=(_idx == 0)):
            st.write(f"**工单号**：{_o['id']}　|　**任务号**：{_o['task_id']}")
            st.write(f"**截止**：{(_o['deadline'] or '—')[:19]}　|　"
                     f"**责任人**：{_o.get('assignee_name') or '—'}")
            st.write(f"**隐患描述**：{_o['hazard_desc']}")
            st.write(f"**整改要求**：{_o['requirement']}")
            st.write(f"**整改说明**：{_o['submitted_note'] or '—'}")
            for _p in _o.get("submitted_img_paths") or []:
                st.image(_p, caption=os.path.basename(_p), width=320)
            _rc1, _rc2 = st.columns([2, 3])
            if _rc1.button("✅ 通过并销项", key=f"wo_pass_{_o['id']}",
                           use_container_width=True):
                try:
                    ok, msg = order_service.review_order(_o["id"], uid, approve=True)
                except (AuthorizationError, ValueError) as e:
                    st.error(str(e))
                else:
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
            _reject_reason = _rc2.text_input("驳回原因（必填）", key=f"wo_rj_{_o['id']}")
            if _rc2.button("↩️ 驳回重改", key=f"wo_reject_{_o['id']}"):
                try:
                    ok, msg = order_service.review_order(_o["id"], uid,
                                                         approve=False,
                                                         reason=_reject_reason)
                except (AuthorizationError, ValueError) as e:
                    st.error(str(e))
                else:
                    if ok:
                        st.warning(msg)
                        st.rerun()
                    else:
                        st.error(msg)

    # ── 逾期巡检 ──
    st.divider()
    st.subheader("逾期巡检（演示 · 时间游标）")
    _hours_ahead = st.number_input(
        "模拟当前时间往后推（小时）", min_value=0.0, max_value=24 * 30.0,
        value=0.0, step=1.0, key="overdue_offset_hours",
        help="演示催办故事线用：调大即模拟时间流逝，无需真实等待。"
             "生产部署由系统 cron 调度 scripts/overdue_scan.py 驱动同一扫描函数。")
    if st.button("🔍 扫描逾期并催办", key="btn_overdue_scan"):
        res = order_service.scan_overdue(as_of=_now_str(float(_hours_ahead)))
        m1c, m2c, m3c = st.columns(3)
        m1c.metric("逾期工单", res["overdue"])
        m2c.metric("催办记录", res["notified"])
        m3c.metric("越级升级", res["escalated"])
        st.caption(f"巡检时刻(as_of):{res['as_of']}；结果均写入 audit_logs"
                   f"(overdue_notify / overdue_escalate)。")

    # ── 外部推送 ──
    st.divider()
    st.subheader("外部推送")
    _cfg_demo = admin_console.notify_demo_mode_default()
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
        for _rec in admin_console.mock_capture_tail(10):
            st.json(_rec)
    _nlogs = admin_console.notification_logs()
    st.caption(f"推送留痕（最近 {len(_nlogs)} 条）")
    if _nlogs:
        st.dataframe([{
            "时间": r["created_at"],
            "告警": r["alarm_id"],
            "通道": r["channel"],
            "状态": r["status"],
            "错误": r["error"] or "",
        } for r in _nlogs])
    else:
        st.caption("暂无推送记录")

    # ── 风险周报 ──
    st.divider()
    st.subheader("风险周报（v0.3）")
    _today = st.session_state.get("_report_today") or datetime.now().date()
    _rc_s, _rc_e, _rc_g = st.columns([2, 2, 1.4])
    _r_start = _rc_s.date_input("起始日期", value=_today - timedelta(days=6),
                                key="wr_start")
    _r_end = _rc_e.date_input("结束日期", value=_today, key="wr_end")
    if _rc_g.button("📊 生成周报", key="btn_weekly_report", use_container_width=True):
        try:
            _res = admin_console.weekly_report(
                _r_start.isoformat(), _r_end.isoformat(), uid)
        except AuthorizationError as e:
            st.error(f"权限不足：{e}")
        else:
            st.session_state["_weekly_report"] = _res["data"]
    _wr = st.session_state.get("_weekly_report")
    if _wr:
        s = _wr["stats"]
        m1c, m2c, m3c, m4c = st.columns(4)
        m1c.metric("检测帧", s["frames"])
        m2c.metric("不合规帧", s["bad"])
        m3c.metric("新增工单", s["orders_total"])
        m4c.metric("存量逾期", s["overdue_open_now"])
        for line in s["conclusions"]:
            st.markdown(f"- {line}")
        pa = s["per_assignee"]
        if pa:
            with st.expander("责任人整改进度"):
                st.dataframe([{
                    "责任人": a["name"], "派发": a["assigned"],
                    "销项": a["closed_n"], "在办": a["active_n"],
                    "逾期": a["overdue_n"],
                    "逾期率": f"{a['overdue_rate']*100:.0f}%",
                } for a in pa])
        pdf_path = _wr["file_path"]
        if os.path.exists(pdf_path):
            with open(pdf_path, "rb") as fh:
                st.download_button("⬇️ 下载周报 PDF", fh,
                                   file_name=os.path.basename(pdf_path),
                                   mime="application/pdf",
                                   key="dl_weekly_report")

    # ── 模型评估摘要 ──
    st.divider()
    st.subheader("模型评估摘要")
    eval_rows = admin_console.eval_summary_rows()
    if eval_rows and "error" not in eval_rows[0]:
        st.dataframe(eval_rows)
    elif not eval_rows:
        st.caption("暂无评测文件，可运行 scripts/evaluate_models.py 生成")
    else:
        st.error(eval_rows[0]["error"])

    # ── 模型版本与回滚 ──
    st.divider()
    st.subheader("模型版本与回滚")
    models = admin_console.model_families()
    if not models:
        st.caption("暂无模型注册记录")
    else:
        families: dict = {}
        for m in models:
            families.setdefault(m["name"], []).append(m)
        for name, rows in families.items():
            def _ver_num(ver: str) -> int:
                digits = ""
                for ch in ver.lstrip("v"):
                    if ch.isdigit():
                        digits += ch
                    else:
                        break
                return int(digits or 0)

            rows = sorted(rows, key=lambda r: _ver_num(r["version"]), reverse=True)
            ver_to_row = {r["version"]: r for r in rows}
            active = next((r for r in rows if r["active"]), None)
            active_ver = active["version"] if active else rows[0]["version"]
            versions = [r["version"] for r in rows]

            def _fmt(ver, _rows=rows):
                r = next((x for x in _rows if x["version"] == ver), None)
                if r is None:
                    return ver
                tag = " ✅当前" if r["active"] else ""
                return f"{ver}{tag}  | mAP50 {r['mAP50'] or '—'} | mAP50-95 {r['mAP50_95'] or '—'}"

            default_idx = versions.index(active_ver) if active_ver in versions else 0
            chosen_ver = st.selectbox(
                f"{name}：选择版本",
                options=versions,
                format_func=_fmt,
                index=default_idx,
                key=f"model_ver_sel_{name}",
            )
            chosen = ver_to_row.get(chosen_ver)
            if chosen:
                st.caption(f"路径 {chosen['path']}")
                if not chosen["active"]:
                    if st.button(f"一键切换 {name} → {chosen['version']}",
                                 key=f"switch_model_{name}"):
                        admin_console.switch_model(name, chosen["id"], uid)
                        _reload_running_engines()
                        st.success(f"已切换 {name} 到 {chosen['version']}")
                        st.rerun()
                else:
                    st.caption("已是当前活跃版本")

    # ── 复训与模型替换 ──
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
            elif ok:
                st.success(msg)
            else:
                st.error(f"早停失败: {msg}")
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
            old = admin_console.active_model(name)
            existing = [
                m for m in models
                if m["name"] == name and m["version"] == r.get("version")
            ]
            if not existing:
                data_yaml = os.path.join("data", "combined", name, "data.yaml")
                admin_console.register_model(
                    name=name, version=r.get("version"), path=r.get("path"),
                    data_yaml=data_yaml if os.path.exists(data_yaml) else None,
                    imgsz=640, mAP50=r.get("mAP50"), mAP50_95=r.get("mAP50_95"),
                    notes="管理端复训自动注册", user_id=uid)
                existing = [
                    m for m in admin_console.model_families()
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
                    admin_console.switch_model(name, existing[0]["id"], uid)
                    _reload_running_engines()
                    st.success(f"已切换 {name}")
                    st.rerun()
            elif existing and existing[0]["active"]:
                st.caption(f"{name}：已是当前版本")

    # ── 清空全部监测数据 ──
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
                result = admin_console.clear_all_data(uid, confirm_text.strip())
                deleted = sum(result["deleted"].values())
                st.session_state.pop("_clear_armed", None)
                st.success(f"已清空 {deleted} 条监测/任务记录")
                st.rerun()
            except AuthorizationError as exc:
                st.error(str(exc))
            except ValueError as exc:
                st.error(str(exc))
