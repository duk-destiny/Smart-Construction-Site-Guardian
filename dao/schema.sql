-- 海之子·动火安全智能体 数据库 Schema（SQLite 3, WAL）
-- 8 表 + 索引 + 审计仅追加触发器 + 3 视图
-- 全部使用 IF NOT EXISTS，保证 init_db() 幂等可重复执行
-- 类型约定：时间用 TEXT(ISO8601)，布尔用 INTEGER(0/1)；外键需 PRAGMA foreign_keys=ON

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,
    username   TEXT NOT NULL UNIQUE,
    pwd_hash   TEXT NOT NULL,
    role       TEXT NOT NULL CHECK(role IN ('safety','admin')),
    created_at TEXT NOT NULL
);

-- 任务表（一次检测任务）
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    permit_json TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL
);

-- 视觉检测结果
CREATE TABLE IF NOT EXISTS detections (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    frame_path      TEXT,
    cls             TEXT NOT NULL,
    conf            REAL NOT NULL,
    bbox_json       TEXT,
    violation_desc  TEXT
);

-- 规范合规结果
CREATE TABLE IF NOT EXISTS compliances (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    verdict     TEXT NOT NULL,
    clause_no   TEXT,
    clause_text TEXT,
    score       REAL
);

-- 风险融合结论（含人工改判）
CREATE TABLE IF NOT EXISTS risks (
    id               TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL REFERENCES tasks(id),
    risk_level       TEXT NOT NULL CHECK(risk_level IN ('低','一般','较大','重大')),
    reasons_json     TEXT,
    filtered_fp_json TEXT,
    override_level   TEXT,
    override_reason  TEXT
);

-- 整改工单 + 工人提示
CREATE TABLE IF NOT EXISTS work_orders (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL REFERENCES tasks(id),
    hazard_desc    TEXT NOT NULL,
    clause         TEXT,
    requirement    TEXT,
    risk_level     TEXT NOT NULL,
    worker_notice  TEXT,
    created_at     TEXT NOT NULL
);

-- 审计日志（仅追加）
CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT,
    action      TEXT NOT NULL,
    detail_json TEXT,
    created_at  TEXT NOT NULL
);

-- 实时检测记录（B3）：每帧每个检测项一行，用于历史追踪/合规率统计/日期筛选
CREATE TABLE IF NOT EXISTS detection_records (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,            -- 一次实时监测会话（进入页面生成）
    scene_id    TEXT,                     -- 来源场景（construction_ppe/hot_work）
    mode        TEXT NOT NULL DEFAULT 'realtime',  -- realtime / upload
    frame_status TEXT NOT NULL,           -- 该帧三级合规：合规/警告/不合规
    cls         TEXT NOT NULL,            -- 项目隐患键
    conf        REAL,
    severity    TEXT,                     -- safe/warning/critical
    created_at  TEXT NOT NULL
);

-- 规范文档登记
CREATE TABLE IF NOT EXISTS kb_docs (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    imported_by  TEXT,
    created_at   TEXT NOT NULL
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_tasks_user        ON tasks(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_detections_task   ON detections(task_id);
CREATE INDEX IF NOT EXISTS idx_compliances_task  ON compliances(task_id);
CREATE INDEX IF NOT EXISTS idx_risks_task        ON risks(task_id);
CREATE INDEX IF NOT EXISTS idx_workorders_task   ON work_orders(task_id);
CREATE INDEX IF NOT EXISTS idx_audit_user_time   ON audit_logs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_kbdocs_name       ON kb_docs(filename);
CREATE INDEX IF NOT EXISTS idx_detrec_session     ON detection_records(session_id);
CREATE INDEX IF NOT EXISTS idx_detrec_time        ON detection_records(created_at);
CREATE INDEX IF NOT EXISTS idx_detrec_cls         ON detection_records(cls);

-- 触发器：禁止更新审计日志
CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs is append-only: UPDATE denied');
END;

-- 触发器：禁止删除审计日志
CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs is append-only: DELETE denied');
END;

-- 视图：任务汇总（任务 + 风险等级 + 工单概要）
CREATE VIEW IF NOT EXISTS v_task_summary AS
SELECT t.id AS task_id, t.user_id, t.created_at,
       r.risk_level, r.override_level,
       w.hazard_desc, w.clause, w.requirement, w.worker_notice
FROM tasks t
LEFT JOIN risks r        ON r.task_id = t.id
LEFT JOIN work_orders w  ON w.task_id = t.id;

-- 视图：重大风险未改判任务
CREATE VIEW IF NOT EXISTS v_high_risk AS
SELECT task_id, risk_level, reasons_json
FROM risks
WHERE risk_level = '重大' AND override_level IS NULL;

-- 视图：最近审计（管理页）
CREATE VIEW IF NOT EXISTS v_audit_recent AS
SELECT a.id, a.user_id, u.username, a.action, a.detail_json, a.created_at
FROM audit_logs a
LEFT JOIN users u ON u.id = a.user_id
ORDER BY a.created_at DESC;
