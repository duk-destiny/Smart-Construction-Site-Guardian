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

    def insert(self, username: str, pwd_hash: str, role: str) -> str:
        uid = _new_id("u")
        self.conn.execute(
            "INSERT INTO users(id,username,pwd_hash,role,created_at) "
            "VALUES(?,?,?,?,datetime('now'))",
            (uid, username, pwd_hash, role))
        self.conn.commit()
        return uid

    def get_by_name(self, username: str):
        return self.conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)).fetchone()


class TaskDAO:
    """任务创建与查询。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, user_id: str, permit_json: str, status: str = "pending") -> str:
        tid = _new_id("t")
        self.conn.execute(
            "INSERT INTO tasks(id,user_id,permit_json,status,created_at) "
            "VALUES(?,?,?,?,datetime('now'))",
            (tid, user_id, permit_json, status))
        self.conn.commit()
        return tid

    def get(self, task_id: str):
        return self.conn.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def list_by_user(self, user_id: str):
        return self.conn.execute(
            "SELECT * FROM tasks WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)).fetchall()

    def update_status(self, task_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE tasks SET status=? WHERE id=?", (status, task_id))
        self.conn.commit()


class DetectionDAO:
    """批量写入视觉检测结果。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def bulk_insert(self, rows: list[dict]) -> None:
        data = [(
            _new_id("d"), r["task_id"], r.get("frame_path"), r["cls"],
            r["conf"], r.get("bbox_json"), r.get("violation_desc"))
            for r in rows]
        self.conn.executemany(
            "INSERT INTO detections(id,task_id,frame_path,cls,conf,bbox_json,violation_desc) "
            "VALUES(?,?,?,?,?,?,?)", data)
        self.conn.commit()


class ComplianceDAO:
    """批量写入规范合规结果。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def bulk_insert(self, rows: list[dict]) -> None:
        data = [(
            _new_id("c"), r["task_id"], r["verdict"], r.get("clause_no"),
            r.get("clause_text"), r.get("score")) for r in rows]
        self.conn.executemany(
            "INSERT INTO compliances(id,task_id,verdict,clause_no,clause_text,score) "
            "VALUES(?,?,?,?,?,?)", data)
        self.conn.commit()


class RiskDAO:
    """风险写入与人工改判。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, task_id: str, risk_level: str, reasons_json: str,
               filtered_fp_json: str) -> str:
        rid = _new_id("r")
        self.conn.execute(
            "INSERT INTO risks(id,task_id,risk_level,reasons_json,filtered_fp_json) "
            "VALUES(?,?,?,?,?)",
            (rid, task_id, risk_level, reasons_json, filtered_fp_json))
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
               risk_level: str, worker_notice: str) -> str:
        wid = _new_id("w")
        self.conn.execute(
            "INSERT INTO work_orders(id,task_id,hazard_desc,clause,requirement,risk_level,worker_notice,created_at) "
            "VALUES(?,?,?,?,?,?,?,datetime('now'))",
            (wid, task_id, hazard_desc, clause, requirement, risk_level, worker_notice))
        self.conn.commit()
        return wid

    def get_by_task(self, task_id: str):
        return self.conn.execute(
            "SELECT * FROM work_orders WHERE task_id=?", (task_id,)).fetchone()

    def list_all_with_risk(self):
        """历史研判列表：工单 + 风险等级 + 改判信息，按时间倒序。"""
        return self.conn.execute("""
            SELECT w.*, r.risk_level AS auto_level,
                   r.override_level, r.override_reason
            FROM work_orders w
            LEFT JOIN risks r ON r.task_id = w.task_id
            ORDER BY w.created_at DESC
        """).fetchall()

    def update_notice(self, task_id: str, worker_notice: str) -> None:
        """异步润色完成后回填工人提示（不阻塞主链路）。"""
        self.conn.execute(
            "UPDATE work_orders SET worker_notice=? WHERE task_id=?",
            (worker_notice, task_id))
        self.conn.commit()


class AuditDAO:
    """审计写入。仅 INSERT/SELECT，无 update/delete（C4 + DB 文档 §4/§6）。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, user_id: str | None, action: str, detail_json: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO audit_logs(user_id,action,detail_json,created_at) "
            "VALUES(?,?,?,datetime('now'))", (user_id, action, detail_json))
        self.conn.commit()
        return cur.lastrowid


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
            rows = [{"scene_id": None, "cls": "none", "conf": 1.0, "severity": "safe"}]
        data = [(
            _new_id("r"), session_id, r.get("scene_id"), mode, frame_status,
            r["cls"], r.get("conf"), r.get("severity"),
        ) for r in rows]
        self.conn.executemany(
            "INSERT INTO detection_records"
            "(id,session_id,scene_id,mode,frame_status,cls,conf,severity,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,datetime('now'))", data)
        self.conn.commit()

    def query(self, start: str | None = None, end: str | None = None,
              severity: str | None = None, cls: str | None = None) -> list:
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
        sql += " ORDER BY created_at DESC"
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
