"""DAO 层：承载等价"存储过程"的业务逻辑（代码规范 §8，全参数化查询）。

SQLite 无存储过程，业务逻辑由本模块 Python 方法承载（DB 文档 §6）。
所有 SQL 使用 ? 占位符，杜绝字符串拼接防注入。
AuditDAO 仅提供 insert/select，无 update/delete（C4 + 配合 §4 触发器双重保障）。
"""
from __future__ import annotations

import sqlite3
import uuid


def _new_id(prefix: str) -> str:
    """生成带前缀的唯一 ID，如 u_xxxx / t_xxxx。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class UserDAO:
    """用户注册/查询/密码哈希读取。密码校验在 AuthService（bcrypt）。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, username: str, pwd_hash: str, role: str,
               must_change_password: int = 0) -> str:
        uid = _new_id("u")
        self.conn.execute(
            "INSERT INTO users(id,username,pwd_hash,role,must_change_password,created_at) "
            "VALUES(?,?,?,?,?,datetime('now'))",
            (uid, username, pwd_hash, role, int(must_change_password)))
        self.conn.commit()
        return uid

    def get_by_name(self, username: str):
        return self.conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def list_by_role(self, role: str) -> list:
        """按角色列出用户（v0.2 派发下拉：responsible 候选人）。"""
        return self.conn.execute(
            "SELECT * FROM users WHERE role=? ORDER BY username", (role,)).fetchall()

    def list_all(self) -> list:
        """全量用户（v0.8 管理端用户治理），按角色与用户名稳定排序。"""
        return self.conn.execute(
            "SELECT * FROM users ORDER BY role, username").fetchall()

    def get_by_id(self, user_id: str):
        return self.conn.execute(
            "SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    def update_password(self, user_id: str, pwd_hash: str) -> None:
        """更新密码哈希并清除「初始密码未改」标记（v0.8）。"""
        self.conn.execute(
            "UPDATE users SET pwd_hash=?, must_change_password=0 WHERE id=?",
            (pwd_hash, user_id))
        self.conn.commit()

    def set_must_change_password(self, user_id: str, flag: int = 1) -> None:
        """设置/清除初始密码标记（v0.8 管理员重置密码后强制对方改密）。"""
        self.conn.execute(
            "UPDATE users SET must_change_password=? WHERE id=?",
            (int(flag), user_id))
        self.conn.commit()

    def set_disabled(self, user_id: str, disabled: bool) -> None:
        """停用/启用账号（v0.8）。登录与权限校验均拒绝 disabled=1。"""
        self.conn.execute(
            "UPDATE users SET disabled=? WHERE id=?",
            (1 if disabled else 0, user_id))
        self.conn.commit()


class TaskDAO:
    """任务创建与查询。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, user_id: str, permit_json: str, status: str = "pending",
               source: str = "upload", commit: bool = True) -> str:
        tid = _new_id("t")
        self.conn.execute(
            "INSERT INTO tasks(id,user_id,permit_json,status,source,created_at) "
            "VALUES(?,?,?,?,?,datetime('now'))",
            (tid, user_id, permit_json, status, source))
        if commit:
            self.conn.commit()
        return tid

    def get(self, task_id: str):
        return self.conn.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def list_by_user(self, user_id: str):
        return self.conn.execute(
            "SELECT * FROM tasks WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)).fetchall()

    def update_status(self, task_id: str, status: str,
                      commit: bool = True) -> None:
        self.conn.execute(
            "UPDATE tasks SET status=? WHERE id=?", (status, task_id))
        if commit:
            self.conn.commit()


class DetectionDAO:
    """批量写入视觉检测结果。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def bulk_insert(self, rows: list[dict], commit: bool = True) -> None:
        data = [(
            _new_id("d"), r["task_id"], r.get("frame_path"), r["cls"],
            r["conf"], r.get("bbox_json"), r.get("violation_desc"))
            for r in rows]
        self.conn.executemany(
            "INSERT INTO detections(id,task_id,frame_path,cls,conf,bbox_json,violation_desc) "
            "VALUES(?,?,?,?,?,?,?)", data)
        if commit:
            self.conn.commit()


class ComplianceDAO:
    """批量写入规范合规结果。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def bulk_insert(self, rows: list[dict], commit: bool = True) -> None:
        data = [(
            _new_id("c"), r["task_id"], r["verdict"], r.get("clause_no"),
            r.get("clause_text"), r.get("score")) for r in rows]
        self.conn.executemany(
            "INSERT INTO compliances(id,task_id,verdict,clause_no,clause_text,score) "
            "VALUES(?,?,?,?,?,?)", data)
        if commit:
            self.conn.commit()


class RiskDAO:
    """风险写入与人工改判。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, task_id: str, risk_level: str, reasons_json: str,
               filtered_fp_json: str, commit: bool = True) -> str:
        rid = _new_id("r")
        self.conn.execute(
            "INSERT INTO risks(id,task_id,risk_level,reasons_json,filtered_fp_json) "
            "VALUES(?,?,?,?,?)",
            (rid, task_id, risk_level, reasons_json, filtered_fp_json))
        if commit:
            self.conn.commit()
        return rid

    def get_by_task(self, task_id: str):
        return self.conn.execute(
            "SELECT * FROM risks WHERE task_id=?", (task_id,)).fetchone()

    def override(self, risk_id: str, level: str, reason: str) -> None:
        self.conn.execute(
            "UPDATE risks SET override_level=?, override_reason=? WHERE id=?",
            (level, reason, risk_id))
        self.conn.commit()


class WorkOrderDAO:
    """工单写入与查询。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, task_id: str, hazard_desc: str, clause: str, requirement: str,
               risk_level: str, worker_notice: str,
               commit: bool = True) -> str:
        wid = _new_id("w")
        self.conn.execute(
            "INSERT INTO work_orders(id,task_id,hazard_desc,clause,requirement,risk_level,worker_notice,created_at) "
            "VALUES(?,?,?,?,?,?,?,datetime('now'))",
            (wid, task_id, hazard_desc, clause, requirement, risk_level, worker_notice))
        if commit:
            self.conn.commit()
        return wid

    def get_by_task(self, task_id: str):
        return self.conn.execute(
            "SELECT * FROM work_orders WHERE task_id=?", (task_id,)).fetchone()

    def list_all_with_risk(self):
        """历史研判列表：工单 + 风险等级 + 改判信息 + 任务来源，按时间倒序。"""
        return self.conn.execute("""
            SELECT w.*, r.risk_level AS auto_level,
                   r.override_level, r.override_reason,
                   t.source AS source
            FROM work_orders w
            LEFT JOIN risks r ON r.task_id = w.task_id
            LEFT JOIN tasks t ON t.id = w.task_id
            ORDER BY w.created_at DESC
        """).fetchall()

    def update_notice(self, task_id: str, worker_notice: str) -> None:
        """异步润色完成后回填工人提示（不阻塞主链路）。"""
        self.conn.execute(
            "UPDATE work_orders SET worker_notice=? WHERE task_id=?",
            (worker_notice, task_id))
        self.conn.commit()

    # ---------- v0.2 工单闭环：派发 → 整改 → 验收 ----------
    # status 流转：open → submitted → closed；驳回置 rejected，再提交/改派回 open。
    # 「逾期」为派生状态（deadline < now 且 status IN ('open','rejected')），不入库。

    def get(self, order_id: str):
        return self.conn.execute(
            "SELECT * FROM work_orders WHERE id=?", (order_id,)).fetchone()

    def set_dispatch(self, order_id: str, assignee_id: str | None,
                     deadline: str | None, dispatched_at: str) -> None:
        """派发：落责任人、截止时间与派发时间戳，状态置回 open。"""
        self.conn.execute(
            "UPDATE work_orders SET assignee_id=?, deadline=?, "
            "dispatched_at=?, status='open' WHERE id=?",
            (assignee_id, deadline, dispatched_at, order_id))
        self.conn.commit()

    def set_submitted(self, order_id: str, note: str, imgs_json: str | None) -> None:
        """责任人提交整改说明/照片，进入待验收。"""
        self.conn.execute(
            "UPDATE work_orders SET submitted_note=?, submitted_imgs=?, "
            "status='submitted' WHERE id=?",
            (note, imgs_json, order_id))
        self.conn.commit()

    def set_reviewed(self, order_id: str, approved: bool, reviewer_id: str,
                     reason: str = "") -> None:
        """验收：通过→closed（留验收人与时间）；驳回→rejected 留驳回原因可再改。"""
        if approved:
            self.conn.execute(
                "UPDATE work_orders SET status='closed', approved_by=?, "
                "approved_at=datetime('now'), closed_at=datetime('now') WHERE id=?",
                (reviewer_id, order_id))
        else:
            self.conn.execute(
                "UPDATE work_orders SET status='rejected', review_reason=?, "
                "approved_by=NULL, approved_at=NULL, closed_at=NULL WHERE id=?",
                (reason, order_id))
        self.conn.commit()

    def list_by_assignee(self, assignee_id: str,
                         statuses: tuple[str, ...] = ("open", "rejected", "submitted")) -> list:
        """责任人的工单视图（默认排除已闭环）。"""
        placeholders = ",".join("?" for _ in statuses)
        return self.conn.execute(
            f"SELECT * FROM work_orders WHERE assignee_id=? AND status IN ({placeholders}) "
            "ORDER BY created_at DESC",
            (assignee_id, *statuses)).fetchall()

    def list_by_status(self, status: str, limit: int = 200) -> list:
        return self.conn.execute(
            "SELECT * FROM work_orders WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (status, limit)).fetchall()

    def list_overdue(self, as_of: str,
                     statuses: tuple[str, ...] = ("open", "rejected")) -> list:
        """逾期未销项工单（deadline 非空且早于 as_of）。"""
        placeholders = ",".join("?" for _ in statuses)
        return self.conn.execute(
            f"SELECT * FROM work_orders WHERE deadline IS NOT NULL "
            f"AND deadline < ? AND status IN ({placeholders}) "
            "ORDER BY deadline ASC",
            (as_of, *statuses)).fetchall()


class AuditDAO:
    """审计写入。仅 INSERT/SELECT，无 update/delete（C4 + DB 文档 §4/§6）。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, user_id: str | None, action: str, detail_json: str,
               commit: bool = True) -> int:
        cur = self.conn.execute(
            "INSERT INTO audit_logs(user_id,action,detail_json,created_at) "
            "VALUES(?,?,?,datetime('now'))", (user_id, action, detail_json))
        if commit:
            self.conn.commit()
        return cur.lastrowid

    def list_range(self, start: str | None = None, end: str | None = None,
                   limit: int = 100_000) -> list:
        """按日期范围取审计流水（含用户名联查），供导出/归档（v0.8）。

        start/end 均为 'YYYY-MM-DD'；end 含当日（< end + 1 天）。
        """
        sql = ("SELECT a.id, a.user_id, u.username, a.action, "
               "a.detail_json, a.created_at "
               "FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id "
               "WHERE 1=1")
        params: list = []
        if start:
            sql += " AND a.created_at >= ?"
            params.append(start)
        if end:
            sql += " AND a.created_at < date(?, '+1 day')"
            params.append(end)
        sql += " ORDER BY a.id ASC LIMIT ?"
        params.append(int(limit))
        return self.conn.execute(sql, params).fetchall()

    def count_before(self, before: str) -> int:
        """统计 created_at < before（'YYYY-MM-DD'，不含当日）的行数。"""
        row = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM audit_logs WHERE created_at < ?",
            (before,)).fetchone()
        return int(row["cnt"]) if row else 0


class KbDocDAO:
    """规范文档登记。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, filename: str, chunk_count: int, imported_by: str) -> str:
        did = _new_id("k")
        self.conn.execute(
            "INSERT INTO kb_docs(id,filename,chunk_count,imported_by,created_at) "
            "VALUES(?,?,?,?,datetime('now'))",
            (did, filename, chunk_count, imported_by))
        self.conn.commit()
        return did

    def list_all(self):
        return self.conn.execute(
            "SELECT * FROM kb_docs ORDER BY created_at DESC").fetchall()


class DetectionRecordDAO:
    """实时/上传检测记录写入与历史查询（B3）。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def bulk_insert(self, session_id: str, frame_status: str,
                   rows: list[dict], mode: str = "realtime") -> None:
        """写入一帧的检测项；rows: [{"scene_id","cls","conf","severity"}, ...]。"""
        if not rows:
            # 仍记录一帧"无违规"占位，保证合规率统计口径完整
            rows = [{
                "scene_id": None, "cls": "none", "conf": 1.0,
                "severity": "safe", "track_id": None, "track_frames": 1,
            }]
        data = [(
            _new_id("r"), session_id, r.get("scene_id"), mode, frame_status,
            r["cls"], r.get("conf"), r.get("severity"),
            r.get("track_id"), r.get("track_frames"),
        ) for r in rows]
        self.conn.executemany(
            "INSERT INTO detection_records"
            "(id,session_id,scene_id,mode,frame_status,cls,conf,severity,"
            "track_id,track_frames,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))", data)
        self.conn.commit()

    def query(self, start: str | None = None, end: str | None = None,
              severity: str | None = None, cls: str | None = None,
              limit: int = 2000) -> list:
        """按日期范围/严重度/类别查询（B5 日期筛选）。"""
        sql = "SELECT * FROM detection_records WHERE 1=1"
        params: list = []
        if start:
            sql += " AND created_at >= ?"
            params.append(start)
        if end:
            sql += " AND created_at <= ?"
            params.append(end + " 23:59:59")
        if severity:
            sql += " AND severity = ?"
            params.append(severity)
        if cls:
            sql += " AND cls = ?"
            params.append(cls)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def stats_by_date(self, start: str | None = None, end: str | None = None) -> list:
        """按日聚合：检测总次数、各合规级别帧数（B4 合规率趋势）。"""
        sql = """
            SELECT date(created_at) AS day,
                   COUNT(DISTINCT session_id || '|' || created_at) AS frames,
                   SUM(CASE WHEN frame_status='不合规' THEN 1 ELSE 0 END) AS non_compliant,
                   SUM(CASE WHEN frame_status='警告' THEN 1 ELSE 0 END) AS warning,
                   SUM(CASE WHEN frame_status='合规' THEN 1 ELSE 0 END) AS compliant
            FROM detection_records WHERE 1=1
        """
        params: list = []
        if start:
            sql += " AND created_at >= ?"
            params.append(start)
        if end:
            sql += " AND created_at <= ?"
            params.append(end + " 23:59:59")
        sql += " GROUP BY day ORDER BY day"
        return self.conn.execute(sql, params).fetchall()

    def severity_breakdown(self, start: str | None = None, end: str | None = None) -> list:
        """各类别命中次数（B4 柱状图）。"""
        sql = """
            SELECT cls, COUNT(*) AS cnt FROM detection_records
            WHERE cls <> 'none'
        """
        params: list = []
        if start:
            sql += " AND created_at >= ?"
            params.append(start)
        if end:
            sql += " AND created_at <= ?"
            params.append(end + " 23:59:59")
        sql += " GROUP BY cls ORDER BY cnt DESC"
        return self.conn.execute(sql, params).fetchall()


class AgentRunDAO:
    """Agent 运行证据链：一次任务内各 Agent 的执行摘要。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def bulk_insert(self, rows: list[dict], commit: bool = True) -> None:
        """写入多条 Agent 运行记录。rows: [{task_id, agent, status, cost_ms, input_json, output_json, error}]。"""
        data = [(
            _new_id("ar"), r["task_id"], r["agent"], r.get("status", "unknown"),
            int(r.get("cost_ms", 0) or 0), r.get("input_json"),
            r.get("output_json"), r.get("error"),
        ) for r in rows]
        self.conn.executemany(
            "INSERT INTO agent_runs"
            "(id,task_id,agent,status,cost_ms,input_json,output_json,error,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,datetime('now'))", data)
        if commit:
            self.conn.commit()

    def list_by_task(self, task_id: str) -> list:
        """按任务返回 Agent 运行轨迹，按创建时间与执行顺序排列。"""
        return self.conn.execute(
            "SELECT * FROM agent_runs WHERE task_id=? ORDER BY created_at ASC, id ASC",
            (task_id,)).fetchall()


class FeedbackDAO:
    """人工纠偏反馈样本：改判/误报/漏报记录，用于证据链与后续训练集构建。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, task_id: str, user_id: str | None,
               auto_risk_level: str | None, corrected_risk_level: str,
               reason: str, feedback_type: str = "override",
               source_json: str | None = None,
               image_path: str | None = None,
               detection_json: str | None = None,
               corrected_labels_json: str | None = None,
               status: str = "pending") -> str:
        fid = _new_id("fb")
        self.conn.execute(
            "INSERT INTO feedback_samples"
            "(id,task_id,user_id,auto_risk_level,corrected_risk_level,"
            "reason,feedback_type,source_json,image_path,detection_json,"
            "corrected_labels_json,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (fid, task_id, user_id, auto_risk_level, corrected_risk_level,
             reason, feedback_type, source_json, image_path, detection_json,
             corrected_labels_json, status))
        self.conn.commit()
        return fid

    def list_all(self, limit: int = 500) -> list:
        """按时间倒序返回纠偏样本。"""
        return self.conn.execute(
            "SELECT * FROM feedback_samples ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS cnt FROM feedback_samples").fetchone()
        return int(row["cnt"]) if row else 0

    def update_review(self, feedback_id: str, status: str,
                      reviewed_by: str | None) -> None:
        self.conn.execute(
            "UPDATE feedback_samples SET status=?, reviewed_by=?, "
            "reviewed_at=datetime('now') WHERE id=?",
            (status, reviewed_by, feedback_id))
        self.conn.commit()

    def update_corrections(self, feedback_id: str,
                           corrected_labels_json: str,
                           reviewed_by: str | None) -> None:
        self.conn.execute(
            "UPDATE feedback_samples SET corrected_labels_json=?, "
            "reviewed_by=?, reviewed_at=datetime('now') WHERE id=?",
            (corrected_labels_json, reviewed_by, feedback_id))
        self.conn.commit()


class AlarmEventDAO:
    """告警生命周期：记录实时监测告警并支持人工状态流转。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, session_id: str | None, task_id: str | None,
               scene_id: str | None, cls: str | None, conf: float | None,
               image_path: str | None = None, source: str | None = None,
               status: str = "new") -> str:
        aid = _new_id("al")
        self.conn.execute(
            "INSERT INTO alarm_events"
            "(id,session_id,task_id,scene_id,cls,conf,image_path,source,"
            "status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))",
            (aid, session_id, task_id, scene_id, cls, conf,
             image_path, source, status))
        self.conn.commit()
        return aid

    def find_open(self, session_id: str, cls: str) -> object | None:
        """查找同一会话/类别下仍未关闭的告警，避免短时间重复创建。"""
        return self.conn.execute(
            "SELECT * FROM alarm_events "
            "WHERE session_id=? AND cls=? AND status IN ('new','confirmed') "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id, cls)).fetchone()

    def list_all(self, limit: int = 500) -> list:
        return self.conn.execute(
            "SELECT * FROM alarm_events ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()

    def get_by_id(self, alarm_id: str):
        """按 ID 返回告警行（供推送/证据回填）。"""
        return self.conn.execute(
            "SELECT * FROM alarm_events WHERE id=?", (alarm_id,)).fetchone()

    def update_clause(self, alarm_id: str, clause: str) -> None:
        """异步回填规范条款（RAG 检索结果，告警后挂载，不阻塞告警触发）。"""
        self.conn.execute(
            "UPDATE alarm_events SET clause=?, updated_at=datetime('now') "
            "WHERE id=?",
            (clause, alarm_id))
        self.conn.commit()

    def set_image(self, alarm_id: str, image_path: str | None) -> None:
        """回填告警证据截图路径。"""
        self.conn.execute(
            "UPDATE alarm_events SET image_path=?, updated_at=datetime('now') "
            "WHERE id=?",
            (image_path, alarm_id))
        self.conn.commit()

    def update_status(self, alarm_id: str, status: str,
                      reviewed_by: str | None, commit: bool = True) -> None:
        self.conn.execute(
            "UPDATE alarm_events SET status=?, reviewed_by=?, updated_at=datetime('now') "
            "WHERE id=?",
            (status, reviewed_by, alarm_id))
        if commit:
            self.conn.commit()


class NotificationLogDAO:
    """外部推送留痕：每次告警推送的状态与错误信息。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, alarm_id: str, channel: str,
               status: str = "pending", error: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO notification_logs(alarm_id,channel,status,error,created_at) "
            "VALUES(?,?,?,?,datetime('now'))",
            (alarm_id, channel, status, error))
        self.conn.commit()
        return cur.lastrowid

    def list_by_alarm(self, alarm_id: str, limit: int = 50) -> list:
        return self.conn.execute(
            "SELECT * FROM notification_logs WHERE alarm_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (alarm_id, limit)).fetchall()

    def list_all(self, limit: int = 200) -> list:
        return self.conn.execute(
            "SELECT * FROM notification_logs ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()


class ModelRegistryDAO:
    """模型版本注册表。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, name: str, version: str, path: str,
               data_yaml: str | None = None, imgsz: int | None = None,
               mAP50: float | None = None, mAP50_95: float | None = None,
               notes: str | None = None, active: int = 0) -> str:
        mid = _new_id("md")
        self.conn.execute(
            "INSERT INTO model_registry"
            "(id,name,version,path,data_yaml,imgsz,mAP50,mAP50_95,active,notes,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (mid, name, version, path, data_yaml, imgsz, mAP50, mAP50_95,
             active, notes))
        self.conn.commit()
        return mid

    def list_all(self, limit: int = 200) -> list:
        return self.conn.execute(
            "SELECT * FROM model_registry ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()

    def get_active(self, name: str):
        return self.conn.execute(
            "SELECT * FROM model_registry WHERE name=? AND active=1 "
            "ORDER BY created_at DESC LIMIT 1",
            (name,)).fetchone()

    def set_active(self, name: str, model_id: str) -> None:
        self.conn.execute(
            "UPDATE model_registry SET active=0 WHERE name=?", (name,))
        self.conn.execute(
            "UPDATE model_registry SET active=1 WHERE id=?", (model_id,))
        self.conn.commit()


class AgentChatDAO:
    """认知层存储（设计文档 §5.5/§5.6）：会话/消息/认知任务 run/步骤四表合一。

    与 AgentRunDAO 完全独立（不碰 agent_runs）；状态翻转为条件 UPDATE，
    返回 rowcount 供调用方校验（配合认知层 _RUN_LOCK 原子「查再置」防 TOCTOU）。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ---------- chat_sessions：会话 ----------

    def create_session(self, user_id: str, title: str | None = None) -> str:
        sid = _new_id("cs")
        self.conn.execute(
            "INSERT INTO chat_sessions(id,user_id,title,created_at,updated_at) "
            "VALUES(?,?,?,datetime('now'),datetime('now'))",
            (sid, user_id, title))
        self.conn.commit()
        return sid

    def get_session(self, session_id: str):
        return self.conn.execute(
            "SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()

    def list_sessions(self, user_id: str, limit: int = 50,
                      include_archived: bool = False,
                      archived_only: bool = False) -> list:
        """按最近活跃倒序列出用户会话（对话窗口默认只看活跃档）。"""
        cond = "user_id=?"
        if archived_only:
            cond += " AND archived=1"
        elif not include_archived:
            cond += " AND archived=0"
        return self.conn.execute(
            f"SELECT * FROM chat_sessions WHERE {cond} "
            "ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?",
            (user_id, limit)).fetchall()

    def rename_session(self, session_id: str, title: str) -> None:
        self.conn.execute(
            "UPDATE chat_sessions SET title=?, updated_at=datetime('now') "
            "WHERE id=?", (title, session_id))
        self.conn.commit()

    def set_session_archived(self, session_id: str, archived: bool) -> None:
        self.conn.execute(
            "UPDATE chat_sessions SET archived=? WHERE id=?",
            (int(bool(archived)), session_id))
        self.conn.commit()

    def cancel_active_runs(self, session_id: str) -> int:
        """删除会话前的兜底：未完结 run 一律条件翻转为 cancelled。

        防两类问题：删除后进行中 run 的进度轮询 404（前端误报
        「任务不存在」）；僵尸 worker 向已删会话写步骤（FK 失败）。
        """
        cur = self.conn.execute(
            "UPDATE agent_chat_runs SET status='cancelled', "
            "error=COALESCE(NULLIF(error,''),'会话删除，自动取消'), "
            "updated_at=datetime('now') "
            "WHERE session_id=? AND status IN "
            "('pending','running','pending_confirm')", (session_id,))
        self.conn.commit()
        return cur.rowcount

    def delete_session(self, session_id: str) -> int:
        """物理删除会话及其全部消息/认知 run/步骤（管理页删除，不可逆）。

        逐表先删子再删父，返回删除的会话数（0/1）供调用方判定存在性。
        """
        # 删除顺序满足外键：消息.run_id → runs；steps.run_id → runs
        self.conn.execute(
            "DELETE FROM agent_chat_run_steps WHERE run_id IN "
            "(SELECT id FROM agent_chat_runs WHERE session_id=?)", (session_id,))
        self.conn.execute(
            "DELETE FROM chat_messages WHERE session_id=?", (session_id,))
        self.conn.execute(
            "DELETE FROM agent_chat_runs WHERE session_id=?", (session_id,))
        cur = self.conn.execute(
            "DELETE FROM chat_sessions WHERE id=?", (session_id,))
        self.conn.commit()
        return cur.rowcount

    # ---------- chat_messages：消息 ----------

    def insert_message(self, session_id: str, role: str, content: str,
                       run_id: str | None = None, intent: str | None = None,
                       digest: str | None = None,
                       attachments_json: str | None = None,
                       commit: bool = True) -> int:
        """写一条消息并刷新会话活跃时间，返回自增 id。"""
        cur = self.conn.execute(
            "INSERT INTO chat_messages"
            "(session_id,role,content,intent,run_id,digest,attachments,"
            "created_at) VALUES(?,?,?,?,?,?,?,datetime('now'))",
            (session_id, role, content, intent, run_id, digest,
             attachments_json))
        self.conn.execute(
            "UPDATE chat_sessions SET updated_at=datetime('now') WHERE id=?",
            (session_id,))
        if commit:
            self.conn.commit()
        return cur.lastrowid

    def list_messages(self, session_id: str, limit: int = 200) -> list:
        """按时间正序取会话消息（渲染与拼上下文同序）。"""
        return self.conn.execute(
            "SELECT * FROM chat_messages WHERE session_id=? "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (session_id, limit)).fetchall()

    # ---------- agent_chat_runs：认知任务主表 ----------

    def create_run(self, session_id: str, user_id: str, user_input: str,
                   intent: str | None = None,
                   deadline_sec: float = 30.0,
                   attachments_json: str | None = None) -> str:
        rid = _new_id("acr")
        self.conn.execute(
            "INSERT INTO agent_chat_runs"
            "(id,session_id,user_id,intent,user_input,status,deadline_sec,"
            "attachments_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'pending',?,?,datetime('now'),datetime('now'))",
            (rid, session_id, user_id, intent, user_input, float(deadline_sec),
             attachments_json))
        self.conn.commit()
        return rid

    def get_run(self, run_id: str):
        return self.conn.execute(
            "SELECT * FROM agent_chat_runs WHERE id=?", (run_id,)).fetchone()

    def get_run_by_session(self, session_id: str):
        """会话最近一次认知任务（消息回填 run_id 前的兜底查询）。"""
        return self.conn.execute(
            "SELECT * FROM agent_chat_runs WHERE session_id=? "
            "ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()

    def update_run(self, run_id: str, commit: bool = True, **fields) -> None:
        """条件更新 run 非状态字段（白名单制，防任意列写入）。

        支持字段：intent/plan_json/current_step_idx/need_confirm/
        confirm_payload/result_json/error/task_id/deadline_sec/
        attachments_json。
        """
        allowed = {"intent", "plan_json", "current_step_idx", "need_confirm",
                   "confirm_payload", "result_json", "error", "task_id",
                   "deadline_sec", "attachments_json"}
        sets = [f"{k}=?" for k in fields if k in allowed]
        if not sets:
            return
        params = [fields[k] for k in fields if k in allowed]
        if "need_confirm" in fields:
            params[sets.index("need_confirm=?")] = int(fields["need_confirm"])
        sql = (f"UPDATE agent_chat_runs SET {', '.join(sets)}, "
               "updated_at=datetime('now') WHERE id=?")
        self.conn.execute(sql, (*params, run_id))
        if commit:
            self.conn.commit()

    def transition_status(self, run_id: str, expected: str, new: str,
                          error: str | None = None,
                          result_json: str | None = None,
                          commit: bool = True) -> bool:
        """条件状态翻转：仅当前状态为 expected 时置 new，返回是否生效。

        调用方必须校验返回值（竞争失败/孤儿扫描已处置时返回 False，
        副作用不得重复执行）。配合认知层 _RUN_LOCK 原子「查再置」。
        """
        cur = self.conn.execute(
            "UPDATE agent_chat_runs SET status=?, error=?, result_json=?, "
            "updated_at=datetime('now') "
            "WHERE id=? AND status=?",
            (new, error, result_json, run_id, expected))
        if commit:
            self.conn.commit()
        return cur.rowcount == 1

    def list_runs_by_status(self, statuses: tuple[str, ...],
                            updated_before: str | None = None,
                            limit: int = 200) -> list:
        """按状态集列 run（孤儿扫描：status 且 updated_at 超阈）。"""
        placeholders = ",".join("?" for _ in statuses)
        sql = (f"SELECT * FROM agent_chat_runs WHERE status IN ({placeholders})")
        params: list = list(statuses)
        if updated_before:
            sql += " AND updated_at < ?"
            params.append(updated_before)
        sql += " ORDER BY updated_at ASC LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    # ---------- agent_chat_run_steps：步骤明细 ----------

    def insert_step(self, run_id: str, step_idx: int, tool: str,
                    args_json: str | None = None, status: str = "pending",
                    commit: bool = True) -> int:
        """写入一步；(run_id, step_idx) 冲突抛 sqlite3.IntegrityError，
        由调用方按幂等恢复语义处置（查已有 success 则跳过）。"""
        cur = self.conn.execute(
            "INSERT INTO agent_chat_run_steps"
            "(run_id,step_idx,tool,args_json,status,created_at) "
            "VALUES(?,?,?,?,?,datetime('now'))",
            (run_id, step_idx, tool, args_json, status))
        if commit:
            self.conn.commit()
        return cur.lastrowid

    def get_step(self, run_id: str, step_idx: int):
        return self.conn.execute(
            "SELECT * FROM agent_chat_run_steps WHERE run_id=? AND step_idx=?",
            (run_id, step_idx)).fetchone()

    def list_steps(self, run_id: str) -> list:
        """按执行顺序取 run 全部步骤（证据链/断点恢复）。"""
        return self.conn.execute(
            "SELECT * FROM agent_chat_run_steps WHERE run_id=? "
            "ORDER BY step_idx ASC", (run_id,)).fetchall()

    def update_step(self, run_id: str, step_idx: int, status: str,
                    result_digest: str | None = None,
                    error: str | None = None, cost_ms: int = 0,
                    tool: str | None = None,
                    args_json: str | None = None) -> None:
        """步骤执行完毕后回填结果（失败也留痕）。

        tool/args_json 可选回填：改计划（modified_plan）替换执行时，
        同一 (run_id, step_idx) 行记录的实际执行工具与入参随之改写，
        保证证据链与真实执行一致（§5.6.2）。
        """
        sets = ["status=?", "result_digest=?", "error=?", "cost_ms=?"]
        vals: list = [status, result_digest, error, int(cost_ms)]
        if tool is not None:
            sets.append("tool=?")
            vals.append(tool)
        if args_json is not None:
            sets.append("args_json=?")
            vals.append(args_json)
        vals += [run_id, step_idx]
        self.conn.execute(
            f"UPDATE agent_chat_run_steps SET {', '.join(sets)} "
            "WHERE run_id=? AND step_idx=?", vals)
        self.conn.commit()
